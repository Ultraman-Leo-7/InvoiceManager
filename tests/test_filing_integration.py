"""Real PDF/filesystem checks for migration into 发票 and 异常."""

from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import app
from purchase_tracker import add_purchase, set_manual_match


@pytest.fixture
def filing_case(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / app.DB_FILENAME)
    monkeypatch.setattr(app, "EXPORT_PATH", tmp_path / app.EXPORT_FILENAME)
    monkeypatch.setattr(app, "LOCAL_BACKUP_DIR", tmp_path / app.LOCAL_BACKUP_DIRNAME)
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *args, **kwargs: pytest.fail("Scanning must not ask to delete files"))
    app.init_db()
    return SimpleNamespace(root=tmp_path, invoices=tmp_path / "发票", abnormal=tmp_path / "异常")


def make_invoice(folder, name="invoice.pdf", number="12345678901234567890", amount="12.50"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    lines = ["电子发票", "开票日期：2026年10月13日", "测试商品（仅限自动化测试，不代表真实发票）"]
    if number is not None:
        lines.append(f"发票号码：{number}")
    if amount is not None:
        lines.append(f"价税合计（小写）￥{amount}")
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "\n".join(lines), fontname="china-s", fontsize=12)
        document.save(path)
    return path


@pytest.mark.parametrize("parser_version", [app.PARSER_VERSION, app.PARSER_VERSION - 1])
def test_flat_layout_upgrade_preserves_manual_data_purchases_and_hash_identity(filing_case, parser_version):
    case = filing_case
    original = make_invoice(case.root, "historical.pdf")
    content = original.read_bytes()
    digest = app.sha256_file(original)
    with app.connect_db() as conn:
        conn.execute(
            "INSERT INTO invoices(digest, filename, invoice_no, total, parser_version, confirmed, note, active, last_seen) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (digest, original.name, "12345678901234567890", 12.5, parser_version, 1, "历史人工备注", 1, "2026-10-12T12:00:00"),
        )
        purchase_id = add_purchase(conn, "历史购买记录", "12.50", False, 0)
        set_manual_match(conn, purchase_id, "商品", digest)
    app.set_setting("synthetic_preserved_setting", "test-value")

    stats = app.sync_folder()

    assert not original.exists()
    assert (case.invoices / original.name).read_bytes() == content
    assert stats["unique"] == 1
    assert stats["removed"] == 0
    assert stats["abnormal_count"] == 0
    rows = app.all_active_rows()
    assert len(rows) == 1
    assert (rows[0]["digest"], rows[0]["filename"], rows[0]["confirmed"], rows[0]["note"]) == (digest, original.name, 1, "历史人工备注")
    assert app.get_setting("synthetic_preserved_setting") == "test-value"
    with app.connect_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 1
        override = conn.execute("SELECT purchase_id, invoice_digest FROM purchase_invoice_overrides").fetchone()
        assert tuple(override) == (purchase_id, digest)
    component = app.current_match_data()["purchase_results"][0]["components"][0]
    assert component["manual"] is True
    assert component["matched"] is True
    assert component["invoice"]["filename"] == original.name
    assert app.sync_folder()["duplicates"] == []


@pytest.mark.parametrize("amount", ["12.50", "0.00", "-12.50"])
def test_recognized_invoice_including_zero_and_red_invoice_is_filed(filing_case, amount):
    case = filing_case
    original = make_invoice(case.root, "invoice.PDF", amount=amount)
    content = original.read_bytes()

    stats = app.sync_folder()

    assert not original.exists()
    assert (case.invoices / original.name).read_bytes() == content
    assert stats["abnormal_count"] == 0
    assert stats["unique"] == 1
    assert app.all_active_rows()[0]["total"] == float(amount)


