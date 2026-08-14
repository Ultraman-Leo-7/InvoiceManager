# -*- coding: utf-8 -*-
"""
发票管理工具 v5 GUI

- 当前 exe / 脚本所在目录就是“发票文件夹”
- SQLite 保存自动识别缓存 + 人工确认/备注
- 人工字段与自动解析字段分离，刷新不会覆盖确认状态
- 可从 QQ 邮箱获取京东电子发票
- 可按需导出 Excel
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import sys
import threading
import traceback
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from invoice_extract import AVAILABLE_FIELDS, DEFAULT_FIELDS, PARSER_VERSION, parse_invoice, sha256_file
from jd_qq import fetch_jd_invoices


APP_TITLE = "发票管理工具"
DB_FILENAME = ".invoice_manager.db"
EXPORT_FILENAME = "发票汇总.xlsx"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = app_dir()
DB_PATH = BASE_DIR / DB_FILENAME
EXPORT_PATH = BASE_DIR / EXPORT_FILENAME


# ---------- Windows DPAPI：保存 QQ 邮箱授权码 ----------

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(data: bytes):
    if not data:
        data = b"\x00"
    buf = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(
        len(data),
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buf


def protect_secret(text: str) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8")
    if os.name != "nt":
        # 项目主要面向 Windows；非 Windows 仅作为开发兼容。
        return "plain:" + base64.b64encode(raw).decode("ascii")

    in_blob, in_buf = _blob_from_bytes(raw)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()

    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _ = in_buf


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")

    if not value.startswith("dpapi:") or os.name != "nt":
        return ""

    encrypted = base64.b64decode(value[6:])
    in_blob, in_buf = _blob_from_bytes(encrypted)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()

    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw.decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _ = in_buf


def hide_windows_file(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    FILE_ATTRIBUTE_HIDDEN = 0x2
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs == 0xFFFFFFFF:
        return
    ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)


# ---------- 数据库 ----------

def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                digest TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                invoice_no TEXT,
                issue_date TEXT,
                buyer TEXT,
                seller TEXT,
                project TEXT,
                total REAL,
                amount_wo_tax REAL,
                tax REAL,
                issuer TEXT,
                parse_status TEXT,
                parser_version INTEGER NOT NULL DEFAULT 0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_invoices_filename
                ON invoices(filename);
            CREATE INDEX IF NOT EXISTS idx_invoices_invoice_no
                ON invoices(invoice_no);
            CREATE INDEX IF NOT EXISTS idx_invoices_active
                ON invoices(active);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        if get_setting_conn(conn, "selected_fields") is None:
            fields = DEFAULT_FIELDS.copy()
            legacy = BASE_DIR / "settings.json"
            if legacy.exists():
                try:
                    data = json.loads(legacy.read_text(encoding="utf-8-sig"))
                    raw = data.get("fields")
                    if isinstance(raw, list):
                        fields = [x for x in raw if x in AVAILABLE_FIELDS] or fields
                except Exception:
                    pass
            set_setting_conn(conn, "selected_fields", json.dumps(fields, ensure_ascii=False))

    hide_windows_file(DB_PATH)


def get_setting_conn(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting_conn(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def get_setting(key: str, default: str = "") -> str:
    with connect_db() as conn:
        value = get_setting_conn(conn, key)
        return default if value is None else value


def set_setting(key: str, value: str) -> None:
    with connect_db() as conn:
        set_setting_conn(conn, key, value)


def selected_fields() -> list[str]:
    try:
        raw = json.loads(get_setting("selected_fields", "[]"))
    except Exception:
        raw = []
    result = [x for x in raw if x in AVAILABLE_FIELDS]
    return result or DEFAULT_FIELDS.copy()


FIELD_DB_MAP = {
    "文件名": "filename",
    "发票号码": "invoice_no",
    "开票日期": "issue_date",
    "购买方名称": "buyer",
    "销售方名称": "seller",
    "项目名称": "project",
    "价税合计": "total",
    "金额（不含税）": "amount_wo_tax",
    "税额": "tax",
    "开票人": "issuer",
    "提取状态": "parse_status",
}


def invoice_record_to_db(record: dict) -> dict:
    return {
        "filename": record.get("文件名", ""),
        "invoice_no": record.get("发票号码", ""),
        "issue_date": record.get("开票日期", ""),
        "buyer": record.get("购买方名称", ""),
        "seller": record.get("销售方名称", ""),
        "project": record.get("项目名称", ""),
        "total": record.get("价税合计"),
        "amount_wo_tax": record.get("金额（不含税）"),
        "tax": record.get("税额"),
        "issuer": record.get("开票人", ""),
        "parse_status": record.get("提取状态", ""),
    }


def sync_folder(progress=None) -> dict:
    pdfs = sorted(BASE_DIR.glob("*.pdf"), key=lambda p: p.name.lower())
    now = datetime.now().isoformat(timespec="seconds")
    stats = {"total": len(pdfs), "reused": 0, "parsed": 0, "failed": 0, "removed": 0}

    with connect_db() as conn:
        old_active_digests = {
            row["digest"]
            for row in conn.execute(
                "SELECT digest FROM invoices WHERE active=1"
            ).fetchall()
        }
        conn.execute("UPDATE invoices SET active=0")

        for i, pdf in enumerate(pdfs, 1):
            if progress:
                progress(f"扫描 {i}/{len(pdfs)}：{pdf.name}")

            try:
                digest = sha256_file(pdf)
                row = conn.execute(
                    "SELECT * FROM invoices WHERE digest=?",
                    (digest,),
                ).fetchone()

                if row and int(row["parser_version"] or 0) == PARSER_VERSION:
                    conn.execute(
                        """
                        UPDATE invoices
                        SET filename=?, active=1, last_seen=?
                        WHERE digest=?
                        """,
                        (pdf.name, now, digest),
                    )
                    stats["reused"] += 1
                    continue

                # 解析新内容。若同文件名/同发票号对应旧记录，继承人工字段。
                record = parse_invoice(pdf)
                data = invoice_record_to_db(record)

                manual = conn.execute(
                    """
                    SELECT confirmed, note
                    FROM invoices
                    WHERE filename=?
                    ORDER BY active DESC, last_seen DESC
                    LIMIT 1
                    """,
                    (pdf.name,),
                ).fetchone()

                if not manual and data["invoice_no"]:
                    manual = conn.execute(
                        """
                        SELECT confirmed, note
                        FROM invoices
                        WHERE invoice_no=?
                        ORDER BY active DESC, last_seen DESC
                        LIMIT 1
                        """,
                        (data["invoice_no"],),
                    ).fetchone()

                confirmed = int(manual["confirmed"]) if manual else 0
                note = str(manual["note"] or "") if manual else ""

                conn.execute(
                    """
                    INSERT INTO invoices(
                        digest, filename, invoice_no, issue_date, buyer, seller,
                        project, total, amount_wo_tax, tax, issuer, parse_status,
                        parser_version, confirmed, note, active, last_seen
                    )
                    VALUES(
                        :digest, :filename, :invoice_no, :issue_date, :buyer, :seller,
                        :project, :total, :amount_wo_tax, :tax, :issuer, :parse_status,
                        :parser_version, :confirmed, :note, 1, :last_seen
                    )
                    ON CONFLICT(digest) DO UPDATE SET
                        filename=excluded.filename,
                        invoice_no=excluded.invoice_no,
                        issue_date=excluded.issue_date,
                        buyer=excluded.buyer,
                        seller=excluded.seller,
                        project=excluded.project,
                        total=excluded.total,
                        amount_wo_tax=excluded.amount_wo_tax,
                        tax=excluded.tax,
                        issuer=excluded.issuer,
                        parse_status=excluded.parse_status,
                        parser_version=excluded.parser_version,
                        active=1,
                        last_seen=excluded.last_seen
                    """,
                    {
                        "digest": digest,
                        "parser_version": PARSER_VERSION,
                        "confirmed": confirmed,
                        "note": note,
                        "last_seen": now,
                        **data,
                    },
                )
                stats["parsed"] += 1

            except Exception as e:
                stats["failed"] += 1
                if progress:
                    progress(f"解析失败：{pdf.name} | {type(e).__name__}: {e}")

        current_active_digests = {
            row["digest"]
            for row in conn.execute(
                "SELECT digest FROM invoices WHERE active=1"
            ).fetchall()
        }
        stats["removed"] = len(old_active_digests - current_active_digests)

    return stats


def list_active(search: str = "", only_unconfirmed: bool = False):
    sql = "SELECT * FROM invoices WHERE active=1"
    params = []

    if only_unconfirmed:
        sql += " AND confirmed=0"

    if search.strip():
        q = f"%{search.strip()}%"
        sql += """
            AND (
                filename LIKE ? OR project LIKE ? OR invoice_no LIKE ?
                OR seller LIKE ? OR note LIKE ?
            )
        """
        params.extend([q, q, q, q, q])

    sql += " ORDER BY filename COLLATE NOCASE"
    with connect_db() as conn:
        return conn.execute(sql, params).fetchall()


def set_confirmed(digest: str, value: bool) -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE invoices SET confirmed=? WHERE digest=?",
            (1 if value else 0, digest),
        )


def set_note(digest: str, note: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "UPDATE invoices SET note=? WHERE digest=?",
            (note, digest),
        )


# ---------- Excel 导出 ----------

def export_excel(fields: list[str]) -> int:
    rows = list_active()
    wb = Workbook()
    ws = wb.active
    ws.title = "发票汇总"

    headers = fields + ["确认", "备注"]
    ws.append(headers)

    for row in rows:
        values = []
        for field in fields:
            key = FIELD_DB_MAP[field]
            values.append(row[key])
        values.extend(["是" if row["confirmed"] else "否", row["note"]])
        ws.append(values)

    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = {
        "文件名": 42,
        "发票号码": 24,
        "开票日期": 16,
        "购买方名称": 28,
        "销售方名称": 32,
        "项目名称": 46,
        "价税合计": 14,
        "金额（不含税）": 16,
        "税额": 12,
        "开票人": 12,
        "提取状态": 38,
        "确认": 10,
        "备注": 30,
    }

    for idx, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(h, 18)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for row in ws.iter_rows(min_row=2):
        for idx, cell in enumerate(row, 1):
            h = headers[idx - 1]
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if h in {"价税合计", "金额（不含税）", "税额"}:
                cell.number_format = "0.00"

    wb.save(EXPORT_PATH)
    wb.close()
    return len(rows)


# ---------- GUI ----------

class InvoiceApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x680")
        self.minsize(860, 520)

        init_db()
        self._build_ui()
        self.refresh_table()

        # 首次打开自动同步一次。
        self.after(150, lambda: self.run_background_sync(silent=True))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")

        ttk.Label(top, text=f"当前目录：{BASE_DIR}").pack(side="left")
        self.count_var = tk.StringVar(value="0 张发票")
        ttk.Label(top, textvariable=self.count_var).pack(side="right")

        actions = ttk.Frame(self, padding=(12, 0, 12, 8))
        actions.pack(fill="x")

        ttk.Button(
            actions, text="同步文件夹", command=self.run_background_sync
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="从QQ邮箱获取京东发票", command=self.open_jd_fetch_dialog
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="导出 Excel", command=self.do_export
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="设置", command=self.open_settings
        ).pack(side="left", padx=6)

        filters = ttk.Frame(self, padding=(12, 0, 12, 8))
        filters.pack(fill="x")

        ttk.Label(filters, text="搜索：").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filters, textvariable=self.search_var, width=34)
        search_entry.pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", lambda *_: self.refresh_table())

        self.only_unconfirmed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters,
            text="只看未确认",
            variable=self.only_unconfirmed_var,
            command=self.refresh_table,
        ).pack(side="left")

        table_frame = ttk.Frame(self, padding=(12, 0, 12, 8))
        table_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table_frame, show="headings", selectmode="browse")
        ybar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Return>", lambda _e: self.toggle_selected_confirmed())

        bottom = ttk.Frame(self, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")

        ttk.Button(
            bottom, text="切换确认", command=self.toggle_selected_confirmed
        ).pack(side="left")
        ttk.Button(
            bottom, text="编辑备注", command=self.edit_selected_note
        ).pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="right")

    def set_status(self, text: str):
        self.after(0, lambda: self.status_var.set(text))

    def refresh_table(self):
        fields = selected_fields()
        columns = ["_digest"] + fields + ["确认", "备注"]

        self.tree["columns"] = columns
        self.tree.column("_digest", width=0, stretch=False)
        self.tree.heading("_digest", text="")

        widths = {
            "文件名": 280,
            "项目名称": 300,
            "价税合计": 100,
            "发票号码": 180,
            "开票日期": 120,
            "购买方名称": 220,
            "销售方名称": 240,
            "金额（不含税）": 120,
            "税额": 90,
            "开票人": 100,
            "提取状态": 240,
            "确认": 80,
            "备注": 220,
        }

        for field in fields + ["确认", "备注"]:
            self.tree.heading(field, text=field)
            self.tree.column(field, width=widths.get(field, 150), minwidth=70)

        for item in self.tree.get_children():
            self.tree.delete(item)

        rows = list_active(
            search=self.search_var.get() if hasattr(self, "search_var") else "",
            only_unconfirmed=(
                self.only_unconfirmed_var.get()
                if hasattr(self, "only_unconfirmed_var")
                else False
            ),
        )

        for row in rows:
            values = [row["digest"]]
            for field in fields:
                value = row[FIELD_DB_MAP[field]]
                if field in {"价税合计", "金额（不含税）", "税额"} and isinstance(value, (int, float)):
                    value = f"{value:.2f}"
                values.append("" if value is None else value)
            values.extend(["✓" if row["confirmed"] else "", row["note"]])
            self.tree.insert("", "end", values=values)

        self.count_var.set(f"{len(rows)} 张发票")

    def selected_digest(self):
        sel = self.tree.selection()
        if not sel:
            return None
        values = self.tree.item(sel[0], "values")
        return values[0] if values else None

    def toggle_selected_confirmed(self):
        digest = self.selected_digest()
        if not digest:
            return
        with connect_db() as conn:
            row = conn.execute(
                "SELECT confirmed FROM invoices WHERE digest=?",
                (digest,),
            ).fetchone()
        if not row:
            return
        set_confirmed(digest, not bool(row["confirmed"]))
        self.refresh_table()

    def on_tree_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        try:
            idx = int(column.lstrip("#")) - 1
            name = self.tree["columns"][idx]
        except Exception:
            return
        if name == "确认":
            self.toggle_selected_confirmed()

    def edit_selected_note(self):
        digest = self.selected_digest()
        if not digest:
            messagebox.showinfo(APP_TITLE, "请先选中一张发票。")
            return

        with connect_db() as conn:
            row = conn.execute(
                "SELECT filename, note FROM invoices WHERE digest=?",
                (digest,),
            ).fetchone()

        if not row:
            return

        win = tk.Toplevel(self)
        win.title("编辑备注")
        win.transient(self)
        win.grab_set()
        win.geometry("520x230")

        ttk.Label(
            win,
            text=row["filename"],
            padding=(12, 12, 12, 6),
        ).pack(anchor="w")

        text = tk.Text(win, height=6, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        text.insert("1.0", row["note"] or "")
        text.focus_set()

        buttons = ttk.Frame(win, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")

        def save():
            set_note(digest, text.get("1.0", "end").strip())
            win.destroy()
            self.refresh_table()

        ttk.Button(buttons, text="保存", command=save).pack(side="right")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="right", padx=(0, 6))

    def run_background_sync(self, silent=False):
        def worker():
            try:
                self.set_status("正在同步文件夹...")
                stats = sync_folder(progress=self.set_status)
                self.after(0, self.refresh_table)
                msg = (
                    f"同步完成：{stats['total']} 张；"
                    f"复用 {stats['reused']}，解析 {stats['parsed']}，"
                    f"移除 {stats['removed']}，异常 {stats['failed']}"
                )
                self.set_status(msg)
                if not silent and stats["failed"]:
                    self.after(
                        0,
                        lambda: messagebox.showwarning(
                            APP_TITLE,
                            msg + "\n\n有解析异常，可在“提取状态”字段中查看。",
                        ),
                    )
            except Exception as e:
                self.set_status("同步失败")
                detail = traceback.format_exc()
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP_TITLE,
                        f"同步失败：{type(e).__name__}: {e}\n\n{detail[-1800:]}",
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("设置")
        win.transient(self)
        win.grab_set()
        win.geometry("620x650")

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="QQ邮箱（京东发票获取）").pack(anchor="w")

        email_var = tk.StringVar(value=get_setting("qq_email", ""))
        ttk.Label(outer, text="QQ邮箱").pack(anchor="w", pady=(10, 2))
        ttk.Entry(outer, textvariable=email_var).pack(fill="x")

        ttk.Label(outer, text="QQ邮箱16位授权码").pack(anchor="w", pady=(10, 2))
        auth_var = tk.StringVar()
        auth_entry = ttk.Entry(outer, textvariable=auth_var, show="•")
        auth_entry.pack(fill="x")
        ttk.Label(
            outer,
            text="留空表示保留原授权码；Windows 下使用 DPAPI 加密保存。",
        ).pack(anchor="w", pady=(3, 8))

        sep = ttk.Separator(outer)
        sep.pack(fill="x", pady=8)

        ttk.Label(outer, text="表格显示字段").pack(anchor="w", pady=(4, 6))
        current = set(selected_fields())
        field_vars = {}
        fields_box = ttk.Frame(outer)
        fields_box.pack(fill="both", expand=True)

        for field in AVAILABLE_FIELDS:
            var = tk.BooleanVar(value=field in current)
            field_vars[field] = var
            ttk.Checkbutton(fields_box, text=field, variable=var).pack(anchor="w")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))

        def save():
            fields = [f for f, v in field_vars.items() if v.get()]
            if not fields:
                messagebox.showwarning(APP_TITLE, "至少选择一个显示字段。", parent=win)
                return

            email_addr = email_var.get().strip()
            if email_addr and "@" not in email_addr:
                messagebox.showwarning(APP_TITLE, "QQ邮箱格式看起来不正确。", parent=win)
                return

            try:
                with connect_db() as conn:
                    set_setting_conn(conn, "qq_email", email_addr)
                    set_setting_conn(
                        conn,
                        "selected_fields",
                        json.dumps(fields, ensure_ascii=False),
                    )
                    if auth_var.get().strip():
                        set_setting_conn(
                            conn,
                            "qq_auth_code",
                            protect_secret(auth_var.get().strip()),
                        )
            except Exception as e:
                messagebox.showerror(
                    APP_TITLE,
                    f"保存设置失败：{type(e).__name__}: {e}",
                    parent=win,
                )
                return

            win.destroy()
            self.refresh_table()
            self.set_status("设置已保存")

        def clear_auth():
            if messagebox.askyesno(
                APP_TITLE,
                "确定清除已保存的 QQ 邮箱授权码？",
                parent=win,
            ):
                set_setting("qq_auth_code", "")
                auth_var.set("")

        ttk.Button(buttons, text="保存", command=save).pack(side="right")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text="清除授权码", command=clear_auth).pack(side="left")

    def open_jd_fetch_dialog(self):
        email_addr = get_setting("qq_email", "").strip()
        enc = get_setting("qq_auth_code", "").strip()

        if not email_addr or not enc:
            messagebox.showinfo(
                APP_TITLE,
                "请先在“设置”里填写 QQ 邮箱和16位授权码。",
            )
            self.open_settings()
            return

        try:
            auth_code = unprotect_secret(enc)
        except Exception as e:
            messagebox.showerror(
                APP_TITLE,
                f"读取已保存授权码失败：{type(e).__name__}: {e}\n请在设置里重新保存授权码。",
            )
            return

        if not auth_code:
            messagebox.showinfo(APP_TITLE, "请在“设置”里重新保存 QQ 邮箱授权码。")
            return

        win = tk.Toplevel(self)
        win.title("从QQ邮箱获取京东发票")
        win.transient(self)
        win.grab_set()
        win.geometry("560x360")

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=f"邮箱：{email_addr}").pack(anchor="w")
        last = get_setting("jd_last_fetch", "")
        last_text = last.replace("T", " ")[:16] if last else "尚未成功获取过"
        ttk.Label(outer, text=f"上次成功获取：{last_text}").pack(anchor="w", pady=(4, 12))

        mode_var = tk.StringVar(value="last" if last else "all")

        ttk.Radiobutton(
            outer,
            text="从上次成功获取之后（推荐）",
            variable=mode_var,
            value="last",
            state="normal" if last else "disabled",
        ).pack(anchor="w", pady=3)
        ttk.Radiobutton(
            outer,
            text="自定义起始时间",
            variable=mode_var,
            value="custom",
        ).pack(anchor="w", pady=3)

        custom_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d 00:00"))
        ttk.Entry(outer, textvariable=custom_var).pack(fill="x", padx=(24, 0), pady=(2, 6))
        ttk.Label(
            outer,
            text="格式：YYYY-MM-DD 或 YYYY-MM-DD HH:MM；只填日期默认 00:00",
        ).pack(anchor="w", padx=(24, 0), pady=(0, 6))

        ttk.Radiobutton(
            outer,
            text="扫描全部历史收件箱",
            variable=mode_var,
            value="all",
        ).pack(anchor="w", pady=3)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(18, 0))

        def start():
            mode = mode_var.get()
            start_time = None
            if mode == "last":
                start_time = last
            elif mode == "custom":
                start_time = custom_var.get().strip()
            elif mode == "all":
                start_time = None

            win.destroy()
            self.run_jd_fetch(email_addr, auth_code, start_time)

        ttk.Button(buttons, text="开始获取", command=start).pack(side="right")
        ttk.Button(buttons, text="取消", command=win.destroy).pack(side="right", padx=(0, 6))

    def run_jd_fetch(self, email_addr: str, auth_code: str, start_time):
        def worker():
            started = datetime.now()
            try:
                self.set_status("正在连接 QQ 邮箱...")
                result = fetch_jd_invoices(
                    email_addr=email_addr,
                    auth_code=auth_code,
                    output_dir=BASE_DIR,
                    start_time=start_time,
                    progress=self.set_status,
                )

                # 只有没有下载失败时才推进“上次成功获取”时间，避免漏掉失败项。
                if result["failed"] == 0:
                    set_setting("jd_last_fetch", started.isoformat(timespec="seconds"))

                sync_stats = sync_folder(progress=self.set_status)
                self.after(0, self.refresh_table)

                msg = (
                    f"邮箱扫描 {result['emails']} 封，发现 {result['links']} 个 PDF 链接。\n"
                    f"新下载 {result['downloaded']}，已存在 {result['skipped']}，失败 {result['failed']}。\n"
                    f"当前文件夹共 {sync_stats['total']} 张 PDF。"
                )
                self.set_status(
                    f"邮箱获取完成：新下载 {result['downloaded']}，失败 {result['failed']}"
                )

                if result["failed"]:
                    detail = "\n".join(result["errors"][:5])
                    if len(result["errors"]) > 5:
                        detail += "\n……"
                    msg += (
                        "\n\n存在失败项，因此“上次成功获取时间”没有推进，"
                        "下次可以安全重试。\n\n" + detail
                    )
                    self.after(0, lambda: messagebox.showwarning(APP_TITLE, msg))
                else:
                    self.after(0, lambda: messagebox.showinfo(APP_TITLE, msg))

            except Exception as e:
                detail = traceback.format_exc()
                self.set_status("邮箱获取失败")
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP_TITLE,
                        f"邮箱获取失败：{type(e).__name__}: {e}\n\n{detail[-1800:]}",
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def do_export(self):
        try:
            count = export_excel(selected_fields())
            messagebox.showinfo(
                APP_TITLE,
                f"已导出 {count} 条记录：\n{EXPORT_PATH}",
            )
            self.set_status(f"已导出 Excel：{EXPORT_FILENAME}")
        except PermissionError:
            messagebox.showerror(
                APP_TITLE,
                f"无法写入 {EXPORT_FILENAME}。\n请先关闭 Excel/WPS 中打开的同名文件。",
            )
        except Exception as e:
            messagebox.showerror(
                APP_TITLE,
                f"导出失败：{type(e).__name__}: {e}",
            )


def main():
    app = InvoiceApp()
    app.mainloop()


if __name__ == "__main__":
    main()
