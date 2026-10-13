"""Synthetic mail archives: import, deduplication, corruption and bounded reads."""

import io
import stat
import struct
import zipfile
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import invoice_extract
import jd_qq


PDF_A = b"%PDF-1.4\nsynthetic archive invoice A\n%%EOF"
PDF_B = b"%PDF-1.4\nsynthetic archive invoice B\n%%EOF"


def make_zip(entries, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


def make_message(data, source="alipay", filename="批量发票.zip", subtype="zip"):
    rule = jd_qq.MAIL_SOURCES[source]
    message = EmailMessage()
    message["From"] = rule["sender"]
    message["To"] = "test@example.invalid"
    message["Subject"] = rule["subject"]
    message.set_content("Synthetic invoice export for automated testing.")
    message.add_attachment(data, maintype="application", subtype=subtype, filename=filename)
    return message


@pytest.fixture
def fetch_mail(monkeypatch):
    def fetch(messages, output_dir, **kwargs):
        class Mail:
            def login(self, *args):
                return "OK", []

            def select(self, folder, readonly=False):
                assert folder == "INBOX" and readonly
                return "OK", []

            def uid(self, operation, *args):
                if operation == "search":
                    return "OK", [b" ".join(str(i).encode() for i in range(len(messages)))]
                assert operation == "fetch" and args[1] == "(BODY.PEEK[])"
                return "OK", [(b"BODY[]", messages[int(args[0])].as_bytes())]

            def logout(self):
                return "OK", []

        monkeypatch.setattr(jd_qq.imaplib, "IMAP4_SSL", lambda *args: Mail())
        return jd_qq.fetch_qq_invoices("test@example.invalid", "test-auth", output_dir, **kwargs)

    return fetch


@pytest.mark.parametrize("source", ["alipay", "pinduoduo"])
@pytest.mark.parametrize("filename,subtype", [
    ("批量发票.ZIP", "octet-stream"), ("发票附件", "x-zip-compressed"),
])
def test_zip_imports_nested_pdf_and_ignores_other_files(tmp_path, fetch_mail, source, filename, subtype):
    payload = make_zip([
        ("子目录/商品发票.pdf", PDF_A), ("其他/运费.PDF", PDF_B),
        ("附带.xml", b"<invoice/>"), ("附带.ofd", b"other format"),
        ("说明.html", b"<html>not an invoice</html>"),
    ])
    result = fetch_mail([make_message(payload, source, filename, subtype)], tmp_path)
    assert (result["emails"], result["archives"], result["attachments"]) == (1, 1, 2)
    assert (result["downloaded"], result["skipped"], result["failed"]) == (2, 0, 0)
    assert result["sources"][source]["archives"] == 1
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {PDF_A, PDF_B}
    assert all(path.suffix == ".pdf" for path in tmp_path.iterdir())
    assert any("商品发票" in path.name for path in tmp_path.iterdir())


def test_zip_same_name_different_content_and_same_content_different_name(tmp_path, fetch_mail):
    payload = make_zip([
        ("甲/发票.pdf", PDF_A), ("乙/发票.pdf", PDF_B), ("不同名.pdf", PDF_A),
    ])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["skipped"]) == (3, 2, 1)
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {PDF_A, PDF_B}


def test_zip_unsafe_names_are_flattened_and_cannot_escape_output(tmp_path, fetch_mail):
    output = tmp_path / "output"
    payload = make_zip([
        ("../../outside.pdf", PDF_A), (r"C:\outside.pdf", PDF_B),
    ])
    result = fetch_mail([make_message(payload)], output)
    assert result["downloaded"] == 2
    assert list(tmp_path.iterdir()) == [output]
    assert {path.read_bytes() for path in output.iterdir()} == {PDF_A, PDF_B}


