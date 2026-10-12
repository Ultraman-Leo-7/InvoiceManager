import pytest

from jd_qq import InvoiceLinkParser, extract_order_number, parse_start_time, safe_filename


def test_extract_order_number_from_jd_subject():
    subject = "您的京东订单【3557465013915946】电子发票已开具"
    assert extract_order_number(subject) == "3557465013915946"


def test_date_only_defaults_to_midnight():
    dt, imap_date = parse_start_time("2026-08-01")
    assert (dt.hour, dt.minute) == (0, 0)
    assert imap_date == "01-Aug-2026"


def test_datetime_keeps_hour_and_minute():
    dt, imap_date = parse_start_time("2026-08-01 13:25")
    assert (dt.hour, dt.minute) == (13, 25)
    assert imap_date == "01-Aug-2026"


def test_safe_filename_removes_windows_invalid_characters():
    result = safe_filename('订单:123/发票*?.pdf')
    assert ":" not in result
    assert "/" not in result
    assert "*" not in result
    assert "?" not in result


@pytest.mark.parametrize("label", [
    "发票PDF文件下载",
    "发票文件下载",
    "下载电子发票（PDF）",
    "获取发票",
    "发票ｐｄｆ文件下载",
    "<span>发票</span>\n&nbsp;<strong>pdf</strong><span>文件 下载</span>",
])
def test_jd_invoice_link_accepts_template_wording_and_markup_variants(label):
    parser = InvoiceLinkParser()
    parser.feed(f'<p>发票号码：00000000000000000001</p><a href="https://example.invalid/file?id=1&amp;token=synthetic">{label}</a>')
    assert parser.links == [{
        "url": "https://example.invalid/file?id=1&token=synthetic",
        "context": "发票号码：00000000000000000001",
    }]


@pytest.mark.parametrize("anchor", [
    '<a href="https://example.invalid/file" title="下载电子发票">下载</a>',
    '<a href="https://example.invalid/file" aria-label="获取发票">点击这里</a>',
    '<a href="https://example.invalid/file"><img alt="发票PDF下载" src="icon.png"></a>',
    '<a href="https://example.invalid/invoice/example.pdf">下载</a>',
    '<a href="https://example.invalid/fapiao/example.PDF?token=synthetic">下载</a>',
    '<a href="//example.invalid/file">发票文件下载</a>',
])
def test_jd_invoice_link_uses_accessible_labels_and_specific_pdf_paths(anchor):
    parser = InvoiceLinkParser()
    parser.feed(anchor)
    assert len(parser.links) == 1
    assert parser.links[0]["url"].startswith("https://example.invalid/")


@pytest.mark.parametrize("anchor", [
    '<a href="https://example.invalid/file">发票XML文件下载</a>',
    '<a href="https://example.invalid/file">下载电子发票（ＯＦＤ）</a>',
    '<a href="https://example.invalid/file">发票ZIP压缩包下载</a>',
    '<a href="https://example.invalid/invoice.xml">发票文件下载</a>',
    '<a href="https://example.invalid/invoice.ofd">发票PDF文件下载</a>',
    '<a href="https://example.invalid/invoice.zip">获取发票</a>',
    '<a href="https://example.invalid/manual.pdf">下载</a>',
    '<a href="https://example.invalid/invoice/example.pdf">退订发票邮件</a>',
    '<a href="https://example.invalid/invoice/example.pdf">广告：下载发票助手</a>',
    '<a href="javascript:alert(1)">发票PDF文件下载</a>',
    '<a href="file:///tmp/invoice.pdf">发票文件下载</a>',
    '<a href="mailto:invoice@example.invalid">获取发票</a>',
    '<a href="#invoice">发票文件下载</a>',
])
def test_jd_invoice_link_rejects_other_formats_unrelated_links_and_schemes(anchor):
    parser = InvoiceLinkParser()
    parser.feed(anchor)
    assert parser.links == []
