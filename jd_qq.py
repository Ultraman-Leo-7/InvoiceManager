# -*- coding: utf-8 -*-
"""QQ 收件箱发票导入：京东下载链接、拼多多与支付宝发票管家 PDF 附件。"""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import os
import re
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from email.header import decode_header, make_header
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
JD_SENDER = "customer_service@jd.com"
SUBJECT_KEYWORD = "电子发票已开具"

MAIL_SOURCES = {
    "jd": {"name": "京东", "sender": JD_SENDER, "subject": SUBJECT_KEYWORD},
    "pinduoduo": {
        "name": "拼多多", "sender": "invoice@pinduoduo.com", "subject": "拼多多商家电子发票",
    },
    "alipay": {
        "name": "支付宝", "sender": "service@service.kfp.cn", "subject": "发票导出成功提醒",
    },
}


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip().rstrip(". ")
    return name[:180] or "发票"


def normalize_href(href: str) -> str:
    href = html.unescape((href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    return href


def _link_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if not char.isspace() and unicodedata.category(char) != "Cf")


def _is_invoice_download_link(label: str, url: str) -> bool:
    """Combine invoice/action hints; the downloaded bytes must still pass the PDF check."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return False
    except ValueError:
        return False
    label = _link_text(label)
    path = urllib.parse.unquote(parsed.path).casefold()
    other_formats = ("xml", "ofd", "zip", "rar", "7z", "xlsx", "xls", "csv")
    if any(kind in label or path.endswith("." + kind) for kind in other_formats):
        return False
    if any(word in label for word in ("退订", "unsubscribe", "广告", "帮助", "说明书", "操作指南")):
        return False
    has_invoice = any(word in label for word in ("发票", "invoice", "fapiao"))
    has_action = any(word in label for word in ("下载", "获取", "download"))
    if has_invoice and has_action:
        return True
    # Generic download buttons need an explicit invoice PDF path, not just any PDF URL.
    return has_action and path.endswith(".pdf") and any(
        word in path for word in ("invoice", "fapiao", "发票")
    )


class InvoiceLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.all_text: list[str] = []
        self.current_href = None
        self.current_anchor_text: list[str] = []
        self.current_context = ""
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a" and self.current_href is None:
            attr = dict(attrs)
            self.current_href = attr.get("href")
            self.current_anchor_text = [attr.get("title", "") or "", attr.get("aria-label", "") or ""]
            self.current_context = "".join(self.all_text)[-600:]
        elif tag.lower() == "img" and self.current_href is not None:
            self.current_anchor_text.append(dict(attrs).get("alt", "") or "")

    def handle_data(self, data):
        self.all_text.append(data)
        if self.current_href is not None:
            self.current_anchor_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current_href is not None:
            anchor_text = "".join(self.current_anchor_text)
            url = normalize_href(self.current_href)
            if _is_invoice_download_link(anchor_text, url):
                self.links.append(
                    {
                        "url": url,
                        "context": self.current_context,
                    }
                )
            self.current_href = None
            self.current_anchor_text = []
            self.current_context = ""


def part_to_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        try:
            return str(part.get_content())
        except Exception:
            return ""

    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "gb18030"):
        try:
            return payload.decode(enc)
        except Exception:
            pass
    return payload.decode("utf-8", errors="replace")


def extract_html_bodies(msg):
    bodies = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                disposition = (part.get("Content-Disposition") or "").lower()
                if "attachment" not in disposition:
                    bodies.append(part_to_text(part))
    elif msg.get_content_type() == "text/html":
        bodies.append(part_to_text(msg))
    return bodies


def extract_order_number(subject: str) -> str:
    for pattern in (
        r"【(\d{10,})】",
        r"\[(\d{10,})\]",
        r"订单[^\d]{0,10}(\d{10,})",
    ):
        m = re.search(pattern, subject)
        if m:
            return m.group(1)
    return ""


def extract_invoice_number(context: str) -> str:
    matches = re.findall(r"发票号码\s*[：:]\s*(\d{8,30})", context)
    return matches[-1] if matches else ""


def parse_start_time(value) -> tuple[datetime | None, str | None]:
    """返回本地时区 datetime 和 IMAP SINCE 日期。"""
    if value is None:
        return None, None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None, None
        dt = None
        for parser in (
            lambda s: datetime.fromisoformat(s),
            lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M"),
            lambda s: datetime.strptime(s, "%Y-%m-%d"),
        ):
            try:
                dt = parser(text)
                break
            except ValueError:
                pass
        if dt is None:
            raise ValueError("起始时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")

    local_tz = datetime.now().astimezone().tzinfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    else:
        dt = dt.astimezone(local_tz)

    months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    imap_date = f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year:04d}"
    return dt, imap_date


def message_time(msg):
    raw = msg.get("Date", "")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)


def _diagnostic_text(value, limit=160) -> str:
    """Keep untrusted mail headers and errors readable in one bounded line."""
    text = str(value)
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    text = re.sub(r"https?://[^\s<>\"']+", "[链接已隐藏]", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _message_error_context(msg, source, uid) -> str:
    name = "支付宝管家" if source == "alipay" else MAIL_SOURCES[source]["name"]
    subject = _diagnostic_text(decode_mime_header(str(msg.get("Subject", ""))), 100) or "无主题"
    try:
        dt = message_time(msg)
    except (ValueError, OverflowError, OSError):
        dt = None
    date = dt.strftime("%Y-%m-%d %H:%M:%S %z") if dt is not None else (
        _diagnostic_text(msg.get("Date", ""), 60) or "未提供"
    )
    uid_text = _diagnostic_text(uid.decode("ascii", errors="replace"), 40)
    return f"{name}｜主题：{subject}｜时间：{date}｜UID：{uid_text}"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _existing_pdf_hashes(output_dir: Path) -> set[str]:
    result = set()
    for path in output_dir.iterdir():
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            result.add(h.hexdigest())
        except OSError:
            pass
    return result


def _download_pdf(url: str, retries=3):
    if not url.lower().startswith(("http://", "https://")):
        return None, "链接不是 http/https 地址"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "Referer": "https://order.jd.com/",
    }

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = resp.read()
                final_url = resp.geturl()
                content_type = (resp.headers.get("Content-Type") or "").lower()
            if not data.startswith(b"%PDF-"):
                host = urllib.parse.urlparse(final_url).netloc
                return None, (
                    "返回内容不是 PDF"
                    f"（Content-Type={content_type or '未知'}，最终域名={host or '未知'}）"
                )
            return data, ""
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(1.2 * attempt)
    return None, last_error


def _message_source(msg, sources) -> str | None:
    # IMAP FROM is a substring search; verify the actual mailbox after fetching.
    try:
        addresses = getaddresses([str(value) for value in msg.get_all("From", [])])
    except (TypeError, ValueError):
        return None
    if len(addresses) != 1:
        return None
    sender = addresses[0][1].strip().lower()
    subject = decode_mime_header(str(msg.get("Subject", "")))
    for source in sources:
        rule = MAIL_SOURCES[source]
        if sender == rule["sender"] and rule["subject"] in subject:
            return source
    return None


def _pdf_attachments(msg):
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = decode_mime_header(part.get_filename() or "")
        if part.get_content_type() != "application/pdf" and not filename.lower().endswith(".pdf"):
            continue
        yield filename or "发票.pdf", part.get_payload(decode=True) or b""


def _save_pdf(data: bytes, output_dir: Path, stem: str, digest: str) -> Path:
    """Publish a completed file without replacing any existing path."""
    stem = safe_filename(stem)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".invoice-", suffix=".part", dir=output_dir, delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(data)
        attempt = 0
        while True:
            if attempt == 0:
                filename = f"{stem}.pdf"
            elif attempt == 1:
                filename = f"{stem}_{digest[:12]}.pdf"
            else:
                filename = f"{stem}_{digest[:12]}_({attempt}).pdf"
            target = output_dir / filename
            # lexists also protects directories and dangling symbolic links.
            if os.path.lexists(target):
                attempt += 1
                continue
            try:
                if os.name == "nt":
                    # Windows rename fails if target appears concurrently.
                    os.rename(tmp, target)
                else:
                    # POSIX rename replaces existing files; link creates exclusively.
                    os.link(tmp, target)
                return target
            except FileExistsError:
                attempt += 1
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def fetch_qq_invoices(
    email_addr: str,
    auth_code: str,
    output_dir: Path,
    start_time=None,
    progress=None,
    sources=None,
) -> dict:
    """Import approved invoice emails from QQ INBOX, without changing the mailbox.

    sources: jd / pinduoduo / alipay; None selects all three.
    emails counts matching messages; links counts unique JD URLs; attachments
    counts PDF attachment candidates. File outcomes are downloaded/skipped/failed;
    failed also includes unreadable messages or messages with no supported PDF.
    Content hashes, never filenames or invoice numbers, determine duplicates.
    """
    if sources is None:
        sources = tuple(MAIL_SOURCES)
    elif isinstance(sources, str):
        sources = (sources,)
    else:
        sources = tuple(dict.fromkeys(sources))
    if not sources or any(source not in MAIL_SOURCES for source in sources):
        raise ValueError("发票来源必须是 jd、pinduoduo 或 alipay")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt, imap_date = parse_start_time(start_time)
    counters = ("emails", "links", "attachments", "downloaded", "skipped", "failed")
    result = {key: 0 for key in counters}
    result["errors"] = []
    result["sources"] = {
        source: {key: 0 for key in counters} for source in sources
    }
    existing_hashes = _existing_pdf_hashes(output_dir)

    def count(source, key):
        result[key] += 1
        result["sources"][source][key] += 1

    def report_error(context, detail):
        # A transport exception can include a signed URL. Never echo credentials
        # or download URLs while adding more useful mail identification.
        if auth_code:
            context = context.replace(auth_code, "[授权码已隐藏]")
            detail = str(detail).replace(auth_code, "[授权码已隐藏]")
        result["errors"].append(f"{context}：{_diagnostic_text(detail, 300)}")

    def save_candidate(source, stem, data, context, error="", filename=""):
        file_context = f"{context}｜文件：{_diagnostic_text(filename or stem, 140)}"
        if data is None or not data.startswith(b"%PDF-"):
            count(source, "failed")
            report_error(file_context, error or "附件内容不是 PDF")
            return
        digest = _sha256_bytes(data)
        if digest in existing_hashes:
            count(source, "skipped")
            return
        try:
            _save_pdf(data, output_dir, stem, digest)
        except Exception as exc:
            count(source, "failed")
            report_error(file_context, f"保存 PDF 失败：{type(exc).__name__}: {exc}")
        else:
            existing_hashes.add(digest)
            count(source, "downloaded")

    mail = None
    try:
        if progress:
            progress("连接 QQ 邮箱...")
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(email_addr, auth_code)
        typ, _ = mail.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("无法以只读方式打开 QQ 邮箱收件箱")

        uids = []
        seen_uids = set()
        for source in sources:
            rule = MAIL_SOURCES[source]
            criteria = f'(FROM "{rule["sender"]}"'
            if imap_date:
                criteria += f" SINCE {imap_date}"
            criteria += ")"
            typ, data = mail.uid("search", None, criteria)
            if typ != "OK":
                raise RuntimeError(f"搜索{rule['name']}邮件失败")
            for uid in data[0].split() if data and data[0] else []:
                if uid not in seen_uids:
                    seen_uids.add(uid)
                    uids.append(uid)

        seen_urls = set()
        for i, uid in enumerate(uids, 1):
            if progress:
                progress(f"扫描发票邮件 {i}/{len(uids)}...")
            typ, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
            uid_context = f"来源未确定｜UID：{_diagnostic_text(uid.decode('ascii', errors='replace'), 40)}"
            if typ != "OK":
                result["failed"] += 1
                report_error(uid_context, "读取候选邮件失败，可稍后重试")
                continue
            raw = next(
                (item[1] for item in fetched or []
                 if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes)),
                None,
            )
            if not raw:
                result["failed"] += 1
                report_error(uid_context, "候选邮件内容为空，可稍后重试")
                continue
            msg = email.message_from_bytes(raw, policy=default)
            source = _message_source(msg, sources)
            if source is None:
                continue
            if start_dt is not None:
                dt = message_time(msg)
                if dt is not None and dt < start_dt:
                    continue
            count(source, "emails")
            context = _message_error_context(msg, source, uid)
            if source != "jd":
                attachment_count = 0
                for filename, data in _pdf_attachments(msg):
                    attachment_count += 1
                    count(source, "attachments")
                    # A digest suffix avoids common attachment-name collisions.
                    attachment_stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
                    stem = f"{MAIL_SOURCES[source]['name']}_{safe_filename(attachment_stem)[:120]}_{_sha256_bytes(data)[:12]}"
                    save_candidate(source, stem, data, context, filename=filename)
                if not attachment_count:
                    count(source, "failed")
                    report_error(context, "邮件未找到 PDF 附件（暂不支持压缩包或下载页面）")
                continue

            subject = decode_mime_header(str(msg.get("Subject", "")))
            order = extract_order_number(subject) or f"邮件{uid.decode(errors='ignore')}"
            link_index = 0
            for body in extract_html_bodies(msg):
                parser = InvoiceLinkParser()
                try:
                    parser.feed(body)
                except Exception:
                    continue
                for link in parser.links:
                    url = link.get("url") or ""
                    if not url:
                        continue
                    link_index += 1
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    count(source, "links")
                    invoice_no = extract_invoice_number(link.get("context", ""))
                    stem = f"{order}_{invoice_no}" if invoice_no else f"{order}_发票{link_index}"
                    if progress:
                        progress(f"下载京东发票 {result['links']}...")
                    data, error = _download_pdf(url)
                    save_candidate(source, stem, data, context, error)
            if not link_index:
                count(source, "failed")
                report_error(context, "京东邮件未找到发票 PDF 下载链接")
        return result
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


def fetch_jd_invoices(
    email_addr: str,
    auth_code: str,
    output_dir: Path,
    start_time=None,
    progress=None,
) -> dict:
    """Backward-compatible JD-only entry point."""
    return fetch_qq_invoices(
        email_addr, auth_code, output_dir, start_time, progress, sources=("jd",),
    )
