"""Synthetic-only tests for supported invoice email templates and file safety."""

import hashlib
import io
import os
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

import jd_qq


PDF_A = b"%PDF-1.4\nsynthetic invoice A\n%%EOF"
PDF_B = b"%PDF-1.4\nsynthetic invoice B\n%%EOF"
JD_URL = "https://example.invalid/invoice.pdf"


def invoice_message(source, data=PDF_A, filename="测试发票.pdf", content_type="application/pdf"):
    rule = jd_qq.MAIL_SOURCES[source]
    msg = EmailMessage()
    msg["From"] = f"测试发票服务 <{rule['sender']}>"
    msg["To"] = "test@example.invalid"
    msg["Subject"] = rule["subject"]
    msg["Date"] = format_datetime(datetime(2026, 10, 12, 10, tzinfo=timezone.utc))
    msg.set_content("此邮件仅用于自动化测试，不含真实个人信息。")
    if source == "jd":
        msg.add_alternative(
            f'<p>发票号码：00000000000000000001</p><a href="{JD_URL}">发票PDF文件下载</a>',
            subtype="html",
        )
    elif data is not None:
        maintype, subtype = content_type.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


class FakeIMAP:
    def __init__(self, messages):
        self.messages = {str(i).encode(): msg.as_bytes() for i, msg in enumerate(messages, 1)}
        self.searches = []
        self.fetches = []
        self.logged_out = False
        self.selected = None
        self.fail_fetch = None
        self.empty_fetch = False

    def login(self, address, auth_code):
        assert (address, auth_code) == ("test@example.invalid", "test-auth-code")

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            self.searches.append(args)
            # Return broad matches, including sender-substring false positives.
            return "OK", [b" ".join(self.messages)]
        if command == "fetch":
            self.fetches.append(args)
            if self.fail_fetch is not None:
                return self.fail_fetch, []
            if self.empty_fetch:
                return "OK", [b"empty"]
            return "OK", [(b"BODY[]", self.messages[args[0]])]
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True


@pytest.fixture
def mailbox(monkeypatch):
    def configure(messages):
        mail = FakeIMAP(messages)

        def connect(host, port):
            assert (host, port) == ("imap.qq.com", 993)
            return mail

        monkeypatch.setattr(jd_qq.imaplib, "IMAP4_SSL", connect)
        monkeypatch.setattr(jd_qq, "_download_pdf", lambda url: (PDF_A, ""))
        return mail

    return configure


def fetch(tmp_path, **kwargs):
    return jd_qq.fetch_qq_invoices("test@example.invalid", "test-auth-code", tmp_path, **kwargs)


def jd_link_message(*links):
    msg = invoice_message("jd")
    msg.get_payload()[1].set_content(
        "".join(f'<a href="{url}">{label}</a>' for url, label in links),
        subtype="html",
    )
    return msg


@pytest.fixture
def response_mailbox(mailbox, monkeypatch):
    """Exercise the real downloader using synthetic in-memory HTTP responses."""
    real_download = jd_qq._download_pdf

    def configure(messages, responses):
        mailbox(messages)
        monkeypatch.setattr(jd_qq, "_download_pdf", real_download)
        monkeypatch.setattr(jd_qq.time, "sleep", lambda seconds: None)
        requested_urls = []

        class Response(io.BytesIO):
            def __init__(self, url, body, content_type):
                super().__init__(body)
                self.url = url
                self.headers = {"Content-Type": content_type}

            def geturl(self):
                return self.url

        def urlopen(request, timeout):
            url = request.full_url
            requested_urls.append(url)
            response = responses[url]
            if isinstance(response, Exception):
                raise response
            return Response(url, *response)

        monkeypatch.setattr(jd_qq.urllib.request, "urlopen", urlopen)
        return requested_urls

    return configure


