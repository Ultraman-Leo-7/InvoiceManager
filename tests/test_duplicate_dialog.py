from types import SimpleNamespace

import fitz
import pytest

import app


class FakeVariable:
    def __init__(self, value="", **kwargs):
        self.value = value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.text = []
        self.focused = False

    def pack(self, **kwargs):
        pass

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def insert(self, index, text):
        self.text.append(text)

    def yview(self, *args):
        pass

    def set(self, *args):
        pass

    def focus_set(self):
        self.focused = True


class FakeWindow(FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocols = {}
        self.bindings = {}
        self.destroyed = False

    def title(self, value):
        self.options["title"] = value

    def transient(self, parent):
        pass

    def geometry(self, value):
        pass

    def minsize(self, *args):
        pass

    def grab_set(self):
        pass

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def bind(self, name, callback):
        self.bindings[name] = callback

    def destroy(self):
        self.destroyed = True


class ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


class HeadlessInvoiceView:
    queue_duplicate_prompt = app.InvoiceApp.queue_duplicate_prompt
    _show_pending_duplicate_prompt = app.InvoiceApp._show_pending_duplicate_prompt

    def __init__(self):
        self._duplicate_dialog_open = False
        self._duplicate_prompt_scheduled = False
        self._pending_duplicate_prompt = ([], False)
        self._handled_duplicate_files = set()
        self.tk = SimpleNamespace(call=lambda *args: "")
        self._w = "."
        self._sync_running = False
        self.status_var = FakeVariable()
        self.refreshed = 0

    def after(self, delay, callback):
        callback()

    def refresh_all(self):
        self.refreshed += 1


@pytest.fixture
def dialog_case(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(app, "LOCAL_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(app, "parse_invoice", lambda path: {
        "文件名": path.name,
        "发票号码": app.sha256_file(path)[:20],
        "项目名称": "测试商品",
        "价税合计": 12.5,
        "提取状态": "正常",
    })
    app.init_db()
    folder = tmp_path / "发票"
    folder.mkdir(exist_ok=True)
    original = folder / "original.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Test invoice")
        document.save(original)
    content = original.read_bytes()
    app.sync_folder()
    copy = folder / "copy.pdf"
    copy.write_bytes(content)
    groups = app.sync_folder()["duplicates"]
    windows, buttons, choices = [], [], []

    def new_window(*args, **kwargs):
        window = FakeWindow(*args, **kwargs)
        windows.append(window)
        return window

    def new_button(*args, **kwargs):
        button = FakeWidget(*args, **kwargs)
        buttons.append(button)
        return button

    monkeypatch.setattr(app, "tk", SimpleNamespace(Toplevel=new_window, Text=FakeWidget, StringVar=FakeVariable))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(Frame=FakeWidget, Label=FakeWidget, Scrollbar=FakeWidget, Button=new_button))
    monkeypatch.setattr(app, "threading", SimpleNamespace(Thread=ImmediateThread))
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *args, **kwargs: pytest.fail("Duplicate handling failed"))
    real_handle = app.handle_duplicate_files

    def track_choice(selected_groups, *, delete):
        choices.append(delete)
        return real_handle(selected_groups, delete=delete)

    monkeypatch.setattr(app, "handle_duplicate_files", track_choice)
    return SimpleNamespace(
        folder=folder, original=original, copy=copy, content=content,
        groups=groups, windows=windows, buttons=buttons, choices=choices,
        view=HeadlessInvoiceView(),
    )


def button_command(case, text_prefix):
    return next(button.options["command"] for button in case.buttons if button.options["text"].startswith(text_prefix))


def test_opening_confirmation_dialog_does_not_delete_or_rename_files(dialog_case):
    case = dialog_case

    case.view.queue_duplicate_prompt(case.groups)

    assert len(case.windows) == 1
    assert not case.windows[0].destroyed
    assert case.choices == []
    assert case.original.read_bytes() == case.content
    assert case.copy.read_bytes() == case.content
    assert sorted(path.name for path in case.folder.glob("*.pdf")) == ["copy.pdf", "original.pdf"]
    assert next(button for button in case.buttons if button.options["text"].startswith("不删除")).focused


def test_confirmation_button_deletes_only_the_duplicate(dialog_case):
    case = dialog_case
    case.view.queue_duplicate_prompt(case.groups)

    button_command(case, "确认删除")()

    assert case.choices == [True]
    assert case.windows[0].destroyed
    assert case.original.read_bytes() == case.content
    assert not case.copy.exists()
    assert sorted(path.name for path in case.folder.glob("*.pdf")) == ["original.pdf"]
    assert [row["filename"] for row in app.all_active_rows()] == ["original.pdf"]
    assert case.view.refreshed == 1


@pytest.mark.parametrize("action", ["refusal-button", "window-close", "escape"])
def test_decline_or_dismissal_preserves_bytes_and_prefixes_duplicate(dialog_case, action):
    case = dialog_case
    case.view.queue_duplicate_prompt(case.groups)
    window = case.windows[0]

    if action == "refusal-button":
        button_command(case, "不删除")()
    elif action == "window-close":
        window.protocols["WM_DELETE_WINDOW"]()
    else:
        window.bindings["<Escape>"](SimpleNamespace())

    assert case.choices == [False]
    assert window.destroyed
    assert case.original.read_bytes() == case.content
    assert not case.copy.exists()
    assert (case.folder / "哈希副本-copy.pdf").read_bytes() == case.content
    assert sorted(path.name for path in case.folder.glob("*.pdf")) == ["original.pdf", "哈希副本-copy.pdf"]
    assert [row["filename"] for row in app.all_active_rows()] == ["original.pdf"]
    assert case.view.refreshed == 1
    assert app.sync_folder()["duplicates"] == []
    assert len(case.windows) == 1


@pytest.mark.parametrize("force", [False, True])
def test_queue_skips_already_prefixed_duplicate_even_on_manual_refresh(dialog_case, force):
    case = dialog_case
    prefixed = case.folder / "哈希副本-copy.pdf"
    case.copy.rename(prefixed)
    group = {**case.groups[0], "remove": [prefixed.name]}

    case.view.queue_duplicate_prompt([group], force=force)

    assert case.windows == []
    assert case.choices == []
    assert case.original.read_bytes() == case.content
    assert prefixed.read_bytes() == case.content
