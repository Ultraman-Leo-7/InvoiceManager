from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: str, marker: str, text_to_insert: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if marker not in text:
        raise RuntimeError(f"marker not found in {path}: {marker[:100]!r}")
    p.write_text(text.replace(marker, text_to_insert + marker, 1), encoding="utf-8")


# ---------------- purchase_tracker.py ----------------
p = Path("purchase_tracker.py")
text = p.read_text(encoding="utf-8")

anchor = '''    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_audit_purchase_id ON purchase_audit(purchase_id, audit_id)"
    )

    # SQLite triggers ensure history is written even if a future caller changes data
'''
addition = '''    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_audit_purchase_id ON purchase_audit(purchase_id, audit_id)"
    )

    # Manual invoice overrides take precedence over price matching.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS purchase_invoice_overrides (
            purchase_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            invoice_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(purchase_id, kind),
            UNIQUE(invoice_digest)
        );

        CREATE TABLE IF NOT EXISTS purchase_invoice_override_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            invoice_digest TEXT NOT NULL,
            action TEXT NOT NULL,
            audited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_insert
        AFTER INSERT ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(NEW.purchase_id, NEW.kind, NEW.invoice_digest, 'SET');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_update
        BEFORE UPDATE ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(OLD.purchase_id, OLD.kind, OLD.invoice_digest, 'REPLACE_OLD');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_delete
        BEFORE DELETE ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(OLD.purchase_id, OLD.kind, OLD.invoice_digest, 'CLEAR');
        END;
        """
    )

    # SQLite triggers ensure history is written even if a future caller changes data
'''
if anchor not in text:
    raise RuntimeError("purchase override schema anchor not found")
text = text.replace(anchor, addition, 1)

old_update_tail = '''    conn.execute(
        """
        UPDATE purchases
        SET name=?, item_price=?, has_shipping=?, shipping_fee=?, updated_at=?
        WHERE id=?
        """,
        (
            name,
            item_price,
            1 if has_shipping else 0,
            shipping_fee,
            datetime.now().isoformat(timespec="seconds"),
            int(purchase_id),
        ),
    )


def delete_purchase(conn: sqlite3.Connection, purchase_id: int) -> None:
    conn.execute("DELETE FROM purchases WHERE id=?", (int(purchase_id),))


def clear_purchases(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM purchases")
'''
new_update_tail = '''    conn.execute(
        """
        UPDATE purchases
        SET name=?, item_price=?, has_shipping=?, shipping_fee=?, updated_at=?
        WHERE id=?
        """,
        (
            name,
            item_price,
            1 if has_shipping else 0,
            shipping_fee,
            datetime.now().isoformat(timespec="seconds"),
            int(purchase_id),
        ),
    )
    if not has_shipping:
        conn.execute(
            "DELETE FROM purchase_invoice_overrides WHERE purchase_id=? AND kind='快递费'",
            (int(purchase_id),),
        )


def delete_purchase(conn: sqlite3.Connection, purchase_id: int) -> None:
    conn.execute("DELETE FROM purchase_invoice_overrides WHERE purchase_id=?", (int(purchase_id),))
    conn.execute("DELETE FROM purchases WHERE id=?", (int(purchase_id),))


def clear_purchases(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM purchase_invoice_overrides")
    conn.execute("DELETE FROM purchases")
'''
if old_update_tail not in text:
    raise RuntimeError("purchase update/delete anchor not found")
text = text.replace(old_update_tail, new_update_tail, 1)