def test_all_three_sources_and_readonly_mailbox(tmp_path, mailbox, monkeypatch):
    mail = mailbox([
        invoice_message("jd"),
        invoice_message("pinduoduo", PDF_B),
        invoice_message("alipay", PDF_A + b"third"),
    ])
    progress = []
    result = fetch(tmp_path, progress=progress.append)
    assert (result["emails"], result["links"], result["attachments"]) == (3, 1, 2)
    assert (result["downloaded"], result["skipped"], result["failed"]) == (3, 0, 0)
    assert {p.read_bytes() for p in tmp_path.glob("*.pdf")} == {PDF_A, PDF_B, PDF_A + b"third"}
    assert all(stats["downloaded"] == 1 for stats in result["sources"].values())
    assert mail.selected == ("INBOX", True)
    assert all(args[1] == "(BODY.PEEK[])" for args in mail.fetches)
    assert len(mail.searches) == 3
    assert mail.logged_out
    assert progress


def test_pdd_same_attachment_name_different_content_saves_both(tmp_path, mailbox):
    mailbox([invoice_message("pinduoduo", data) for data in (PDF_A, PDF_B)])
    result = fetch(tmp_path)
    paths = list(tmp_path.glob("*.pdf"))
    assert result["downloaded"] == 2
    assert len(paths) == 2
    assert {p.read_bytes() for p in paths} == {PDF_A, PDF_B}
    assert all(p.name.startswith("拼多多_测试发票_") for p in paths)


def test_content_dedupe_existing_uppercase_pdf_and_cross_source(tmp_path, mailbox):
    original = tmp_path / "已存在.PDF"
    original.write_bytes(PDF_A)
    mailbox([
        invoice_message("jd"),
        invoice_message("pinduoduo", PDF_B),
        invoice_message("alipay", PDF_B, filename="完全不同的名称.pdf"),
        invoice_message("alipay", PDF_A),
    ])
    result = fetch(tmp_path)
    assert (result["downloaded"], result["skipped"], result["failed"]) == (1, 3, 0)
    assert original.read_bytes() == PDF_A
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("sender", [
    '"invoice@pinduoduo.com" <wrong@example.invalid>',
    "invoice@pinduoduo.com.example.invalid",
    "prefix-invoice@pinduoduo.com",
    "invoice@pinduoduo.com, extra@example.invalid",
])
def test_sender_substrings_display_names_and_multiple_senders_are_rejected(tmp_path, mailbox, sender):
    msg = invoice_message("pinduoduo")
    msg.replace_header("From", sender)
    mailbox([msg])
    result = fetch(tmp_path)
    assert result["emails"] == 0
    assert result["downloaded"] == 0
    assert list(tmp_path.iterdir()) == []


def test_sender_is_case_insensitive_and_subject_rule_is_required(tmp_path, mailbox):
    accepted = invoice_message("alipay")
    accepted.replace_header("From", "发票管家 <SERVICE@SERVICE.KFP.CN>")
    rejected = invoice_message("alipay", PDF_B)
    rejected.replace_header("Subject", "其他消息")
    mailbox([accepted, rejected])
    result = fetch(tmp_path)
    assert result["emails"] == result["downloaded"] == 1


def test_rfc2231_chinese_filename_and_octet_stream_are_supported(tmp_path, mailbox):
    mailbox([invoice_message("alipay", filename="中文 发票.PDF", content_type="application/octet-stream")])
    result = fetch(tmp_path)
    assert result["downloaded"] == 1
    assert next(tmp_path.glob("*.pdf")).name.startswith("支付宝_中文 发票_")


def test_encoded_word_filename_and_unsafe_path_are_sanitized(tmp_path, mailbox):
    msg = invoice_message("pinduoduo", filename="ignored.pdf")
    part = list(msg.iter_attachments())[0]
    del part["Content-Disposition"]
    encoded = Header("../带斜线:发票.pdf", "utf-8").encode()
    part["Content-Disposition"] = f'attachment; filename="{encoded}"'
    mailbox([msg])
    result = fetch(tmp_path)
    assert result["downloaded"] == 1
    path = next(tmp_path.glob("*.pdf"))
    assert path.parent == tmp_path
    assert "带斜线_发票" in path.name
    assert "/" not in path.name and ":" not in path.name


