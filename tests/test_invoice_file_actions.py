"""Right-click targets must stay tied to the displayed invoice, not row selection."""

import os
import subprocess
import sys
from tkinter import Menu as REAL_MENU
from types import MethodType, SimpleNamespace

import pytest

import app
from purchase_tracker import add_purchase, set_manual_match

REAL_POPEN = subprocess.Popen


class Tree:
    def __init__(self, columns=()):
        self.columns = columns
        self.rows = {}
        self.selected = "previous-selection"
        self.region = "cell"
        self.hit_row = ""
        self.hit_column = "#1"
        self.serial = 0

    def insert(self, parent, position, *, values):
        self.serial += 1
        item = str(self.serial)
        self.rows[item] = dict(zip(self.columns, values))
        return item

    def get_children(self):
        return list(self.rows)

    def delete(self, item):
        del self.rows[item]

    def identify_region(self, x, y):
        return self.region

    def identify_row(self, y):
        return self.hit_row

    def identify_column(self, x):
        return self.hit_column

    def column(self, column, option):
        assert option == "id"
        return self.columns[int(column[1:]) - 1]

    def set(self, item, column):
        return self.rows[item][column]

    def selection_set(self, item):
        self.selected = item

    def focus(self, item):
        self.focused = item


class Menu:
    def __init__(self, *args, **kwargs):
        self.commands = []

    def delete(self, *args):
        self.commands.clear()

    def add_command(self, **kwargs):
        self.commands.append(kwargs)

    def tk_popup(self, x, y):
        self.popup = (x, y)

    def grab_release(self):
        self.released = True


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / app.DB_FILENAME)
    app.init_db()
    notices, launches = [], []
    monkeypatch.setattr(app.messagebox, "showerror", lambda *args: notices.append(args[-1]))
    monkeypatch.setattr(app.subprocess, "Popen", lambda *args, **kwargs: launches.append(args[0]))
    monkeypatch.setattr(app.tk, "Menu", Menu)
    view = SimpleNamespace(
        invoice_tree=Tree(("_digest", "文件名")),
        purchase_tree=Tree(("id", "名称", "商品价格", "有快递费", "快递费", "商品发票", "快递费发票", "状态")),
        missing_tree=Tree(("购买", "类型", "金额")),
        unused_invoice_tree=Tree(("文件名", "价税合计")),
        purchase_summary_var=SimpleNamespace(set=lambda value: None),
    )
    for name in ("_show_invoice_file_menu", "open_invoice_file"):
        setattr(view, name, MethodType(getattr(app.InvoiceApp, name), view))
    return SimpleNamespace(root=tmp_path, view=view, notices=notices, launches=launches)


def invoice(case, name="商品 发票.pdf", total=12.5, directory="发票"):
    folder = case.root / directory
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(b"%PDF-1.7\nSynthetic invoice for file-action tests: " + name.encode())
    digest = app.sha256_file(path)
    with app.connect_db() as conn:
        conn.execute(
            "INSERT INTO invoices(digest, filename, total, last_seen) VALUES(?,?,?,?)",
            (digest, name, total, "2026-10-19T12:00:00"),
        )
    return path, digest


def click(tree, item, column="#1"):
    tree.hit_row = item
    tree.hit_column = column
    return SimpleNamespace(x=20, y=30, x_root=120, y_root=130)


def test_invoice_right_click_uses_clicked_row_instead_of_previous_selection(case):
    view = case.view
    calls = []
    view.open_invoice_file = lambda *args, **kwargs: calls.append((args, kwargs))
    tree = view.invoice_tree
    tree.insert("", "end", values=("old-digest", "old.pdf"))
    clicked = tree.insert("", "end", values=("clicked-digest", "clicked.pdf"))

    assert app.InvoiceApp.on_invoice_context_menu(view, click(tree, clicked)) == "break"
    assert tree.selected == tree.focused == clicked
    menu = view._invoice_file_menu
    assert menu.popup == (120, 130)
    assert menu.released
    menu.commands[0]["command"]()
    menu.commands[1]["command"]()
    assert calls == [(("clicked-digest",), {"reveal": True}), (("clicked-digest",), {})]