manual_helpers_marker = '''def required_components(purchase) -> list[dict]:
'''
manual_helpers = '''def set_manual_match(
    conn: sqlite3.Connection,
    purchase_id: int,
    kind: str,
    invoice_digest: str,
) -> None:
    purchase = get_purchase(conn, purchase_id)
    if not purchase:
        raise ValueError("购买记录不存在")
    valid_kinds = {x["kind"] for x in required_components(purchase)}
    if kind not in valid_kinds:
        raise ValueError(f"当前购买记录没有“{kind}”这一项")
    invoice = conn.execute(
        "SELECT digest FROM invoices WHERE digest=? AND active=1",
        (str(invoice_digest),),
    ).fetchone()
    if not invoice:
        raise ValueError("所选发票当前不存在或已被移出发票文件夹")
    owner = conn.execute(
        "SELECT purchase_id, kind FROM purchase_invoice_overrides WHERE invoice_digest=?",
        (str(invoice_digest),),
    ).fetchone()
    if owner and (int(owner["purchase_id"]), str(owner["kind"])) != (int(purchase_id), str(kind)):
        raise ValueError(
            f"这张发票已经被手动关联到购买记录 #{owner['purchase_id']} 的“{owner['kind']}”。"
            "请先在原记录中恢复自动匹配，再重新关联。"
        )
    conn.execute(
        """
        INSERT INTO purchase_invoice_overrides(purchase_id, kind, invoice_digest, created_at)
        VALUES(?,?,?,?)
        ON CONFLICT(purchase_id, kind) DO UPDATE SET
            invoice_digest=excluded.invoice_digest,
            created_at=excluded.created_at
        """,
        (
            int(purchase_id),
            str(kind),
            str(invoice_digest),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def clear_manual_match(conn: sqlite3.Connection, purchase_id: int, kind: str) -> None:
    conn.execute(
        "DELETE FROM purchase_invoice_overrides WHERE purchase_id=? AND kind=?",
        (int(purchase_id), str(kind)),
    )


def list_manual_matches(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM purchase_invoice_overrides ORDER BY purchase_id, kind"
    ).fetchall()


'''
if manual_helpers_marker not in text:
    raise RuntimeError("required_components marker not found")
text = text.replace(manual_helpers_marker, manual_helpers + manual_helpers_marker, 1)

match_marker = "def match_purchases(conn: sqlite3.Connection) -> dict:\n"
pos = text.find(match_marker)
if pos < 0:
    raise RuntimeError("match_purchases marker not found")
text = text[:pos] + '''def match_purchases(conn: sqlite3.Connection) -> dict:
    """One-to-one matching: manual overrides first, then exact-price automatic matching."""
    purchases = list_purchases(conn)
    invoices = conn.execute(
        """
        SELECT * FROM invoices
        WHERE active=1
        ORDER BY filename COLLATE NOCASE, digest
        """
    ).fetchall()
    invoice_by_digest = {str(inv["digest"]): inv for inv in invoices}

    overrides = {
        (int(row["purchase_id"]), str(row["kind"])): str(row["invoice_digest"])
        for row in conn.execute(
            "SELECT purchase_id, kind, invoice_digest FROM purchase_invoice_overrides"
        ).fetchall()
    }
    reserved_manual = {
        digest for digest in overrides.values() if digest in invoice_by_digest
    }

    by_price: dict[int, list] = {}
    for inv in invoices:
        cents = _money_to_cents(inv["total"])
        if cents is None:
            continue
        by_price.setdefault(cents, []).append(inv)

    used_digests: set[str] = set()
    invoice_match_map: dict[str, dict] = {}
    purchase_results: list[dict] = []
    missing_components: list[dict] = []

    for purchase in purchases:
        component_results = []
        purchase_id = int(purchase["id"])

        for component in required_components(purchase):
            kind = component["kind"]
            key = (purchase_id, kind)
            manual_digest = overrides.get(key)
            manual = manual_digest is not None
            manual_missing = False
            found = None

            if manual:
                found = invoice_by_digest.get(manual_digest)
                manual_missing = found is None
            else:
                candidates = by_price.get(component["cents"], [])
                while candidates:
                    candidate = candidates.pop(0)
                    digest = str(candidate["digest"])
                    if digest in used_digests or digest in reserved_manual:
                        continue
                    found = candidate
                    break

            component_result = {
                "kind": kind,
                "price": component["price"],
                "invoice": found,
                "matched": found is not None,
                "manual": manual,
                "manual_missing": manual_missing,
            }
            component_results.append(component_result)

            if found is not None:
                digest = str(found["digest"])
                used_digests.add(digest)
                invoice_match_map[digest] = {
                    "purchase_id": purchase_id,
                    "purchase_name": str(purchase["name"]),
                    "kind": kind,
                    "price": component["price"],
                    "manual": manual,
                }
            else:
                missing_components.append(
                    {
                        "purchase_id": purchase_id,
                        "purchase_name": str(purchase["name"]),
                        "kind": kind,
                        "price": component["price"],
                        "manual": manual,
                        "manual_missing": manual_missing,
                    }
                )

        purchase_results.append(
            {
                "purchase": purchase,
                "components": component_results,
                "complete": all(x["matched"] for x in component_results),
            }
        )

    unused_invoices = [
        inv for inv in invoices if str(inv["digest"]) not in used_digests
    ]

    return {
        "purchase_results": purchase_results,
        "invoice_match_map": invoice_match_map,
        "missing_components": missing_components,
        "unused_invoices": unused_invoices,
    }
'''
p.write_text(text, encoding="utf-8")


