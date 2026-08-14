# -*- coding: utf-8 -*-
"""Portable SQLite backups and Nutstore WebDAV transport for InvoiceManager."""

from __future__ import annotations

import base64
import os
import re
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

NUTSTORE_DAV_ROOT = "https://dav.jianguoyun.com/dav/"
REMOTE_FOLDER = "InvoiceManager"
REMOTE_BACKUP_FOLDER = "backups"
LATEST_NAME = "latest.db"
BACKUP_RE = re.compile(r"^InvoiceManager-(\d{8}-\d{6})\.db$")
PORTABLE_SECRET_KEYS = ("qq_auth_code", "nutstore_app_password")


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return bool(row and str(row[0]).lower() == "ok")


def validate_snapshot(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("备份文件为空或不存在")
    conn = sqlite3.connect(path)
    try:
        if not _integrity_ok(conn):
            raise ValueError("SQLite 完整性检查失败")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"settings", "purchases", "invoices"}
        missing = required - tables
        if missing:
            raise ValueError("备份缺少必要数据表：" + ", ".join(sorted(missing)))
    finally:
        conn.close()


def create_portable_snapshot(source_db: Path, destination: Path) -> Path:
    """Create a transactionally consistent copy and remove device-bound secrets."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    source = sqlite3.connect(source_db)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.executemany("DELETE FROM settings WHERE key=?", [(key,) for key in PORTABLE_SECRET_KEYS])
        target.commit()
        if not _integrity_ok(target):
            raise ValueError("创建备份后完整性检查失败")
    finally:
        target.close()
        source.close()
    return destination


def create_local_safety_backup(source_db: Path, backup_dir: Path, reason: str = "auto", keep: int = 20) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = re.sub(r"[^0-9A-Za-z_-]+", "-", reason).strip("-") or "auto"
    destination = backup_dir / f"InvoiceManager-local-{stamp}-{safe_reason}.db"
    source = sqlite3.connect(source_db)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
        if not _integrity_ok(target):
            raise ValueError("本地安全备份完整性检查失败")
    finally:
        target.close()
        source.close()
    backups = sorted(backup_dir.glob("InvoiceManager-local-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[max(keep, 1):]:
        try:
            old.unlink()
        except OSError:
            pass
    return destination


def restore_snapshot(snapshot: Path, target_db: Path, local_backup_dir: Path) -> Path:
    validate_snapshot(snapshot)
    before = create_local_safety_backup(target_db, local_backup_dir, reason="before-restore") if target_db.exists() else None
    tmp_target = target_db.with_name(target_db.name + ".restore.tmp")
    if tmp_target.exists():
        tmp_target.unlink()
    source = sqlite3.connect(snapshot)
    target = sqlite3.connect(tmp_target)
    try:
        source.backup(target)
        target.commit()
        if not _integrity_ok(target):
            raise ValueError("恢复后的数据库完整性检查失败")
    finally:
        target.close()
        source.close()
    os.replace(tmp_target, target_db)
    return before


class NutstoreWebDAV:
    def __init__(self, email: str, app_password: str, timeout: int = 30):
        self.email = email.strip()
        self.app_password = app_password.strip()
        self.timeout = timeout
        if not self.email or not self.app_password:
            raise ValueError("坚果云账号和应用密码不能为空")
        token = base64.b64encode(f"{self.email}:{self.app_password}".encode("utf-8")).decode("ascii")
        self.auth_header = f"Basic {token}"

    def _url(self, *parts: str) -> str:
        encoded = "/".join(urllib.parse.quote(part.strip("/"), safe="") for part in parts if part)
        return urllib.parse.urljoin(NUTSTORE_DAV_ROOT, encoded + ("/" if parts and str(parts[-1]).endswith("/") else ""))

    def _request(self, method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        request_headers = {"Authorization": self.auth_header, "User-Agent": "InvoiceManager"}
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if method == "MKCOL" and exc.code in (405, 409):
                if exc.code == 405:
                    return b""
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"坚果云 WebDAV 请求失败：HTTP {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接坚果云：{exc.reason}") from exc

    @property
    def root_url(self) -> str:
        return self._url(REMOTE_FOLDER) + "/"

    @property
    def backups_url(self) -> str:
        return self._url(REMOTE_FOLDER, REMOTE_BACKUP_FOLDER) + "/"

    def ensure_folders(self) -> None:
        try:
            self._request("MKCOL", self.root_url)
        except RuntimeError as exc:
            if "HTTP 409" not in str(exc):
                raise
        try:
            self._request("MKCOL", self.backups_url)
        except RuntimeError as exc:
            if "HTTP 409" not in str(exc):
                raise

    def test_connection(self) -> None:
        self.ensure_folders()
        self._request("PROPFIND", self.root_url, data=b"", headers={"Depth": "0"})

    def upload_snapshot(self, local_snapshot: Path, keep_history: int = 30) -> str:
        self.ensure_folders()
        data = local_snapshot.read_bytes()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        history_name = f"InvoiceManager-{stamp}.db"
        self._request("PUT", urllib.parse.urljoin(self.backups_url, history_name), data=data, headers={"Content-Type": "application/octet-stream"})
        self._request("PUT", urllib.parse.urljoin(self.root_url, LATEST_NAME), data=data, headers={"Content-Type": "application/octet-stream"})
        try:
            self.prune_history(keep_history)
        except Exception:
            pass
        return history_name

    def list_history(self) -> list[str]:
        self.ensure_folders()
        payload = self._request("PROPFIND", self.backups_url, data=b"", headers={"Depth": "1"})
        names: list[str] = []
        try:
            root = ET.fromstring(payload)
            for href in root.findall(".//{DAV:}href"):
                path = urllib.parse.unquote(href.text or "").rstrip("/")
                name = path.rsplit("/", 1)[-1]
                if BACKUP_RE.match(name):
                    names.append(name)
        except ET.ParseError:
            return []
        return sorted(set(names), reverse=True)

    def prune_history(self, keep: int = 30) -> None:
        names = self.list_history()
        for name in names[max(keep, 1):]:
            try:
                self._request("DELETE", urllib.parse.urljoin(self.backups_url, name))
            except Exception:
                pass

    def download_backup(self, destination: Path, name: str = LATEST_NAME) -> Path:
        if name == LATEST_NAME:
            url = urllib.parse.urljoin(self.root_url, LATEST_NAME)
        elif BACKUP_RE.match(name):
            url = urllib.parse.urljoin(self.backups_url, name)
        else:
            raise ValueError("无效的备份文件名")
        data = self._request("GET", url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        validate_snapshot(destination)
        return destination


def temporary_snapshot_path(prefix: str = "InvoiceManager-cloud-") -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".db")
    os.close(fd)
    return Path(name)
