import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import invoice_filing as filing


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch):
    monkeypatch.setattr(filing, "SETTLE_SECONDS", 0)


def record(path, *, total=12.5, number="TEST-001"):
    return {"文件名": path.name, "发票号码": number, "价税合计": total,
            "提取状态": "需复核：缺少开票日期"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_classifies_root_and_invoice_files_without_touching_other_directories(tmp_path):
    valid = b"valid invoice bytes"
    (tmp_path / "new.PDF").write_bytes(valid)
    (tmp_path / "note.txt").write_text("keep these exact bytes")
    filing.invoice_dir(tmp_path).mkdir()
    (filing.invoice_dir(tmp_path) / "invalid.pdf").write_bytes(b"invalid")
    unrelated = tmp_path / "other-folder"
    unrelated.mkdir()
    (unrelated / "inside.pdf").write_bytes(valid)

    result = filing.prepare_invoice_files(
        tmp_path, parse_invoice=lambda path: record(path, total=None if path.name == "invalid.pdf" else 12.5))

    assert result["errors"] == []
    assert result["moved"] == 3
    assert result["abnormal"] == 2
    assert result["records"][0]["path"] == filing.invoice_dir(tmp_path) / "new.PDF"
    assert result["records"][0]["record"]["提取状态"].startswith("需复核")
    assert (filing.invoice_dir(tmp_path) / "new.PDF").read_bytes() == valid
    assert (filing.abnormal_dir(tmp_path) / "note.txt").read_text() == "keep these exact bytes"
    assert (unrelated / "inside.pdf").read_bytes() == valid


@pytest.mark.parametrize("total", [0, -12.5, 1.25])
def test_finite_amount_including_zero_and_red_invoice_is_valid(tmp_path, total):
    (tmp_path / "bill.pdf").write_bytes(b"invoice")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=lambda path: record(path, total=total))
    assert len(result["records"]) == 1
    assert result["abnormal"] == 0


@pytest.mark.parametrize("total,number", [
    (None, "123"), (float("nan"), "123"), (float("inf"), "123"),
    (-float("inf"), "123"), (True, "123"), ("12.5", "123"), (12.5, ""), (12.5, "  "), (12.5, None),
])
def test_missing_amount_or_number_and_nonfinite_values_are_abnormal(tmp_path, total, number):
    (tmp_path / "bill.pdf").write_bytes(b"invoice")
    result = filing.prepare_invoice_files(
        tmp_path, parse_invoice=lambda path: record(path, total=total, number=number))
    assert result["records"] == []
    assert result["abnormal"] == 1
    assert result["errors"] == []


def test_malformed_pdf_goes_to_abnormal_but_read_error_stays_for_retry(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"broken")
    (tmp_path / "locked.pdf").write_bytes(b"temporarily locked")

    def parse(path):
        if path.name == "locked.pdf":
            raise PermissionError("in use")
        raise ValueError("damaged PDF")

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert result["abnormal"] == 1
    assert (filing.abnormal_dir(tmp_path) / "broken.pdf").read_bytes() == b"broken"
    assert (tmp_path / "locked.pdf").exists()
    assert result["deferred_paths"] == [tmp_path / "locked.pdf"]
    assert len(result["errors"]) == 1


def test_collision_keeps_both_files_and_keeps_existing_name(tmp_path):
    folder = filing.invoice_dir(tmp_path)
    folder.mkdir()
    (folder / "bill.pdf").write_bytes(b"old")
    (tmp_path / "bill.pdf").write_bytes(b"new")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert (folder / "bill.pdf").read_bytes() == b"old"
    assert (folder / "bill (2).pdf").read_bytes() == b"new"
    assert len(result["records"]) == 2
    assert result["moved"] == 1
    assert {entry["record"]["文件名"] for entry in result["records"]} == {"bill.pdf", "bill (2).pdf"}