# ---------------- app.py ----------------
replace_once("app.py", '"""InvoiceManager v5.2.9 GUI."""', '"""InvoiceManager v5.2.10 GUI."""')
replace_once("app.py", 'APP_VERSION = "5.2.9"', 'APP_VERSION = "5.2.10"')
replace_once("app.py", '    clear_purchases,\n    delete_purchase,', '    clear_manual_match,\n    clear_purchases,\n    delete_purchase,')
replace_once("app.py", '    match_purchases,\n    update_purchase,', '    match_purchases,\n    required_components,\n    set_manual_match,\n    update_purchase,')
replace_once("app.py", 'self.search_scope_var = tk.StringVar(value="全部字段")', 'self.search_scope_var = tk.StringVar(value="全部显示字段")')

old_toolbar = '''        actions = ttk.Frame(self, padding=(12, 0, 12, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="刷新发票文件夹", command=self.run_background_sync).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="从QQ邮箱获取京东发票", command=self.open_jd_fetch_dialog).pack(side="left", padx=6)
        ttk.Button(actions, text="导出 Excel", command=self.do_export).pack(side="left", padx=6)
        ttk.Button(actions, text="数据备份", command=self.open_backup_dialog).pack(side="left", padx=6)
        ttk.Button(actions, text="检查更新", command=self.check_for_updates).pack(side="left", padx=6)
        ttk.Button(actions, text="设置", command=self.open_settings).pack(side="left", padx=6)
'''
new_toolbar = '''        actions = ttk.Frame(self, padding=(12, 0, 12, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="从QQ邮箱获取京东发票", command=self.open_jd_fetch_dialog).pack(side="left")

        more_button = ttk.Menubutton(actions, text="更多")
        more_menu = tk.Menu(more_button, tearoff=False)
        more_menu.add_command(label="设置", command=self.open_settings)
        more_menu.add_separator()
        more_menu.add_command(label="刷新发票文件夹", command=self.run_background_sync)
        more_menu.add_command(label="导出 Excel", command=self.do_export)
        more_menu.add_command(label="数据备份与恢复", command=self.open_backup_dialog)
        more_menu.add_command(label="检查更新", command=self.check_for_updates)
        more_button["menu"] = more_menu
        more_button.pack(side="left", padx=(8, 0))
'''
replace_once("app.py", old_toolbar, new_toolbar)

sort_marker = '''    def _build_invoice_tab(self):
'''
visible_scope_method = '''    def _visible_search_scopes(self):
        return ["全部显示字段"] + selected_fields() + ["购买匹配", "人工确认", "备注"]

'''
insert_before("app.py", sort_marker, visible_scope_method)

