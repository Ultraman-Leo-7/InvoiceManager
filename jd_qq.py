# -*- coding: utf-8 -*-
"""QQ 收件箱发票导入：京东链接、拼多多与支付宝管家 PDF / ZIP 附件。"""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import io
import os
import re
import stat
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
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
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_PDF_BYTES = 50 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 200 * 1024 * 1024

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
    directory_info = output_dir.lstat()
    if _linked_file_stat(directory_info) or not stat.S_ISDIR(directory_info.st_mode):
        raise ValueError(f"不读取链接目录或非文件夹：{output_dir.name}")
    result = set()
    for path in output_dir.iterdir():
        if path.suffix.lower() != ".pdf":
            continue
        try:
            info = path.lstat()
            if _linked_file_stat(info) or not stat.S_ISREG(info.st_mode):
                continue
            h = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as f:
                opened = os.fstat(f.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    continue
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            result.add(h.hexdigest())
        except OSError:
            pass
    return result


def _linked_file_stat(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _non_pdf_response_error(data: bytes, content_type: str) -> str:
    """Empty reason means a recognized non-PDF file that can be ignored."""
    if not data.strip():
        return "返回内容为空，未取得 PDF"
    # A landing/login/error page is not an invoice file, even with HTTP 200.
    if "html" in content_type or re.search(
        br"<(?:!doctype\s+html|html|head|body|script)\b", data[:4096], re.I,
    ):
        return "返回内容是网页，不是 PDF（可能是下载页面、登录页面或失效提示）"
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        root = None
    if root is not None:
        tags = {str(node.tag).rsplit("}", 1)[-1].casefold() for node in root.iter()}
        if tags & {"html", "head", "body", "script"}:
            return "返回内容是网页，不是 PDF"
        if tags & {"error", "errors", "errorresponse", "fault"}:
            # Keep only a short service error code; signed URLs may occur elsewhere.
            code = next((node.text or "" for node in root.iter()
                         if str(node.tag).rsplit("}", 1)[-1].casefold() in ("code", "faultcode")), "")
            return "下载服务返回 XML 错误，未取得 PDF" + (f"（{_diagnostic_text(code, 80)}）" if code else "")
        return ""
    # ZIP also covers OFD/Office containers. Recognize bytes, not just MIME labels.
    if zipfile.is_zipfile(io.BytesIO(data)):
        return ""
    if (data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"\xff\xd8\xff")
            or data.startswith((b"GIF87a", b"GIF89a"))):
        return ""
    return "返回内容不是 PDF，也未识别为正常的其他文件格式"


def _download_pdf(url: str, retries=3):
    """Return (PDF bytes, '') or (None, error); (None, '') ignores another file."""
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
                error = _non_pdf_response_error(data, content_type)
                if not error:
                    return None, ""
                host = urllib.parse.urlparse(final_url).netloc
                return None, (
                    error +
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


def _invoice_attachments(msg):
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = decode_mime_header(part.get_filename() or "")
        content_type = part.get_content_type()
        if filename.lower().endswith(".zip") or content_type in (
            "application/zip", "application/x-zip-compressed", "application/x-zip",
        ):
            yield "zip", filename or "发票.zip", part.get_payload(decode=True) or b""
        elif content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            yield "pdf", filename or "发票.pdf", part.get_payload(decode=True) or b""


def _zip_member_name(info) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:
        # Older Windows exporters commonly use GBK without the ZIP UTF-8 flag.
        try:
            name = name.encode("cp437").decode("gb18030")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _zip_pdf_attachments(data):
    """Yield (member name, PDF bytes, error); an empty name describes the archive.

    An empty name with no error means a readable archive containing no PDFs;
    the caller decides whether the complete email has any supported PDF.

    Never extract members to disk: member paths are labels only. Budget both
    declared and actual reads, and keep other usable members after a bad member.
    """
    if len(data) > MAX_ZIP_BYTES:
        yield "", None, f"ZIP 压缩包超过大小限制（{MAX_ZIP_BYTES // 1024 // 1024} MiB）"
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
        yield "", None, f"无法读取 ZIP 压缩包，文件可能已损坏或格式不受支持：{type(exc).__name__}"
        return
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            yield "", None, f"ZIP 条目数超过限制（{MAX_ZIP_ENTRIES} 项）"
            return
        candidates = 0
        read_budget = 0
        for info in entries:
            name = _zip_member_name(info)
            if info.is_dir() or not name.lower().endswith(".pdf"):
                continue
            candidates += 1
            if stat.S_ISLNK(info.external_attr >> 16):
                yield name, None, "ZIP 中的 PDF 条目是符号链接，已拒绝读取"
                continue
            if info.flag_bits & 1:
                yield name, None, "ZIP 中的 PDF 已加密，暂不支持密码解压"
                continue
            if info.file_size > MAX_ZIP_PDF_BYTES:
                yield name, None, f"ZIP 内 PDF 超过单文件限制（{MAX_ZIP_PDF_BYTES // 1024 // 1024} MiB）"
                continue
            if read_budget + info.file_size > MAX_ZIP_TOTAL_BYTES:
                yield name, None, f"ZIP 内 PDF 累计大小超过限制（{MAX_ZIP_TOTAL_BYTES // 1024 // 1024} MiB）"
                continue
            # Reserve before opening so corrupt members also consume the budget.
            read_budget += info.file_size
            try:
                with archive.open(info) as stream:
                    payload = stream.read(min(info.file_size, MAX_ZIP_PDF_BYTES) + 1)
                if len(payload) != info.file_size:
                    raise ValueError("解压内容大小与 ZIP 记录不一致")
            except NotImplementedError:
                yield name, None, "ZIP 中的 PDF 使用了不支持的压缩方式"
                continue
            except Exception as exc:
                # zipfile delegates to zlib/bz2/lzma; malformed streams can raise
                # codec-specific errors as well as BadZipFile or RuntimeError.
                yield name, None, f"ZIP 内 PDF 解压失败（损坏或压缩方式不可用）：{type(exc).__name__}"
                continue
            yield name, payload, ""
        if not candidates:
            yield "", None, ""


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
    dedupe_dirs=None,
) -> dict:
    """Import approved invoice emails from QQ INBOX, without changing the mailbox.

    sources: jd / pinduoduo / alipay; None selects all three.
    emails counts matching messages; links counts unique JD URLs; attachments
    counts PDF attachment candidates, including members of ZIP attachments;
    archives counts ZIP attachments. File outcomes are downloaded/skipped/failed;
    failed also includes unreadable messages or messages with no supported PDF.
    Content hashes, never filenames or invoice numbers, determine duplicates.
    dedupe_dirs optionally includes other folders' direct PDF files in that check.
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
    counters = ("emails", "links", "attachments", "archives", "downloaded", "skipped", "failed")
    result = {key: 0 for key in counters}
    result["errors"] = []
    result["sources"] = {
        source: {key: 0 for key in counters} for source in sources
    }
    existing_hashes = _existing_pdf_hashes(output_dir)
    if isinstance(dedupe_dirs, (str, Path)):
        dedupe_dirs = (dedupe_dirs,)
    for folder in dedupe_dirs or ():
        folder = Path(folder)
        try:
            folder_info = folder.lstat()
        except FileNotFoundError:
            continue
        if _linked_file_stat(folder_info):
            raise ValueError(f"不读取链接目录：{folder.name}")
        if stat.S_ISDIR(folder_info.st_mode) and folder != output_dir:
            existing_hashes.update(_existing_pdf_hashes(folder))

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

        url_results = {}
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
                archives_without_pdf = []
                failures_before = result["failed"]
                for kind, attachment_name, data in _invoice_attachments(msg):
                    if kind == "zip":
                        count(source, "archives")
                        candidates = _zip_pdf_attachments(data)
                        file_context = f"{context}｜压缩包：{_diagnostic_text(attachment_name, 100)}"
                    else:
                        candidates = ((attachment_name, data, ""),)
                        file_context = context
                    for filename, payload, error in candidates:
                        if not filename:  # An archive outcome is not a PDF candidate.
                            if error:
                                count(source, "failed")
                                report_error(file_context, error)
                            else:
                                archives_without_pdf.append(attachment_name)
                            continue
                        attachment_count += 1
                        count(source, "attachments")
                        # Flatten ZIP members into the existing safe, collision-proof naming.
                        basename = filename.replace("\\", "/").rsplit("/", 1)[-1] if kind == "zip" else filename
                        attachment_stem = basename[:-4] if basename.lower().endswith(".pdf") else basename
                        stem = f"{MAIL_SOURCES[source]['name']}_{safe_filename(attachment_stem)[:120]}_{_sha256_bytes(payload or b'')[:12]}"
                        save_candidate(source, stem, payload, file_context, error, filename)
                if not attachment_count and result["failed"] == failures_before:
                    count(source, "failed")
                    if archives_without_pdf:
                        names = "、".join(_diagnostic_text(name, 100) for name in archives_without_pdf)
                        report_error(context, f"邮件未找到 PDF 附件，ZIP 压缩包中也未找到 PDF 文件（压缩包：{names}）")
                    else:
                        report_error(context, "邮件未找到 PDF 附件或含 PDF 的 ZIP 压缩包（暂不支持下载页面）")
                continue

            subject = decode_mime_header(str(msg.get("Subject", "")))
            order = extract_order_number(subject) or f"邮件{uid.decode(errors='ignore')}"
            link_index = 0
            message_urls = set()
            pdf_found = False
            failures_before = result["failed"]
            for body in extract_html_bodies(msg):
                parser = InvoiceLinkParser()
                try:
                    parser.feed(body)
                except Exception:
                    continue
                for link in parser.links:
                    url = link.get("url") or ""
                    if not url or url in message_urls:
                        continue
                    message_urls.add(url)
                    link_index += 1
                    if url in url_results:
                        has_pdf, error = url_results[url]
                        pdf_found |= has_pdf
                        if error:
                            count(source, "failed")
                            report_error(context, error)
                        continue
                    count(source, "links")
                    invoice_no = extract_invoice_number(link.get("context", ""))
                    stem = f"{order}_{invoice_no}" if invoice_no else f"{order}_发票{link_index}"
                    if progress:
                        progress(f"下载京东发票 {result['links']}...")
                    data, error = _download_pdf(url)
                    has_pdf = data is not None and data.startswith(b"%PDF-")
                    url_results[url] = (has_pdf, error)
                    pdf_found |= has_pdf
                    if data is not None or error:
                        save_candidate(source, stem, data, context, error)
            if not link_index:
                count(source, "failed")
                report_error(context, "京东邮件未找到发票 PDF 下载链接")
            elif not pdf_found and result["failed"] == failures_before:
                count(source, "failed")
                report_error(context, "京东邮件未取得 PDF：下载入口只返回了非 PDF 文件，已忽略")
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