def test_duplicate_hashes_are_retained_and_parsed_once(tmp_path):
    for name in ("first.pdf", "copy.pdf"):
        (tmp_path / name).write_bytes(b"identical")
    calls = []

    def parse(path):
        calls.append(path.name)
        return record(path)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert len(calls) == 1
    assert len(result["records"]) == 2
    assert len(list(filing.invoice_dir(tmp_path).iterdir())) == 2
    assert [entry["reused"] for entry in result["records"]] == [False, True]


def test_uses_current_cache_without_mutating_it_and_preserves_arrival_time(tmp_path):
    path = tmp_path / "renamed.pdf"
    path.write_bytes(b"cached")
    original_info = path.stat()
    original_arrival = getattr(original_info, "st_birthtime", original_info.st_ctime)
    cache_record = record(Path("previous.pdf"))

    def should_not_parse(path):
        pytest.fail("cache should avoid parsing")

    result = filing.prepare_invoice_files(
        tmp_path, parse_invoice=should_not_parse, cached_records={digest(b"cached"): cache_record})
    entry = result["records"][0]
    assert entry["reused"] is True
    assert entry["arrival_time"] == original_arrival
    assert entry["record"]["文件名"] == "renamed.pdf"
    assert cache_record["文件名"] == "previous.pdf"


def test_abnormal_is_not_reparsed_until_user_moves_file_to_root(tmp_path):
    abnormal = filing.abnormal_dir(tmp_path)
    abnormal.mkdir()
    file = abnormal / "retry.pdf"
    file.write_bytes(b"invoice")
    calls = []

    def parse(path):
        calls.append(path)
        return record(path)

    first = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert first["abnormal"] == 1
    assert calls == []
    file.rename(tmp_path / file.name)
    second = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert second["abnormal"] == 0
    assert len(second["records"]) == 1
    assert calls == [tmp_path / "retry.pdf"]


def test_program_database_export_backup_and_development_files_are_protected(tmp_path):
    protected_names = [
        ".invoice_manager.db", ".invoice_manager.db-wal", ".invoice_manager.db-shm",
        ".invoice_manager.db-journal", "settings.json", "InvoiceManager-Windows-x64.exe",
        "InvoiceManager.update", "InvoiceManager-20261013-120000.db", "latest.db",
        "SHA256SUMS.txt", "SHA256SUMS.txt.update", "发票汇总.xlsx", "app.py", "README.md", "build.bat", "run.cmd",
        "install.ps1", "app.spec", "requirements.txt", "requirements-dev.txt", "LICENSE",
        "part.pdf.crdownload", "pending.tmp", "custom-data.bin", "custom-export.xlsx",
    ]
    for name in protected_names:
        (tmp_path / name).write_bytes(b"must stay")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record, protected_paths=[
        tmp_path / "custom-data.bin", tmp_path / "custom-export.xlsx"])
    assert result["moved"] == 0
    assert result["abnormal"] == 0
    assert all((tmp_path / name).read_bytes() == b"must stay" for name in protected_names)


def test_inventory_tracks_only_inputs_managed_files_and_abnormal_count(tmp_path):
    filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    baseline = filing.inventory_signature(tmp_path)
    (tmp_path / ".invoice_manager.db").write_bytes(b"changed database")
    assert filing.inventory_signature(tmp_path) == baseline
    (tmp_path / "new.pdf").write_bytes(b"candidate")
    assert filing.inventory_signature(tmp_path) != baseline
    filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    baseline = filing.inventory_signature(tmp_path)
    (filing.abnormal_dir(tmp_path) / "review.txt").write_text("review")
    assert filing.inventory_signature(tmp_path) != baseline
    assert filing.count_abnormal(tmp_path) == 1
    (filing.abnormal_dir(tmp_path) / "nested").mkdir()
    (filing.abnormal_dir(tmp_path) / "nested" / "ignored.txt").write_text("nested")
    assert filing.count_abnormal(tmp_path) == 1


