from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------- app.py ----------------
p = Path("app.py")
text = p.read_text(encoding="utf-8")

text = text.replace('"""InvoiceManager v5.2.10 GUI."""', '"""InvoiceManager v5.2.11 GUI."""', 1)
text = text.replace('import traceback\n', 'import traceback\nimport webbrowser\n', 1)
text = text.replace('APP_VERSION = "5.2.10"', 'APP_VERSION = "5.2.11"', 1)
text = text.replace(
    'LOCAL_BACKUP_DIRNAME = ".invoice_manager_backups"\n',
    'LOCAL_BACKUP_DIRNAME = ".invoice_manager_backups"\nPROJECT_URL = "https://github.com/Ultraman-Leo-7/InvoiceManager"\n',
    1,
)

old_menu = '''        more_menu.add_command(label="设置", command=self.open_settings)\n        more_menu.add_separator()\n        more_menu.add_command(label="刷新发票文件夹", command=self.run_background_sync)\n        more_menu.add_command(label="导出 Excel", command=self.do_export)\n        more_menu.add_command(label="数据备份与恢复", command=self.open_backup_dialog)\n        more_menu.add_command(label="检查更新", command=self.check_for_updates)\n'''
new_menu = '''        more_menu.add_command(label="设置", command=self.open_settings)\n        more_menu.add_separator()\n        more_menu.add_command(label="刷新发票文件夹", command=self.run_background_sync)\n        more_menu.add_command(label="导出 Excel", command=self.do_export)\n'''
if old_menu not in text:
    raise RuntimeError("main More menu anchor not found")
text = text.replace(old_menu, new_menu, 1)

text = text.replace(
    'raise ValueError("请先在“数据备份”中设置坚果云账号和应用密码")',
    'raise ValueError("请先在“设置 → 备份与恢复”中设置坚果云账号和应用密码")',
    1,
)