def test_exact_start_time_and_imap_since(tmp_path, mailbox):
    cutoff = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    old = invoice_message("pinduoduo", PDF_A)
    old.replace_header("Date", format_datetime(cutoff - timedelta(seconds=1)))
    current = invoice_message("alipay", PDF_B)
    current.replace_header("Date", format_datetime(cutoff))
    mail = mailbox([old, current])
    result = fetch(tmp_path, start_time=cutoff)
    assert result["emails"] == result["downloaded"] == 1
    assert next(tmp_path.glob("*.pdf")).read_bytes() == PDF_B
    imap_date = jd_qq.parse_start_time(cutoff)[1]
    assert all(f" SINCE {imap_date}" in args[1] for args in mail.searches)


@pytest.mark.parametrize("data", [b"<html>expired</html>", b""])
def test_non_pdf_attachment_bytes_fail_without_files(tmp_path, mailbox, data):
    mailbox([invoice_message("alipay", data)])
    result = fetch(tmp_path)
    assert (result["attachments"], result["downloaded"], result["failed"]) == (1, 0, 1)
    assert "不是 PDF" in result["errors"][0]
    assert "支付宝管家｜主题：发票导出成功提醒" in result["errors"][0]
    assert "UID：1｜文件：测试发票.pdf" in result["errors"][0]
    assert result["sources"]["alipay"]["failed"] == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("source", ["jd", "pinduoduo", "alipay"])
def test_matched_message_without_supported_pdf_reports_failure(tmp_path, mailbox, source):
    msg = invoice_message(source, data=None)
    if source == "jd":
        msg.clear_content()
        msg.set_content("发票模板已变更，没有下载链接")
    mailbox([msg])
    result = fetch(tmp_path)
    assert (result["emails"], result["downloaded"], result["failed"]) == (1, 0, 1)
    assert "未找到" in result["errors"][0]
    name = "支付宝管家" if source == "alipay" else jd_qq.MAIL_SOURCES[source]["name"]
    assert result["errors"][0].startswith(f"{name}｜主题：")
    assert "｜时间：2026-10-12 " in result["errors"][0]
    assert "｜UID：1" in result["errors"][0]
    assert result["sources"][source]["failed"] == 1


def test_identical_failures_identify_each_encoded_chinese_message(tmp_path, mailbox):
    messages = []
    for suffix in ("合成甲", "合成乙"):
        msg = invoice_message("pinduoduo", data=None)
        msg.replace_header("Subject", Header(f"拼多多商家电子发票 {suffix}", "utf-8").encode())
        assert b"=?utf-8?" in msg.as_bytes().lower()
        messages.append(msg)
    mailbox(messages)
    result = fetch(tmp_path)
    assert result["failed"] == result["sources"]["pinduoduo"]["failed"] == 2
    first, second = result["errors"]
    assert "主题：拼多多商家电子发票 合成甲" in first and "UID：1" in first
    assert "主题：拼多多商家电子发票 合成乙" in second and "UID：2" in second
    assert all("未找到 PDF 附件" in error for error in result["errors"])


@pytest.mark.parametrize("date, expected", [(None, "未提供"), ("synthetic invalid date", "synthetic invalid date")])
def test_missing_or_invalid_message_date_keeps_error_useful(tmp_path, mailbox, date, expected):
    msg = invoice_message("alipay", data=None)
    del msg["Date"]
    mail = mailbox([msg])
    if date is not None:
        # EmailMessage serialization drops an invalid Date; mimic wire bytes.
        mail.messages[b"1"] = f"Date: {date}\n".encode() + mail.messages[b"1"]
    result = fetch(tmp_path)
    assert f"｜时间：{expected}｜UID：1" in result["errors"][0]


def test_error_headers_collapse_controls_and_limit_long_subjects(tmp_path, mailbox):
    msg = invoice_message("pinduoduo", data=None)
    msg.replace_header("Subject", "拼多多商家电子发票\t\t合成\x00标题\u202e " + "长" * 500)
    mailbox([msg])
    error = fetch(tmp_path)["errors"][0]
    subject = error.split("主题：", 1)[1].split("｜时间：", 1)[0]
    assert subject.startswith("拼多多商家电子发票 合成 标题 ")
    assert len(subject) == 100 and subject.endswith("…")
    assert not any(char in error for char in ("\r", "\n", "\t", "\x00", "\u202e"))
    assert "｜UID：1" in error


