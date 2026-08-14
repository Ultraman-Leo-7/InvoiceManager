# -*- coding: utf-8 -*-
"""Manual GitHub Release update support for InvoiceManager."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
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
    req = urllib.request.Request(url, headers={"User-Agent": "InvoiceManager", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, destination: Path, timeout: int = 60) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "InvoiceManager"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, destination.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
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


def download_verified_update(release: dict, directory: Path) -> Path:
    exe = _find_asset(release, EXE_ASSET)
    sums = _find_asset(release, CHECKSUM_ASSET)
    if not exe or not sums:
        raise RuntimeError("Release 缺少程序文件或 SHA256SUMS.txt")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (EXE_ASSET + ".update")
    sums_path = directory / (CHECKSUM_ASSET + ".update")
    _download(exe["browser_download_url"], target)
    _download(sums["browser_download_url"], sums_path)
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
    return target


def schedule_windows_replacement(current_exe: Path, downloaded_exe: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("自动替换程序目前只支持 Windows")
    current_exe = current_exe.resolve()
    downloaded_exe = downloaded_exe.resolve()
    script = current_exe.parent / ".invoice_manager_update.cmd"
    content = f'''@echo off\r\nsetlocal\r\nset "OLD={current_exe}"\r\nset "NEW={downloaded_exe}"\r\nfor /L %%I in (1,1,30) do (\r\n  move /Y "%NEW%" "%OLD%" >nul 2>nul && goto :updated\r\n  timeout /t 1 /nobreak >nul\r\n)\r\nexit /b 1\r\n:updated\r\nstart "" "%OLD%"\r\ndel "%~f0"\r\n'''
    script.write_text(content, encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["cmd.exe", "/c", str(script)], cwd=str(current_exe.parent), creationflags=creationflags)
    return script