@pytest.mark.parametrize("dirname", [filing.INVOICE_DIRNAME, filing.ABNORMAL_DIRNAME])
def test_managed_name_occupied_by_file_blocks_without_moving_input(tmp_path, dirname):
    (tmp_path / dirname).write_bytes(b"existing user bytes")
    (tmp_path / "bill.pdf").write_bytes(b"invoice")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["blocked"] is True
    assert result["errors"]
    assert (tmp_path / "bill.pdf").exists()
    assert (tmp_path / dirname).read_bytes() == b"existing user bytes"


def make_symlink_or_skip(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("symlink permission unavailable")


def test_linked_managed_directory_is_rejected_and_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    make_symlink_or_skip(filing.abnormal_dir(tmp_path), outside, directory=True)
    (tmp_path / "note.txt").write_bytes(b"stay here")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["blocked"] is True
    assert (tmp_path / "note.txt").exists()
    assert list(outside.iterdir()) == []
    with pytest.raises(ValueError, match="联接"):
        filing.count_abnormal(tmp_path)


def test_file_symlinks_are_not_processed_or_counted(tmp_path):
    target = tmp_path / "target.txt"
    target.write_bytes(b"do not change")
    make_symlink_or_skip(tmp_path / "linked.pdf", target)
    filing.abnormal_dir(tmp_path).mkdir()
    make_symlink_or_skip(filing.abnormal_dir(tmp_path) / "linked.txt", target)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record, protected_paths=[target])
    assert result["records"] == []
    assert result["abnormal"] == 0
    assert target.read_bytes() == b"do not change"


def test_windows_reparse_attribute_is_rejected_without_following(monkeypatch, tmp_path):
    path = tmp_path / "junction"
    path.mkdir()
    real_lstat = Path.lstat

    def reparse_lstat(self):
        info = real_lstat(self)
        if self == path:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(ValueError, match="联接"):
        filing._directory_stat(path)


def test_file_changing_during_settle_stays_in_root_until_next_pass(tmp_path, monkeypatch):
    path = tmp_path / "copying.pdf"
    path.write_bytes(b"partial")
    monkeypatch.setattr(filing.time, "sleep", lambda seconds: path.write_bytes(b"complete invoice"))
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["deferred_paths"] == [path]
    assert result["errors"] == []
    assert result["records"] == []
    assert path.read_bytes() == b"complete invoice"
    monkeypatch.setattr(filing.time, "sleep", lambda seconds: None)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert len(result["records"]) == 1


def test_file_modified_during_parsing_is_retained_and_not_misclassified(tmp_path):
    path = tmp_path / "changing.pdf"
    path.write_bytes(b"first")

    def parse(path):
        path.write_bytes(b"updated content")
        return record(path, total=None)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert result["records"] == []
    assert result["abnormal"] == 0
    assert result["deferred_paths"] == [path]
    assert len(result["errors"]) == 1
    assert path.read_bytes() == b"updated content"


def test_move_failure_retains_source_and_reports_for_database_preservation(tmp_path, monkeypatch):
    path = tmp_path / "locked.pdf"
    path.write_bytes(b"invoice")

    def denied(*args):
        raise PermissionError("in use")

    monkeypatch.setattr(filing, "_move_no_replace", denied)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["records"] == []
    assert result["deferred_paths"] == [path]
    assert "PermissionError" in result["errors"][0]
    assert path.read_bytes() == b"invoice"


def test_destination_race_cannot_overwrite_existing_bytes(tmp_path, monkeypatch):
    path = tmp_path / "bill.pdf"
    path.write_bytes(b"incoming invoice")
    original_move = filing._move_no_replace
    first = True

    def race(source, destination, expected, signature):
        nonlocal first
        if first:
            first = False
            destination.write_bytes(b"concurrently created")
        return original_move(source, destination, expected, signature)

    monkeypatch.setattr(filing, "_move_no_replace", race)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["errors"] == []
    assert (filing.invoice_dir(tmp_path) / "bill.pdf").read_bytes() == b"concurrently created"
    assert (filing.invoice_dir(tmp_path) / "bill (2).pdf").read_bytes() == b"incoming invoice"