def test_zip_and_direct_pdfs_dedupe_across_archive_and_extra_dirs(tmp_path, fetch_mail):
    output = tmp_path / "input"
    invoices = tmp_path / "发票"
    abnormal = tmp_path / "异常"
    invoices.mkdir()
    abnormal.mkdir()
    (invoices / "renamed.PDF").write_bytes(PDF_A)
    (abnormal / "old.pdf").write_bytes(PDF_B)
    payload = make_zip([("one.pdf", PDF_A), ("two.pdf", PDF_B)])
    direct = make_message(PDF_A, filename="direct.pdf", subtype="pdf")
    result = fetch_mail([make_message(payload), direct], output,
                        dedupe_dirs=[invoices, abnormal, tmp_path / "missing"])
    assert (result["downloaded"], result["skipped"], result["failed"]) == (0, 3, 0)
    assert list(output.iterdir()) == []


def test_extra_dedupe_folder_accepts_path_and_only_reads_direct_pdfs(tmp_path, fetch_mail):
    output = tmp_path / "input"
    other = tmp_path / "other"
    (other / "nested").mkdir(parents=True)
    (other / "nested" / "a.pdf").write_bytes(PDF_A)
    (other / "b.txt").write_bytes(PDF_B)
    result = fetch_mail([make_message(make_zip([("a.pdf", PDF_A), ("b.pdf", PDF_B)]))],
                        output, dedupe_dirs=other)
    assert result["downloaded"] == 2


def test_rescan_after_moving_downloaded_files_to_invoice_dir_skips_them(tmp_path, fetch_mail):
    output = tmp_path / "input"
    invoices = tmp_path / "发票"
    invoices.mkdir()
    messages = [make_message(make_zip([("票.pdf", PDF_A)]))]
    assert fetch_mail(messages, output)["downloaded"] == 1
    next(output.iterdir()).rename(invoices / "user-renamed.pdf")
    result = fetch_mail(messages, output, dedupe_dirs=[invoices])
    assert (result["downloaded"], result["skipped"], result["failed"]) == (0, 1, 0)


def test_existing_hashes_ignore_symlink_pdf(tmp_path):
    source = tmp_path / "outside.pdf"
    source.write_bytes(PDF_A)
    output = tmp_path / "input"
    output.mkdir()
    try:
        (output / "linked.pdf").symlink_to(source)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    assert jd_qq._existing_pdf_hashes(output) == set()


@pytest.mark.parametrize("extra_dir", [False, True])
def test_existing_hashes_refuse_linked_directory(tmp_path, fetch_mail, extra_dir):
    target = tmp_path / "target"
    target.mkdir()
    (target / "invoice.pdf").write_bytes(PDF_A)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(ValueError, match="链接目录"):
        if extra_dir:
            fetch_mail([], tmp_path / "input", dedupe_dirs=[linked])
        else:
            jd_qq._existing_pdf_hashes(linked)


@pytest.mark.parametrize("is_directory", [False, True])
def test_hash_scan_refuses_windows_reparse_points(tmp_path, monkeypatch, is_directory):
    output = tmp_path / "input"
    output.mkdir()
    target = output if is_directory else output / "linked.pdf"
    if not is_directory:
        target.write_bytes(PDF_A)
    real_lstat = Path.lstat

    def reparse_lstat(path):
        info = real_lstat(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    if is_directory:
        with pytest.raises(ValueError, match="链接目录"):
            jd_qq._existing_pdf_hashes(output)
    else:
        assert jd_qq._existing_pdf_hashes(output) == set()


@pytest.mark.parametrize("payload,reason", [
    (b"not a zip", "损坏"),
    (make_zip([("invoice.xml", b"<invoice/>")]), "未找到 PDF"),
    (make_zip([]), "未找到 PDF"),
])
def test_bad_or_pdf_free_zip_has_one_specific_failure(tmp_path, fetch_mail, payload, reason):
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["failed"]) == (1, 0, 1)
    assert len(result["errors"]) == 1 and reason in result["errors"][0]
    assert "压缩包：批量发票.zip" in result["errors"][0]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entries", [
    [], [("invoice.xml", b"<invoice/>"), ("invoice.ofd", b"other format")],
])
def test_pdf_free_zip_is_ignored_when_mail_also_has_direct_pdf(tmp_path, fetch_mail, entries):
    message = make_message(make_zip(entries))
    message.add_attachment(PDF_A, maintype="application", subtype="pdf", filename="direct.pdf")
    result = fetch_mail([message], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 1, 1, 0)
    assert result["errors"] == []
    assert next(tmp_path.iterdir()).read_bytes() == PDF_A


