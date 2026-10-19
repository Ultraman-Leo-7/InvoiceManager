from types import SimpleNamespace

import pytest

import app


class ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


class MailView:
    def __init__(self):
        self.callbacks = []
        self.statuses = []
        self.duplicate_prompts = []
        self.refreshed = 0

    def set_status(self, message):
        self.statuses.append(message)

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def refresh_all(self):
        self.refreshed += 1

    def queue_duplicate_prompt(self, groups, force=False):
        self.duplicate_prompts.append(groups)

    def finish_callbacks(self):
        while self.callbacks:
            self.callbacks.pop(0)()


@pytest.fixture
def fetch_case(tmp_path, monkeypatch):
    result = {
        "emails": 3,
        "links": 1,
        "attachments": 2,
        "downloaded": 3,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "sources": {
            "jd": {"emails": 1, "links": 1, "attachments": 0, "downloaded": 1, "skipped": 0, "failed": 0},
            "pinduoduo": {"emails": 1, "links": 0, "attachments": 1, "downloaded": 1, "skipped": 0, "failed": 0},
            "alipay": {"emails": 1, "links": 0, "attachments": 1, "downloaded": 1, "skipped": 0, "failed": 0},
        },
    }
    groups = [{"keep": "invoice.pdf", "remove": ["invoice-copy.pdf"]}]
    calls, settings, notices, sync_calls = [], [], [], []
    stored_settings = {"qq_invoice_import_rules_version": "2"}

    def save_setting(key, value):
        settings.append((key, value))
        stored_settings[key] = value

    def fetch(**kwargs):
        calls.append(kwargs)
        return result

    def sync(**kwargs):
        sync_calls.append(kwargs)
        return {"total": 4, "duplicates": groups}

    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "fetch_qq_invoices", fetch)
    monkeypatch.setattr(app, "get_setting", lambda key, default="": stored_settings.get(key, default))
    monkeypatch.setattr(app, "set_setting", save_setting)
    monkeypatch.setattr(app, "sync_folder", sync)
    monkeypatch.setattr(app, "folder_signature", lambda: ("folder-after-download",))
    monkeypatch.setattr(app, "threading", SimpleNamespace(Thread=ImmediateThread))
    monkeypatch.setattr(app, "messagebox", SimpleNamespace(
        showinfo=lambda title, message: notices.append(("info", message)),
        showwarning=lambda title, message: notices.append(("warning", message)),
        showerror=lambda title, message: notices.append(("error", message)),
    ))
    view = MailView()

    def show_result(message, failed, duplicates):
        notices.append(("warning" if failed else "info", message))
        view.queue_duplicate_prompt(duplicates)

    view.show_mail_fetch_result = show_result
    return SimpleNamespace(
        view=view, result=result, calls=calls, settings=settings,
        notices=notices, sync_calls=sync_calls, groups=groups, folder=tmp_path,
        stored_settings=stored_settings,
    )


def run_fetch(case, start_time=None):
    app.InvoiceApp.run_qq_fetch(case.view, "test@example.com", "fake-auth-code", start_time)


def test_unified_worker_passes_credentials_and_folder_to_mail_fetcher(fetch_case):
    case = fetch_case

    run_fetch(case)

    assert len(case.calls) == 1
    assert case.calls[0] == {
        "email_addr": "test@example.com",
        "auth_code": "fake-auth-code",
        "output_dir": case.folder,
        "dedupe_dirs": [case.folder / "发票", case.folder / "异常"],
        "start_time": None,
        "progress": case.view.set_status,
    }
    assert case.sync_calls == [{"progress": case.view.set_status}]
    assert case.view._last_folder_signature == ("folder-after-download",)


def test_success_updates_all_sources_cursor_and_reports_attachments(fetch_case):
    case = fetch_case

    run_fetch(case)
    assert case.notices == []
    case.view.finish_callbacks()

    assert app.datetime.fromisoformat(dict(case.settings)["qq_invoice_last_fetch"])
    assert case.stored_settings["qq_invoice_import_rules_version"] == "2"
    assert case.view.refreshed == 1
    assert case.view.duplicate_prompts == [case.groups]
    kind, message = case.notices[0]
    assert kind == "info"
    assert "2" in message and "附件" in message
    assert "1" in message and "链接" in message
    assert all(name in message for name in ("京东", "拼多多", "支付宝发票管家"))


