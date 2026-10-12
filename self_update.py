# -*- coding: utf-8 -*-
"""Manual GitHub Release update support for InvoiceManager."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = "Ultraman-Leo-7/InvoiceManager"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
EXE_ASSET = "InvoiceManager-Windows-x64.exe"
CHECKSUM_ASSET = "SHA256SUMS.txt"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(str(text).strip())
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "InvoiceManager", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _positive_size(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        size = int(value)
        return size if size > 0 else None
    return None


def _format_bytes(size: float) -> str:
    if size < 1024:
        return f"{size:.0f} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _download(
    url: str,
    destination: Path,
    timeout: int = 60,
    *,
    progress: Callable[[str], None] | None = None,
    total_size: int | None = None,
) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "InvoiceManager"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, destination.open("wb") as f:
        total = _positive_size(resp.headers.get("Content-Length")) or _positive_size(total_size)
        downloaded = 0
        sample_bytes = 0
        sample_time = time.monotonic()
        speed = 0.0

        def report(finished: bool = False) -> None:
            if progress is None:
                return
            if total is None:
                amount = f"{_format_bytes(downloaded)}（总大小未知）"
            else:
                # 100% only describes the finished transfer, never SHA256 verification.
                percent = min(100.0 if finished else 99.9, downloaded * 100 / total)
                amount = f"{percent:.1f}% · {_format_bytes(downloaded)} / {_format_bytes(total)}"
            state = "程序下载完成" if finished else "下载更新"
            suffix = "（待校验）" if finished else ""
            progress(f"{state}：{amount} · {_format_bytes(speed)}/s{suffix}")

        report()
        # read1 returns available network data instead of waiting to fill a large block.
        read = getattr(resp, "read1", resp.read)
        while True:
            chunk = read(64 * 1024)
            now = time.monotonic()
            if not chunk:
                if total is not None and downloaded < total:
                    raise RuntimeError(
                        f"程序下载不完整：已下载 {_format_bytes(downloaded)}，预期 {_format_bytes(total)}，请重试"
                    )
                if downloaded > sample_bytes and now > sample_time:
                    speed = (downloaded - sample_bytes) / (now - sample_time)
                report(finished=True)
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total is not None and downloaded > total:
                # An inconsistent advertised size cannot provide a reliable percentage.
                total = None
            if now - sample_time >= 0.25:
                speed = (downloaded - sample_bytes) / (now - sample_time)
                report()
                sample_bytes, sample_time = downloaded, now
    return destination


def latest_release(current_version: str) -> dict:
    current = parse_version(current_version)
    if current is None:
        raise ValueError(f"当前版本号无法识别：{current_version}")
    try:
        releases = _get_json(RELEASES_API)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 GitHub 检查更新：{exc.reason}") from exc
    candidates: list[tuple[tuple[int, int, int], dict]] = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        parsed = parse_version(release.get("tag_name", ""))
        if parsed:
            candidates.append((parsed, release))
    if not candidates:
        return {"available": False, "reason": "暂无正式 Release", "current": current_version}
    newest_version, release = max(candidates, key=lambda item: item[0])
    return {
        "available": newest_version > current,
        "current": current_version,
        "latest": ".".join(map(str, newest_version)),
        "release": release,
    }


def _find_asset(release: dict, name: str) -> dict | None:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset
    return None


def download_verified_update(
    release: dict,
    directory: Path,
    progress: Callable[[str], None] | None = None,
) -> Path:
    exe = _find_asset(release, EXE_ASSET)
    sums = _find_asset(release, CHECKSUM_ASSET)
    if not exe or not sums:
        raise RuntimeError("Release 缺少程序文件或 SHA256SUMS.txt")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (EXE_ASSET + ".update")
    sums_path = directory / (CHECKSUM_ASSET + ".update")
    if progress:
        progress("正在连接下载服务器…")
    _download(exe["browser_download_url"], target, progress=progress, total_size=exe.get("size"))
    if progress:
        progress("正在下载校验文件…")
    _download(sums["browser_download_url"], sums_path)
    if progress:
        progress("正在校验 SHA256…")
    checksum_text = sums_path.read_text(encoding="utf-8", errors="replace")
    expected = None
    for line in checksum_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == EXE_ASSET:
            expected = parts[0].lower()
            break
    if not expected:
        raise RuntimeError("SHA256SUMS.txt 中找不到程序校验值")
    actual = hashlib.sha256(target.read_bytes()).hexdigest().lower()
    if actual != expected:
        try:
            target.unlink()
        except OSError:
            pass
        raise RuntimeError("下载文件 SHA256 校验失败，已取消更新")
    try:
        sums_path.unlink()
    except OSError:
        pass
    if progress:
        progress("SHA256 校验通过，准备安装…")
    return target


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def schedule_windows_replacement(current_exe: Path, downloaded_exe: Path) -> None:
    """Replace a running PyInstaller onefile executable and relaunch it safely.

    A PyInstaller onefile process exports internal ``_PYI_*`` environment
    variables. If the replacement executable is launched from a helper process
    that inherited those variables, PyInstaller can mistake the new launch for
    a child process of the old instance and try to reuse the old ``_MEI``
    extraction directory. The old instance then removes that directory while
    exiting, which can produce errors such as ``Failed to load Python DLL``.

    The helper therefore waits for the Python application process to exit,
    replaces the executable, and sets ``PYINSTALLER_RESET_ENVIRONMENT=1`` before
    launching the new executable so it creates an independent extraction
    environment.
    """
    if os.name != "nt":
        raise RuntimeError("自动替换程序目前只支持 Windows")

    current_exe = current_exe.resolve()
    downloaded_exe = downloaded_exe.resolve()
    old = _ps_quote(str(current_exe))
    new = _ps_quote(str(downloaded_exe))
    workdir = _ps_quote(str(current_exe.parent))
    app_pid = os.getpid()

    command = (
        f"$old={old}; $new={new}; $workdir={workdir}; $appPid={app_pid}; "
        "Wait-Process -Id $appPid -Timeout 30 -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 800; "
        "$ok=$false; "
        "for($i=0; $i -lt 60; $i++){ "
        "  try { Move-Item -LiteralPath $new -Destination $old -Force -ErrorAction Stop; $ok=$true; break } "
        "  catch { Start-Sleep -Milliseconds 500 } "
        "}; "
        "if(-not $ok){ exit 1 }; "
        "$env:PYINSTALLER_RESET_ENVIRONMENT='1'; "
        "Remove-Item Env:_PYI_ARCHIVE_FILE -ErrorAction SilentlyContinue; "
        "Remove-Item Env:_PYI_APPLICATION_HOME_DIR -ErrorAction SilentlyContinue; "
        "Start-Process -FilePath $old -WorkingDirectory $workdir"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        cwd=str(current_exe.parent),
        creationflags=creationflags,
    )
