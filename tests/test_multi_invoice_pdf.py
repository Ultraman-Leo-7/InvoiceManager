"""Synthetic invoice text only; no user invoice/account identifiers."""

import hashlib

import fitz
import pytest

import invoice_filing
from invoice_extract import (
    MULTI_INVOICE_STATUS,
    PARSER_VERSION,
    cache_record_is_reusable,
    invoice_numbers_from_pdf_bytes,
    parse_invoice,
)


FIRST = "10000000000000000001"
SECOND = "10000000000000000002"
ORIGINAL = "20000000000000000001"


def ticket(number=FIRST, total="12.50", extra=""):
    return (f"电子发票（普通发票）\n发票号码：{number}\n开票日期：2026年10月13日\n"
            f"价税合计（小写）：{total}\n项目名称：测试服务\n{extra}")


def pdf_bytes(*pages):
    with fitz.open() as document:
        for blocks in pages:
            page = document.new_page()
            for index, text in enumerate(blocks):
                page.insert_text((30, 40 + index * 370), text, fontname="china-s", fontsize=11)
        return document.tobytes()


@pytest.mark.parametrize("pages", [
    [[ticket(FIRST), ticket(SECOND)]],
    [[ticket(FIRST)], [ticket(SECOND)]],
])
def test_two_actual_invoices_same_or_different_pages_have_no_single_invoice_amount(tmp_path, pages):
    data = pdf_bytes(*pages)
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST, SECOND}
    path = tmp_path / "combined.pdf"
    path.write_bytes(data)
    record = parse_invoice(path)
    assert record["提取状态"] == MULTI_INVOICE_STATUS
    assert record["发票号码"] == ""
    assert record["开票日期"] == ""
    assert record["价税合计"] is None
    assert record["金额（不含税）"] is None
    assert record["税额"] is None


@pytest.mark.parametrize("second_page", [ticket(FIRST), "发票明细续页\n项目名称：其他服务\n数量：1"])
def test_one_invoice_multiple_pages_is_still_one_invoice(tmp_path, second_page):
    data = pdf_bytes([ticket(FIRST)], [second_page])
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST}
    path = tmp_path / "multi-page.pdf"
    path.write_bytes(data)
    record = parse_invoice(path)
    assert record["发票号码"] == FIRST
    assert record["价税合计"] == 12.5
    assert record["提取状态"] != MULTI_INVOICE_STATUS


@pytest.mark.parametrize("reference", [
    f"备注：对应蓝字发票号码：{ORIGINAL}",
    f"备注：原发票代码：123456789012\n发票号码：{ORIGINAL}",
    f"备注：关联的电子发票号码：{ORIGINAL}",
    f"备注：原电子发票号码：{ORIGINAL}\n原发票开票日期：2026年09月01日",
    f"备注：被冲红发票号码：{ORIGINAL}",
])
def test_red_invoice_original_blue_reference_is_not_a_second_invoice(tmp_path, reference):
    data = pdf_bytes([ticket(FIRST, "-12.50", reference)])
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST}
    path = tmp_path / "red.pdf"
    path.write_bytes(data)
    record = parse_invoice(path)
    assert record["发票号码"] == FIRST
    assert record["价税合计"] == -12.5
    assert record["提取状态"] != MULTI_INVOICE_STATUS


def test_reference_before_actual_header_does_not_replace_actual_invoice_number(tmp_path):
    data = pdf_bytes([f"关联蓝字发票号码：{ORIGINAL}\n原开票日期：2026年09月01日\n" + ticket(FIRST, "-12.50")])
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST}
    path = tmp_path / "red-with-preface.pdf"
    path.write_bytes(data)
    assert parse_invoice(path)["发票号码"] == FIRST


def test_merged_red_and_regular_invoice_excludes_referenced_original_number():
    data = pdf_bytes([ticket(FIRST, "-12.50", f"备注：蓝字发票号码：{ORIGINAL}"), ticket(SECOND)])
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST, SECOND}


def test_unexplained_extra_number_cannot_be_used_as_single_original_coverage():
    data = pdf_bytes([ticket(FIRST, extra=f"备注：发票号码：{SECOND}")])
    assert invoice_numbers_from_pdf_bytes(data) == frozenset()


@pytest.mark.parametrize("colon", ["：", "", ":"])
@pytest.mark.parametrize("separate_page", [False, True])
def test_unreadable_second_invoice_number_cannot_be_used_as_single_original_coverage(colon, separate_page):
    second = ticket("无法读取").replace("发票号码：无法读取", f"发票号码{colon}\n无法读取")
    data = pdf_bytes([ticket(FIRST)], [second]) if separate_page else pdf_bytes([ticket(FIRST), second])
    assert invoice_numbers_from_pdf_bytes(data) == frozenset()