def test_legacy_entrypoint_fetches_only_jd(tmp_path, mailbox):
    mail = mailbox([invoice_message("jd"), invoice_message("alipay", PDF_B)])
    result = jd_qq.fetch_jd_invoices("test@example.invalid", "test-auth-code", tmp_path)
    assert result["emails"] == result["downloaded"] == 1
    assert result["attachments"] == 0
    assert list(result["sources"]) == ["jd"]
    assert len(mail.searches) == 1
    assert "customer_service@jd.com" in mail.searches[0][1]


def test_jd_filename_collision_does_not_skip_or_overwrite(tmp_path, mailbox):
    original = tmp_path / "邮件1_00000000000000000001.pdf"
    original.write_bytes(PDF_B)
    mailbox([invoice_message("jd")])
    result = fetch(tmp_path)
    assert result["downloaded"] == 1
    assert original.read_bytes() == PDF_B
    assert {p.read_bytes() for p in tmp_path.glob("*.pdf")} == {PDF_A, PDF_B}


def test_jd_same_invoice_number_different_links_get_hash_checked(tmp_path, mailbox, monkeypatch):
    first, second = invoice_message("jd"), invoice_message("jd")
    second.get_payload()[1].set_content(
        '<p>发票号码：00000000000000000001</p><a href="https://example.invalid/other.pdf">发票PDF文件下载</a>',
        subtype="html",
    )
    mailbox([first, second])
    monkeypatch.setattr(jd_qq, "_download_pdf", lambda url: (PDF_A if url == JD_URL else PDF_B, ""))
    result = fetch(tmp_path)
    assert (result["links"], result["downloaded"]) == (2, 2)
    assert {p.read_bytes() for p in tmp_path.glob("*.pdf")} == {PDF_A, PDF_B}


def test_publish_collision_race_does_not_overwrite(tmp_path, monkeypatch):
    name = "rename" if os.name == "nt" else "link"
    publish = getattr(jd_qq.os, name)
    calls = []

    def race(source, target):
        calls.append(target)
        if len(calls) == 1:
            target.write_bytes(PDF_B)
            raise FileExistsError("another writer won")
        return publish(source, target)

    monkeypatch.setattr(jd_qq.os, name, race)
    digest = hashlib.sha256(PDF_A).hexdigest()
    saved = jd_qq._save_pdf(PDF_A, tmp_path, "same", digest)
    assert (tmp_path / "same.pdf").read_bytes() == PDF_B
    assert saved.read_bytes() == PDF_A
    assert saved.name == f"same_{digest[:12]}.pdf"
    assert not list(tmp_path.glob("*.part"))


def test_write_failure_cleans_temp_and_preserves_existing_files(tmp_path, mailbox, monkeypatch):
    original = tmp_path / "existing.pdf"
    original.write_bytes(PDF_B)
    mailbox([invoice_message("alipay")])

    def denied(*args):
        raise PermissionError("synthetic failure")

    monkeypatch.setattr(jd_qq.os, "rename" if os.name == "nt" else "link", denied)
    result = fetch(tmp_path)
    assert result["failed"] == 1 and result["downloaded"] == 0
    error = result["errors"][0]
    assert "支付宝管家｜主题：发票导出成功提醒" in error
    assert "UID：1｜文件：测试发票.pdf" in error
    assert "保存 PDF 失败：PermissionError: synthetic failure" in error
    assert result["sources"]["alipay"]["failed"] == 1
    assert original.read_bytes() == PDF_B
    assert list(tmp_path.iterdir()) == [original]


@pytest.mark.parametrize("empty", [False, True])
def test_unreadable_message_reports_failure_and_logs_out(tmp_path, mailbox, empty):
    mail = mailbox([invoice_message("pinduoduo")])
    if empty:
        mail.empty_fetch = True
    else:
        mail.fail_fetch = "NO"
    result = fetch(tmp_path)
    assert result["failed"] == 1
    assert result["errors"]
    assert "来源未确定｜UID：1" in result["errors"][0]
    assert all(stats["failed"] == stats["emails"] == 0 for stats in result["sources"].values())
    assert mail.logged_out