@pytest.mark.parametrize("manual", [False, True])
def test_purchase_invoice_cells_resolve_independent_matches_and_survive_refresh(case, manual):
    view = case.view
    _, goods = invoice(case)
    _, shipping = invoice(case, "快递.pdf", 6.0)
    _, unused = invoice(case, "未关联.pdf", 99.0)
    with app.connect_db() as conn:
        purchase_id = add_purchase(conn, "购买记录", "12.50", True, "6.00")
        if manual:
            set_manual_match(conn, purchase_id, "商品", goods)
            set_manual_match(conn, purchase_id, "快递费", shipping)
    calls = []
    view.open_invoice_file = lambda *args, **kwargs: calls.append((args, kwargs))
    app.InvoiceApp.refresh_purchase_tab(view)
    item, = view.purchase_tree.get_children()
    for column, digest in (("#6", goods), ("#7", shipping)):
        app.InvoiceApp.on_purchase_invoice_context_menu(view, click(view.purchase_tree, item, column))
        assert all(command["state"] == "normal" for command in view._invoice_file_menu.commands)
        view._invoice_file_menu.commands[1]["command"]()
        assert calls[-1] == ((digest,), {})
    # An open menu retains its specific invoice even if the tables refresh.
    callback = view._invoice_file_menu.commands[1]["command"]
    app.InvoiceApp.refresh_purchase_tab(view)
    callback()
    assert calls[-1] == ((shipping,), {})
    assert all(key[0] != item for key in view._purchase_invoice_digests)
    unused_item, = view.unused_invoice_tree.get_children()
    app.InvoiceApp.on_unused_invoice_context_menu(view, click(view.unused_invoice_tree, unused_item))
    view._invoice_file_menu.commands[1]["command"]()
    assert calls[-1] == ((unused,), {})


def test_unmatched_purchase_cell_disables_menu_and_unrelated_cells_do_nothing(case):
    view = case.view
    with app.connect_db() as conn:
        add_purchase(conn, "缺票购买", "12.50", False, 0)
    app.InvoiceApp.refresh_purchase_tab(view)
    tree = view.purchase_tree
    item, = tree.get_children()
    assert app.InvoiceApp.on_purchase_invoice_context_menu(view, click(tree, item, "#2")) is None
    assert not hasattr(view, "_invoice_file_menu")
    app.InvoiceApp.on_purchase_invoice_context_menu(view, click(tree, item, "#6"))
    assert all(command["state"] == "disabled" for command in view._invoice_file_menu.commands)


@pytest.mark.parametrize("region,row", [("heading", "1"), ("nothing", ""), ("cell", "")])
def test_headers_and_empty_space_do_not_open_menu(case, region, row):
    view = case.view
    for tree_name, handler in (
        ("invoice_tree", app.InvoiceApp.on_invoice_context_menu),
        ("purchase_tree", app.InvoiceApp.on_purchase_invoice_context_menu),
        ("unused_invoice_tree", app.InvoiceApp.on_unused_invoice_context_menu),
    ):
        tree = getattr(view, tree_name)
        event = click(tree, row)
        tree.region = region
        assert handler(view, event) is None
    assert not hasattr(view, "_invoice_file_menu")