def test_partial_failure_preserves_cursor_and_still_syncs_downloads(fetch_case):
    case = fetch_case
    case.result.update(downloaded=2, failed=1, errors=["测试附件保存失败"])

    run_fetch(case)
    case.view.finish_callbacks()

    assert case.settings == []
    assert case.view.refreshed == 1
    assert case.view.duplicate_prompts == [case.groups]
    assert case.notices[0][0] == "warning"
    assert "测试附件保存失败" in case.notices[0][1]
    assert "上次成功获取时间" in case.notices[0][1]


def test_deferred_error_callback_survives_worker_exception_scope(fetch_case, monkeypatch):
    case = fetch_case

    def fail_fetch(**kwargs):
        raise RuntimeError("测试邮箱连接失败")

    monkeypatch.setattr(app, "fetch_qq_invoices", fail_fetch)
    run_fetch(case)
    assert case.notices == []

    # Tk runs this after worker() returns, when Python has cleared the exception variable.
    case.view.finish_callbacks()

    assert case.settings == []
    assert case.sync_calls == []
    assert case.view.duplicate_prompts == []
    assert case.notices[0][0] == "error"
    assert "RuntimeError" in case.notices[0][1]
    assert "测试邮箱连接失败" in case.notices[0][1]


def test_sync_failure_keeps_cursor_and_shows_deferred_error(fetch_case, monkeypatch):
    case = fetch_case

    def fail_sync(**kwargs):
        raise RuntimeError("测试文件夹同步失败")

    monkeypatch.setattr(app, "sync_folder", fail_sync)

    run_fetch(case)
    assert len(case.calls) == 1
    assert case.notices == []
    case.view.finish_callbacks()

    assert case.settings == []
    assert case.view.refreshed == 0
    assert case.view.duplicate_prompts == []
    assert case.notices[0][0] == "error"
    assert "测试文件夹同步失败" in case.notices[0][1]


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs

    def pack(self, **kwargs):
        pass

    def title(self, value):
        pass

    def transient(self, parent):
        pass

    def grab_set(self):
        pass

    def geometry(self, value):
        pass

    def destroy(self):
        pass


class ReportWidget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.text = []
        self.grid_options = {}
        self.row_weights = {}
        self.column_weights = {}

    def pack(self, **kwargs):
        pass

    def grid(self, **kwargs):
        self.grid_options = kwargs

    def rowconfigure(self, index, **kwargs):
        self.row_weights[index] = kwargs

    grid_rowconfigure = rowconfigure

    def columnconfigure(self, index, **kwargs):
        self.column_weights[index] = kwargs

    grid_columnconfigure = columnconfigure

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def insert(self, index, text):
        self.text.append(text)

    def yview(self, *args):
        pass

    def set(self, *args):
        pass

    def focus_set(self):
        pass


class ReportWindow(ReportWidget):
    def __init__(self):
        super().__init__()
        self.protocols = {}
        self.bindings = {}
        self.destroy_count = 0
        self.transient_calls = []
        self.grab_calls = 0

    def title(self, value):
        self.options["title"] = value

    def geometry(self, value):
        self.options["geometry"] = value

    def minsize(self, *args):
        self.options["minsize"] = args

    def transient(self, parent):
        self.transient_calls.append(parent)

    def grab_set(self):
        self.grab_calls += 1

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def bind(self, name, callback):
        self.bindings[name] = callback

    def destroy(self):
        self.destroy_count += 1


@pytest.mark.parametrize("dismiss", ["button", "return", "escape", "close"])
def test_result_dialog_keeps_full_details_and_prompts_duplicates_only_after_close(monkeypatch, dismiss):
    window = ReportWindow()
    texts, buttons, scrollbars, frames = [], [], [], []

    def collect(target, *args, **kwargs):
        widget = ReportWidget(*args, **kwargs)
        target.append(widget)
        return widget

    monkeypatch.setattr(app, "tk", SimpleNamespace(
        Toplevel=lambda *args: window,
        Text=lambda *args, **kwargs: collect(texts, *args, **kwargs),
    ))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(
        Frame=lambda *args, **kwargs: collect(frames, *args, **kwargs), Label=ReportWidget,
        Scrollbar=lambda *args, **kwargs: collect(scrollbars, *args, **kwargs),
        Button=lambda *args, **kwargs: collect(buttons, *args, **kwargs),
    ))
    view = MailView()
    duplicates = [{"remove": ["synthetic-copy.pdf"]}]
    report = "\n".join(f"合成失败详情 {i}" for i in range(50))

    app.InvoiceApp.show_mail_fetch_result(view, report, True, duplicates)

    assert view.duplicate_prompts == []
    assert texts[0].text == [report]
    assert texts[0].options["state"] == "disabled"
    assert scrollbars[0].options["command"] == texts[0].yview
    assert window.transient_calls == []
    assert window.grab_calls == 0
    assert frames[0].row_weights[1]["weight"] == 1
    assert frames[1].grid_options["row"] == 1
    assert buttons[0].grid_options["row"] == 2
    if dismiss == "button":
        buttons[0].options["command"]()
    elif dismiss == "return":
        window.bindings["<Return>"](None)
    elif dismiss == "escape":
        window.bindings["<Escape>"](None)
    else:
        window.protocols["WM_DELETE_WINDOW"]()
    assert window.destroy_count == 1
    assert view.duplicate_prompts == [duplicates]
    window.protocols["WM_DELETE_WINDOW"]()
    assert window.destroy_count == 1
    assert view.duplicate_prompts == [duplicates]


