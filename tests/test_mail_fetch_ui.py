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

    def queue_duplicate_prompt(self, groups):
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

    def fetch(**kwargs):
        calls.append(kwargs)
        return result

    def sync(**kwargs):
        sync_calls.append(kwargs)
        return {"total": 4, "duplicates": groups}

    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "fetch_qq_invoices", fetch)
    monkeypatch.setattr(app, "set_setting", lambda key, value: settings.append((key, value)))
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
    )


def run_fetch(case):
    app.InvoiceApp.run_qq_fetch(case.view, "test@example.com", "fake-auth-code", None)


def test_unified_worker_passes_credentials_and_folder_to_mail_fetcher(fetch_case):
    case = fetch_case

    run_fetch(case)

    assert len(case.calls) == 1
    assert case.calls[0] == {
        "email_addr": "test@example.com",
        "auth_code": "fake-auth-code",
        "output_dir": case.folder,
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

    assert len(case.settings) == 1
    key, timestamp = case.settings[0]
    assert key == "qq_invoice_last_fetch"
    assert app.datetime.fromisoformat(timestamp)
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


@pytest.mark.parametrize("dismiss", ["button", "escape", "close"])
def test_result_dialog_keeps_full_details_and_prompts_duplicates_only_after_close(monkeypatch, dismiss):
    from tests.test_duplicate_dialog import FakeWidget as ReportWidget, FakeWindow

    window = FakeWindow()
    texts, buttons, scrollbars = [], [], []

    def collect(target, *args, **kwargs):
        widget = ReportWidget(*args, **kwargs)
        target.append(widget)
        return widget

    monkeypatch.setattr(app, "tk", SimpleNamespace(
        Toplevel=lambda *args: window,
        Text=lambda *args, **kwargs: collect(texts, *args, **kwargs),
    ))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(
        Frame=ReportWidget, Label=ReportWidget,
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
    if dismiss == "button":
        buttons[0].options["command"]()
    elif dismiss == "escape":
        window.bindings["<Escape>"](None)
    else:
        window.protocols["WM_DELETE_WINDOW"]()
    assert window.destroyed
    assert view.duplicate_prompts == [duplicates]
    window.protocols["WM_DELETE_WINDOW"]()
    assert view.duplicate_prompts == [duplicates]


def test_upgrading_from_jd_only_defaults_to_all_history(monkeypatch):
    legacy_settings = {
        "qq_email": "test@example.com",
        "qq_auth_code": "encrypted-placeholder",
        "jd_last_fetch": "2026-10-12T12:00:00",
    }
    read_keys, buttons, fetch_calls = [], [], []

    def get_setting(key, default=""):
        read_keys.append(key)
        return legacy_settings.get(key, default)

    def button(*args, **kwargs):
        widget = FakeWidget(*args, **kwargs)
        buttons.append(widget)
        return widget

    monkeypatch.setattr(app, "get_setting", get_setting)
    monkeypatch.setattr(app, "unprotect_secret", lambda encrypted: "fake-auth-code")
    monkeypatch.setattr(app, "tk", SimpleNamespace(Toplevel=FakeWidget, StringVar=FakeVariable))
    monkeypatch.setattr(app, "ttk", SimpleNamespace(
        Frame=FakeWidget, Label=FakeWidget, Radiobutton=FakeWidget,
        Entry=FakeWidget, Button=button,
    ))
    view = SimpleNamespace(run_qq_fetch=lambda *args: fetch_calls.append(args))

    app.InvoiceApp.open_qq_fetch_dialog(view)
    next(widget for widget in buttons if widget.options["text"] == "开始获取").options["command"]()

    assert "qq_invoice_last_fetch" in read_keys
    assert "jd_last_fetch" not in read_keys
    assert fetch_calls == [("test@example.com", "fake-auth-code", None)]
