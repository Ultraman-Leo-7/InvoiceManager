from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app


class ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


class UpdateView:
    set_update_status = app.InvoiceApp.set_update_status

    def __init__(self):
        self.callbacks = []
        self.statuses = []
        self.update_statuses = []
        self.status_var = SimpleNamespace(set=self.statuses.append)
        self.update_status_var = SimpleNamespace(set=self.update_statuses.append)
        self._update_busy = False
        self.destroy = Mock()
        self.download_and_install_update = Mock()

    def set_status(self, message):
        self.statuses.append(message)

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def finish_callbacks(self):
        # Tk executes these after worker() has returned and cleared its except variable.
        while self.callbacks:
            self.callbacks.pop(0)()


@pytest.fixture
def update_case(tmp_path, monkeypatch):
    originals = {
        tmp_path / "InvoiceManager-Windows-x64.exe": b"existing executable",
        tmp_path / app.DB_FILENAME: b"synthetic database sentinel",
        tmp_path / "invoice.pdf": b"%PDF-synthetic invoice sentinel",
    }
    for path, content in originals.items():
        path.write_bytes(content)
    current_exe = tmp_path / "InvoiceManager-Windows-x64.exe"
    downloaded_exe = tmp_path / "InvoiceManager-Windows-x64.exe.update"
    notices = []
    download = Mock(return_value=downloaded_exe)
    replace = Mock()
    connect_db = Mock(side_effect=AssertionError("Updater must not open the invoice database"))
    sync_folder = Mock(side_effect=AssertionError("Updater must not scan or alter invoice files"))
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / app.DB_FILENAME)
    monkeypatch.setattr(app, "sys", SimpleNamespace(frozen=True, executable=str(current_exe)))
    monkeypatch.setattr(app, "threading", SimpleNamespace(Thread=ImmediateThread))
    monkeypatch.setattr(app, "download_verified_update", download)
    monkeypatch.setattr(app, "schedule_windows_replacement", replace)
    monkeypatch.setattr(app, "connect_db", connect_db)
    monkeypatch.setattr(app, "sync_folder", sync_folder)
    monkeypatch.setattr(app, "messagebox", SimpleNamespace(
        showerror=lambda title, message: notices.append((title, message)),
        showinfo=Mock(),
        askyesno=Mock(return_value=True),
    ))
    return SimpleNamespace(
        view=UpdateView(), folder=tmp_path, originals=originals, notices=notices,
        download=download, replace=replace, connect_db=connect_db,
        sync_folder=sync_folder, current_exe=current_exe, downloaded_exe=downloaded_exe,
    )


def assert_local_data_unchanged(case):
    for path, content in case.originals.items():
        assert path.read_bytes() == content
    case.connect_db.assert_not_called()
    case.sync_folder.assert_not_called()


def test_check_failure_shows_original_error_after_worker_returns(update_case, monkeypatch):
    case = update_case
    check = Mock(side_effect=RuntimeError("无法连接 GitHub 检查更新：测试连接超时"))
    monkeypatch.setattr(app, "latest_release", check)

    app.InvoiceApp.check_for_updates(case.view)

    assert case.notices == []
    case.view.finish_callbacks()

    check.assert_called_once_with(app.APP_VERSION)
    assert case.view.statuses[-1] == "检查更新失败"
    assert len(case.notices) == 1
    assert "RuntimeError" in case.notices[0][1]
    assert "测试连接超时" in case.notices[0][1]
    case.view.download_and_install_update.assert_not_called()
    case.download.assert_not_called()
    case.replace.assert_not_called()
    case.view.destroy.assert_not_called()
    assert_local_data_unchanged(case)


def test_download_failure_shows_error_without_installing_or_closing(update_case):
    case = update_case
    case.download.side_effect = RuntimeError("下载文件 SHA256 校验失败，已取消更新")
    release = {"tag_name": "v9.0.0"}

    app.InvoiceApp.download_and_install_update(case.view, release, "9.0.0")

    assert case.notices == []
    case.view.finish_callbacks()

    case.download.assert_called_once_with(release, case.folder, progress=case.view.set_update_status)
    assert case.view.statuses[-1] == "下载更新失败"
    assert len(case.notices) == 1
    assert "RuntimeError" in case.notices[0][1]
    assert "SHA256 校验失败" in case.notices[0][1]
    case.replace.assert_not_called()
    case.view.destroy.assert_not_called()
    assert_local_data_unchanged(case)