old_scope = '''        scope = ttk.Combobox(
            filters,
            textvariable=self.search_scope_var,
            values=SEARCH_SCOPES,
            state="readonly",
            width=15,
        )
        scope.pack(side="left", padx=(4, 4))
        scope.bind("<<ComboboxSelected>>", lambda _e: self.refresh_invoice_table())
'''
new_scope = '''        self.search_scope_box = ttk.Combobox(
            filters,
            textvariable=self.search_scope_var,
            values=self._visible_search_scopes(),
            state="readonly",
            width=15,
        )
        self.search_scope_box.pack(side="left", padx=(4, 4))
        self.search_scope_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_invoice_table())
'''
replace_once("app.py", old_scope, new_scope)

old_refresh_start = '''        fields = selected_fields()
        columns = ["_digest"] + fields + ["购买匹配", "人工确认", "备注"]
'''
new_refresh_start = '''        fields = selected_fields()
        scopes = self._visible_search_scopes()
        self.search_scope_box["values"] = scopes
        if self.search_scope_var.get() not in scopes:
            self.search_scope_var.set("全部显示字段")
        columns = ["_digest"] + fields + ["购买匹配", "人工确认", "备注"]
'''
replace_once("app.py", old_refresh_start, new_refresh_start)

old_haystack = '''                haystack = " ".join(values.values()) if scope == "全部字段" else values.get(scope, "")
'''
new_haystack = '''                if scope == "全部显示字段":
                    visible = [name for name in scopes if name != "全部显示字段"]
                    haystack = " ".join(values.get(name, "") for name in visible)
                else:
                    haystack = values.get(scope, "")
'''
replace_once("app.py", old_haystack, new_haystack)

replace_once(
    "app.py",
    'data["购买匹配"] = f"{match_info[\'purchase_name\']} {match_info[\'kind\']}" if match_info else ""',
    'data["购买匹配"] = (f"{match_info[\'purchase_name\']} {match_info[\'kind\']}" + (" 手动" if match_info.get("manual") else "")) if match_info else ""',
)
replace_once(
    "app.py",
    'f"{info[\'purchase_name\']}（{info[\'kind\']}）" if info else "",',
    'f"{info[\'purchase_name\']}（{info[\'kind\']}{\'，手动\' if info.get(\'manual\') else \'\'}）" if info else "",',
)
replace_once(
    "app.py",
    'f"✓ {info[\'purchase_name\']}（{info[\'kind\']}）" if info else "",',
    'f"✓ {info[\'purchase_name\']}（{info[\'kind\']}{\'，手动\' if info.get(\'manual\') else \'\'}）" if info else "",',
)

old_purchase_actions = '''        ttk.Button(p_actions, text="编辑选中项", command=self.load_selected_purchase_for_edit).pack(side="left")
        ttk.Button(p_actions, text="删除选中项", command=self.delete_selected_purchase).pack(side="left", padx=(6, 0))
'''
new_purchase_actions = '''        ttk.Button(p_actions, text="编辑选中项", command=self.load_selected_purchase_for_edit).pack(side="left")
        ttk.Button(p_actions, text="调整发票关联", command=self.open_manual_match_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(p_actions, text="删除选中项", command=self.delete_selected_purchase).pack(side="left", padx=(6, 0))
'''
replace_once("app.py", old_purchase_actions, new_purchase_actions)