def test_multiple_result_windows_only_queue_latest_duplicates_when_last_closes(monkeypatch):
    windows = []

    def make_window(*args):
        window = ReportWindow()
        windows.append(window)
        return window

    monkeypatch.setattr(app, "tk", SimpleNamespace(Toplevel=make_window, Text=ReportWidget))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(
        Frame=ReportWidget, Label=ReportWidget, Scrollbar=ReportWidget, Button=ReportWidget,
    ))
    view = MailView()
    older = [{"remove": ["synthetic-old-copy.pdf"]}]
    latest = [{"remove": ["synthetic-latest-copy.pdf"]}]
    app.InvoiceApp.show_mail_fetch_result(view, "较早的合成结果", False, older)
    app.InvoiceApp.show_mail_fetch_result(view, "最新的合成结果", False, latest)

    windows[1].protocols["WM_DELETE_WINDOW"]()
    assert view.duplicate_prompts == []
    windows[0].protocols["WM_DELETE_WINDOW"]()
    assert view.duplicate_prompts == [latest]
    assert view._mail_result_windows == set()
    windows[0].bindings["<Return>"](None)
    assert view.duplicate_prompts == [latest]


@pytest.mark.parametrize("force", [False, True])
def test_scan_defers_duplicate_prompt_while_result_is_open_without_polling(force):
    view = MailView()
    view._mail_result_windows = {ReportWindow()}
    view._handled_duplicate_files = set()
    view._duplicate_dialog_open = False
    view._duplicate_prompt_scheduled = False
    view._show_pending_duplicate_prompt = lambda: pytest.fail("Result is still open")
    latest = [{"digest": "synthetic-hash", "keep": "synthetic.pdf", "remove": ["synthetic-copy.pdf"]}]

    app.InvoiceApp.queue_duplicate_prompt(view, latest, force=force)

    assert view._pending_duplicate_prompt == (latest, force)
    assert view._duplicate_prompt_scheduled is False
    assert view.callbacks == []


def test_already_scheduled_duplicate_prompt_waits_for_result_close_without_polling():
    view = MailView()
    view._mail_result_windows = {ReportWindow()}
    view._duplicate_dialog_open = False
    view._duplicate_prompt_scheduled = True
    groups = [{"digest": "synthetic-hash", "keep": "synthetic.pdf", "remove": ["synthetic-copy.pdf"]}]
    view._pending_duplicate_prompt = (groups, False)

    app.InvoiceApp._show_pending_duplicate_prompt(view)

    assert view._duplicate_prompt_scheduled is False
    assert view._pending_duplicate_prompt == (groups, False)
    assert view._duplicate_dialog_open is False
    assert view.callbacks == []