@pytest.mark.parametrize("kind", ["missing-amount", "missing-number", "corrupt-pdf", "xml", "text"])
def test_unrecognized_input_moves_to_abnormal_without_becoming_an_invoice(filing_case, kind):
    case = filing_case
    if kind == "missing-amount":
        original = make_invoice(case.root, amount=None)
    elif kind == "missing-number":
        original = make_invoice(case.root, number=None)
    else:
        name, content = {
            "corrupt-pdf": ("damaged.pdf", b"%PDF-1.7\nnot a complete PDF"),
            "xml": ("invoice.xml", b"<?xml version='1.0'?><invoice/>"),
            "text": ("unrelated.txt", b"Synthetic unrelated document"),
        }[kind]
        original = case.root / name
        original.write_bytes(content)
    content = original.read_bytes()

    stats = app.sync_folder()

    assert not original.exists()
    assert (case.abnormal / original.name).read_bytes() == content
    assert stats["abnormal_count"] == 1
    assert app.abnormal_count() == 1
    assert stats["failed"] == 0
    assert stats["unique"] == 0
    assert not app.all_active_rows()


def test_bad_files_already_inside_invoice_directory_are_also_filed_as_abnormal(filing_case):
    case = filing_case
    original = make_invoice(case.invoices, "unpriced.pdf", amount=None)
    other = case.invoices / "attachment.xml"
    other.write_bytes(b"<invoice/>")
    contents = {path.name: path.read_bytes() for path in (original, other)}

    stats = app.sync_folder()

    assert stats["abnormal_count"] == 2
    assert not list(case.invoices.iterdir())
    assert {path.name: path.read_bytes() for path in case.abnormal.iterdir()} == contents
    assert not app.all_active_rows()


def test_abnormal_is_not_retried_until_user_moves_file_back_to_root(filing_case, monkeypatch):
    case = filing_case
    original = make_invoice(case.root, amount=None)
    app.sync_folder()
    quarantined = case.abnormal / original.name
    quarantined.unlink()
    make_invoice(case.abnormal, original.name)
    real_parse = app.parse_invoice
    parsed = []

    def record_parse(path):
        parsed.append(Path(path))
        return real_parse(path)

    monkeypatch.setattr(app, "parse_invoice", record_parse)
    stats = app.sync_folder()
    assert stats["abnormal_count"] == 1
    assert parsed == []
    assert not app.all_active_rows()
    quarantined.rename(original)

    stats = app.sync_folder()

    assert stats["abnormal_count"] == 0
    assert stats["unique"] == 1
    assert (case.invoices / original.name).exists()
    assert not original.exists()
    assert parsed


def test_incoming_name_collision_preserves_both_different_invoices(filing_case):
    case = filing_case
    previous = make_invoice(case.invoices, "same.pdf", number="12345678901234567890")
    previous_content = previous.read_bytes()
    app.sync_folder()
    incoming = make_invoice(case.root, "same.pdf", number="98765432109876543210", amount="33.00")
    incoming_content = incoming.read_bytes()

    stats = app.sync_folder()

    assert not incoming.exists()
    assert previous.read_bytes() == previous_content
    assert {path.read_bytes() for path in case.invoices.iterdir()} == {previous_content, incoming_content}
    assert stats["unique"] == 2
    assert stats["duplicates"] == []
    assert len({row["filename"] for row in app.all_active_rows()}) == 2


def test_incoming_hash_copy_uses_existing_human_confirmation_policy(filing_case):
    case = filing_case
    original = make_invoice(case.invoices)
    app.sync_folder()
    content = original.read_bytes()
    incoming = case.root / "new-copy.pdf"
    incoming.write_bytes(content)

    stats = app.sync_folder()

    assert stats["unique"] == 1
    assert stats["duplicates"] == [{"digest": app.sha256_file(original), "keep": original.name, "remove": [incoming.name]}]
    assert (case.invoices / incoming.name).read_bytes() == content
    result = app.handle_duplicate_files(stats["duplicates"], delete=False)
    assert result["errors"] == []
    assert (case.invoices / "哈希副本-new-copy.pdf").read_bytes() == content
    assert app.sync_folder()["duplicates"] == []


def test_runtime_database_export_and_backup_assets_are_left_in_root(filing_case):
    case = filing_case
    assets = {
        "InvoiceManager-Windows-x64.exe": b"Synthetic executable placeholder",
        app.EXPORT_FILENAME: b"Synthetic exported workbook placeholder",
        "settings.json": b"{}",
    }
    for name, content in assets.items():
        (case.root / name).write_bytes(content)
    app.LOCAL_BACKUP_DIR.mkdir()
    backup = app.LOCAL_BACKUP_DIR / "historical-backup.zip"
    backup.write_bytes(b"Synthetic backup placeholder")
    app.set_setting("synthetic_keep_db", "preserved")

    stats = app.sync_folder()

    assert stats["abnormal_count"] == 0
    for name, content in assets.items():
        assert (case.root / name).read_bytes() == content
    assert backup.read_bytes() == b"Synthetic backup placeholder"
    assert app.DB_PATH.exists()
    assert app.get_setting("synthetic_keep_db") == "preserved"