def test_pdf_free_zip_is_ignored_when_another_archive_has_pdf(tmp_path, fetch_mail):
    message = make_message(make_zip([("invoice.xml", b"<invoice/>")]))
    message.add_attachment(make_zip([("invoice.pdf", PDF_A)]), maintype="application", subtype="zip",
                           filename="有发票.zip")
    result = fetch_mail([message], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (2, 1, 1, 0)
    assert result["errors"] == []


def test_multiple_pdf_free_archives_produce_one_message_failure(tmp_path, fetch_mail):
    message = make_message(make_zip([("invoice.xml", b"<invoice/>")]))
    message.add_attachment(make_zip([("invoice.ofd", b"ofd")]), maintype="application", subtype="zip",
                           filename="另一包.zip")
    result = fetch_mail([message], tmp_path)
    assert (result["archives"], result["attachments"], result["failed"]) == (2, 0, 1)
    assert len(result["errors"]) == 1 and "未找到 PDF" in result["errors"][0]
    assert "另一包.zip" in result["errors"][0]


def test_pdf_free_archive_does_not_add_failure_when_another_archive_is_corrupt(tmp_path, fetch_mail):
    message = make_message(make_zip([("invoice.xml", b"<invoice/>")]))
    message.add_attachment(b"corrupt", maintype="application", subtype="zip", filename="损坏.zip")
    result = fetch_mail([message], tmp_path)
    assert result["failed"] == 1
    assert len(result["errors"]) == 1 and "损坏.zip" in result["errors"][0]
    assert "未找到 PDF" not in result["errors"][0]


def test_invalid_pdf_member_fails_but_other_pdfs_still_import(tmp_path, fetch_mail):
    payload = make_zip([("坏.pdf", b"<html>expired</html>"), ("好.pdf", PDF_A)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["failed"]) == (2, 1, 1)
    assert "文件：坏.pdf" in result["errors"][0] and "不是 PDF" in result["errors"][0]
    assert next(tmp_path.iterdir()).read_bytes() == PDF_A


def mutate_first_entry(data, field, value):
    """Change both headers for one synthetic entry, retaining the second entry."""
    data = bytearray(data)
    end_record = data.rfind(b"PK\x05\x06")
    central = struct.unpack_from("<I", data, end_record + 16)[0]
    if field == "flag":
        struct.pack_into("<H", data, 6, value)
        struct.pack_into("<H", data, central + 8, value)
    elif field == "compression":
        struct.pack_into("<H", data, 8, value)
        struct.pack_into("<H", data, central + 10, value)
    elif field == "crc":
        struct.pack_into("<I", data, 14, value)
        struct.pack_into("<I", data, central + 16, value)
    return bytes(data)


@pytest.mark.parametrize("field,value,reason", [
    ("flag", 1, "已加密"), ("compression", 99, "不支持的压缩方式"), ("crc", 0, "解压失败"),
])
def test_bad_member_does_not_prevent_next_member(tmp_path, fetch_mail, field, value, reason):
    payload = make_zip([("bad.pdf", PDF_B), ("good.pdf", PDF_A)], zipfile.ZIP_STORED)
    payload = mutate_first_entry(payload, field, value)
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["failed"]) == (2, 1, 1)
    assert reason in result["errors"][0] and "文件：bad.pdf" in result["errors"][0]
    assert next(tmp_path.iterdir()).read_bytes() == PDF_A


def test_zip_symlink_member_is_rejected(tmp_path, fetch_mail):
    link = zipfile.ZipInfo("link.pdf")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    payload = make_zip([(link, b"../../somewhere.pdf"), ("good.pdf", PDF_A)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert result["downloaded"] == result["failed"] == 1
    assert "符号链接" in result["errors"][0]


def test_corrupt_deflate_stream_does_not_abort_remaining_mail(tmp_path, fetch_mail):
    payload = bytearray(make_zip([("bad.pdf", PDF_B), ("good.pdf", PDF_A)]))
    filename_size, extra_size = struct.unpack_from("<HH", payload, 26)
    offset = 30 + filename_size + extra_size
    payload[offset:offset + 2] = b"\xff\xff"
    result = fetch_mail([make_message(bytes(payload))], tmp_path)
    assert result["downloaded"] == result["failed"] == 1
    assert "解压失败" in result["errors"][0]
    assert next(tmp_path.iterdir()).read_bytes() == PDF_A


@pytest.mark.parametrize("limit,value,reason,downloads,candidates", [
    ("MAX_ZIP_BYTES", 10, "压缩包超过大小限制", 0, 0),
    ("MAX_ZIP_ENTRIES", 1, "条目数超过限制", 0, 0),
    ("MAX_ZIP_PDF_BYTES", len(PDF_A), "单文件限制", 1, 2),
    ("MAX_ZIP_TOTAL_BYTES", len(PDF_A), "累计大小超过限制", 1, 2),
])
def test_zip_budgets_are_enforced(tmp_path, fetch_mail, monkeypatch, limit, value, reason, downloads, candidates):
    monkeypatch.setattr(jd_qq, limit, value)
    payload = make_zip([("good.pdf", PDF_A), ("large.pdf", PDF_B + b"more")])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["downloaded"], result["attachments"], result["failed"]) == (downloads, candidates, 1)
    assert reason in result["errors"][0]


def test_gbk_archive_filename_decodes_to_chinese(tmp_path, fetch_mail):
    # Legacy writers put GBK bytes in the name without the UTF-8 header flag.
    name = "测试发票.pdf".encode("gbk")
    placeholder = b"abcdefgh.pdf"
    assert len(name) == len(placeholder)
    payload = make_zip([(placeholder.decode(), PDF_A)], zipfile.ZIP_STORED)
    payload = payload.replace(placeholder, name)
    result = fetch_mail([make_message(payload)], tmp_path)
    assert result["downloaded"] == 1
    assert "测试发票" in next(tmp_path.iterdir()).name


def test_zip_does_not_change_sender_or_subject_filter(tmp_path, fetch_mail):
    payload = make_zip([("invoice.pdf", PDF_A)])
    wrong_sender = make_message(payload)
    wrong_sender.replace_header("From", "unrelated@example.invalid")
    wrong_subject = make_message(payload)
    wrong_subject.replace_header("Subject", "其他主题")
    result = fetch_mail([wrong_sender, wrong_subject], tmp_path)
    assert result["emails"] == result["archives"] == result["downloaded"] == 0
    assert list(tmp_path.iterdir()) == []


def test_nested_zip_imports_all_twenty_four_pdfs_and_counts_only_outer_archive(tmp_path, fetch_mail):
    pdfs = [PDF_A + str(index).encode() for index in range(24)]
    inner = make_zip([(f"文件夹/票{index}.PDF", data) for index, data in enumerate(pdfs)])
    payload = make_zip([("子目录/独立票.zip", inner), ("明细.xlsx", b"ignored spreadsheet")])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["emails"], result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 1, 24, 24, 0)
    assert {path.read_bytes() for path in tmp_path.iterdir()} == set(pdfs)
    again = fetch_mail([make_message(payload)], tmp_path)
    assert (again["archives"], again["attachments"], again["downloaded"], again["skipped"]) == (1, 24, 0, 24)


def test_inner_zip_error_includes_hierarchy_and_keeps_outer_pdf(tmp_path, fetch_mail):
    middle = make_zip([("broken.zip", b"not a zip")])
    payload = make_zip([("middle.zip", middle), ("good.pdf", PDF_A)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 1, 1, 1)
    assert "middle.zip/broken.zip" in result["errors"][0]
    assert "损坏" in result["errors"][0]


def test_inner_pdf_error_includes_both_zip_and_member_name(tmp_path, fetch_mail):
    inner = mutate_first_entry(make_zip([("bad.pdf", PDF_A)], zipfile.ZIP_STORED), "flag", 1)
    payload = make_zip([("inner.zip", inner), ("good.pdf", PDF_B)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 2, 1, 1)
    assert "inner.zip/bad.pdf" in result["errors"][0] and "已加密" in result["errors"][0]


def test_encrypted_inner_zip_is_not_counted_as_pdf_candidate(tmp_path, fetch_mail):
    inner = make_zip([("inside.pdf", PDF_A)])
    payload = make_zip([("locked.zip", inner), ("good.pdf", PDF_B)], zipfile.ZIP_STORED)
    payload = mutate_first_entry(payload, "flag", 1)
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 1, 1, 1)
    assert "locked.zip" in result["errors"][0] and "已加密" in result["errors"][0]


@pytest.mark.parametrize("depth,failed", [(4, 0), (5, 1)])
def test_zip_depth_limit_includes_outer_attachment(tmp_path, fetch_mail, depth, failed):
    payload = make_zip([("deep.pdf", PDF_A)])
    for index in range(depth - 1):
        payload = make_zip([(f"level{index}.zip", payload)])
    message = make_message(payload)
    message.add_attachment(PDF_B, maintype="application", subtype="pdf", filename="direct.pdf")
    result = fetch_mail([message], tmp_path)
    assert result["failed"] == failed
    assert result["downloaded"] == 2 - failed
    assert result["archives"] == 1
    if failed:
        assert "嵌套层数超过限制" in result["errors"][0]


def test_zip_entry_budget_is_shared_across_sibling_archives(tmp_path, fetch_mail, monkeypatch):
    monkeypatch.setattr(jd_qq, "MAX_ZIP_ENTRIES", 4)
    first = make_zip([("first.pdf", PDF_A)])
    second = make_zip([("second.pdf", PDF_B)])
    payload = make_zip([("first.zip", first), ("second.zip", second), ("last.pdf", PDF_B)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 2, 2, 1)
    assert "second.zip" in result["errors"][0] and "累计条目数" in result["errors"][0]


def test_zip_total_budget_includes_nested_archive_payloads_and_all_pdfs(tmp_path, fetch_mail, monkeypatch):
    first = make_zip([("first.pdf", PDF_A)])
    second = make_zip([("second.pdf", PDF_B)])
    monkeypatch.setattr(jd_qq, "MAX_ZIP_TOTAL_BYTES", len(first) + len(PDF_A) + len(second))
    payload = make_zip([("first.zip", first), ("second.zip", second)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["failed"]) == (2, 1, 1)
    assert "second.zip/second.pdf" in result["errors"][0] and "累计大小" in result["errors"][0]


def test_nested_pdf_free_archives_with_direct_pdf_are_silent(tmp_path, fetch_mail):
    payload = make_zip([("inner.zip", make_zip([("invoice.xml", b"<invoice/>")]))])
    message = make_message(payload)
    message.add_attachment(PDF_A, maintype="application", subtype="pdf", filename="direct.pdf")
    result = fetch_mail([message], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["failed"]) == (1, 1, 1, 0)


@pytest.fixture
def synthetic_invoice_numbers(monkeypatch):
    payloads = {
        "first": PDF_A, "second": PDF_B, "third": PDF_A + b"third",
        "merged": PDF_A + b"merged", "other_merged": PDF_A + b"other-merged",
        "unknown": PDF_A + b"unknown", "damaged": PDF_A + b"damaged",
    }
    identities = {
        payloads["first"]: frozenset({"synthetic-A"}), payloads["second"]: frozenset({"synthetic-B"}),
        payloads["third"]: frozenset({"synthetic-C"}), payloads["merged"]: frozenset({"synthetic-A", "synthetic-B"}),
        payloads["other_merged"]: frozenset({"synthetic-A", "synthetic-B"}), payloads["unknown"]: frozenset(),
    }

    def numbers(data):
        if data == payloads["damaged"]:
            raise ValueError("Synthetic unreadable PDF")
        return identities.get(data, frozenset())

    monkeypatch.setattr(invoice_extract, "invoice_numbers_from_pdf_bytes", numbers)
    return payloads


def test_fully_covered_merged_copy_is_ignored_even_when_it_precedes_inner_zip(tmp_path, fetch_mail, synthetic_invoice_numbers):
    data = synthetic_invoice_numbers
    inner = make_zip([("first.pdf", data["first"]), ("second.pdf", data["second"])])
    payload = make_zip([("combined.pdf", data["merged"]), ("individual.zip", inner)])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["merged_ignored"], result["failed"]) == (1, 2, 2, 1, 0)
    assert result["sources"]["alipay"]["merged_ignored"] == 1
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {data["first"], data["second"]}
    again = fetch_mail([make_message(payload)], tmp_path)
    assert (again["attachments"], again["skipped"], again["merged_ignored"]) == (2, 2, 1)


@pytest.mark.parametrize("extra", ["first", "other_merged", "unknown", "damaged"])
def test_incomplete_or_unreliable_coverage_keeps_merged_pdf(tmp_path, fetch_mail, synthetic_invoice_numbers, extra):
    data = synthetic_invoice_numbers
    payload = make_zip([("combined.pdf", data["merged"]), ("other.pdf", data[extra])])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["merged_ignored"]) == (2, 2, 0)
    assert data["merged"] in {path.read_bytes() for path in tmp_path.iterdir()}


def test_archive_error_keeps_merged_pdf_despite_apparent_full_coverage(tmp_path, fetch_mail, synthetic_invoice_numbers):
    data = synthetic_invoice_numbers
    payload = make_zip([
        ("first.pdf", data["first"]), ("second.pdf", data["second"]),
        ("combined.pdf", data["merged"]), ("broken.zip", b"damaged"),
    ])
    result = fetch_mail([make_message(payload)], tmp_path)
    assert (result["attachments"], result["downloaded"], result["merged_ignored"], result["failed"]) == (3, 3, 0, 1)


@pytest.mark.parametrize("separate_messages", [False, True])
def test_merged_coverage_never_crosses_outer_archives(tmp_path, fetch_mail, synthetic_invoice_numbers, separate_messages):
    data = synthetic_invoice_numbers
    singles = make_zip([("first.pdf", data["first"]), ("second.pdf", data["second"])])
    merged = make_zip([("combined.pdf", data["merged"])])
    messages = [make_message(singles)]
    if separate_messages:
        messages.append(make_message(merged))
    else:
        messages[0].add_attachment(merged, maintype="application", subtype="zip", filename="second.zip")
    result = fetch_mail(messages, tmp_path)
    assert (result["archives"], result["attachments"], result["downloaded"], result["merged_ignored"]) == (2, 3, 3, 0)


def test_real_notice_pdf_cannot_cover_an_original_invoice_in_merged_copy(tmp_path, fetch_mail):
    def render_pdf(pages):
        with fitz.open() as document:
            for text in pages:
                page = document.new_page()
                page.insert_text((40, 40), text, fontname="china-s", fontsize=10)
            return document.tobytes()

    first_number = "11111111111111111111"
    second_number = "22222222222222222222"
    first = f"电子发票（普通发票）\n发票号码：{first_number}\n开票日期：2026年10月13日\n价税合计（小写）￥12.50"
    second = f"电子发票（普通发票）\n发票号码：{second_number}\n开票日期：2026年10月13日\n价税合计（小写）￥20.00"
    notice = render_pdf([
        f"发票下载说明\n请联系开票单位处理此发票\n发票号码：{first_number}\n本文件仅为通知，没有发票金额。"
    ])
    original_second = render_pdf([second])
    merged = render_pdf([first, second])
    payload = make_zip([
        ("notice.pdf", notice), ("second.pdf", original_second), ("combined.pdf", merged),
    ])

    result = fetch_mail([make_message(payload)], tmp_path)

    assert (result["attachments"], result["downloaded"], result["merged_ignored"], result["failed"]) == (3, 3, 0, 0)
    assert result["sources"]["alipay"]["merged_ignored"] == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {notice, original_second, merged}