@pytest.mark.parametrize("stored_cursor,rules_version,expected_start", [
    (None, None, None),
    ("2026-10-12T12:00:00", None, "2026-10-12T12:00:00"),
    ("2026-10-12T12:00:00", "1", "2026-10-12T12:00:00"),
    ("2026-10-12T12:00:00", "2", "2026-10-12T12:00:00"),
    (None, "1", None),
    (None, "2", None),
])
@pytest.mark.parametrize("selected_mode", [None, "all", "custom"])
def test_mail_dialog_defaults_to_saved_cursor_regardless_of_import_rules(
        monkeypatch, stored_cursor, rules_version, expected_start, selected_mode):
    legacy_settings = {
        "qq_email": "test@example.com",
        "qq_auth_code": "encrypted-placeholder",
        "jd_last_fetch": "2026-10-12T12:00:00",
    }
    if stored_cursor is not None:
        legacy_settings["qq_invoice_last_fetch"] = stored_cursor
    if rules_version is not None:
        legacy_settings["qq_invoice_import_rules_version"] = rules_version
    read_keys, buttons, fetch_calls, radios = [], [], [], []

    def get_setting(key, default=""):
        read_keys.append(key)
        return legacy_settings.get(key, default)

    def button(*args, **kwargs):
        widget = FakeWidget(*args, **kwargs)
        buttons.append(widget)
        return widget

    def radio(*args, **kwargs):
        widget = FakeWidget(*args, **kwargs)
        radios.append(widget)
        return widget

    monkeypatch.setattr(app, "get_setting", get_setting)
    monkeypatch.setattr(app, "unprotect_secret", lambda encrypted: "fake-auth-code")
    monkeypatch.setattr(app, "tk", SimpleNamespace(Toplevel=FakeWidget, StringVar=FakeVariable))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(
        Frame=FakeWidget, Label=FakeWidget, Radiobutton=radio,
        Entry=FakeWidget, Button=button,
    ))
    view = SimpleNamespace(run_qq_fetch=lambda *args: fetch_calls.append(args))

    app.InvoiceApp.open_qq_fetch_dialog(view)
    assert radios[0].options["variable"].get() == ("last" if stored_cursor else "all")
    assert radios[0].options["state"] == ("normal" if stored_cursor else "disabled")
    if selected_mode is not None:
        radios[0].options["variable"].value = selected_mode
        expected_start = None if selected_mode == "all" else app.datetime.now().strftime("%Y-%m-%d 00:00")
    next(widget for widget in buttons if widget.options["text"] == "开始获取").options["command"]()

    assert "qq_invoice_last_fetch" in read_keys
    assert "jd_last_fetch" not in read_keys
    assert fetch_calls == [("test@example.com", "fake-auth-code", expected_start)]


@pytest.mark.parametrize("kind", ["filing-error", "changing-file"])
def test_filing_error_or_deferred_file_keeps_mail_cursor_and_retries_scan(fetch_case, monkeypatch, kind):
    case = fetch_case
    stats = {
        "total": 3,
        "abnormal_count": 0,
        "duplicates": [],
        "failed": 1 if kind == "filing-error" else 0,
        "filing_errors": ["合成文件整理错误"] if kind == "filing-error" else [],
        "deferred_paths": [case.folder / "changing.pdf"],
    }
    monkeypatch.setattr(app, "sync_folder", lambda **kwargs: stats)

    run_fetch(case)
    case.view.finish_callbacks()

    assert case.settings == []
    assert case.view._last_folder_signature is None
    assert case.view.refreshed == 1
    assert case.notices[0][0] == "warning"
    assert "上次成功获取时间" in case.notices[0][1]
    if kind == "filing-error":
        assert "合成文件整理错误" in case.notices[0][1]


def test_successful_full_history_import_marks_current_rules_version(fetch_case):
    case = fetch_case
    case.stored_settings["qq_invoice_import_rules_version"] = "1"
    case.stored_settings["qq_invoice_last_fetch"] = "2026-10-12T12:00:00"

    run_fetch(case)
    case.view.finish_callbacks()

    assert case.calls[0]["start_time"] is None
    assert dict(case.settings)["qq_invoice_import_rules_version"] == "2"
    assert app.datetime.fromisoformat(dict(case.settings)["qq_invoice_last_fetch"])
    assert case.notices[0][0] == "info"


def test_successful_limited_import_updates_cursor_but_keeps_history_migration_pending(fetch_case):
    case = fetch_case
    case.stored_settings["qq_invoice_import_rules_version"] = "1"
    start_time = "2026-10-13T00:00:00"

    run_fetch(case, start_time)
    case.view.finish_callbacks()

    assert case.calls[0]["start_time"] == start_time
    assert "qq_invoice_import_rules_version" not in dict(case.settings)
    assert case.stored_settings["qq_invoice_import_rules_version"] == "1"
    assert app.datetime.fromisoformat(dict(case.settings)["qq_invoice_last_fetch"])
    assert "历史" in case.notices[0][1]
    assert "扫描" in case.notices[0][1]


def test_failed_full_history_import_does_not_mark_new_rules_completed(fetch_case):
    case = fetch_case
    case.stored_settings["qq_invoice_import_rules_version"] = "1"
    previous_cursor = "2026-10-12T12:00:00"
    case.stored_settings["qq_invoice_last_fetch"] = previous_cursor
    case.result.update(failed=1, errors=["Synthetic nested archive failure"])

    run_fetch(case)
    case.view.finish_callbacks()

    assert case.settings == []
    assert case.stored_settings["qq_invoice_import_rules_version"] == "1"
    assert case.stored_settings["qq_invoice_last_fetch"] == previous_cursor
    assert case.notices[0][0] == "warning"