new_settings = r'''    def open_settings(self):
        win = tk.Toplevel(self)
        win.title(f"设置 - {APP_TITLE}")
        win.transient(self)
        win.grab_set()
        win.geometry("920x680")
        win.minsize(840, 600)

        root = ttk.Frame(win, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(root, padding=(0, 0, 12, 0))
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="设置", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=6, pady=(2, 10))
        nav = ttk.Treeview(sidebar, show="tree", selectmode="browse", height=10)
        nav.column("#0", width=160, minwidth=150, stretch=False)
        nav.pack(fill="y", expand=True)

        categories = [
            ("general", "通用"),
            ("mail", "邮箱与发票"),
            ("backup", "备份与恢复"),
            ("update", "更新与关于"),
        ]
        for key, label in categories:
            nav.insert("", "end", iid=key, text=label)

        content = ttk.Frame(root, padding=(16, 4, 4, 4))
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        title_var = tk.StringVar(value="通用")
        ttk.Label(content, textvariable=title_var, font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )
        page_host = ttk.Frame(content)
        page_host.grid(row=1, column=0, sticky="nsew")
        page_host.columnconfigure(0, weight=1)
        page_host.rowconfigure(0, weight=1)

        pages = {}
        for key, _label in categories:
            page = ttk.Frame(page_host)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            pages[key] = page

        # ---- 通用 ----
        current = set(selected_fields())
        field_vars = {}
        general = pages["general"]
        display_box = ttk.LabelFrame(general, text="发票列表", padding=12)
        display_box.pack(fill="x")
        ttk.Label(
            display_box,
            text="选择主表要显示的字段。搜索范围会自动跟随当前显示字段，不再展示隐藏字段。",
            wraplength=640,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for idx, field in enumerate(AVAILABLE_FIELDS):
            var = tk.BooleanVar(value=field in current)
            field_vars[field] = var
            ttk.Checkbutton(display_box, text=field, variable=var).grid(
                row=1 + idx // 2,
                column=idx % 2,
                sticky="w",
                padx=(0, 26),
                pady=3,
            )
        display_box.columnconfigure(0, weight=1)
        display_box.columnconfigure(1, weight=1)

        behavior_box = ttk.LabelFrame(general, text="工作方式", padding=12)
        behavior_box.pack(fill="x", pady=(14, 0))
        ttk.Label(
            behavior_box,
            text="发票文件夹会自动监控并刷新。手动刷新只是故障排查或需要立即重扫时的备用操作，因此留在“更多”菜单中。",
            wraplength=640,
        ).pack(anchor="w")
        ttk.Label(
            behavior_box,
            text="Excel 只作为按需导出结果，不是主数据库。",
            wraplength=640,
        ).pack(anchor="w", pady=(6, 0))

        # ---- 邮箱与发票 ----
        mail = pages["mail"]
        email_box = ttk.LabelFrame(mail, text="QQ 邮箱 · 京东电子发票", padding=12)
        email_box.pack(fill="x")
        ttk.Label(
            email_box,
            text="用于从 QQ 邮箱收件箱获取京东电子发票。这里需要 QQ 邮箱生成的 16 位授权码，不是 QQ 登录密码。",
            wraplength=640,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(email_box, text="QQ 邮箱").grid(row=1, column=0, sticky="w", pady=4)
        email_var = tk.StringVar(value=get_setting("qq_email", ""))
        ttk.Entry(email_box, textvariable=email_var).grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(email_box, text="16 位授权码").grid(row=2, column=0, sticky="w", pady=4)
        auth_var = tk.StringVar()
        ttk.Entry(email_box, textvariable=auth_var, show="•").grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=4)
        auth_saved_var = tk.StringVar()

        def refresh_auth_tip():
            has_saved = bool(get_setting("qq_auth_code", ""))
            auth_saved_var.set(
                ("已保存授权码；输入框留空会继续使用原授权码。" if has_saved else "尚未保存授权码。")
                + " Windows 下使用 DPAPI 加密保存。"
            )

        refresh_auth_tip()
        ttk.Label(email_box, textvariable=auth_saved_var, wraplength=520).grid(
            row=3, column=1, sticky="w", padx=(12, 0), pady=(2, 8)
        )
        email_box.columnconfigure(1, weight=1)

        def clear_auth():
            if messagebox.askyesno(APP_TITLE, "确定清除已保存的 QQ 邮箱授权码？", parent=win):
                set_setting("qq_auth_code", "")
                auth_var.set("")
                refresh_auth_tip()
                settings_status_var.set("已清除 QQ 邮箱授权码。")

        ttk.Button(email_box, text="清除已保存授权码", command=clear_auth).grid(
            row=4, column=1, sticky="w", padx=(12, 0), pady=(2, 0)
        )

        # ---- 备份与恢复 ----
        backup = pages["backup"]
        backup_box = ttk.LabelFrame(backup, text="坚果云 WebDAV", padding=12)
        backup_box.pack(fill="x")
        ttk.Label(
            backup_box,
            text="配置一次后即可自动备份。云端备份包含购买记录、人工确认、备注、显示设置等；PDF 发票本身不会上传。",
            wraplength=640,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(
            backup_box,
            text="QQ 授权码和坚果云应用密码属于设备密钥，不写入云端备份；换电脑恢复后需要重新填写。",
            wraplength=640,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))

        nut_email_var = tk.StringVar(value=get_setting("nutstore_email", ""))
        nut_pass_var = tk.StringVar()
        nut_auto_var = tk.BooleanVar(value=get_setting("nutstore_auto_backup", "1") == "1")
        nut_saved_var = tk.StringVar()

        def refresh_nut_tip():
            has_saved = bool(get_setting("nutstore_app_password", ""))
            nut_saved_var.set(
                ("已保存应用密码；输入框留空会继续使用原密码。" if has_saved else "尚未保存应用密码。")
                + " Windows 下使用 DPAPI 加密保存。"
            )

        refresh_nut_tip()
        ttk.Label(backup_box, text="坚果云账号邮箱").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(backup_box, textvariable=nut_email_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(backup_box, text="第三方应用密码").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(backup_box, textvariable=nut_pass_var, show="•").grid(row=3, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(backup_box, textvariable=nut_saved_var, wraplength=520).grid(
            row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(2, 6)
        )
        ttk.Checkbutton(
            backup_box,
            text="数据变化后自动备份到坚果云（推荐）",
            variable=nut_auto_var,
        ).grid(row=5, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(2, 8))
        backup_box.columnconfigure(1, weight=1)

        last = get_setting("nutstore_last_backup", "")
        last_text = last.replace("T", " ")[:19] if last else "尚无成功备份"
        last_error = get_setting("nutstore_last_backup_error", "")
        backup_status_var = tk.StringVar(
            value=f"最近成功备份：{last_text}" + (f"\n最近失败：{last_error}" if last_error else "")
        )
        ttk.Label(backup_box, textvariable=backup_status_var, wraplength=640).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(4, 8)
        )

        # ---- 更新与关于 ----
        update = pages["update"]
        update_box = ttk.LabelFrame(update, text="软件更新", padding=12)
        update_box.pack(fill="x")
        ttk.Label(update_box, text=f"当前版本：v{APP_VERSION}", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            update_box,
            text="默认不会在启动时自动联网检查更新。需要时手动点击下面的按钮即可。",
            wraplength=640,
        ).pack(anchor="w", pady=(6, 10))
        ttk.Button(update_box, text="检查更新", command=self.check_for_updates).pack(anchor="w")

        about_box = ttk.LabelFrame(update, text="项目与反馈", padding=12)
        about_box.pack(fill="x", pady=(14, 0))
        ttk.Label(about_box, text="GitHub 项目主页").pack(anchor="w")
        link = tk.Label(
            about_box,
            text=PROJECT_URL,
            fg="#0969da",
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "underline"),
        )
        link.pack(anchor="w", pady=(4, 8))
        link.bind("<Button-1>", lambda _e: webbrowser.open(PROJECT_URL))
        ttk.Button(
            about_box,
            text="打开 GitHub 项目主页",
            command=lambda: webbrowser.open(PROJECT_URL),
        ).pack(anchor="w")
        ttk.Separator(about_box).pack(fill="x", pady=12)
        ttk.Label(
            about_box,
            text="如果觉得好用，欢迎给个 Star ⭐ 喵～  ฅ^•ﻌ•^ฅ",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w")
        ttk.Button(
            about_box,
            text="去 GitHub 给个 Star ⭐",
            command=lambda: webbrowser.open(PROJECT_URL),
        ).pack(anchor="w", pady=(8, 0))

        settings_status_var = tk.StringVar(value="")

        def save_all(show_status=True):
            fields = [f for f, v in field_vars.items() if v.get()]
            if not fields:
                messagebox.showwarning(APP_TITLE, "至少选择一个显示字段。", parent=win)
                return False
            qq_email = email_var.get().strip()
            if qq_email and "@" not in qq_email:
                messagebox.showwarning(APP_TITLE, "QQ 邮箱格式看起来不正确。", parent=win)
                return False
            nut_email = nut_email_var.get().strip()
            if nut_email and "@" not in nut_email:
                messagebox.showwarning(APP_TITLE, "坚果云账号邮箱格式看起来不正确。", parent=win)
                return False
            try:
                with connect_db() as conn:
                    set_setting_conn(conn, "selected_fields", json.dumps(fields, ensure_ascii=False))
                    set_setting_conn(conn, "qq_email", qq_email)
                    if auth_var.get().strip():
                        set_setting_conn(conn, "qq_auth_code", protect_secret(auth_var.get().strip()))
                    set_setting_conn(conn, "nutstore_email", nut_email)
                    set_setting_conn(conn, "nutstore_auto_backup", "1" if nut_auto_var.get() else "0")
                    if nut_pass_var.get().strip():
                        set_setting_conn(conn, "nutstore_app_password", protect_secret(nut_pass_var.get().strip()))
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"保存设置失败：{type(e).__name__}: {e}", parent=win)
                return False
            refresh_auth_tip()
            refresh_nut_tip()
            self.refresh_all()
            self.schedule_auto_cloud_backup()
            if show_status:
                settings_status_var.set("设置已保存。")
                self.set_status("设置已保存")
            return True

        def client_from_form() -> NutstoreWebDAV:
            email = nut_email_var.get().strip()
            password = nut_pass_var.get().strip()
            if not password:
                enc = get_setting("nutstore_app_password", "").strip()
                if enc:
                    password = unprotect_secret(enc)
            if not email or not password:
                raise ValueError("请填写坚果云账号邮箱和第三方应用密码")
            return NutstoreWebDAV(email, password)

        def test_connection():
            if not save_all(show_status=False):
                return
            backup_status_var.set("正在测试坚果云连接...")

            def worker():
                try:
                    client_from_form().test_connection()
                    self.after(0, lambda: backup_status_var.set("连接成功。InvoiceManager 备份目录已准备好。"))
                except Exception as e:
                    self.after(0, lambda: backup_status_var.set(f"连接失败：{type(e).__name__}: {e}"))

            threading.Thread(target=worker, daemon=True).start()

        def backup_now():
            if not save_all(show_status=False):
                return
            backup_status_var.set("正在备份到坚果云...")

            def worker():
                try:
                    name = self._perform_cloud_backup()
                    text = f"备份成功：{name}"
                    self.set_status(text)
                    self.after(0, lambda: backup_status_var.set(text))
                except Exception as e:
                    error_text = f"{type(e).__name__}: {e}"
                    try:
                        set_setting("nutstore_last_backup_error", error_text)
                    except Exception:
                        pass
                    self.after(0, lambda: backup_status_var.set(f"备份失败：{error_text}"))

            threading.Thread(target=worker, daemon=True).start()

        def restart_after_restore():
            messagebox.showinfo(
                APP_TITLE,
                "恢复完成。程序将重新启动。\n\n为保证跨电脑安全，QQ 邮箱授权码和坚果云应用密码不会从云备份恢复，需要重新填写。",
                parent=win,
            )
            try:
                subprocess.Popen([sys.executable], cwd=str(BASE_DIR))
            except Exception:
                pass
            self.destroy()

        def show_restore_choice(client: NutstoreWebDAV, history: list[str]):
            choice_win = tk.Toplevel(win)
            choice_win.title("选择要恢复的备份")
            choice_win.transient(win)
            choice_win.grab_set()
            choice_win.geometry("560x220")
            box = ttk.Frame(choice_win, padding=14)
            box.pack(fill="both", expand=True)
            values = ["最新备份"] + history
            choice_var = tk.StringVar(value=values[0])
            ttk.Label(box, text="选择备份：").pack(anchor="w")
            ttk.Combobox(box, textvariable=choice_var, values=values, state="readonly").pack(fill="x", pady=(4, 10))
            ttk.Label(
                box,
                text="恢复会先在本机自动保存当前数据库，再替换为所选云端备份。恢复后程序会自动重启。",
                wraplength=520,
            ).pack(anchor="w", pady=(0, 10))

            def do_restore():
                selected = choice_var.get()
                remote_name = LATEST_NAME if selected == "最新备份" else selected
                if not messagebox.askyesno(
                    APP_TITLE,
                    "确定恢复这个备份？\n\n当前数据库会先自动做本地安全备份。",
                    parent=choice_win,
                ):
                    return
                choice_win.destroy()
                backup_status_var.set("正在下载并校验备份...")

                def worker():
                    snapshot = temporary_snapshot_path(prefix="InvoiceManager-restore-")
                    try:
                        client.download_backup(snapshot, remote_name)
                        restore_snapshot(snapshot, DB_PATH, LOCAL_BACKUP_DIR)
                        hide_windows_file(DB_PATH)
                        hide_windows_file(LOCAL_BACKUP_DIR)
                        self.after(0, restart_after_restore)
                    except Exception as e:
                        self.after(0, lambda: backup_status_var.set(f"恢复失败：{type(e).__name__}: {e}"))
                    finally:
                        try:
                            snapshot.unlink()
                        except OSError:
                            pass

                threading.Thread(target=worker, daemon=True).start()

            ttk.Button(box, text="恢复", command=do_restore).pack(side="right")
            ttk.Button(box, text="取消", command=choice_win.destroy).pack(side="right", padx=(0, 6))

        def restore_from_cloud():
            if not save_all(show_status=False):
                return
            backup_status_var.set("正在读取坚果云历史备份...")

            def worker():
                try:
                    client = client_from_form()
                    history = client.list_history()
                    self.after(0, lambda: show_restore_choice(client, history))
                except Exception as e:
                    self.after(0, lambda: backup_status_var.set(f"读取备份失败：{type(e).__name__}: {e}"))

            threading.Thread(target=worker, daemon=True).start()

        def clear_nut_password():
            if messagebox.askyesno(APP_TITLE, "确定清除已保存的坚果云应用密码？", parent=win):
                set_setting("nutstore_app_password", "")
                nut_pass_var.set("")
                refresh_nut_tip()
                settings_status_var.set("已清除坚果云应用密码。")

        action_row = ttk.Frame(backup_box)
        action_row.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Button(action_row, text="测试连接", command=test_connection).pack(side="left")
        ttk.Button(action_row, text="立即备份", command=backup_now).pack(side="left", padx=(6, 0))
        ttk.Button(action_row, text="从坚果云恢复", command=restore_from_cloud).pack(side="left", padx=(6, 0))
        ttk.Button(action_row, text="清除应用密码", command=clear_nut_password).pack(side="left", padx=(6, 0))

        def show_page(key: str):
            for page in pages.values():
                page.grid_remove()
            pages[key].grid()
            title_var.set(dict(categories)[key])

        def on_nav(_event=None):
            selected = nav.selection()
            if selected:
                show_page(selected[0])

        nav.bind("<<TreeviewSelect>>", on_nav)
        nav.selection_set("general")
        nav.focus("general")
        show_page("general")

        bottom = ttk.Frame(root, padding=(0, 12, 0, 0))
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(bottom, textvariable=settings_status_var).pack(side="left")
        ttk.Button(bottom, text="关闭", command=win.destroy).pack(side="right")
        ttk.Button(bottom, text="保存设置", command=save_all).pack(side="right", padx=(0, 8))
'''