def test_download_progress_reaches_both_views_on_ui_callbacks(update_case):
    case = update_case
    progress_text = "下载更新：42.5% · 17.0 MiB / 40.0 MiB · 1.8 MiB/s"

    def download(release, directory, *, progress):
        progress(progress_text)
        progress("正在下载校验文件...")
        progress("正在校验 SHA256...")
        # The worker must not write directly to Tk variables.
        assert case.view.update_statuses == case.view.statuses == []
        return case.downloaded_exe

    case.download.side_effect = download
    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")
    case.view.finish_callbacks()

    assert case.view.update_statuses == case.view.statuses
    assert progress_text in case.view.update_statuses
    assert case.view.update_statuses.index(progress_text) < case.view.update_statuses.index("正在校验 SHA256...")
    assert "通过 SHA256 校验" in case.view.update_statuses[-1]
    assert_local_data_unchanged(case)


def test_repeated_update_action_does_not_start_second_download(update_case):
    case = update_case
    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")
    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")
    app.InvoiceApp.check_for_updates(case.view)

    assert case.download.call_count == 1
    case.view.finish_callbacks()
    assert case.replace.call_count == 1


def test_download_can_be_retried_after_failure(update_case):
    case = update_case
    case.download.side_effect = RuntimeError("测试连接中断")
    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")
    case.view.finish_callbacks()
    assert not case.view._update_busy

    case.download.side_effect = None
    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")
    case.view.finish_callbacks()
    assert case.download.call_count == 2
    case.replace.assert_called_once()


def test_check_can_be_retried_after_failure(update_case, monkeypatch):
    case = update_case
    check = Mock(side_effect=RuntimeError("测试检查失败"))
    monkeypatch.setattr(app, "latest_release", check)
    app.InvoiceApp.check_for_updates(case.view)
    app.InvoiceApp.check_for_updates(case.view)
    assert check.call_count == 1
    case.view.finish_callbacks()
    assert not case.view._update_busy

    check.side_effect = None
    check.return_value = {"available": False, "latest": app.APP_VERSION}
    app.InvoiceApp.check_for_updates(case.view)
    case.view.finish_callbacks()
    assert check.call_count == 2
    assert not case.view._update_busy


def test_accepting_update_releases_check_guard_before_download(update_case, monkeypatch):
    case = update_case
    release = {"tag_name": "v9.0.0"}
    monkeypatch.setattr(app, "latest_release", lambda version: {
        "available": True, "latest": "9.0.0", "release": release,
    })
    case.view.download_and_install_update = lambda release, latest: app.InvoiceApp.download_and_install_update(
        case.view, release, latest,
    )

    app.InvoiceApp.check_for_updates(case.view)
    case.view.finish_callbacks()

    case.download.assert_called_once_with(release, case.folder, progress=case.view.set_update_status)
    case.replace.assert_called_once()
    assert_local_data_unchanged(case)


def test_installer_launch_failure_leaves_application_open(update_case):
    case = update_case
    case.replace.side_effect = OSError("测试 PowerShell 无法启动")

    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")

    case.replace.assert_not_called()
    case.view.destroy.assert_not_called()
    case.view.finish_callbacks()

    case.replace.assert_called_once_with(case.current_exe, case.downloaded_exe)
    assert case.view.statuses[-1] == "安装更新失败"
    assert len(case.notices) == 1
    assert "OSError" in case.notices[0][1]
    assert "测试 PowerShell 无法启动" in case.notices[0][1]
    case.view.destroy.assert_not_called()
    assert_local_data_unchanged(case)


def test_verified_update_closes_only_after_replacement_is_scheduled(update_case):
    case = update_case
    actions = []
    case.replace.side_effect = lambda *args: actions.append("schedule")
    case.view.destroy.side_effect = lambda: actions.append("close")

    app.InvoiceApp.download_and_install_update(case.view, {}, "9.0.0")

    assert actions == []
    case.view.finish_callbacks()

    assert actions == ["schedule", "close"]
    case.replace.assert_called_once_with(case.current_exe, case.downloaded_exe)
    assert case.notices == []
    assert_local_data_unchanged(case)