def test_download_failure_logs_out_and_preserves_files(tmp_path, mailbox, monkeypatch):
    mail = mailbox([invoice_message("jd")])
    monkeypatch.setattr(jd_qq, "_download_pdf", lambda url: (None, "synthetic expired link"))
    result = fetch(tmp_path)
    assert result["failed"] == 1
    assert "synthetic expired link" in result["errors"][0]
    assert "京东｜主题：电子发票已开具" in result["errors"][0]
    assert "UID：1｜文件：邮件1_00000000000000000001" in result["errors"][0]
    assert result["sources"]["jd"]["failed"] == 1
    assert list(tmp_path.iterdir()) == []
    assert mail.logged_out


def test_transport_diagnostics_do_not_echo_signed_urls_or_auth_codes(tmp_path, mailbox, monkeypatch):
    mailbox([invoice_message("jd")])
    monkeypatch.setattr(
        jd_qq, "_download_pdf",
        lambda url: (None, "TimeoutError: https://example.invalid/invoice?token=synthetic-secret test-auth-code"),
    )
    error = fetch(tmp_path)["errors"][0]
    assert "TimeoutError:" in error
    assert "[链接已隐藏]" in error and "[授权码已隐藏]" in error
    assert "synthetic-secret" not in error and "test-auth-code" not in error


def test_login_error_logs_out(tmp_path, mailbox, monkeypatch):
    mail = mailbox([])

    def denied(*args):
        raise RuntimeError("synthetic login failure")

    monkeypatch.setattr(mail, "login", denied)
    with pytest.raises(RuntimeError, match="synthetic login"):
        fetch(tmp_path)
    assert mail.logged_out


def test_multiple_alipay_attachments_import_pdfs_and_ignore_ofd(tmp_path, mailbox):
    msg = invoice_message("alipay", PDF_A)
    msg.add_attachment(PDF_B, maintype="application", subtype="pdf", filename="第二张.pdf")
    msg.add_attachment(b"synthetic OFD", maintype="application", subtype="octet-stream", filename="发票.ofd")
    mailbox([msg])
    result = fetch(tmp_path)
    assert (result["emails"], result["attachments"], result["downloaded"], result["failed"]) == (1, 2, 2, 0)
    assert {p.read_bytes() for p in tmp_path.iterdir()} == {PDF_A, PDF_B}


def test_repeated_jd_link_downloads_only_once(tmp_path, mailbox, monkeypatch):
    mailbox([invoice_message("jd"), invoice_message("jd")])
    urls = []

    def download(url):
        urls.append(url)
        return PDF_A, ""

    monkeypatch.setattr(jd_qq, "_download_pdf", download)
    result = fetch(tmp_path)
    assert (result["emails"], result["links"], result["downloaded"], result["failed"]) == (2, 1, 1, 0)
    assert urls == [JD_URL]


@pytest.mark.parametrize("label", ["发票PDF文件下载", "发票文件下载"])
def test_jd_old_and_new_template_download_and_rescan_skip_same_content(tmp_path, mailbox, monkeypatch, label):
    msg = invoice_message("jd")
    msg.get_payload()[1].set_content(
        f'<a href="{JD_URL}">{label}</a>', subtype="html",
    )
    mailbox([msg])
    downloads = []

    def download(url):
        downloads.append(url)
        return PDF_A, ""

    monkeypatch.setattr(jd_qq, "_download_pdf", download)
    first = fetch(tmp_path)
    assert (first["links"], first["downloaded"], first["skipped"], first["failed"]) == (1, 1, 0, 0)
    saved_path = next(tmp_path.glob("*.pdf"))
    renamed = saved_path.with_name("用户重新命名.pdf")
    saved_path.rename(renamed)
    second = fetch(tmp_path, start_time="2020-01-01")
    assert (second["links"], second["downloaded"], second["skipped"], second["failed"]) == (1, 0, 1, 0)
    assert downloads == [JD_URL, JD_URL]
    assert list(tmp_path.iterdir()) == [renamed]
    assert renamed.read_bytes() == PDF_A