pattern = re.compile(r"    def open_settings\(self\):\n.*?\n    def open_jd_fetch_dialog\(self\):\n", re.S)
match = pattern.search(text)
if not match:
    raise RuntimeError("open_settings block not found")
text = text[:match.start()] + new_settings + "\n    def open_jd_fetch_dialog(self):\n" + text[match.end():]

p.write_text(text, encoding="utf-8")

# ---------------- README.md ----------------
p = Path("README.md")
readme = p.read_text(encoding="utf-8")
readme = readme.replace(
    "| 坚果云备份 | 通过 WebDAV 备份数据库，可从最新或历史备份恢复 |",
    "| 坚果云备份 | 在“设置 → 备份与恢复”中配置一次，之后可按数据变化自动备份，也可从最新或历史备份恢复 |",
)
readme = readme.replace(
    "| 手动程序更新 | 只有用户点击“检查更新”时才联网检查；默认不自动检查或自动更新 |",
    "| 手动程序更新 | 在“设置 → 更新与关于”中手动检查；默认不自动检查或自动更新，并提供项目主页与 Star 入口 |",
)
marker = "## 数据安全原则\n"
settings_intro = '''## 设置界面\n\n设置采用常见的左侧分类导航，避免把大量低频功能直接堆在主界面：\n\n- **通用**：发票表显示字段，以及自动刷新 / Excel 导出的工作方式说明；\n- **邮箱与发票**：QQ 邮箱和 16 位授权码；\n- **备份与恢复**：坚果云账号、第三方应用密码、自动备份、测试连接、立即备份和恢复；\n- **更新与关于**：当前版本、手动检查更新、GitHub 项目主页和 Star 入口。\n\n主界面的 **更多** 菜单只保留真正的一键式低频操作：**设置、刷新发票文件夹、导出 Excel**。需要先配置参数的功能统一进入设置页。\n\n'''
if marker not in readme:
    raise RuntimeError("README settings marker not found")