manual_dialog_marker = '''    def _update_shipping_entry_state(self):
'''
manual_dialog = '''    def open_manual_match_dialog(self):
        ids = self.selected_purchase_ids()
        if len(ids) != 1:
            messagebox.showinfo(APP_TITLE, "调整关联时请只选中一条购买记录。")
            return
        purchase_id = ids[0]
        with connect_db() as conn:
            purchase = get_purchase(conn, purchase_id)
        if not purchase:
            return

        components = required_components(purchase)
        component_by_kind = {x["kind"]: x for x in components}

        win = tk.Toplevel(self)
        win.title("调整发票关联")
        win.transient(self)
        win.grab_set()
        win.geometry("860x560")
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text=f"购买记录 #{purchase_id}：{purchase['name']}",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="手动关联优先于自动金额匹配；恢复自动匹配后，程序会重新按价格分配。",
        ).pack(anchor="w", pady=(4, 10))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="要调整：").pack(side="left")
        kind_var = tk.StringVar(value=components[0]["kind"])
        kind_box = ttk.Combobox(
            controls,
            textvariable=kind_var,
            values=[x["kind"] for x in components],
            state="readonly",
            width=10,
        )
        kind_box.pack(side="left", padx=(4, 14))
        ttk.Label(controls, text="搜索发票：").pack(side="left")
        search_var = tk.StringVar()
        ttk.Entry(controls, textvariable=search_var, width=32).pack(side="left", padx=(4, 0))

        current_var = tk.StringVar()
        ttk.Label(outer, textvariable=current_var).pack(anchor="w", pady=(0, 6))

        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        columns = ("_digest", "文件名", "价税合计", "项目名称", "当前关联")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("_digest", text="")
        tree.column("_digest", width=0, stretch=False)
        for name, width in [("文件名", 320), ("价税合计", 100), ("项目名称", 220), ("当前关联", 240)]:
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=80)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ybar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        def fresh_data():
            with connect_db() as conn:
                current_purchase = get_purchase(conn, purchase_id)
                data = match_purchases(conn)
                invoices = conn.execute(
                    "SELECT digest, filename, total, project FROM invoices WHERE active=1 ORDER BY filename COLLATE NOCASE"
                ).fetchall()
            result = next(
                (x for x in data["purchase_results"] if int(x["purchase"]["id"]) == purchase_id),
                None,
            )
            return current_purchase, data, invoices, result

        def refresh_dialog(*_args):
            for item in tree.get_children():
                tree.delete(item)
            current_purchase, data, invoices, result = fresh_data()
            if not current_purchase or not result:
                current_var.set("购买记录已不存在。")
                return
            kind = kind_var.get()
            component = next((x for x in result["components"] if x["kind"] == kind), None)
            current_digest = None
            if component and component.get("invoice") is not None:
                current_digest = str(component["invoice"]["digest"])
                mode = "手动关联" if component.get("manual") else "自动匹配"
                current_var.set(f"当前：{mode} → {component['invoice']['filename']}")
            elif component and component.get("manual_missing"):
                current_var.set("当前：手动关联的发票已不在当前文件夹；恢复该 PDF 后会重新生效。")
            else:
                current_var.set("当前：未匹配")

            query = search_var.get().strip().lower()
            match_map = data["invoice_match_map"]
            selected_item = None
            for inv in invoices:
                total = inv["total"]
                total_text = f"{total:.2f}" if isinstance(total, (int, float)) else ""
                project = str(inv["project"] or "")
                haystack = f"{inv['filename']} {total_text} {project}".lower()
                if query and query not in haystack:
                    continue
                digest = str(inv["digest"])
                info = match_map.get(digest)
                association = ""
                if info:
                    mode = "手动" if info.get("manual") else "自动"
                    association = f"{info['purchase_name']}（{info['kind']}，{mode}）"
                item_id = tree.insert(
                    "",
                    "end",
                    values=(digest, inv["filename"], total_text, project, association),
                )
                if digest == current_digest:
                    selected_item = item_id
            if selected_item:
                tree.selection_set(selected_item)
                tree.see(selected_item)

        def set_selected_manual():
            selection = tree.selection()
            if len(selection) != 1:
                messagebox.showinfo(APP_TITLE, "请先选择一张发票。", parent=win)
                return
            values = tree.item(selection[0], "values")
            digest = str(values[0])
            filename = str(values[1])
            kind = kind_var.get()
            expected = component_by_kind[kind]["price"]
            try:
                actual = float(values[2])
            except Exception:
                actual = None
            if actual is not None and int(round(actual * 100)) != int(round(expected * 100)):
                if not messagebox.askyesno(
                    APP_TITLE,
                    f"金额不同，仍要手动关联吗？\n\n购买记录“{kind}”：¥{expected:.2f}\n"
                    f"所选发票：¥{actual:.2f}\n{filename}",
                    parent=win,
                ):
                    return
            if not self._create_local_safety_backup_or_block("before-manual-invoice-match"):
                return
            try:
                with connect_db() as conn:
                    set_manual_match(conn, purchase_id, kind, digest)
            except ValueError as e:
                messagebox.showwarning(APP_TITLE, str(e), parent=win)
                return
            self.refresh_all()
            self.schedule_auto_cloud_backup()
            self.set_status(f"已手动关联：{purchase['name']}（{kind}） → {filename}")
            refresh_dialog()

        def restore_auto():
            kind = kind_var.get()
            if not self._create_local_safety_backup_or_block("before-clear-manual-invoice-match"):
                return
            with connect_db() as conn:
                clear_manual_match(conn, purchase_id, kind)
            self.refresh_all()
            self.schedule_auto_cloud_backup()
            self.set_status(f"已恢复自动匹配：{purchase['name']}（{kind}）")
            refresh_dialog()

        kind_box.bind("<<ComboboxSelected>>", refresh_dialog)
        search_var.trace_add("write", refresh_dialog)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="设为手动关联", command=set_selected_manual).pack(side="left")
        ttk.Button(buttons, text="恢复自动匹配", command=restore_auto).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=win.destroy).pack(side="right")
        refresh_dialog()

'''
insert_before("app.py", manual_dialog_marker, manual_dialog)

