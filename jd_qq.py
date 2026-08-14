# -*- coding: utf-8 -*-
"""QQ 邮箱 -> 京东电子发票 PDF 下载模块。"""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.header import decode_header, make_header
from email.policy import default
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
JD_SENDER = "customer_service@jd.com"
SUBJECT_KEYWORD = "电子发票已开具"
PDF_LINK_TEXT = "发票PDF文件下载"


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
            self.current_anchor_text = []
            self.current_context = "".join(self.all_text)[-600:]

    def handle_data(self, data):
        self.all_text.append(data)
        if self.current_href is not None:
            self.current_anchor_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current_href is not None:
            anchor_text = "".join(self.current_anchor_text)
            if PDF_LINK_TEXT in re.sub(r"\s+", "", anchor_text):
                self.links.append(
                    {
                        "url": normalize_href(self.current_href),
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _existing_pdf_hashes(output_dir: Path) -> set[str]:
    result = set()
    for path in output_dir.glob("*.pdf"):
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


def fetch_jd_invoices(
    email_addr: str,
    auth_code: str,
    output_dir: Path,
    start_time=None,
    progress=None,
) -> dict:
    """从 QQ 邮箱收件箱下载京东电子发票 PDF 到 output_dir。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt, imap_date = parse_start_time(start_time)

    result = {
        "emails": 0,
        "links": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    mail = None
    try:
        if progress:
            progress("连接 QQ 邮箱...")
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(email_addr, auth_code)
        typ, _ = mail.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("无法以只读方式打开 QQ 邮箱收件箱")

        if imap_date:
            criteria = f'(FROM "{JD_SENDER}" SINCE {imap_date})'
        else:
            criteria = f'(FROM "{JD_SENDER}")'

        typ, data = mail.uid("search", None, criteria)
        if typ != "OK":
            raise RuntimeError("搜索京东邮件失败")

        uids = data[0].split() if data and data[0] else []
        candidates = []

        for i, uid in enumerate(uids, 1):
            if progress:
                progress(f"扫描京东邮件 {i}/{len(uids)}...")

            typ, fetched = mail.uid("fetch", uid, "(RFC822)")
            if typ != "OK":
                continue
            raw = None
            for item in fetched:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw = item[1]
                    break
            if not raw:
                continue

            msg = email.message_from_bytes(raw, policy=default)
            if start_dt is not None:
                dt = message_time(msg)
                if dt is not None and dt < start_dt:
                    continue

            subject = decode_mime_header(msg.get("Subject", ""))
            if SUBJECT_KEYWORD not in subject:
                continue

            result["emails"] += 1
            order_no = extract_order_number(subject)
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
                    candidates.append(
                        {
                            "uid": uid.decode(errors="ignore"),
                            "order_no": order_no,
                            "invoice_no": extract_invoice_number(link.get("context", "")),
                            "index": link_index,
                            "url": url,
                        }
                    )

        deduped = []
        seen = set()
        for item in candidates:
            key = (
                ("invoice", item["invoice_no"])
                if item["invoice_no"]
                else ("url", item["url"])
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        result["links"] = len(deduped)
        existing_hashes = _existing_pdf_hashes(output_dir)

        for i, item in enumerate(deduped, 1):
            if progress:
                progress(f"下载发票 {i}/{len(deduped)}...")

            order = item["order_no"] or f"邮件{item['uid']}"
            if item["invoice_no"]:
                stem = f"{order}_{item['invoice_no']}"
            else:
                stem = f"{order}_发票{item['index']}"
            target = output_dir / (safe_filename(stem) + ".pdf")

            if target.exists() and target.stat().st_size > 4:
                result["skipped"] += 1
                continue

            data, error = _download_pdf(item["url"])
            if data is None:
                result["failed"] += 1
                result["errors"].append(f"{stem}: {error}")
                continue

            digest = _sha256_bytes(data)
            if digest in existing_hashes:
                result["skipped"] += 1
                continue

            tmp = target.with_suffix(".pdf.part")
            try:
                tmp.write_bytes(data)
                tmp.replace(target)
                existing_hashes.add(digest)
                result["downloaded"] += 1
            except Exception as e:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                result["failed"] += 1
                result["errors"].append(f"{stem}: {type(e).__name__}: {e}")

        return result

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass
