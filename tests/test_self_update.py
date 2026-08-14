import os

import pytest

import self_update
from self_update import parse_version


def test_parse_plain_release_versions():
    assert parse_version("v5.2.9") == (5, 2, 9)
    assert parse_version("5.3.0") == (5, 3, 0)


def test_reject_beta_or_invalid_versions():
    assert parse_version("v5.2.9-beta.1") is None
    assert parse_version("v5.2") is None
    assert parse_version("hello") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows updater only")
def test_windows_relaunch_resets_pyinstaller_environment(monkeypatch, tmp_path):
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(self_update.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(self_update.os, "getpid", lambda: 12345)

    self_update.schedule_windows_replacement(
        tmp_path / "InvoiceManager-Windows-x64.exe",
        tmp_path / "InvoiceManager-Windows-x64.exe.update",
    )

    assert len(calls) == 1
    args, _kwargs = calls[0]
    command = args[-1]
    assert "$appPid=12345" in command
    assert "Wait-Process -Id $appPid" in command
    assert "PYINSTALLER_RESET_ENVIRONMENT='1'" in command
    assert "Env:_PYI_ARCHIVE_FILE" in command
    assert "Env:_PYI_APPLICATION_HOME_DIR" in command