def test_jd_normal_xml_only_reports_missing_pdf_for_the_message(tmp_path, response_mailbox):
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票文件下载"))],
        {JD_URL: (b"<?xml version='1.0'?><invoice/>", "application/xml")},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL]
    assert (result["links"], result["downloaded"], result["skipped"], result["failed"]) == (1, 0, 0, 1)
    assert len(result["errors"]) == 1
    assert "未取得 PDF" in result["errors"][0]
    assert result["sources"]["jd"]["failed"] == 1
    assert list(tmp_path.iterdir()) == []


def normal_zip_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("invoice.xml", "<invoice/>")
    return stream.getvalue()


@pytest.mark.parametrize("body, content_type", [
    (b"<?xml version='1.0'?><Invoice><Number>synthetic-01</Number></Invoice>", "application/xml"),
    (b"<?xml version='1.0'?><Invoice/>", "application/octet-stream"),
    (normal_zip_bytes(), "application/zip"),
])
def test_jd_pdf_and_unlabelled_normal_non_pdf_only_count_pdf(
    tmp_path, response_mailbox, body, content_type,
):
    extra_url = "https://example.invalid/download?file=other"
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票PDF文件下载"), (extra_url, "发票文件下载"))],
        {JD_URL: (PDF_A, "application/pdf"), extra_url: (body, content_type)},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL, extra_url]
    assert (result["emails"], result["links"], result["downloaded"], result["skipped"], result["failed"]) == (1, 2, 1, 0, 0)
    assert result["errors"] == []
    assert [path.read_bytes() for path in tmp_path.iterdir()] == [PDF_A]


def test_jd_explicit_xml_link_is_not_requested(tmp_path, response_mailbox):
    xml_label_url = "https://example.invalid/download?file=xml"
    xml_path_url = "https://example.invalid/invoice.xml?signature=synthetic"
    requested_urls = response_mailbox(
        [jd_link_message(
            (xml_label_url, "发票XML文件下载"),
            (xml_path_url, "发票文件下载"),
            (JD_URL, "发票PDF文件下载"),
        )],
        {JD_URL: (PDF_A, "application/pdf")},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL]
    assert (result["links"], result["downloaded"], result["skipped"], result["failed"]) == (1, 1, 0, 0)
    assert result["errors"] == []


@pytest.mark.parametrize("body, content_type, reason", [
    (b"<html><body>Invoice download expired</body></html>", "text/html", "网页"),
    (b"<html><body>Login required</body></html>", "application/xml", "网页"),
    (b"<Error><Code>AccessDenied</Code><Message>Expired link</Message></Error>", "application/xml", "AccessDenied"),
    (b"<Error><Code>AccessDenied</Code></Error>", "application/octet-stream", "AccessDenied"),
    (b"", "application/pdf", "内容为空"),
])
def test_jd_page_error_response_and_empty_content_remain_specific_failure(
    tmp_path, response_mailbox, body, content_type, reason,
):
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票文件下载"))],
        {JD_URL: (body, content_type)},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL]
    assert (result["downloaded"], result["skipped"], result["failed"]) == (0, 0, 1)
    assert len(result["errors"]) == 1
    assert "UID：1" in result["errors"][0]
    assert reason in result["errors"][0]
    assert "｜文件：" in result["errors"][0]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("error, expected", [
    (urllib.error.HTTPError(JD_URL, 403, "Forbidden", {}, None), "HTTPError"),
    (TimeoutError("synthetic timeout"), "TimeoutError"),
])
def test_jd_transport_failures_keep_reason_and_do_not_add_missing_pdf_failure(
    tmp_path, response_mailbox, error, expected,
):
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票文件下载"))], {JD_URL: error},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL] * 3
    assert (result["downloaded"], result["skipped"], result["failed"]) == (0, 0, 1)
    assert len(result["errors"]) == 1
    assert expected in result["errors"][0]
    assert "未取得 PDF" not in result["errors"][0]
    assert list(tmp_path.iterdir()) == []


