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


class DownloadResponse:
    def __init__(self, chunks, clock, *, length=None, delays=None, error=None):
        self.chunks = iter(chunks)
        self.clock = clock
        self.headers = {} if length is None else {"Content-Length": length}
        self.delays = iter(delays or [])
        self.read_sizes = []
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        raise AssertionError("Use read1 so a slow connection need not fill the block")

    def read1(self, size):
        self.read_sizes.append(size)
        self.clock[0] += next(self.delays, 0)
        chunk = next(self.chunks, None)
        if chunk is None:
            if self.error:
                raise self.error
            return b""
        return chunk


def install_download_response(monkeypatch, response, clock):
    monkeypatch.setattr(self_update.urllib.request, "urlopen", lambda request, timeout: response)
    monkeypatch.setattr(self_update.time, "monotonic", lambda: clock[0])


def test_download_reports_header_size_and_recent_interval_speed(monkeypatch, tmp_path):
    clock = [0.0]
    response = DownloadResponse(
        [b"a" * 1024, b"b" * 1024], clock, length="2048", delays=[0.5, 0.25]
    )
    install_download_response(monkeypatch, response, clock)
    messages = []
    destination = tmp_path / "update"

    self_update._download("https://example.test/exe", destination, progress=messages.append, total_size=4096)

    assert destination.read_bytes() == b"a" * 1024 + b"b" * 1024
    assert messages[0] == "下载更新：0.0% · 0 B / 2.0 KiB · 0 B/s"
    assert "50.0% · 1.0 KiB / 2.0 KiB · 2.0 KiB/s" in messages[1]
    # Second interval is 4 KiB/s, not the lifetime average of 2.7 KiB/s.
    assert "99.9% · 2.0 KiB / 2.0 KiB · 4.0 KiB/s" in messages[2]
    assert messages[-1] == "程序下载完成：100.0% · 2.0 KiB / 2.0 KiB · 4.0 KiB/s（待校验）"
    assert all(size == 64 * 1024 for size in response.read_sizes)


def test_download_throttles_callbacks_but_always_reports_final_sample(monkeypatch, tmp_path):
    clock = [0.0]
    response = DownloadResponse([b"a" * 1024] * 5, clock, length="5120", delays=[0.0625] * 5)
    install_download_response(monkeypatch, response, clock)
    messages = []

    self_update._download("https://example.test/exe", tmp_path / "update", progress=messages.append)

    assert len(messages) == 3
    assert "80.0%" in messages[1]
    assert "100.0%" in messages[-1]
    assert "16.0 KiB/s" in messages[-1]


@pytest.mark.parametrize("length", [None, "broken", "-1", "0", "1.5", ""])
def test_download_uses_asset_size_when_header_is_unusable(monkeypatch, tmp_path, length):
    clock = [0.0]
    response = DownloadResponse([b"a" * 1024], clock, length=length, delays=[0.5])
    install_download_response(monkeypatch, response, clock)
    messages = []

    self_update._download(
        "https://example.test/exe", tmp_path / "update", progress=messages.append, total_size=1024
    )

    assert "100.0% · 1.0 KiB / 1.0 KiB" in messages[-1]
    assert "2.0 KiB/s" in messages[-1]


@pytest.mark.parametrize("asset_size", [None, 0, -1, "broken", True, 1.5])
def test_download_unknown_size_reports_bytes_and_speed_without_percentage(monkeypatch, tmp_path, asset_size):
    clock = [0.0]
    response = DownloadResponse([b"a" * 1024], clock, delays=[0.5])
    install_download_response(monkeypatch, response, clock)
    messages = []

    self_update._download(
        "https://example.test/exe", tmp_path / "update", progress=messages.append, total_size=asset_size
    )

    assert all("%" not in message for message in messages)
    assert "1.0 KiB（总大小未知） · 2.0 KiB/s" in messages[-1]
    assert messages[-1].endswith("（待校验）")


def test_download_discards_inconsistent_size_instead_of_exceeding_100_percent(monkeypatch, tmp_path):
    clock = [0.0]
    response = DownloadResponse([b"a" * 1024], clock, length="500", delays=[0.5])
    install_download_response(monkeypatch, response, clock)
    messages = []

    self_update._download("https://example.test/exe", tmp_path / "update", progress=messages.append)

    assert "总大小未知" in messages[-1]
    assert "%" not in messages[-1]