@pytest.mark.parametrize("directory", ["", "发票"])
@pytest.mark.parametrize("reveal", [False, True])
def test_windows_opens_exact_pdf_or_selects_it_in_explorer(case, monkeypatch, directory, reveal):
    path, digest = invoice(case, directory=directory)
    monkeypatch.setattr(app, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(app.os, "startfile", lambda path: case.launches.append(path), raising=False)

    case.view.open_invoice_file(digest, reveal=reveal)

    expected = ["explorer.exe", "/select,", str(path)] if reveal else str(path)
    assert case.launches == [expected]
    assert not case.notices


def test_menu_uses_latest_filename_for_same_digest(case, monkeypatch):
    path, digest = invoice(case)
    renamed = path.with_name("重命名 发票.pdf")
    path.rename(renamed)
    with app.connect_db() as conn:
        conn.execute("UPDATE invoices SET filename=? WHERE digest=?", (renamed.name, digest))
    monkeypatch.setattr(app, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(app.os, "startfile", lambda path: case.launches.append(path), raising=False)
    case.view.open_invoice_file(digest)
    assert case.launches == [str(renamed)]
    assert not case.notices


@pytest.mark.parametrize("problem", ["deleted", "replaced", "inactive", "unknown", "unsafe-name", "launch-failed"])
def test_missing_or_changed_invoice_and_launch_failure_show_error(case, monkeypatch, problem):
    path, digest = invoice(case)
    if problem == "deleted":
        path.unlink()
    elif problem == "replaced":
        path.write_bytes(b"different file at the same path")
    elif problem in {"inactive", "unsafe-name"}:
        with app.connect_db() as conn:
            if problem == "inactive":
                conn.execute("UPDATE invoices SET active=0 WHERE digest=?", (digest,))
            else:
                conn.execute("UPDATE invoices SET filename='../outside.pdf' WHERE digest=?", (digest,))
    elif problem == "unknown":
        digest = "unknown-digest"
    else:
        def fail_launch(*args, **kwargs):
            raise OSError("Synthetic file manager failure")
        monkeypatch.setattr(app.subprocess, "Popen", fail_launch)
    case.view.open_invoice_file(digest, reveal=True)
    assert not case.launches
    assert len(case.notices) == 1
    assert "无法打开或定位发票" in case.notices[0]


def test_real_tree_bindings_target_goods_shipping_and_invoice_row(case, monkeypatch):
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("Real Tk requires DISPLAY; Windows CI always runs this test")
    # Destroying and reloading Tcl/Tk in the same Windows process can make later
    # GUI fixtures fail to read init.tcl. Keep this full-app test's interpreter
    # in its own process, matching the application's single-root lifetime.
    if not os.environ.get("INVOICEMANAGER_NATIVE_CONTEXT_TEST"):
        monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)
        env = os.environ.copy()
        env["INVOICEMANAGER_NATIVE_CONTEXT_TEST"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q",
             f"{__file__}::test_real_tree_bindings_target_goods_shipping_and_invoice_row"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return
    _, goods = invoice(case)
    _, shipping = invoice(case, "快递.pdf", 6.0)
    with app.connect_db() as conn:
        add_purchase(conn, "界面测试购买", "12.50", True, "6.00")
    # Keep the test local and deterministic, without starting polling or workers.
    monkeypatch.setattr(app.InvoiceApp, "after", lambda *args: None)
    # The headless cases replace Menu; use the real Tk menu for widget callbacks.
    monkeypatch.setattr(app.tk, "Menu", REAL_MENU)
    monkeypatch.setattr(REAL_MENU, "tk_popup", lambda *args: None)
    calls = []
    monkeypatch.setattr(app.InvoiceApp, "open_invoice_file", lambda self, digest, reveal=False: calls.append((digest, reveal)))
    root = app.InvoiceApp()
    try:
        root.geometry("980x640+0+0")
        root.update()
        root.notebook.select(root.purchase_tab)
        root.update()
        item, = root.purchase_tree.get_children()
        for column, digest in (("商品发票", goods), ("快递费发票", shipping)):
            x, y, width, height = root.purchase_tree.bbox(item, column)
            click_x, click_y = x + min(10, width // 2), y + height // 2
            assert click_x < root.purchase_tree.winfo_width()
            display_column = root.purchase_tree.identify_column(click_x)
            assert root.purchase_tree.column(display_column, "id") == column
            root.purchase_tree.event_generate("<Button-3>", x=click_x, y=click_y)
            root.purchase_tree.event_generate("<ButtonRelease-3>", x=click_x, y=click_y)
            root.update()
            root._invoice_file_menu.invoke(0)
            root._invoice_file_menu.invoke(1)
            assert calls[-2:] == [(digest, True), (digest, False)]
        root.notebook.select(root.invoice_tab)
        root.update()
        item = root.invoice_tree.get_children()[0]
        digest = root.invoice_tree.set(item, "_digest")
        x, y, width, height = root.invoice_tree.bbox(item, "文件名")
        root.invoice_tree.event_generate("<Button-3>", x=x + width // 2, y=y + height // 2)
        root.invoice_tree.event_generate("<ButtonRelease-3>", x=x + width // 2, y=y + height // 2)
        root.update()
        root._invoice_file_menu.invoke(1)
        assert calls[-1] == (digest, False)
    finally:
        root.destroy()