readme = readme.replace(marker, settings_intro + marker, 1)
readme = readme.replace("**更多 → 设置**\n\n填写：", "**更多 → 设置 → 邮箱与发票**\n\n填写：", 1)
readme = readme.replace("**更多 → 数据备份与恢复**", "**更多 → 设置 → 备份与恢复**")
readme = readme.replace("**更多 → 检查更新**", "**更多 → 设置 → 更新与关于 → 检查更新**")
p.write_text(readme, encoding="utf-8")

# ---------------- CHANGELOG.md ----------------
p = Path("CHANGELOG.md")
changelog = p.read_text(encoding="utf-8")
entry = '''## v5.2.11\n\n### Improved\n\n- Reworked Settings into a sidebar-based layout with four categories: General, Mail & Invoices, Backup & Restore, and Updates & About\n- Moved all Nutstore WebDAV configuration and recovery controls into Settings instead of exposing backup configuration as a separate top-level command\n- Moved update checking into Settings → Updates & About\n- Reduced the main “更多” menu to one-click low-frequency actions only: Settings, manual folder refresh, and Excel export\n- Added a clickable GitHub project address and a friendly Star prompt in the Updates & About page\n- Preserved manual-only update behavior: InvoiceManager still does not check for updates automatically at startup\n\n'''
if "## v5.2.11" not in changelog:
    changelog = changelog.replace("## v5.2.10\n", entry + "## v5.2.10\n", 1)
p.write_text(changelog, encoding="utf-8")

# ---------------- other docs ----------------
for md in Path(".").rglob("*.md"):
    if md.name in {"README.md", "CHANGELOG.md"}:
        continue
    body = md.read_text(encoding="utf-8")
    newer = body.replace("更多 → 数据备份与恢复", "更多 → 设置 → 备份与恢复")
    newer = newer.replace("更多 → 检查更新", "更多 → 设置 → 更新与关于 → 检查更新")
    if newer != body:
        md.write_text(newer, encoding="utf-8")

print("Applied InvoiceManager v5.2.11 settings UI changes")