def test_folder_monitor_detects_new_input_and_abnormal_cleanup(filing_case):
    case = filing_case
    app.sync_folder()
    initial = app.folder_signature()
    incoming = case.root / "needs-review.xml"
    incoming.write_bytes(b"<synthetic/>")
    assert app.folder_signature() != initial
    app.sync_folder()
    filed = app.folder_signature()
    (case.abnormal / incoming.name).unlink()
    assert app.folder_signature() != filed
    assert app.abnormal_count() == 0
    stats = app.sync_folder()
    assert stats["abnormal_count"] == 0


def test_blocked_managed_directory_preserves_active_records_and_original_bytes(filing_case):
    case = filing_case
    original = make_invoice(case.invoices)
    app.sync_folder()
    digest = app.sha256_file(original)
    app.set_note(digest, "保留已有备注")
    parked = case.root / "synthetic-parked-invoices"
    case.invoices.rename(parked)
    case.invoices.write_bytes(b"A file occupying the managed folder name")

    with pytest.raises(RuntimeError, match="文件整理"):
        app.sync_folder()

    rows = app.all_active_rows()
    assert len(rows) == 1
    assert rows[0]["digest"] == digest
    assert rows[0]["note"] == "保留已有备注"
    assert (parked / original.name).exists()
    assert case.invoices.read_bytes() == b"A file occupying the managed folder name"


def test_temporarily_unreadable_legacy_invoice_keeps_active_record_and_retries(filing_case, monkeypatch):
    case = filing_case
    original = make_invoice(case.invoices)
    app.sync_folder()
    digest = app.sha256_file(original)
    app.set_note(digest, "文件暂时占用时仍保留")
    legacy = case.root / original.name
    original.rename(legacy)
    with app.connect_db() as conn:
        conn.execute("UPDATE invoices SET parser_version=0 WHERE digest=?", (digest,))
    real_parse = app.parse_invoice

    def locked_file(path):
        raise PermissionError("Synthetic locked file")

    monkeypatch.setattr(app, "parse_invoice", locked_file)
    stats = app.sync_folder()

    assert stats["failed"] == 1
    assert legacy in stats["deferred_paths"]
    assert legacy.exists()
    assert not (case.abnormal / legacy.name).exists()
    rows = app.all_active_rows()
    assert len(rows) == 1
    assert rows[0]["digest"] == digest
    assert rows[0]["note"] == "文件暂时占用时仍保留"
    assert app.invoice_file_path(legacy.name) == legacy

    monkeypatch.setattr(app, "parse_invoice", real_parse)
    repaired = app.sync_folder()
    assert repaired["failed"] == 0
    assert repaired["deferred_paths"] == []
    assert not legacy.exists()
    assert (case.invoices / legacy.name).exists()
    assert app.all_active_rows()[0]["note"] == "文件暂时占用时仍保留"


def test_abnormal_count_display_refreshes_after_user_cleans_directory(filing_case):
    case = filing_case
    case.abnormal.mkdir()
    abnormal = case.abnormal / "needs-review.xml"
    abnormal.write_bytes(b"<synthetic/>")
    messages = []
    view = SimpleNamespace(
        refresh_invoice_table=lambda: None,
        refresh_purchase_tab=lambda: None,
        abnormal_count_var=SimpleNamespace(set=messages.append),
    )

    app.InvoiceApp.refresh_all(view)
    assert "异常" in messages[-1]
    assert "1" in messages[-1]
    abnormal.unlink()
    app.InvoiceApp.refresh_all(view)
    assert "异常" in messages[-1]
    assert "0" in messages[-1]