def test_number_list_without_invoice_headers_is_not_reliable_merged_invoice_evidence():
    data = pdf_bytes([[f"发票号码：{FIRST}\n发票号码：{SECOND}"][0]])
    assert invoice_numbers_from_pdf_bytes(data) == frozenset()


def test_textless_pdf_has_no_invoice_numbers_or_amount(tmp_path):
    data = pdf_bytes([])
    assert invoice_numbers_from_pdf_bytes(data) == frozenset()
    path = tmp_path / "scan.pdf"
    path.write_bytes(data)
    assert parse_invoice(path)["价税合计"] is None


def test_damaged_pdf_propagates_error_instead_of_claiming_invoice_numbers():
    with pytest.raises(fitz.FileDataError):
        invoice_numbers_from_pdf_bytes(b"%PDF-1.7\nnot a readable PDF")


@pytest.mark.parametrize("notice", [
    f"发票下载说明\n发票号码：{FIRST}\n请通过邮件入口下载您的发票。",
    f"电子发票下载说明\n发票号码：{FIRST}\n开票日期：2026年10月13日\n请下载原票。",
    f"发票下载说明\n发票号码：{FIRST}\n价税合计（小写）：12.50\n请下载原票。",
])
def test_download_notice_cannot_be_used_as_independent_original_coverage(notice):
    assert invoice_numbers_from_pdf_bytes(pdf_bytes([notice])) == frozenset()


@pytest.mark.parametrize("total", ["0.00", "-12.50", "12.50"])
def test_single_original_coverage_accepts_finite_zero_red_and_regular_amounts(tmp_path, total):
    data = pdf_bytes([ticket(FIRST, total)])
    path = tmp_path / "single.pdf"
    path.write_bytes(data)
    assert invoice_numbers_from_pdf_bytes(data) == {FIRST}
    assert parse_invoice(path)["价税合计"] == float(total)


def test_stricter_archive_coverage_does_not_change_internal_single_invoice_parse(tmp_path):
    data = pdf_bytes([f"发票号码：{FIRST}\n价税合计（小写）：12.50\n测试单张票面兼容文本"])
    assert invoice_numbers_from_pdf_bytes(data) == frozenset()
    path = tmp_path / "legacy-text.pdf"
    path.write_bytes(data)
    parsed = parse_invoice(path)
    assert parsed["发票号码"] == FIRST
    assert parsed["价税合计"] == 12.5


def test_parser_upgrade_invalidates_old_first_invoice_cache():
    assert PARSER_VERSION > 4
    old = {"源文件SHA256": "test-digest", "解析版本": 4, "价税合计": 12.5, "发票号码": FIRST}
    assert not cache_record_is_reusable(old, "test-digest")


def test_filing_returns_cleared_multi_invoice_record_after_successful_quarantine(tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_filing, "SETTLE_SECONDS", 0)
    data = pdf_bytes([ticket(FIRST), ticket(SECOND)])
    invoice_filing.invoice_dir(tmp_path).mkdir()
    path = invoice_filing.invoice_dir(tmp_path) / "previously-imported.pdf"
    path.write_bytes(data)
    result = invoice_filing.prepare_invoice_files(tmp_path, parse_invoice=parse_invoice)
    assert result["records"] == []
    assert result["abnormal"] == 1
    assert result["errors"] == []
    assert len(result["rejected_records"]) == 1
    rejected = result["rejected_records"][0]
    assert rejected["digest"] == hashlib.sha256(data).hexdigest()
    assert rejected["path"] == invoice_filing.abnormal_dir(tmp_path) / path.name
    assert rejected["record"]["发票号码"] == ""
    assert rejected["record"]["价税合计"] is None
    assert rejected["record"]["提取状态"] == MULTI_INVOICE_STATUS
    assert rejected["path"].read_bytes() == data


def test_failed_quarantine_does_not_return_rejected_record(tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_filing, "SETTLE_SECONDS", 0)
    data = pdf_bytes([ticket(FIRST), ticket(SECOND)])
    path = tmp_path / "locked-combined.pdf"
    path.write_bytes(data)

    def cannot_move(*args, **kwargs):
        raise PermissionError("file in use")

    monkeypatch.setattr(invoice_filing, "_move_file", cannot_move)
    result = invoice_filing.prepare_invoice_files(tmp_path, parse_invoice=parse_invoice)
    assert result["rejected_records"] == []
    assert result["errors"]
    assert path.read_bytes() == data


def test_unparseable_pdf_and_other_format_do_not_produce_rejected_parse_records(tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_filing, "SETTLE_SECONDS", 0)
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\nbroken")
    (tmp_path / "broken-copy.pdf").write_bytes(b"%PDF-1.7\nbroken")
    (tmp_path / "summary.xlsx").write_bytes(b"other format")
    result = invoice_filing.prepare_invoice_files(tmp_path, parse_invoice=parse_invoice)
    assert result["rejected_records"] == []
    assert result["abnormal"] == 3
    assert result["errors"] == []