def test_jd_pdf_does_not_hide_xml_error_response(tmp_path, response_mailbox):
    error_url = "https://example.invalid/download?file=expired"
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票PDF文件下载"), (error_url, "发票文件下载"))],
        {
            JD_URL: (PDF_A, "application/pdf"),
            error_url: (b"<Error><Code>ExpiredToken</Code></Error>", "application/xml"),
        },
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL, error_url]
    assert (result["downloaded"], result["skipped"], result["failed"]) == (1, 0, 1)
    assert len(result["errors"]) == 1
    assert "ExpiredToken" in result["errors"][0]
    assert [path.read_bytes() for path in tmp_path.iterdir()] == [PDF_A]


@pytest.mark.parametrize("body, content_type, expected_failures", [
    (PDF_A, "application/pdf", 0),
    (b"<Invoice/>", "application/xml", 2),
    (b"<Error><Code>AccessDenied</Code></Error>", "application/xml", 2),
])
def test_jd_repeated_url_across_messages_preserves_each_message_outcome(
    tmp_path, response_mailbox, body, content_type, expected_failures,
):
    requested_urls = response_mailbox(
        [jd_link_message((JD_URL, "发票文件下载")) for _ in range(2)],
        {JD_URL: (body, content_type)},
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL]
    assert (result["emails"], result["links"], result["skipped"], result["failed"]) == (2, 1, 0, expected_failures)
    assert result["downloaded"] == (1 if body == PDF_A else 0)
    assert len(result["errors"]) == expected_failures
    if expected_failures:
        assert "UID：1" in result["errors"][0] and "UID：2" in result["errors"][1]
        if body == b"<Invoice/>":
            assert all("未取得 PDF" in error for error in result["errors"])
        else:
            assert all("AccessDenied" in error for error in result["errors"])
        assert list(tmp_path.iterdir()) == []


def test_jd_multiple_ignored_files_add_only_one_missing_pdf_failure(tmp_path, response_mailbox):
    second_url = "https://example.invalid/download?file=second"
    requested_urls = response_mailbox(
        [jd_link_message(
            (JD_URL, "发票文件下载"),
            (JD_URL, "获取发票"),
            (second_url, "发票文件下载"),
        )],
        {
            JD_URL: (b"<Invoice/>", "application/xml"),
            second_url: (normal_zip_bytes(), "application/zip"),
        },
    )
    result = fetch(tmp_path)
    assert requested_urls == [JD_URL, second_url]
    assert (result["links"], result["downloaded"], result["skipped"], result["failed"]) == (2, 0, 0, 1)
    assert len(result["errors"]) == 1 and "未取得 PDF" in result["errors"][0]
    assert list(tmp_path.iterdir()) == []


def test_jd_multiple_bad_downloads_do_not_add_extra_missing_pdf_failure(tmp_path, response_mailbox):
    second_url = "https://example.invalid/download?file=second"
    response_mailbox(
        [jd_link_message((JD_URL, "发票文件下载"), (second_url, "发票文件下载"))],
        {
            JD_URL: (b"<html>Expired</html>", "text/html"),
            second_url: (b"<Error><Code>AccessDenied</Code></Error>", "application/xml"),
        },
    )
    result = fetch(tmp_path)
    assert (result["downloaded"], result["skipped"], result["failed"]) == (0, 0, 2)
    assert len(result["errors"]) == 2
    assert "网页" in result["errors"][0] and "AccessDenied" in result["errors"][1]
    assert list(tmp_path.iterdir()) == []


def test_pdf_without_invoice_amount_is_saved_for_later_review(tmp_path, mailbox, monkeypatch):
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Synthetic document without an amount")
        pdf = document.tobytes()
    mailbox([invoice_message("jd")])
    monkeypatch.setattr(jd_qq, "_download_pdf", lambda url: (pdf, ""))
    result = fetch(tmp_path)
    assert (result["downloaded"], result["failed"]) == (1, 0)
    assert next(tmp_path.glob("*.pdf")).read_bytes() == pdf
