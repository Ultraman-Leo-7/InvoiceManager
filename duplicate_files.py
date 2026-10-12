# -*- coding: utf-8 -*-
"""Plan duplicate PDF cleanup, or mark copies that the user chooses to retain."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

DUPLICATE_PREFIX = "哈希副本-"


def pending_duplicate_groups(groups: list[dict]) -> list[dict]:
    """Skip already marked copies, including after an application restart."""
    pending = []
    for group in groups:
        names = [name for name in group["remove"] if not name.startswith(DUPLICATE_PREFIX)]
        if names:
            pending.append({**group, "remove": names})
    return pending


def build_duplicate_groups(
    files_by_hash: dict[str, list[str]], preferred_names: dict[str, str] | None = None,
    *, arrival_times: dict[str, float] | None = None,
) -> list[dict]:
    """Retain the previous DB name, otherwise the earliest arriving filename."""
    preferred_names = preferred_names or {}
    arrival_times = arrival_times or {}
    groups = []
    for digest, filenames in files_by_hash.items():
        names = sorted(
            set(filenames),
            key=lambda name: (arrival_times.get(name, float("inf")), name.casefold(), name),
        )
        if len(names) < 2:
            continue
        preferred = preferred_names.get(digest)
        keep = preferred if preferred in names else names[0]
        groups.append({"digest": digest, "keep": keep, "remove": [name for name in names if name != keep]})
    return sorted(groups, key=lambda group: (group["keep"].casefold(), group["keep"], group["digest"]))


def _pdf_path(base_dir: Path, name: str) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or any(char in name for char in ("/", "\\", ":", "\0"))
        or name in (".", "..")
        or name.endswith((" ", "."))
        or Path(name).suffix.lower() != ".pdf"
    ):
        raise ValueError("文件名不安全或不是当前文件夹中的 PDF")
    return base_dir / name


def _file_signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular_file_stat(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("不是普通文件（不处理符号链接或文件夹）")
    return info


def _verified_hash(path: Path, expected: str) -> tuple:
    """Hash a regular file, checking that its identity and contents stay stable."""
    before = _regular_file_stat(path)
    signature = _file_signature(before)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened_signature = _file_signature(os.fstat(source.fileno()))
        # Windows may report creation time from lstat(), but metadata-change
        # time from fstat(). Compare shared fields across APIs, then check each
        # full signature (including ctime) against its own stable baseline.
        if (
            opened_signature[:-1] != signature[:-1]
            or _file_signature(_regular_file_stat(path)) != signature
        ):
            raise ValueError("文件在检查时已发生变化，请重新扫描")
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        if _file_signature(os.fstat(source.fileno())) != opened_signature:
            raise ValueError("文件在读取时已发生变化，请重新扫描")
    if _file_signature(_regular_file_stat(path)) != signature:
        raise ValueError("文件在检查时已被替换，请重新扫描")
    if digest.hexdigest() != expected:
        raise ValueError("文件内容已变化，与扫描时的哈希不一致，请重新扫描")
    return signature


def cleanup_duplicate_files(base_dir: Path, groups: list[dict]) -> dict:
    """Apply a duplicate cleanup plan without touching keepers or unlisted files.

    Revalidate both files before each unlink. Stat checks protect against stale
    plans and ordinary concurrent changes; portable path-based unlink cannot
    make the final check and deletion fully atomic against external writers.
    """
    result = {"deleted": [], "errors": []}
    try:
        base_dir = Path(base_dir)
        if base_dir.is_symlink():
            raise ValueError("不处理符号链接文件夹")
        base_dir = base_dir.resolve(strict=True)
        base_info = base_dir.stat()
        if not stat.S_ISDIR(base_info.st_mode):
            raise ValueError("发票文件夹不存在")
        base_identity = (base_info.st_dev, base_info.st_ino)
    except (OSError, ValueError) as exc:
        result["errors"].append(f"无法清理：{exc}")
        return result

    # Also protect keepers in other groups if a caller passes an invalid plan.
    protected = {os.path.normcase(group["keep"]) for group in groups}
    processed = set()
    for group in groups:
        keep_name = group["keep"]
        expected = group["digest"]
        try:
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                raise ValueError("哈希无效，请重新扫描")
            expected = expected.lower()
            keeper = _pdf_path(base_dir, keep_name)
            _verified_hash(keeper, expected)
        except (OSError, ValueError) as exc:
            result["errors"].append(f"保留文件“{keep_name}”无法验证，本组未清理：{exc}")
            continue

        for name in group["remove"]:
            try:
                target = _pdf_path(base_dir, name)
                identity = os.path.normcase(name)
                if identity in protected:
                    raise ValueError("该文件被指定为保留文件，已跳过")
                if identity in processed:
                    continue
                target_signature = _verified_hash(target, expected)
            except (OSError, ValueError) as exc:
                result["errors"].append(f"未删除“{name}”：{exc}")
                continue

            try:
                # Check the keeper again for every copy, including after the
                # previous deletion, so loss of the keeper stops this group.
                keep_signature = _verified_hash(keeper, expected)
                if _file_signature(_regular_file_stat(keeper)) != keep_signature:
                    raise ValueError("保留文件已变化，请重新扫描")
            except (OSError, ValueError) as exc:
                result["errors"].append(f"保留文件“{keep_name}”无法验证，停止清理本组：{exc}")
                break

            try:
                current_base = base_dir.lstat()
                if (
                    not stat.S_ISDIR(current_base.st_mode)
                    or (current_base.st_dev, current_base.st_ino) != base_identity
                ):
                    raise ValueError("发票文件夹已变化，请重新扫描")
                if _file_signature(_regular_file_stat(target)) != target_signature:
                    raise ValueError("文件在删除前已变化，请重新扫描")
                target.unlink()
                processed.add(identity)
                result["deleted"].append(name)
            except (OSError, ValueError) as exc:
                result["errors"].append(f"未删除“{name}”：{exc}")
    return result


def _rename_no_replace(source: Path, destination: Path, expected: str, signature: tuple) -> None:
    """Move a file without replacing an existing name on either platform."""
    if os.name == "nt":
        # Windows rename refuses an existing destination, unlike POSIX rename.
        os.rename(source, destination)
        return

    # An exclusive hard link reserves the new name atomically without copying
    # invoice bytes. A collision, even with a broken symlink, raises EEXIST.
    os.link(source, destination, follow_symlinks=False)
    try:
        linked = _file_signature(_regular_file_stat(destination))
        current = _file_signature(_regular_file_stat(source))
        # Adding a hard link changes ctime, but not identity, size, or mtime.
        if current[:-1] != signature[:-1] or linked != current:
            raise ValueError("文件在改名前已变化，请重新扫描")
        verified = _verified_hash(destination, expected)
        if _file_signature(_regular_file_stat(source)) != verified:
            raise ValueError("原文件在改名前已变化，请重新扫描")
        source.unlink()
    except (OSError, ValueError):
        # Roll back our additional name only while the original still points
        # to that very inode. If the source disappeared, retain the new name.
        try:
            source_info = source.lstat()
            target_info = destination.lstat()
            if (source_info.st_dev, source_info.st_ino) == (target_info.st_dev, target_info.st_ino):
                destination.unlink()
        except OSError:
            pass
        raise


def rename_duplicate_files(base_dir: Path, groups: list[dict]) -> dict:
    """Prefix retained copies after the user declines or closes the prompt."""
    result = {"renamed": [], "errors": []}
    try:
        base_dir = Path(base_dir)
        if base_dir.is_symlink():
            raise ValueError("不处理符号链接文件夹")
        base_dir = base_dir.resolve(strict=True)
        info = base_dir.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("发票文件夹不存在")
        base_identity = (info.st_dev, info.st_ino)
    except (OSError, ValueError) as exc:
        result["errors"].append(f"无法标记副本：{exc}")
        return result

    protected = {os.path.normcase(group["keep"]) for group in groups}
    processed = set()
    for group in pending_duplicate_groups(groups):
        keep_name = group["keep"]
        expected = group["digest"]
        try:
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                raise ValueError("哈希无效，请重新扫描")
            expected = expected.lower()
            keeper = _pdf_path(base_dir, keep_name)
            _verified_hash(keeper, expected)
        except (OSError, ValueError) as exc:
            result["errors"].append(f"保留文件“{keep_name}”无法验证，本组未改名：{exc}")
            continue

        for name in group["remove"]:
            try:
                source = _pdf_path(base_dir, name)
                identity = os.path.normcase(name)
                if identity in protected:
                    raise ValueError("该文件被指定为保留文件，已跳过")
                if identity in processed:
                    continue
                source_signature = _verified_hash(source, expected)
                keep_signature = _verified_hash(keeper, expected)
                number = 1
                while True:
                    new_name = DUPLICATE_PREFIX + name if number == 1 else f"{DUPLICATE_PREFIX}{source.stem} ({number}){source.suffix}"
                    destination = _pdf_path(base_dir, new_name)
                    current_base = base_dir.lstat()
                    if (
                        not stat.S_ISDIR(current_base.st_mode)
                        or (current_base.st_dev, current_base.st_ino) != base_identity
                    ):
                        raise ValueError("发票文件夹已变化，请重新扫描")
                    if _file_signature(_regular_file_stat(keeper)) != keep_signature:
                        raise ValueError("保留文件已变化，请重新扫描")
                    if _file_signature(_regular_file_stat(source)) != source_signature:
                        raise ValueError("文件在改名前已变化，请重新扫描")
                    try:
                        _rename_no_replace(source, destination, expected, source_signature)
                        break
                    except FileExistsError:
                        number += 1
                result["renamed"].append((name, new_name))
                processed.add(identity)
            except (OSError, ValueError) as exc:
                result["errors"].append(f"未标记副本“{name}”：{exc}")
    return result