def test_path_and_descriptor_ctime_can_differ_on_windows(tmp_path, monkeypatch):
    path = tmp_path / "bill.pdf"
    path.write_bytes(b"invoice")
    real_fstat = os.fstat

    def windows_fstat(descriptor):
        info = real_fstat(descriptor)
        fields = {name: getattr(info, name) for name in (
            "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
        fields["st_ctime_ns"] += 100
        return SimpleNamespace(**fields)

    monkeypatch.setattr(filing.os, "fstat", windows_fstat)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["errors"] == []
    assert len(result["records"]) == 1


def test_completed_scan_returns_checked_inventory_without_unnecessary_retry(tmp_path):
    (tmp_path / "bill.pdf").write_bytes(b"invoice")
    (tmp_path / "note.txt").write_bytes(b"normal other file")
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["scan_signature"] == filing.inventory_signature(tmp_path)
    assert result["rescan_needed"] is False
    assert result["abnormal"] == 1


@pytest.mark.parametrize("arrival_directory", ["root", filing.INVOICE_DIRNAME])
def test_new_file_arriving_during_parse_requires_rescan(tmp_path, arrival_directory):
    (tmp_path / "first.pdf").write_bytes(b"first")
    calls = []

    def parse(path):
        calls.append(path.name)
        folder = tmp_path if arrival_directory == "root" else filing.invoice_dir(tmp_path)
        (folder / "arrived.pdf").write_bytes(b"arrived during parse")
        return record(path)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert calls == ["first.pdf"]
    assert result["rescan_needed"] is True
    assert result["scan_signature"] == filing.inventory_signature(tmp_path)
    followup = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert {entry["path"].name for entry in followup["records"]} == {"first.pdf", "arrived.pdf"}
    assert followup["rescan_needed"] is False


def test_previously_processed_file_changed_by_later_parse_requires_rescan(tmp_path):
    folder = filing.invoice_dir(tmp_path)
    folder.mkdir()
    (folder / "first.pdf").write_bytes(b"first version")
    (tmp_path / "second.pdf").write_bytes(b"second")

    def parse(path):
        if path.name == "second.pdf":
            (folder / "first.pdf").write_bytes(b"modified after its turn")
        return record(path)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert result["rescan_needed"] is True
    followup = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert followup["rescan_needed"] is False
    assert next(entry for entry in followup["records"] if entry["path"].name == "first.pdf")["digest"] == digest(
        b"modified after its turn")


def test_previously_processed_file_removed_by_later_parse_requires_rescan(tmp_path):
    folder = filing.invoice_dir(tmp_path)
    folder.mkdir()
    (folder / "first.pdf").write_bytes(b"first")
    (tmp_path / "second.pdf").write_bytes(b"second")

    def parse(path):
        if path.name == "second.pdf":
            (folder / "first.pdf").unlink()
        return record(path)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert result["rescan_needed"] is True


def test_file_added_after_snapshot_is_not_absorbed_into_scanned_signature(tmp_path, monkeypatch):
    (tmp_path / "first.pdf").write_bytes(b"first")
    original_inventory = filing.inventory_signature

    def inventory_with_late_arrival(base_dir, protected_paths=()):
        snapshot = original_inventory(base_dir, protected_paths)
        (base_dir / "after-snapshot.pdf").write_bytes(b"late arrival")
        return snapshot

    monkeypatch.setattr(filing, "inventory_signature", inventory_with_late_arrival)
    result = filing.prepare_invoice_files(tmp_path, parse_invoice=record)
    assert result["rescan_needed"] is False
    assert result["scan_signature"] != original_inventory(tmp_path)


def test_abnormal_arrival_is_counted_without_forcing_parse_retry(tmp_path):
    (tmp_path / "bill.pdf").write_bytes(b"invoice")

    def parse(path):
        (filing.abnormal_dir(tmp_path) / "manual-review.txt").write_bytes(b"review")
        return record(path)

    result = filing.prepare_invoice_files(tmp_path, parse_invoice=parse)
    assert result["abnormal"] == 1
    assert result["rescan_needed"] is False