@pytest.mark.parametrize("size, expected", [(512, "512 B"), (1024, "1.0 KiB"), (1572864, "1.5 MiB")])
def test_progress_units_use_binary_scaling(size, expected):
    assert self_update._format_bytes(size) == expected


def update_release():
    return {"assets": [
        {"name": self_update.EXE_ASSET, "browser_download_url": "https://example.test/exe", "size": 1024},
        {"name": self_update.CHECKSUM_ASSET, "browser_download_url": "https://example.test/sums"},
    ]}


def prepare_verified_responses(monkeypatch, checksum, *, error=None, exe_content=b"a" * 1024):
    clock = [0.0]
    responses = {
        "https://example.test/exe": DownloadResponse([exe_content], clock, delays=[0.5], error=error),
        "https://example.test/sums": DownloadResponse([checksum.encode("utf-8")], clock),
    }
    requests = []

    def urlopen(request, timeout):
        requests.append(request.full_url)
        return responses[request.full_url]

    monkeypatch.setattr(self_update.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(self_update.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        self_update, "schedule_windows_replacement",
        lambda *args: pytest.fail("The downloader must not install or replace any program"),
    )
    return requests


@pytest.mark.parametrize("use_callback", [False, True])
def test_verified_download_preserves_optional_callback_and_reports_stages(monkeypatch, tmp_path, use_callback):
    checksum = self_update.hashlib.sha256(b"a" * 1024).hexdigest()
    prepare_verified_responses(monkeypatch, f"{checksum}  {self_update.EXE_ASSET}\r\n")
    messages = []
    kwargs = {"progress": messages.append} if use_callback else {}

    target = self_update.download_verified_update(update_release(), tmp_path, **kwargs)

    assert target.read_bytes() == b"a" * 1024
    assert not (tmp_path / (self_update.CHECKSUM_ASSET + ".update")).exists()
    if use_callback:
        assert messages[0] == "正在连接下载服务器…"
        assert "100.0%" in messages[-4]
        assert messages[-4].endswith("（待校验）")
        assert messages[-3:] == ["正在下载校验文件…", "正在校验 SHA256…", "SHA256 校验通过，准备安装…"]


def test_checksum_failure_still_rejects_download_and_preserves_original_files(monkeypatch, tmp_path):
    prepare_verified_responses(monkeypatch, f"{'0' * 64}  {self_update.EXE_ASSET}\n")
    original = tmp_path / self_update.EXE_ASSET
    original.write_bytes(b"original program")
    messages = []

    with pytest.raises(RuntimeError, match="SHA256 校验失败"):
        self_update.download_verified_update(update_release(), tmp_path, progress=messages.append)

    assert original.read_bytes() == b"original program"
    assert not (tmp_path / (self_update.EXE_ASSET + ".update")).exists()
    assert messages[-1] == "正在校验 SHA256…"
    assert not any("校验通过" in message for message in messages)


def test_network_failure_stops_before_checksum_and_keeps_original_program(monkeypatch, tmp_path):
    requests = prepare_verified_responses(monkeypatch, "unused", error=TimeoutError("download timed out"))
    original = tmp_path / self_update.EXE_ASSET
    original.write_bytes(b"original program")
    messages = []

    with pytest.raises(TimeoutError, match="download timed out"):
        self_update.download_verified_update(update_release(), tmp_path, progress=messages.append)

    assert requests == ["https://example.test/exe"]
    assert original.read_bytes() == b"original program"
    assert not any("下载完成" in message or "校验通过" in message for message in messages)


def test_early_eof_rejects_incomplete_download_before_checksum(monkeypatch, tmp_path):
    requests = prepare_verified_responses(monkeypatch, "unused", exe_content=b"a" * 512)
    messages = []

    with pytest.raises(RuntimeError, match="程序下载不完整"):
        self_update.download_verified_update(update_release(), tmp_path, progress=messages.append)

    assert requests == ["https://example.test/exe"]
    assert "50.0%" in messages[-1]
    assert not any("下载完成" in message or "校验通过" in message for message in messages)