old_purchase_insert = '''            self.purchase_tree.insert("", "end", values=(
                p["id"],
                p["name"],
                f"{p['item_price']:.2f}",
                "✓" if p["has_shipping"] else "",
                f"{p['shipping_fee']:.2f}" if p["has_shipping"] else "",
                item_inv["filename"] if item_inv else "",
                ship_inv["filename"] if ship_inv else "",
                "✓ 完整" if result["complete"] else "缺发票",
            ))
'''
new_purchase_insert = '''            item_text = item_inv["filename"] if item_inv else ""
            ship_text = ship_inv["filename"] if ship_inv else ""
            if item_inv and comp.get("商品", {}).get("manual"):
                item_text = "手动：" + item_text
            if ship_inv and comp.get("快递费", {}).get("manual"):
                ship_text = "手动：" + ship_text
            has_manual = any(x.get("manual") for x in result["components"])
            status_text = "✓ 完整" if result["complete"] else "缺发票"
            if has_manual:
                status_text += " · 手动"
            self.purchase_tree.insert("", "end", values=(
                p["id"],
                p["name"],
                f"{p['item_price']:.2f}",
                "✓" if p["has_shipping"] else "",
                f"{p['shipping_fee']:.2f}" if p["has_shipping"] else "",
                item_text,
                ship_text,
                status_text,
            ))
'''
replace_once("app.py", old_purchase_insert, new_purchase_insert)

# ---------------- README.md ----------------
replace_once(
    "README.md",
    '| 发票核对 | 当前按价格精确匹配，商品价与快递费分别匹配 |',
    '| 发票核对 | 默认按价格精确匹配，商品价与快递费分别匹配；支持手动调整关联 |',
)
replace_once(
    "README.md",
    '| 搜索与排序 | 支持全字段/指定字段搜索，点击表头升降序排序 |',
    '| 搜索与排序 | 搜索范围只跟随当前显示字段，点击表头升降序排序 |',
)

replace_once(
    "README.md",
    '''也可以点击：

**刷新发票文件夹**

立即重新扫描。
''',
    '''正常使用不需要手动刷新。为了减少主界面按钮，手动刷新已收进 **更多 → 刷新发票文件夹**，只在需要立即重新扫描时使用。
''',
)
replace_once("README.md", '**数据备份**', '**更多 → 数据备份与恢复**')
replace_once("README.md", '主界面有：\n\n**检查更新**', '入口位于：\n\n**更多 → 检查更新**')