def test_deleting_selected_legacy_invoice_checks_hash_when_name_is_reused(filing_case, monkeypatch):
    case = filing_case
    original = make_invoice(case.invoices, "same.pdf")
    app.sync_folder()
    selected_digest = app.sha256_file(original)
    legacy = case.root / original.name
    original.rename(legacy)
    replacement = make_invoice(case.invoices, original.name, number="98765432109876543210", amount="33.00")
    replacement_content = replacement.read_bytes()
    replacement_digest = app.sha256_file(replacement)
    assert app.invoice_file_path(original.name, selected_digest) == legacy
    assert app.invoice_file_path(original.name, replacement_digest) == replacement
    scanned = []
    view = SimpleNamespace(
        selected_digests=lambda: [selected_digest],
        run_background_sync=lambda **kwargs: scanned.append(app.sync_folder()),
    )
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(app.messagebox, "showwarning", lambda *args, **kwargs: pytest.fail("Deletion unexpectedly failed"))

    app.InvoiceApp.delete_selected_invoices(view)

    assert not legacy.exists()
    assert replacement.read_bytes() == replacement_content
    assert scanned[0]["unique"] == 1
    rows = app.all_active_rows()
    assert len(rows) == 1
    assert rows[0]["digest"] == replacement_digest


def test_first_migration_keeps_one_safety_backup_across_locked_file_retries(filing_case, monkeypatch):
    case = filing_case
    original = make_invoice(case.root)
    real_backup = app.create_local_safety_backup
    real_parse = app.parse_invoice
    snapshots = []

    def record_backup(*args, **kwargs):
        snapshot = real_backup(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    def locked_file(path):
        raise PermissionError("Synthetic persistent file lock")

    monkeypatch.setattr(app, "create_local_safety_backup", record_backup)
    monkeypatch.setattr(app, "parse_invoice", locked_file)
    for _ in range(3):
        stats = app.sync_folder()
        assert stats["failed"] == 1
        assert original in stats["deferred_paths"]
        assert original.exists()
        assert app.get_setting("filing_layout_version") != "1"
        assert app.get_setting("filing_backup_done") == "1"
    assert len(snapshots) == 1
    assert snapshots[0].exists()

    monkeypatch.setattr(app, "parse_invoice", real_parse)
    stats = app.sync_folder()
    assert stats["failed"] == 0
    assert not original.exists()
    assert (case.invoices / original.name).exists()
    assert app.get_setting("filing_layout_version") == "1"
    assert len(snapshots) == 1


def test_safety_backup_failure_blocks_moving_files_and_does_not_mark_backup_done(filing_case, monkeypatch):
    case = filing_case
    original = make_invoice(case.root)
    content = original.read_bytes()
    real_backup = app.create_local_safety_backup

    def failing_backup(*args, **kwargs):
        raise OSError("Synthetic backup write failure")

    monkeypatch.setattr(app, "create_local_safety_backup", failing_backup)
    with pytest.raises(OSError, match="Synthetic backup write failure"):
        app.sync_folder()

    assert original.read_bytes() == content
    assert not case.invoices.exists()
    assert not case.abnormal.exists()
    assert app.get_setting("filing_backup_done") != "1"
    assert app.get_setting("filing_layout_version") != "1"
    monkeypatch.setattr(app, "create_local_safety_backup", real_backup)
    assert app.sync_folder()["unique"] == 1
    assert app.get_setting("filing_backup_done") == "1"
    assert (case.invoices / original.name).read_bytes() == content


def test_input_arriving_during_scan_triggers_followup_instead_of_being_missed(filing_case, monkeypatch):
    case = filing_case
    make_invoice(case.root, "first.pdf")
    real_parse = app.parse_invoice

    def parse_with_arrival(path):
        if path.name == "first.pdf":
            make_invoice(case.root, "late.pdf", number="98765432109876543210", amount="33.00")
        return real_parse(path)

    monkeypatch.setattr(app, "parse_invoice", parse_with_arrival)
    first_pass = app.sync_folder()

    assert first_pass["rescan_needed"] is True
    assert app.completed_scan_signature(first_pass) is None
    assert (case.root / "late.pdf").exists()
    second_pass = app.sync_folder()
    assert second_pass["rescan_needed"] is False
    assert second_pass["unique"] == 2
    assert not (case.root / "late.pdf").exists()
    assert (case.invoices / "late.pdf").exists()
    assert app.completed_scan_signature(second_pass) == second_pass["scan_signature"]
