"""Classify inbox files without deleting bytes or following linked directories."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import sys
import time
from pathlib import Path

INVOICE_DIRNAME = "发票"
ABNORMAL_DIRNAME = "异常"
SETTLE_SECONDS = 0.2


def invoice_dir(base_dir: Path) -> Path:
    return Path(base_dir) / INVOICE_DIRNAME


def abnormal_dir(base_dir: Path) -> Path:
    return Path(base_dir) / ABNORMAL_DIRNAME


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _directory_stat(path: Path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"“{path.name}”必须是普通文件夹，不能是文件、符号链接或目录联接")
    return info


def _regular_stat(path: Path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError("不是普通文件，不处理链接或文件夹")
    return info


def _signature(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(info) -> tuple:
    return (info.st_dev, info.st_ino)


def _absolute_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _protected_keys(protected_paths) -> set[str]:
    return {_absolute_key(Path(path)) for path in (*protected_paths, sys.executable)}


def _protected_root_file(path: Path, protected: set[str]) -> bool:
    name = path.name.casefold()
    if _absolute_key(path) in protected:
        return True
    if name.startswith((".invoice_manager", ".invoice-", "~$")):
        return True
    if name in {
        "settings.json", "sha256sums.txt", "sha256sums.txt.update", "发票汇总.xlsx", "latest.db",
        "license", "makefile", "pyproject.toml", "pytest.ini", "setup.cfg",
        "requirements.txt", "requirements-dev.txt", "desktop.ini", "thumbs.db",
    }:
        return True
    if name.startswith("invoicemanager") and path.suffix.casefold() in {".exe", ".db", ".update"}:
        return True
    # Source installations must not archive their own scripts/documentation.
    if path.suffix.casefold() in {".py", ".pyw", ".pyc", ".md", ".bat", ".cmd", ".ps1", ".spec"}:
        return True
    # A download/copy is not an input file until its temporary name disappears.
    return name.startswith(".") or name.endswith((".part", ".partial", ".crdownload", ".tmp", ".download"))


def _files(directory: Path, protected: set[str] | None = None) -> list[Path]:
    _directory_stat(directory)
    result = []
    for path in directory.iterdir():
        if protected is not None and _protected_root_file(path, protected):
            continue
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and not _linked(info):
            result.append(path)
    return sorted(result, key=lambda path: (path.name.casefold(), path.name))


def count_abnormal(base_dir: Path) -> int:
    """Count direct regular files; never inspect content inside a linked folder."""
    _directory_stat(Path(base_dir))
    folder = abnormal_dir(base_dir)
    try:
        return len(_files(folder))
    except FileNotFoundError:
        return 0


def inventory_signature(base_dir: Path, protected_paths=()) -> tuple:
    """Cheap polling signature for input, invoice and abnormal files.

    Invalid or temporarily inaccessible folders are represented in the result;
    a subsequent repair changes it and triggers a retry. No directories recurse.
    """
    base_dir = Path(base_dir)
    result = []
    try:
        _directory_stat(base_dir)
    except (OSError, ValueError) as exc:
        return (("root", type(exc).__name__, str(exc)),)
    protected = _protected_keys(protected_paths)
    for label, folder, exclusions in (
        ("root", base_dir, protected),
        (INVOICE_DIRNAME, invoice_dir(base_dir), None),
        (ABNORMAL_DIRNAME, abnormal_dir(base_dir), None),
    ):
        try:
            info = _directory_stat(folder)
            result.append((label, "directory", *_identity(info)))
            for path in _files(folder, exclusions):
                result.append((label, path.name, *_signature(_regular_stat(path))))
        except (OSError, ValueError) as exc:
            result.append((label, type(exc).__name__, str(exc)))
    return tuple(result)


def _hash_stable(path: Path, expected_signature: tuple | None = None) -> tuple[str, tuple]:
    before = _signature(_regular_stat(path))
    if expected_signature is not None and before != expected_signature:
        raise ValueError("文件正在变化，留在原处稍后重试")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        opened = _signature(os.fstat(source.fileno()))
        # Windows lstat/fstat disagree on ctime, so compare it within each API.
        if opened[:-1] != before[:-1] or _signature(_regular_stat(path)) != before:
            raise ValueError("文件已被替换，留在原处稍后重试")
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        if _signature(os.fstat(source.fileno())) != opened:
            raise ValueError("文件正在写入，留在原处稍后重试")
    if _signature(_regular_stat(path)) != before:
        raise ValueError("文件正在变化，留在原处稍后重试")
    return digest.hexdigest(), before


def _valid_invoice(record: dict) -> bool:
    amount = record.get("价税合计")
    if isinstance(amount, (bool, str)) or amount is None:
        return False
    try:
        has_amount = math.isfinite(amount)
    except (TypeError, ValueError, OverflowError):
        return False
    return has_amount and bool(str(record.get("发票号码") or "").strip())


def _move_no_replace(source: Path, destination: Path, digest: str, signature: tuple) -> None:
    if _signature(_regular_stat(source)) != signature:
        raise ValueError("文件在归档前发生变化，留在原处稍后重试")
    if os.name == "nt":
        # Windows rename is exclusive and refuses a locked source/destination.
        os.rename(source, destination)
        return
    os.link(source, destination, follow_symlinks=False)
    try:
        current = _signature(_regular_stat(source))
        linked = _signature(_regular_stat(destination))
        # Adding the new link changes ctime but must not change the other fields.
        if current[:-1] != signature[:-1] or linked != current:
            raise ValueError("文件在归档时发生变化，留在原处稍后重试")
        actual, verified = _hash_stable(source, current)
        if actual != digest or _signature(_regular_stat(destination)) != verified:
            raise ValueError("文件内容在归档时变化，留在原处稍后重试")
        source.unlink()
    except (OSError, ValueError):
        # Only remove the name we created while the source is safely retained.
        try:
            if _identity(source.lstat()) == _identity(destination.lstat()):
                destination.unlink()
        except OSError:
            pass
        raise


def _move_file(source: Path, folder: Path, digest: str, signature: tuple, directories: dict) -> Path:
    for path, identity in directories.items():
        if _identity(_directory_stat(path)) != identity:
            raise ValueError("归档文件夹已变化，请重新扫描")
    if _signature(_regular_stat(source)) != signature:
        raise ValueError("文件在归档前发生变化，留在原处稍后重试")
    if source.parent == folder:
        return source
    index = 1
    while True:
        name = source.name if index == 1 else f"{source.stem} ({index}){source.suffix}"
        destination = folder / name
        try:
            _move_no_replace(source, destination, digest, signature)
            return destination
        except FileExistsError:
            index += 1


def prepare_invoice_files(base_dir: Path, *, parse_invoice, cached_records=None,
                          protected_paths=(), progress=None) -> dict:
    """Route direct files into 发票/异常, retaining all copies and original bytes.

    ``cached_records`` maps SHA256 to current-parser Chinese-field records. Only
    finite numeric totals plus a nonempty invoice number qualify as invoices.
    Caller owns parser-version validation and DB reconciliation. Operation
    failures and unsettled files are retained at their old location and returned
    in ``deferred_paths``. A malformed managed folder blocks the whole pass.
    ``scan_signature`` is the final inventory checked against processed files;
    ``rescan_needed`` flags arrivals or changes that still need another pass.
    """
    base_dir = Path(base_dir)
    result = {"records": [], "abnormal": 0, "errors": [], "moved": 0,
              "deferred_paths": [], "blocked": False, "scan_signature": None,
              "rescan_needed": False}
    try:
        _directory_stat(base_dir)
        for folder in (invoice_dir(base_dir), abnormal_dir(base_dir)):
            folder.mkdir(exist_ok=True)
            _directory_stat(folder)
        directories = {path: _identity(_directory_stat(path)) for path in (
            base_dir, invoice_dir(base_dir), abnormal_dir(base_dir))}
        candidates = _files(invoice_dir(base_dir)) + _files(base_dir, _protected_keys(protected_paths))
    except (OSError, ValueError) as exc:
        result["blocked"] = True
        result["rescan_needed"] = True
        result["errors"].append(f"无法整理文件：{exc}")
        return result

    # One short wait for the batch, not one wait per invoice. Even a freshly
    # downloaded (atomically completed) file can be imported in this pass.
    initial_stats = {}
    for source in candidates:
        try:
            initial_stats[source] = _regular_stat(source)
        except (OSError, ValueError) as exc:
            result["deferred_paths"].append(source)
            result["errors"].append(f"未整理“{source.name}”：{type(exc).__name__}: {exc}")
    if initial_stats:
        time.sleep(SETTLE_SECONDS)
    records_by_hash = dict(cached_records or {})
    processed_files = {}
    for index, source in enumerate(candidates, 1):
        if source not in initial_stats:
            continue
        if progress:
            progress(f"整理 {index}/{len(candidates)}：{source.name}")
        try:
            before = initial_stats[source]
            arrival_time = getattr(before, "st_birthtime", before.st_ctime)
            if _signature(_regular_stat(source)) != _signature(before):
                result["deferred_paths"].append(source)
                continue
            digest, signature = _hash_stable(source, _signature(before))
            record = None
            reused = False
            if source.suffix.casefold() == ".pdf":
                if digest in records_by_hash:
                    record = dict(records_by_hash[digest])
                    reused = True
                else:
                    try:
                        record = dict(parse_invoice(source))
                    except (PermissionError, OSError):
                        raise
                    except Exception:
                        # A stable, readable but malformed PDF belongs in 异常.
                        record = {}
                    records_by_hash[digest] = record
            # Parsing may be slow; recheck bytes before deciding where to move.
            actual, signature = _hash_stable(source, signature)
            if actual != digest:
                raise ValueError("文件内容在识别时变化，留在原处稍后重试")
            valid = record is not None and _valid_invoice(record)
            target = invoice_dir(base_dir) if valid else abnormal_dir(base_dir)
            destination = _move_file(source, target, digest, signature, directories)
            if source != destination:
                result["moved"] += 1
                completed_signature = _signature(_regular_stat(destination))
                # Moving may change ctime, never the verified bytes or mtime.
                if completed_signature[:-1] != signature[:-1]:
                    result["rescan_needed"] = True
            else:
                completed_signature = signature
            processed_files[(target.name, destination.name)] = completed_signature
            if valid:
                record = dict(record)
                record["文件名"] = destination.name
                result["records"].append({"path": destination, "digest": digest, "record": record,
                                          "reused": reused, "arrival_time": arrival_time})
        except (OSError, ValueError) as exc:
            result["deferred_paths"].append(source)
            result["errors"].append(f"未整理“{source.name}”：{type(exc).__name__}: {exc}")
    # Do not let the caller take a newer snapshot after returning: a newly
    # arrived file could otherwise be marked scanned without ever being parsed.
    snapshot = inventory_signature(base_dir, protected_paths)
    result["scan_signature"] = snapshot
    final_files = {entry[:2]: entry[2:] for entry in snapshot if len(entry) == 8}
    result["abnormal"] = sum(key[0] == ABNORMAL_DIRNAME for key in final_files)
    processed_inputs = {key: value for key, value in processed_files.items()
                        if key[0] == INVOICE_DIRNAME}
    final_inputs = {key: value for key, value in final_files.items()
                    if key[0] in ("root", INVOICE_DIRNAME)}
    expected_directories = {
        ("root" if path == base_dir else path.name): identity
        for path, identity in directories.items()
    }
    final_directories = {entry[0]: entry[2:] for entry in snapshot if len(entry) == 4}
    snapshot_errors = [entry for entry in snapshot if len(entry) == 3]
    for label, error_type, detail in snapshot_errors:
        result["errors"].append(f"扫描收尾检查失败（{label}）：{error_type}: {detail}")
    result["rescan_needed"] = bool(
        result["rescan_needed"] or result["deferred_paths"] or snapshot_errors
        or processed_inputs != final_inputs or expected_directories != final_directories
    )
    return result
