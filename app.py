# -*- coding: utf-8 -*-
"""
发票管理工具 v5.1 GUI

核心：
- exe/脚本所在目录就是发票文件夹
- SQLite 保存发票缓存、人工确认/备注、购买记录、设置
- QQ 邮箱自动获取京东发票
- 购买记录按价格与发票一对一匹配
- 京东商品价与快递费分别匹配各自发票
- Excel 仅作为按需导出
"""

from __future__ import annotations

import base64
import ctypes
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

from invoice_extract import (
    AVAILABLE_FIELDS,
    DEFAULT_FIELDS,
    PARSER_VERSION,
    parse_invoice,
    sha256_file,
)
from jd_qq import fetch_jd_invoices
from purchase_tracker import (
    add_purchase,
    clear_purchases,
    delete_purchase,
    get_purchase,
    init_purchase_table,
    match_purchases,
    update_purchase,
)

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


# ---------- Windows DPAPI：安全保存 QQ 授权码 ----------

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
        return "plain:" + base64.b64encode(raw).decode("ascii")

    in_blob, in_buf = _blob_from_bytes(raw)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
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
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)
        _ = in_buf


def hide_windows_file(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    FILE_ATTRIBUTE_HIDDEN = 0x2
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs != 0xFFFFFFFF:
        ctypes.windll.kernel32.SetFileAttributesW(
            str(path), attrs | FILE_ATTRIBUTE_HIDDEN
        )


# ---------- 数据库 ----------

def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting_conn(conn: sqlite3.Connection, key: str):
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_setting_conn(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


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
        init_purchase_table(conn)

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
            set_setting_conn(
                conn,
                "selected_fields",
                json.dumps(fields, ensure_ascii=False),
            )

    hide_windows_file(DB_PATH)


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
    stats = {
        "total": len(pdfs),
        "reused": 0,
        "parsed": 0,
        "failed": 0,
        "removed": 0,
    }

    with connect_db() as conn:
        old_active = {
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
                    "SELECT * FROM invoices WHERE digest=?", (digest,)
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
                    progress(
                        f"解析失败：{pdf.name} | {type(e).__name__}: {e}"
                    )

        current_active = {
            row["digest"]
            for row in conn.execute(
                "SELECT digest FROM invoices WHERE active=1"
            ).fetchall()
        }
        stats["removed"] = len(old_active - current_active)

    return stats


def list_active(
    search: str = "",
    only_unconfirmed: bool = False,
    only_purchase_unmatched: bool = False,
):
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
        rows = conn.execute(sql, params).fetchall()
        if not only_purchase_unmatched:
            return rows
        matches = match_purchases(conn)
        used = set(matches["invoice_match_map"].keys())
        return [r for r in rows if str(r["digest"]) not in used]


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


def current_match_data() -> dict:
    with connect_db() as conn:
        return match_purchases(conn)


# ---------- Excel 导出 ----------

def export_excel(fields: list[str]) -> int:
    rows = list_active()
    matches = current_match_data()
    match_map = matches["invoice_match_map"]

    wb = Workbook()
    ws = wb.active
    ws.title = "发票汇总"

    headers = fields + ["购买匹配", "确认", "备注"]
    ws.append(headers)

    for row in rows:
        values = []
        for field in fields:
            values.append(row[FIELD_DB_MAP[field]])

        info = match_map.get(str(row["digest"]))
        matched_text = ""
        if info:
            matched_text = f"{info['purchase_name']}（{info['kind']}）"

        values.extend(
            [
                matched_text,
                "是" if row["confirmed"] else "否",
                row["note"],
            ]
        )
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
        "购买匹配": 28,
        "确认": 10,
        "备注": 30,
    }
    for idx, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(h, 18)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    pws = wb.create_sheet("购买记录")
    pws.append(
        [
            "名称",
            "商品价格",
            "有快递费",
            "快递费",
            "商品发票",
            "快递费发票",
            "完整匹配",
        ]
    )

    for result in matches["purchase_results"]:
        p = result["purchase"]
        comp = {x["kind"]: x for x in result["components"]}
        item_inv = comp.get("商品", {}).get("invoice")
        ship_inv = comp.get("快递费", {}).get("invoice")

        pws.append(
            [
                p["name"],
                p["item_price"],
                "是" if p["has_shipping"] else "否",
                p["shipping_fee"] if p["has_shipping"] else None,
                item_inv["filename"] if item_inv else "",
                ship_inv["filename"] if ship_inv else "",
                "是" if result["complete"] else "否",
            ]
        )

    for cell in pws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for col, width in enumerate([28, 14, 12, 14, 42, 42, 12], 1):
        pws.column_dimensions[get_column_letter(col)].width = width

    wb.save(EXPORT_PATH)
    wb.close()
    return len(rows)


# ---------- GUI ----------

class InvoiceApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(920, 600)

        init_db()

        self.editing_purchase_id: int | None = None
        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="0 张发票")

        self._build_ui()
        self.refresh_all()

        self.after(180, lambda: self.run_background_sync(silent=True))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text=f"当前目录：{BASE_DIR}").pack(side="left")
        ttk.Label(top, textvariable=self.count_var).pack(side="right")

        actions = ttk.Frame(self, padding=(12, 0, 12, 8))
        actions.pack(fill="x")

        ttk.Button(
            actions, text="同步文件夹", command=self.run_background_sync
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text="从QQ邮箱获取京东发票",
            command=self.open_jd_fetch_dialog,
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="导出 Excel", command=self.do_export
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="设置", command=self.open_settings
        ).pack(side="left", padx=6)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.invoice_tab = ttk.Frame(self.notebook)
        self.purchase_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.invoice_tab, text="发票管理")
        self.notebook.add(self.purchase_tab, text="购买记录")

        self._build_invoice_tab()
        self._build_purchase_tab()

        bottom = ttk.Frame(self, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="right")

    def _build_invoice_tab(self):
        filters = ttk.Frame(self.invoice_tab, padding=(8, 8, 8, 6))
        filters.pack(fill="x")

        ttk.Label(filters, text="搜索：").pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Entry(
            filters, textvariable=self.search_var, width=34
        ).pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", lambda *_: self.refresh_invoice_table())

        self.only_unconfirmed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters,
            text="只看未人工确认",
            variable=self.only_unconfirmed_var,
            command=self.refresh_invoice_table,
        ).pack(side="left", padx=(0, 12))

        self.only_purchase_unmatched_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters,
            text="只看未匹配购买记录的发票",
            variable=self.only_purchase_unmatched_var,
            command=self.refresh_invoice_table,
        ).pack(side="left")

        table_frame = ttk.Frame(self.invoice_tab, padding=(8, 0, 8, 6))
        table_frame.pack(fill="both", expand=True)

        self.invoice_tree = ttk.Treeview(
            table_frame, show="headings", selectmode="browse"
        )
        ybar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.invoice_tree.yview
        )
        xbar = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.invoice_tree.xview
        )
        self.invoice_tree.configure(
            yscrollcommand=ybar.set, xscrollcommand=xbar.set
        )

        self.invoice_tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.invoice_tree.bind("<Double-1>", self.on_invoice_double_click)
        self.invoice_tree.bind(
            "<Return>", lambda _e: self.toggle_selected_confirmed()
        )

        actions = ttk.Frame(self.invoice_tab, padding=(8, 0, 8, 8))
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="切换人工确认",
            command=self.toggle_selected_confirmed,
        ).pack(side="left")
        ttk.Button(
            actions, text="编辑备注", command=self.edit_selected_note
        ).pack(side="left", padx=(6, 0))

    def refresh_invoice_table(self):
        fields = selected_fields()
        columns = ["_digest"] + fields + ["购买匹配", "人工确认", "备注"]

        self.invoice_tree["columns"] = columns
        self.invoice_tree.column("_digest", width=0, stretch=False)
        self.invoice_tree.heading("_digest", text="")

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
            "购买匹配": 190,
            "人工确认": 90,
            "备注": 220,
        }

        for field in fields + ["购买匹配", "人工确认", "备注"]:
            self.invoice_tree.heading(field, text=field)
            self.invoice_tree.column(
                field, width=widths.get(field, 150), minwidth=70
            )

        for item in self.invoice_tree.get_children():
            self.invoice_tree.delete(item)

        rows = list_active(
            search=self.search_var.get(),
            only_unconfirmed=self.only_unconfirmed_var.get(),
            only_purchase_unmatched=self.only_purchase_unmatched_var.get(),
        )
        match_map = current_match_data()["invoice_match_map"]

        for row in rows:
            values = [row["digest"]]
            for field in fields:
                value = row[FIELD_DB_MAP[field]]
                if (
                    field in {"价税合计", "金额（不含税）", "税额"}
                    and isinstance(value, (int, float))
                ):
                    value = f"{value:.2f}"
                values.append("" if value is None else value)

            info = match_map.get(str(row["digest"]))
            match_text = (
                f"✓ {info['purchase_name']}（{info['kind']}）"
                if info
                else ""
            )
            values.extend(
                [
                    match_text,
                    "✓" if row["confirmed"] else "",
                    row["note"],
                ]
            )
            self.invoice_tree.insert("", "end", values=values)

        with connect_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE active=1"
            ).fetchone()[0]
        self.count_var.set(f"{total} 张发票")

    def selected_digest(self):
        sel = self.invoice_tree.selection()
        if not sel:
            return None
        values = self.invoice_tree.item(sel[0], "values")
        return values[0] if values else None

    def toggle_selected_confirmed(self):
        digest = self.selected_digest()
        if not digest:
            return
        with connect_db() as conn:
            row = conn.execute(
                "SELECT confirmed FROM invoices WHERE digest=?", (digest,)
            ).fetchone()
        if row:
            set_confirmed(digest, not bool(row["confirmed"]))
            self.refresh_invoice_table()

    def on_invoice_double_click(self, event):
        if self.invoice_tree.identify_region(event.x, event.y) != "cell":
            return
        col = self.invoice_tree.identify_column(event.x)
        try:
            idx = int(col.lstrip("#")) - 1
            name = self.invoice_tree["columns"][idx]
        except Exception:
            return
        if name == "人工确认":
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
            win, text=row["filename"], padding=(12, 12, 12, 6)
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
            self.refresh_invoice_table()

        ttk.Button(buttons, text="保存", command=save).pack(side="right")
        ttk.Button(
            buttons, text="取消", command=win.destroy
        ).pack(side="right", padx=(0, 6))

    def _build_purchase_tab(self):
        form = ttk.LabelFrame(
            self.purchase_tab, text="新增 / 编辑购买记录", padding=10
        )
        form.pack(fill="x", padx=8, pady=(8, 6))

        ttk.Label(form, text="名称").grid(row=0, column=0, sticky="w")
        self.purchase_name_var = tk.StringVar()
        ttk.Entry(
            form, textvariable=self.purchase_name_var, width=28
        ).grid(row=1, column=0, padx=(0, 12), sticky="ew")

        ttk.Label(form, text="商品价格").grid(row=0, column=1, sticky="w")
        self.purchase_price_var = tk.StringVar()
        ttk.Entry(
            form, textvariable=self.purchase_price_var, width=14
        ).grid(row=1, column=1, padx=(0, 12), sticky="ew")

        self.has_shipping_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            form,
            text="有快递费",
            variable=self.has_shipping_var,
            command=self._update_shipping_entry_state,
        ).grid(row=1, column=2, padx=(0, 12), sticky="w")

        ttk.Label(form, text="快递费").grid(row=0, column=3, sticky="w")
        self.shipping_fee_var = tk.StringVar(value="")
        self.shipping_entry = ttk.Entry(
            form, textvariable=self.shipping_fee_var, width=14
        )
        self.shipping_entry.grid(row=1, column=3, padx=(0, 12), sticky="ew")
        self._update_shipping_entry_state()

        self.purchase_save_button = ttk.Button(
            form, text="新增", command=self.save_purchase_form
        )
        self.purchase_save_button.grid(row=1, column=4, padx=(4, 4))
        ttk.Button(
            form, text="取消编辑", command=self.reset_purchase_form
        ).grid(row=1, column=5, padx=(4, 0))

        table_frame = ttk.Frame(self.purchase_tab, padding=(8, 0, 8, 6))
        table_frame.pack(fill="both", expand=True)

        columns = (
            "id",
            "名称",
            "商品价格",
            "有快递费",
            "快递费",
            "商品发票",
            "快递费发票",
            "状态",
        )
        self.purchase_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=11,
        )
        p_ybar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.purchase_tree.yview
        )
        self.purchase_tree.configure(yscrollcommand=p_ybar.set)

        widths = [60, 220, 110, 90, 100, 280, 280, 100]
        for name, width in zip(columns, widths):
            self.purchase_tree.heading(name, text=name)
            self.purchase_tree.column(
                name,
                width=width,
                minwidth=60,
                stretch=name in {"名称", "商品发票", "快递费发票"},
            )

        self.purchase_tree.grid(row=0, column=0, sticky="nsew")
        p_ybar.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.purchase_tree.bind(
            "<Double-1>", lambda _e: self.load_selected_purchase_for_edit()
        )

        p_actions = ttk.Frame(self.purchase_tab, padding=(8, 0, 8, 6))
        p_actions.pack(fill="x")
        ttk.Button(
            p_actions, text="编辑选中项", command=self.load_selected_purchase_for_edit
        ).pack(side="left")
        ttk.Button(
            p_actions, text="删除选中项", command=self.delete_selected_purchase
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            p_actions, text="一键清除全部", command=self.clear_all_purchases
        ).pack(side="left", padx=(6, 0))

        self.purchase_summary_var = tk.StringVar(value="")
        ttk.Label(
            p_actions, textvariable=self.purchase_summary_var
        ).pack(side="right")

        compare = ttk.Frame(self.purchase_tab, padding=(8, 0, 8, 8))
        compare.pack(fill="both", expand=True)
        compare.columnconfigure(0, weight=1)
        compare.columnconfigure(1, weight=1)
        compare.rowconfigure(1, weight=1)

        ttk.Label(compare, text="已记录购买，但缺少对应发票").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Label(compare, text="有发票，但没有对应购买记录").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        self.missing_tree = ttk.Treeview(
            compare,
            columns=("购买", "类型", "金额"),
            show="headings",
            height=6,
        )
        for c, w in [("购买", 220), ("类型", 90), ("金额", 100)]:
            self.missing_tree.heading(c, text=c)
            self.missing_tree.column(c, width=w)
        self.missing_tree.grid(
            row=1, column=0, sticky="nsew", padx=(0, 8), pady=(4, 0)
        )

        self.unused_invoice_tree = ttk.Treeview(
            compare,
            columns=("文件名", "价税合计"),
            show="headings",
            height=6,
        )
        self.unused_invoice_tree.heading("文件名", text="文件名")
        self.unused_invoice_tree.heading("价税合计", text="价税合计")
        self.unused_invoice_tree.column("文件名", width=360)
        self.unused_invoice_tree.column("价税合计", width=100)
        self.unused_invoice_tree.grid(
            row=1, column=1, sticky="nsew", padx=(8, 0), pady=(4, 0)
        )

    def _update_shipping_entry_state(self):
        if not hasattr(self, "shipping_entry"):
            return
        if self.has_shipping_var.get():
            self.shipping_entry.configure(state="normal")
        else:
            self.shipping_entry.configure(state="disabled")
            self.shipping_fee_var.set("")

    def reset_purchase_form(self):
        self.editing_purchase_id = None
        self.purchase_name_var.set("")
        self.purchase_price_var.set("")
        self.has_shipping_var.set(False)
        self.shipping_fee_var.set("")
        self._update_shipping_entry_state()
        self.purchase_save_button.configure(text="新增")

    def save_purchase_form(self):
        try:
            name = self.purchase_name_var.get().strip()
            item_price = self.purchase_price_var.get().strip()
            has_shipping = self.has_shipping_var.get()
            shipping_fee = (
                self.shipping_fee_var.get().strip()
                if has_shipping
                else 0
            )

            with connect_db() as conn:
                if self.editing_purchase_id is None:
                    add_purchase(
                        conn,
                        name,
                        item_price,
                        has_shipping,
                        shipping_fee,
                    )
                else:
                    update_purchase(
                        conn,
                        self.editing_purchase_id,
                        name,
                        item_price,
                        has_shipping,
                        shipping_fee,
                    )

            self.reset_purchase_form()
            self.refresh_all()
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
        except Exception as e:
            messagebox.showerror(
                APP_TITLE, f"保存购买记录失败：{type(e).__name__}: {e}"
            )

    def selected_purchase_id(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return None
        values = self.purchase_tree.item(sel[0], "values")
        if not values:
            return None
        try:
            return int(values[0])
        except Exception:
            return None

    def load_selected_purchase_for_edit(self):
        purchase_id = self.selected_purchase_id()
        if purchase_id is None:
            messagebox.showinfo(APP_TITLE, "请先选中一条购买记录。")
            return

        with connect_db() as conn:
            p = get_purchase(conn, purchase_id)
        if not p:
            return

        self.editing_purchase_id = purchase_id
        self.purchase_name_var.set(p["name"])
        self.purchase_price_var.set(f"{p['item_price']:.2f}")
        self.has_shipping_var.set(bool(p["has_shipping"]))
        self.shipping_fee_var.set(
            f"{p['shipping_fee']:.2f}" if p["has_shipping"] else ""
        )
        self._update_shipping_entry_state()
        self.purchase_save_button.configure(text="保存修改")

    def delete_selected_purchase(self):
        purchase_id = self.selected_purchase_id()
        if purchase_id is None:
            messagebox.showinfo(APP_TITLE, "请先选中一条购买记录。")
            return

        with connect_db() as conn:
            p = get_purchase(conn, purchase_id)
        if not p:
            return

        if not messagebox.askyesno(
            APP_TITLE, f"确定删除购买记录“{p['name']}”？"
        ):
            return

        with connect_db() as conn:
            delete_purchase(conn, purchase_id)
        if self.editing_purchase_id == purchase_id:
            self.reset_purchase_form()
        self.refresh_all()

    def clear_all_purchases(self):
        with connect_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        if not count:
            messagebox.showinfo(APP_TITLE, "当前没有购买记录。")
            return

        if not messagebox.askyesno(
            APP_TITLE,
            f"确定清空全部 {count} 条购买记录？\n\n此操作不会删除任何 PDF 发票。",
        ):
            return

        with connect_db() as conn:
            clear_purchases(conn)
        self.reset_purchase_form()
        self.refresh_all()

    def refresh_purchase_tab(self):
        with connect_db() as conn:
            data = match_purchases(conn)

        for tree in (
            self.purchase_tree,
            self.missing_tree,
            self.unused_invoice_tree,
        ):
            for item in tree.get_children():
                tree.delete(item)

        for result in data["purchase_results"]:
            p = result["purchase"]
            comp = {x["kind"]: x for x in result["components"]}
            item_inv = comp.get("商品", {}).get("invoice")
            ship_inv = comp.get("快递费", {}).get("invoice")

            self.purchase_tree.insert(
                "",
                "end",
                values=(
                    p["id"],
                    p["name"],
                    f"{p['item_price']:.2f}",
                    "✓" if p["has_shipping"] else "",
                    f"{p['shipping_fee']:.2f}" if p["has_shipping"] else "",
                    item_inv["filename"] if item_inv else "",
                    ship_inv["filename"] if ship_inv else "",
                    "✓ 完整" if result["complete"] else "缺发票",
                ),
            )

        for item in data["missing_components"]:
            self.missing_tree.insert(
                "",
                "end",
                values=(
                    item["purchase_name"],
                    item["kind"],
                    f"{item['price']:.2f}",
                ),
            )

        for inv in data["unused_invoices"]:
            total = inv["total"]
            total_text = (
                f"{total:.2f}" if isinstance(total, (int, float)) else ""
            )
            self.unused_invoice_tree.insert(
                "", "end", values=(inv["filename"], total_text)
            )

        self.purchase_summary_var.set(
            f"购买 {len(data['purchase_results'])} 项 | "
            f"缺票 {len(data['missing_components'])} 项 | "
            f"未对应发票 {len(data['unused_invoices'])} 张"
        )

    def refresh_all(self):
        self.refresh_invoice_table()
        self.refresh_purchase_tab()

    def set_status(self, text: str):
        self.after(0, lambda: self.status_var.set(text))

    def run_background_sync(self, silent=False):
        def worker():
            try:
                self.set_status("正在同步文件夹...")
                stats = sync_folder(progress=self.set_status)
                self.after(0, self.refresh_all)

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
                            msg + "\n\n有解析异常，可显示“提取状态”字段查看。",
                        ),
                    )
            except Exception as e:
                detail = traceback.format_exc()
                self.set_status("同步失败")
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP_TITLE,
                        f"同步失败：{type(e).__name__}: {e}\n\n"
                        + detail[-1800:],
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

        ttk.Label(outer, text="QQ邮箱16位授权码").pack(
            anchor="w", pady=(10, 2)
        )
        auth_var = tk.StringVar()
        ttk.Entry(outer, textvariable=auth_var, show="•").pack(fill="x")
        ttk.Label(
            outer,
            text="留空表示保留原授权码；Windows 下使用 DPAPI 加密保存。",
        ).pack(anchor="w", pady=(3, 8))

        ttk.Separator(outer).pack(fill="x", pady=8)
        ttk.Label(outer, text="发票表显示字段").pack(
            anchor="w", pady=(4, 6)
        )

        current = set(selected_fields())
        field_vars = {}
        fields_box = ttk.Frame(outer)
        fields_box.pack(fill="both", expand=True)

        for field in AVAILABLE_FIELDS:
            var = tk.BooleanVar(value=field in current)
            field_vars[field] = var
            ttk.Checkbutton(
                fields_box, text=field, variable=var
            ).pack(anchor="w")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))

        def save():
            fields = [f for f, v in field_vars.items() if v.get()]
            if not fields:
                messagebox.showwarning(
                    APP_TITLE, "至少选择一个显示字段。", parent=win
                )
                return

            email_addr = email_var.get().strip()
            if email_addr and "@" not in email_addr:
                messagebox.showwarning(
                    APP_TITLE, "QQ邮箱格式看起来不正确。", parent=win
                )
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
            self.refresh_all()
            self.set_status("设置已保存")

        def clear_auth():
            if messagebox.askyesno(
                APP_TITLE,
                "确定清除已保存的 QQ 邮箱授权码？",
                parent=win,
            ):
                set_setting("qq_auth_code", "")
                auth_var.set("")

        ttk.Button(
            buttons, text="保存", command=save
        ).pack(side="right")
        ttk.Button(
            buttons, text="取消", command=win.destroy
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            buttons, text="清除授权码", command=clear_auth
        ).pack(side="left")

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
                f"读取已保存授权码失败：{type(e).__name__}: {e}\n"
                "请在设置里重新保存授权码。",
            )
            return

        if not auth_code:
            messagebox.showinfo(
                APP_TITLE, "请在“设置”里重新保存 QQ 邮箱授权码。"
            )
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
        last_text = (
            last.replace("T", " ")[:16] if last else "尚未成功获取过"
        )
        ttk.Label(
            outer, text=f"上次成功获取：{last_text}"
        ).pack(anchor="w", pady=(4, 12))

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

        custom_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d 00:00")
        )
        ttk.Entry(
            outer, textvariable=custom_var
        ).pack(fill="x", padx=(24, 0), pady=(2, 6))
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
            if mode == "last":
                start_time = last
            elif mode == "custom":
                start_time = custom_var.get().strip()
            else:
                start_time = None

            win.destroy()
            self.run_jd_fetch(email_addr, auth_code, start_time)

        ttk.Button(
            buttons, text="开始获取", command=start
        ).pack(side="right")
        ttk.Button(
            buttons, text="取消", command=win.destroy
        ).pack(side="right", padx=(0, 6))

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

                if result["failed"] == 0:
                    set_setting(
                        "jd_last_fetch",
                        started.isoformat(timespec="seconds"),
                    )

                sync_stats = sync_folder(progress=self.set_status)
                self.after(0, self.refresh_all)

                msg = (
                    f"邮箱扫描 {result['emails']} 封，发现 {result['links']} 个 PDF 链接。\n"
                    f"新下载 {result['downloaded']}，已存在 {result['skipped']}，"
                    f"失败 {result['failed']}。\n"
                    f"当前文件夹共 {sync_stats['total']} 张 PDF。"
                )
                self.set_status(
                    f"邮箱获取完成：新下载 {result['downloaded']}，"
                    f"失败 {result['failed']}"
                )

                if result["failed"]:
                    detail = "\n".join(result["errors"][:5])
                    if len(result["errors"]) > 5:
                        detail += "\n……"
                    msg += (
                        "\n\n存在失败项，因此“上次成功获取时间”没有推进，"
                        "下次可以安全重试。\n\n" + detail
                    )
                    self.after(
                        0, lambda: messagebox.showwarning(APP_TITLE, msg)
                    )
                else:
                    self.after(
                        0, lambda: messagebox.showinfo(APP_TITLE, msg)
                    )

            except Exception as e:
                detail = traceback.format_exc()
                self.set_status("邮箱获取失败")
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP_TITLE,
                        f"邮箱获取失败：{type(e).__name__}: {e}\n\n"
                        + detail[-1800:],
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def do_export(self):
        try:
            count = export_excel(selected_fields())
            messagebox.showinfo(
                APP_TITLE,
                f"已导出 {count} 条发票记录：\n{EXPORT_PATH}",
            )
            self.set_status(f"已导出 Excel：{EXPORT_FILENAME}")
        except PermissionError:
            messagebox.showerror(
                APP_TITLE,
                f"无法写入 {EXPORT_FILENAME}。\n"
                "请先关闭 Excel/WPS 中打开的同名文件。",
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
