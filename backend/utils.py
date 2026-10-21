"""通用工具：文本清洗、日期/金额解析、判重指纹、期间计算。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class FieldError(ValueError):
    """单个字段无法转换为标准值。"""


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(value: Any, multiline: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _CTRL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = _SPACE_RE.sub(" ", text).strip()
    return text


def normalize_key(value: Any) -> str:
    """用于匹配/判重的键：全半角统一、去空白、数值 1.0 -> 1。"""
    text = clean_text(value).casefold()
    text = re.sub(r"\s+", "", text)
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".")[0]
    return text


def display_number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")[:19] if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return format(value, ".10g")
    return clean_text(value, multiline=True)


def display_path_name(path: Path) -> str:
    return path.name


def safe_filename(name: str, fallback: str = "文件") -> str:
    """生成可安全落盘且保留中文的 Windows 文件名。"""
    raw = clean_text(name) or fallback
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = re.sub(r'[<>:"/\\|?*]', "_", raw)
    raw = raw.strip(" .")
    suffix = Path(raw).suffix
    stem = raw[: -len(suffix)] if suffix else raw
    stem = stem[:100] or fallback
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    safe_suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)[:12].lower()
    return f"{stem}{safe_suffix}"


def unique_filename(folder: Path, filename: str) -> Path:
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    suffix = candidate.suffix
    stem = candidate.stem
    for index in range(2, 1000):
        alt = folder / f"{stem}_{index}{suffix}"
        if not alt.exists():
            return alt
    return folder / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"


def truncate_text(value: Any, field: str, limit: int, multiline: bool = False) -> tuple[str, str | None]:
    text = clean_text(value, multiline=multiline)
    if len(text) > limit:
        return text[:limit], f"{field}超过 {limit} 个字符，已截断"
    return text, None


def human_size(num: int | float) -> str:
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def chunked(items: Sequence[Any], size: int = 800) -> Iterator[list[Any]]:
    for index in range(0, len(items), size):
        yield list(items[index:index + size])


_EXCEL_EPOCH = datetime(1899, 12, 30)
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y%m%d", "%Y%m", "%Y.%m.%d", "%Y.%m", "%d-%m-%Y")


def parse_date_value(value: Any) -> tuple[str, bool, str | None]:
    """返回 (ISO 日期, 是否仅精确到月, 提示)。无法识别的日期抛 FieldError。"""
    if value is None or clean_text(value) == "":
        raise FieldError("日期为空")
    if isinstance(value, datetime):
        return value.date().isoformat(), False, None
    if isinstance(value, date):
        return value.isoformat(), False, None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = float(value)
        if 1900 <= number <= 2100 and number.is_integer():
            # 纯年份不猜测月份。
            raise FieldError("日期只有年份，请补充月日")
        if 20000 <= number <= 80000:
            parsed = _EXCEL_EPOCH + timedelta(days=number)
            return parsed.date().isoformat(), False, None

    text = clean_text(value)
    original = text
    if re.fullmatch(r"\d{5}(?:\.0+)?", text):
        serial = float(text)
        if 20000 <= serial <= 80000:
            parsed = _EXCEL_EPOCH + timedelta(days=serial)
            return parsed.date().isoformat(), False, None
    if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日?", original):
        text = re.sub(r"年|月", "-", original).replace("日", "")
    elif re.fullmatch(r"\d{4}年\d{1,2}月?", original):
        text = re.sub(r"年", "-", original).replace("月", "-01")
    else:
        text = text.replace("年", "-").replace("月", "-").replace("日", "")
        text = text.replace("/", "-").replace(".", "-")
        text = re.sub(r"(\d{4}-\d{1,2}-\d{1,2})[ T].*$", r"\1", text)

    month_only = False
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in ("%Y-%m", "%Y%m"):
                month_only = True
                parsed = parsed.replace(day=1)
            if not 1990 <= parsed.year <= 2100:
                raise FieldError("日期超出合理范围（1990–2100 年）")
            warning = "日期仅精确到月份，已按当月 1 日记录" if month_only else None
            return parsed.date().isoformat(), month_only, warning
        except ValueError:
            continue
    raise FieldError(f"日期“{original[:24]}”无法识别，请使用 2026-08-01 或 Excel 日期格式")


def parse_date_input(value: Any, label: str = "日期") -> str:
    """API 入参严格日期，避免模糊输入进入数据库。"""
    text = clean_text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise FieldError(f"{label}格式应为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise FieldError(f"{label}不是有效日期") from exc
    if not 1990 <= parsed.year <= 2100:
        raise FieldError(f"{label}超出合理范围")
    return text


_CURRENCY_RE = re.compile(r"(人民币|rmb|cny|元整|元|¥|￥|\$|,|，|'|_)", re.IGNORECASE)
_MONEY_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_money_cents(value: Any) -> tuple[int, list[str]]:
    """金额转为分，拒绝零、负数与无法识别的值。返回 (分, 提示)。"""
    warnings: list[str] = []
    if value is None or clean_text(value) == "":
        raise FieldError("金额为空")
    if isinstance(value, bool):
        raise FieldError("金额不是有效数字")
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise FieldError("金额不是有效数字")
        amount = Decimal(str(value))
    else:
        text = clean_text(value)
        negative_by_parentheses = text.startswith("(") and text.endswith(")")
        if negative_by_parentheses:
            text = text[1:-1]
        scale = Decimal(1)
        if re.search(r"(亿元|亿)$", text):
            scale = Decimal(100000000)
            text = re.sub(r"(亿元|亿)$", "", text)
        elif re.search(r"(万元|万)$", text):
            scale = Decimal(10000)
            text = re.sub(r"(万元|万)$", "", text)
            warnings.append("金额含“万”单位，已换算为元")
        text = _CURRENCY_RE.sub("", text).strip()
        if not _MONEY_RE.fullmatch(text):
            raise FieldError(f"金额“{clean_text(value)[:24]}”不是有效数字")
        amount = Decimal(text) * scale
        if negative_by_parentheses:
            amount = -abs(amount)
    if amount == 0:
        raise FieldError("金额为 0，请确认是否漏填")
    if amount < 0:
        raise FieldError("金额为负数，请拆分收入/支出或在源表中填写正数")
    if amount >= Decimal("1000000000000"):
        raise FieldError("金额过大，请检查是否录错")
    cents = int((amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100).to_integral_value())
    if cents > 5000000000:
        warnings.append("金额较大（超过 5000 万元），请复核")
    return cents, warnings


_INCOME_TOKENS = {"收入", "收", "入", "入账", "收款", "实收", "流入", "贷", "贷方", "应收", "income", "revenue", "credit", "+", "1"}
_EXPENSE_TOKENS = {"支出", "支", "出", "付款", "实付", "流出", "借", "借方", "应付", "费用", "expense", "debit", "-", "-1", "2"}


def normalize_direction(value: Any) -> str:
    text = normalize_key(value)
    if not text:
        raise FieldError("收支类型为空")
    if text in _INCOME_TOKENS or text.startswith("收入") or text.startswith("收款"):
        return "income"
    if text in _EXPENSE_TOKENS or text.startswith("支出") or text.startswith("付款") or text.startswith("费用"):
        return "expense"
    raise FieldError(f"收支类型“{clean_text(value)[:16]}”无法识别，请用收入/支出")


def direction_looks_like(value: Any) -> bool:
    try:
        normalize_direction(value)
        return True
    except FieldError:
        return False


def direction_label(direction: str) -> str:
    return "收入" if direction == "income" else "支出"


def _effective_doc_no(doc_no: str) -> str:
    normalized = normalize_key(doc_no)
    if normalized in {"", "-", "--", "/", "无", "没有", "na", "n/a", "none"}:
        return ""
    return normalized


def build_fingerprint(
    *,
    date_text: str,
    direction: str,
    amount_cents: int,
    category: str,
    department: str,
    counterparty: str,
    note: str,
    doc_no: str,
) -> str:
    """业务判重指纹。

    有单据号：按“年份 + 收支 + 单据号 + 金额 + 类别”判重，既避免把同一单据的
    多个明细行误合并，也支持重复导入时更新部门、备注等辅助字段；
    无单据号：按完整业务字段判重，宁可拦截疑似重复，也不静默写入两遍。
    """
    doc = _effective_doc_no(doc_no)
    if doc:
        payload = ["v2", "doc", date_text[:4], direction, doc, amount_cents, normalize_key(category)]
    else:
        payload = [
            "v1", "row", date_text, direction, amount_cents,
            normalize_key(category), normalize_key(department), normalize_key(counterparty),
            normalize_key(note),
        ]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def csv_safe(value: Any) -> str:
    """防止 Excel 打开 CSV 时把单元格当公式执行。"""
    text = clean_text(value, multiline=True)
    if text.startswith(("=", "+", "-", "@", "\t")):
        return "'" + text
    return text


def parse_iso_date_or_none(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    return parse_date_input(value, label)


def add_months(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    # 取当月最后一天，保证 1 月 31 日 + 1 月落到 2 月末。
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def month_start(day: date) -> date:
    return day.replace(day=1)


def quarter_start(day: date) -> date:
    month = ((day.month - 1) // 3) * 3 + 1
    return date(day.year, month, 1)


def period_ranges(preset: str, start: str | None = None, end: str | None = None) -> dict:
    """计算当前期间与可比上期。所有边界按自然日、含首尾。"""
    today = date.today()
    preset = preset if preset in {"month", "quarter", "year", "all", "custom"} else "year"
    prev_start = prev_end = None
    prev_label = None

    if preset == "month":
        cur_start = month_start(today)
        cur_end = today
        prev_start = add_months(cur_start, -1)
        prev_end = add_months(today, -1)
        prev_label = "上月同期"
    elif preset == "quarter":
        cur_start = quarter_start(today)
        cur_end = today
        elapsed = (today - cur_start).days
        prev_start = add_months(cur_start, -3)
        prev_end = min(prev_start + timedelta(days=elapsed), add_months(prev_start, 3) - timedelta(days=1))
        prev_label = "上季度同期"
    elif preset == "year":
        cur_start = date(today.year, 1, 1)
        cur_end = today
        try:
            prev_start = date(today.year - 1, 1, 1)
            prev_end = date(today.year - 1, today.month, today.day)
        except ValueError:
            prev_end = date(today.year - 1, 2, 28)
        prev_label = "上年同期"
    elif preset == "all":
        cur_start = date(1990, 1, 1)
        cur_end = today
        prev_start = prev_end = None
    else:
        if not start or not end:
            raise FieldError("自定义期间需要开始和结束日期")
        cur_start = date.fromisoformat(parse_date_input(start, "开始日期"))
        cur_end = date.fromisoformat(parse_date_input(end, "结束日期"))
        if cur_start > cur_end:
            raise FieldError("开始日期不能晚于结束日期")
        span = (cur_end - cur_start).days + 1
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)
        prev_label = "上一个等长周期"

    labels = {"month": "本月", "quarter": "本季度", "year": "本年", "all": "全部", "custom": "自定义期间"}
    return {
        "preset": preset,
        "label": labels[preset],
        "start": cur_start.isoformat(),
        "end": cur_end.isoformat(),
        "previous_start": prev_start.isoformat() if prev_start else None,
        "previous_end": prev_end.isoformat() if prev_end else None,
        "previous_label": prev_label,
    }


def month_sequence(end_month: str, count: int = 12) -> list[str]:
    try:
        end_day = datetime.strptime(end_month, "%Y-%m").date().replace(day=1)
    except ValueError:
        end_day = date.today().replace(day=1)
    months = []
    for offset in range(count - 1, -1, -1):
        day = add_months(end_day, -offset)
        months.append(day.strftime("%Y-%m"))
    return months


def percent_change(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def relative_time(iso_text: str | None) -> str:
    if not iso_text:
        return ""
    try:
        parsed = datetime.fromisoformat(iso_text)
        if parsed.tzinfo:
            now = datetime.now().astimezone()
        else:
            now = datetime.now()
        seconds = int((now - parsed).total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        if seconds < 86400 * 7:
            return f"{seconds // 86400} 天前"
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return iso_text[:10]


def sum_cents(values: Iterable[int]) -> int:
    return sum(int(v or 0) for v in values)
