"""Outlook .msg / .oft 与 .eml 邮件的本地解析。

使用 MIT 许可的 python-oxmsg 读取 MSG 容器；只读取元数据、正文表格与附件二进制，
不访问网络、不执行宏。.pst / .ost 是邮箱数据库格式，需在 Outlook 中导出后再导入。
"""
from __future__ import annotations

import email
import email.policy
import html
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import config
from .utils import clean_text, safe_filename


class EmailError(Exception):
    """邮件解析失败。"""


class _TableCollector(HTMLParser):
    """提取正文顶层 HTML 表格，展开简单 colspan/rowspan。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._colspan = 1
        self._rowspan = 1
        self._pending_rowspans: dict[tuple[int, int], tuple[int, str]] = {}
        self._row_index = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
                self.tables.append(self._rows)
                self._row_index = 0
                self._pending_rowspans = {}
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
            self._colspan = 1
            self._rowspan = 1
        elif tag in ("td", "th"):
            self._cell = []
            try:
                self._colspan = max(1, min(int(attrs_dict.get("colspan", 1)), 20))
            except Exception:
                self._colspan = 1
            try:
                self._rowspan = max(1, min(int(attrs_dict.get("rowspan", 1)), 50))
            except Exception:
                self._rowspan = 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._rows = None
            return
        if self._depth != 1:
            return
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = re.sub(r"\s+", " ", html.unescape("".join(self._cell))).strip()
            col = len(self._row)
            # 先补上被上方 rowspan 占用的列。
            while (self._row_index, col) in self._pending_rowspans:
                remaining, value = self._pending_rowspans.pop((self._row_index, col))
                self._row.append(value)
                if remaining > 1:
                    self._pending_rowspans[(self._row_index + 1, col)] = (remaining - 1, value)
                col += 1
            self._row.append(text)
            for offset in range(1, self._colspan):
                self._row.append("")
            if self._rowspan > 1:
                for offset in range(self._colspan):
                    self._pending_rowspans[(self._row_index + 1, col + offset)] = (self._rowspan - 1, text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            # 行尾补齐 rowspan 单元格
            col = len(self._row)
            while (self._row_index, col) in self._pending_rowspans:
                remaining, value = self._pending_rowspans.pop((self._row_index, col))
                self._row.append(value)
                if remaining > 1:
                    self._pending_rowspans[(self._row_index + 1, col)] = (remaining - 1, value)
                col += 1
            if any(cell for cell in self._row) and self._rows is not None:
                self._rows.append(self._row)
            self._row_index += 1
            self._row = None

    def handle_data(self, data):
        if self._depth == 1 and self._cell is not None:
            self._cell.append(data)


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextCollector()
    try:
        parser.feed(markup or "")
    except Exception:
        return re.sub(r"<[^>]+>", " ", markup or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_html_tables(markup: str) -> list[list[list[str]]]:
    parser = _TableCollector()
    try:
        parser.feed(markup or "")
    except Exception:
        return []
    cleaned = []
    for table in parser.tables[:6]:
        rows = []
        for row in table[:3000]:
            cells = [clean_text(c) for c in row]
            if any(cells):
                rows.append(cells)
        if len(rows) < 2:
            continue
        width = max(len(row) for row in rows)
        if width < 2:
            continue
        rows = [(row + [""] * width)[:width] for row in rows]
        cleaned.append(rows)
    return cleaned


def _decode_mime(value: Any) -> str:
    if not value:
        return ""
    try:
        return clean_text(str(make_header(decode_header(str(value)))))
    except Exception:
        return clean_text(value)


def _date_text(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return clean_text(value.isoformat(sep=" ", timespec="seconds"))
        except Exception:
            return clean_text(value)
    try:
        parsed = parsedate_to_datetime(str(value))
        return clean_text(parsed.isoformat(sep=" ", timespec="seconds"))
    except Exception:
        return clean_text(value)


def _parse_msg(path: Path) -> dict:
    try:
        from oxmsg import Message
    except ImportError as exc:  # pragma: no cover
        raise EmailError("缺少 Outlook 邮件读取组件 python-oxmsg") from exc
    try:
        message = Message.load(str(path))
        html_body = ""
        try:
            html_body = message.html_body or ""
        except Exception:
            html_body = ""
        body = ""
        try:
            body = message.body or ""
        except Exception:
            body = ""
        warnings: list[str] = []
        try:
            subject = clean_text(message.subject) or path.stem
        except Exception:
            subject = path.stem
        try:
            sender = clean_text(message.sender)
        except Exception:
            sender = ""
        try:
            sent_date = _date_text(message.sent_date)
        except Exception:
            sent_date = ""
        attachments: list[dict] = []
        ignored = 0
        try:
            raw_attachments = list(message.attachments)
        except Exception:
            raw_attachments = []
        if len(raw_attachments) > config.MAX_ATTACHMENTS_PER_EMAIL:
            warnings.append(
                f"邮件附件超过 {config.MAX_ATTACHMENTS_PER_EMAIL} 个，只读取前 {config.MAX_ATTACHMENTS_PER_EMAIL} 个"
            )
        for attachment in raw_attachments[:config.MAX_ATTACHMENTS_PER_EMAIL]:
            try:
                name = safe_filename(str(attachment.file_name or ""), "邮件附件")
                payload = attachment.file_bytes
                if not isinstance(payload, (bytes, bytearray)) or not payload:
                    ignored += 1
                    continue
                payload = bytes(payload)
                if len(payload) > config.MAX_FILE_BYTES:
                    warnings.append(f"附件“{name}”超过 80 MB，已跳过")
                    ignored += 1
                    continue
                attachments.append({"name": name, "bytes": payload})
            except Exception:
                ignored += 1
        if ignored:
            warnings.append(f"另有 {ignored} 个附件无法读取（可能是内嵌邮件或特殊对象）")
        return {
            "subject": subject,
            "sender": sender,
            "date": sent_date,
            "body_text": clean_text(body or html_to_text(html_body))[:600],
            "tables": extract_html_tables(html_body),
            "attachments": attachments,
            "warnings": warnings,
        }
    except EmailError:
        raise
    except Exception as exc:
        raise EmailError(f"Outlook 邮件读取失败：{exc}") from exc


def _parse_eml(path: Path) -> dict:
    try:
        data = path.read_bytes()
        message = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:
        raise EmailError(f"邮件文件读取失败：{exc}") from exc
    html_body = ""
    text_body = ""
    try:
        html_part = message.get_body(preferencelist=("html",))
        if html_part:
            html_body = html_part.get_content()
    except Exception:
        pass
    try:
        text_part = message.get_body(preferencelist=("plain",))
        if text_part:
            text_body = text_part.get_content()
    except Exception:
        pass
    if not html_body:
        try:
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    html_body = part.get_content()
                    break
        except Exception:
            pass
    attachments = []
    warnings: list[str] = []
    ignored = 0
    count = 0
    for part in message.walk():
        try:
            filename = part.get_filename()
            if not filename:
                continue
            if count >= config.MAX_ATTACHMENTS_PER_EMAIL:
                ignored += 1
                continue
            count += 1
            name = safe_filename(_decode_mime(filename), "邮件附件")
            payload = part.get_payload(decode=True)
            if not payload:
                ignored += 1
                continue
            if len(payload) > config.MAX_FILE_BYTES:
                warnings.append(f"附件“{name}”超过 80 MB，已跳过")
                ignored += 1
                continue
            attachments.append({"name": name, "bytes": payload})
        except Exception:
            ignored += 1
    if ignored:
        warnings.append(f"另有 {ignored} 个附件无法读取或超出数量限制")
    return {
        "subject": _decode_mime(message.get("Subject", "")) or path.stem,
        "sender": _decode_mime(message.get("From", "")),
        "date": _date_text(message.get("Date", "")),
        "body_text": clean_text(text_body or html_to_text(html_body))[:600],
        "tables": extract_html_tables(html_body),
        "attachments": attachments,
        "warnings": warnings,
    }


def parse_email(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix in {".pst", ".ost"}:
        raise EmailError(
            "暂不直接解析 .pst / .ost 邮箱数据库。请在 Outlook 中将目标邮件另存为 .msg，"
            "或把 Excel 附件直接拖入本页。"
        )
    if suffix in {".msg", ".oft"}:
        return _parse_msg(path)
    if suffix == ".eml":
        return _parse_eml(path)
    raise EmailError("不是可识别的 Outlook 邮件格式")