old_search_doc = '''发票页面支持搜索范围：

- 全部字段
- 文件名
- 项目名称
- 价税合计
- 发票号码
- 开票日期
- 购买方名称
- 销售方名称
- 金额（不含税）
- 税额
- 开票人
- 提取状态
- 购买匹配
- 人工确认
- 备注

点击表头可切换升序/降序。
'''
new_search_doc = '''发票页面的搜索范围会 **跟随当前实际显示的字段**。

例如设置里只显示“文件名、项目名称、价税合计”，搜索下拉框就只提供这些字段，再加上固定显示的“购买匹配、人工确认、备注”和“全部显示字段”。隐藏字段不会继续占据搜索菜单。

点击表头可切换升序/降序。
'''
replace_once("README.md", old_search_doc, new_search_doc)

manual_quick_marker = '''## 9. 人工确认与备注
'''
manual_quick = '''## 9. 手动调整发票关联

自动价格匹配出现错位时，在 **购买记录** 中选中一条记录，点击 **调整发票关联**。

可以分别针对：

- 商品；
- 快递费（如果该购买记录有快递费）

选择一张实际正确的发票并设为 **手动关联**。手动关联优先于自动价格匹配，即使存在多个同价发票，也会固定使用你指定的那张。

如果所选发票金额和购买记录金额不同，程序会额外确认，但仍允许人工指定。

点击 **恢复自动匹配** 后会删除这一人工覆盖关系，重新交给价格匹配算法分配。

一张发票最多只能被一个手动关联占用；手动调整前会创建本地安全快照，修改后也会参与坚果云自动备份。

## 10. 人工确认与备注
'''
replace_once("README.md", manual_quick_marker, manual_quick)
replace_once("README.md", '## 10. 删除发票', '## 11. 删除发票')
replace_once("README.md", '## 11. 导出 Excel', '## 12. 导出 Excel')
replace_once("README.md", '**导出 Excel**\n\n生成：', '**更多 → 导出 Excel**\n\n生成：')

replace_once(
    "README.md",
    '- 保留“刷新发票文件夹”按钮供手动立即刷新。',
    '- 手动刷新属于低频操作，放在“更多 → 刷新发票文件夹”中。',
)

replace_once(
    "README.md",
    '''存在多个完全相同价格时，按购买记录 ID 和发票文件名顺序确定配对，因此同价场景仍需要人工复核。
''',
    '''存在多个完全相同价格时，自动规则按购买记录 ID 和发票文件名顺序确定配对。

用户可以为某个购买组成项建立 **手动发票关联**。手动关联优先于自动价格匹配，并会提前占用对应发票；其他购买记录再从剩余发票中自动匹配。恢复自动匹配后，该人工覆盖关系被删除。
''',
)
replace_once(
    "README.md",
    '只有点击“检查更新”后才访问本项目 GitHub Releases。',
    '只有通过“更多 → 检查更新”主动执行后才访问本项目 GitHub Releases。',
)
replace_once(
    "README.md",
    '发票支持全部字段/指定字段字符串包含搜索。',
    '发票搜索范围只包含当前可见字段；“全部显示字段”也只搜索当前显示列，不再搜索被设置隐藏的字段。',
)

# ---------------- CHANGELOG.md ----------------
p = Path("CHANGELOG.md")
text = p.read_text(encoding="utf-8")
old_heading = "## v5.2.9 (in development)\n"
if old_heading not in text:
    raise RuntimeError("CHANGELOG v5.2.9 heading not found")
new_heading = '''## v5.2.10 (in development)

### Added

- Manual purchase/invoice association overrides for correcting automatic price-match misalignment
- Manual association can be cleared at any time to return a component to automatic price matching
- Manual overrides are persisted in SQLite, included in normal backup/restore, and have an audit trail

### Improved

- Simplified the main command area: only the frequent JD-invoice fetch remains directly visible; refresh, Excel export, backup/restore, update check and settings are grouped under a compact “更多” menu
- Invoice search scopes now follow the fields actually visible in the invoice table instead of exposing hidden fields
- Manual associations are visibly marked in both invoice and purchase tables
- Manual-association changes create a local safety snapshot before modifying state and trigger configured cloud backup afterward

## v5.2.9
'''
text = text.replace(old_heading, new_heading, 1)
p.write_text(text, encoding="utf-8")

print("v5.2.10 migration applied")
