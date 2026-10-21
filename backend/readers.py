"""Excel / CSV / xls 读取与表头识别。

所有表格统一为：
{
  "headers": ["日期", ...], "header_row": 2, "rows": [{"n": 3, "c": ["2026-08-01", ...]}],
  "warnings": [...], "top_rows": [...]
}
n 为原始文件中的行号，便于异常定位。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import config
from .fields import DIRECTION_ALIASES, FIELD_DEFS
from .mapping import normalize_header
from .utils import clean_text, display_number

log = logging.getLogger(__name__)


class ReaderError(Exception):
    """文件层面的可展示读取错误。"""


_ALIAS_TOKENS = set()
for _field in FIELD_DEFS:
    for _alias in _field["aliases"]:
        token = normalize_header(_alias)
        if token:
            _ALIAS_TOKENS.add(token)
for _alias in DIRECTION_ALIASES:
    token = normalize_header(_alias)
    if token:
        _ALIAS_TOKENS.add(token)


def _cell_value(value: Any) -> str:
    """把单元格统一转为可 JSON 化的文本；数值精确保留。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, bool):
        return "是" if value else "否"
    return display_number(value)


def _looks_numeric(text: str) -> bool:
    if not text:
        return False
    cleaned = re.sub(r"[,\s¥￥$%]", "", text)
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned))


def _looks_date(text: str) -> bool:
    return bool(re.fullmatch(r"\d{4}[-/.年]\d{1,2}([-/.月]\d{1,2}日?)?", text.strip()))


def _score_header(values: list[Any], row_index: int, rows: list[tuple[int, list[Any]]]) -> float:
    texts = [clean_text(v) for v in values]
    nonempty = [t for t in texts if t]
    if len(nonempty) < 2:
        base = -12
    else:
        normalized = [normalize_header(t) for t in nonempty]
        known = sum(1 for token in normalized if token in _ALIAS_TOKENS)
        unique = len(set(normalized)) / len(normalized)
        text_like = sum(1 for t in nonempty if not _looks_numeric(t) and not _looks_date(t))
        text_ratio = text_like / len(nonempty)
        below = 0
        if row_index + 1 < len(rows):
            below = sum(1 for v in rows[row_index + 1][1] if clean_text(v))
        numeric_penalty = sum(1 for t in nonempty if _looks_numeric(t) or _looks_date(t)) * 1.5
        base = known * 6 + min(len(nonempty), 14) * 0.7 + unique * 2.4 + text_ratio * 2.2
        base += min(below, 12) * 0.35 - numeric_penalty - row_index * 0.12
        if known == 0 and text_ratio < 0.7:
            base -= 10
        if len(nonempty) > 40:
            base -= 3
    return base


def _detect_header(rows: list[tuple[int, list[Any]]]) -> tuple[int, list[str]] | None:
    """在前 20 个非空行中找最像表头的一行。"""
    if not rows:
        return None
    limit = min(len(rows), 20)
    scores: list[tuple[float, int]] = []
    for idx in range(limit):
        values = rows[idx][1]
        # 表头行上方不可能有太多有效数据；标题行通常只有一个非空格。
        if sum(1 for v in values if clean_text(v)) == 0:
            continue
        scores.append((_score_header(values, idx, rows), idx))
    if not scores:
        return None
    scores.sort(reverse=True)
    best_idx = scores[0][1]
    # 若最高分低于合理阈值，仍取第一个有 2 个以上非空单元格的行。
    if scores[0][0] < 4:
        for idx in range(limit):
            if sum(1 for v in rows[idx][1] if clean_text(v)) >= 2:
                best_idx = idx
                break
    values = rows[best_idx][1]
    width = max(
        [len(values)] + [len(r[1]) for r in rows[best_idx + 1:best_idx + 21]]
    )
    width = min(max(width, 1), config.MAX_TABLE_COLS)
    headers: list[str] = []
    seen: dict[str, int] = {}
    for column in range(width):
        raw = values[column] if column < len(values) else ""
        label = clean_text(raw) or f"第 {column + 1} 列"
        label = re.sub(r"\s+", " ", label)[:120]
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label}（{seen[label]}）"
        headers.append(label)
    return best_idx, headers


def build_table_from_raw(
    raw_rows: Iterable[tuple[int, list[Any]]],
    *,
    sheet_name: str,
    hidden: bool = False,
    warnings: list[str] | None = None,
    max_rows: int | None = None,
) -> dict | None:
    """把原始行（行号 + 单元格）整理成标准表。"""
    warnings = list(warnings or [])
    rows: list[tuple[int, list[Any]]] = []
    for row_number, values in raw_rows:
        vals = list(values or [])
        while vals and clean_text(vals[-1]) == "":
            vals.pop()
        if not any(clean_text(v) for v in vals):
            continue
        if len(vals) > config.MAX_TABLE_COLS:
            raise ReaderError(f"工作表“{sheet_name}”超过 {config.MAX_TABLE_COLS} 列，请拆分后再导入")
        rows.append((row_number, vals))
        if len(rows) > (max_rows or config.MAX_TABLE_ROWS) + 80:
            raise ReaderError(
                f"工作表“{sheet_name}”超过 {config.MAX_TABLE_ROWS:,} 行。"
                "为保证汇总准确，请按年度或部门拆分后再导入。"
            )
    if len(rows) < 2:
        return None
    detected = _detect_header(rows)
    if not detected:
        return None
    header_index, headers = detected
    data = []
    for row_number, values in rows[header_index + 1:]:
        padded = [_cell_value(v) for v in values[:len(headers)]]
        if len(padded) < len(headers):
            padded += [""] * (len(headers) - len(padded))
        if not any(padded):
            continue
        data.append({"n": row_number, "c": padded})
    if not data:
        return None
    if len(data) != len(rows) - header_index - 1:
        warnings.append(f"忽略了 {len(rows) - header_index - 1 - len(data)} 行空白数据")
    if hidden:
        warnings.append("这是隐藏工作表，请确认是否需要导入")
    top_rows = []
    for row_number, values in rows[:12]:
        cells = [_cell_value(v) for v in values[:len(headers)]]
        cells += [""] * (len(headers) - len(cells))
        top_rows.append({"n": row_number, "c": cells})
    return {
        "sheet_name": sheet_name,
        "headers": headers,
        "header_row": rows[header_index][0],
        "rows": data,
        "top_rows": top_rows,
        "warnings": warnings,
        "hidden": hidden,
    }


def _read_xlsx(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ReaderError("缺少 Excel 读取组件 openpyxl，无法导入 .xlsx 文件") from exc
    try:
        workbook = load_workbook(filename=str(path), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise ReaderError(f"无法打开 Excel 文件：{exc}") from exc
    tables: list[dict] = []
    try:
        sheets = list(workbook.worksheets)
        if len(sheets) > config.MAX_SHEETS:
            raise ReaderError(f"文件含 {len(sheets)} 个工作表，超过 {config.MAX_SHEETS} 个，请拆分后再导入")
        for sheet in sheets:
            if getattr(sheet, "sheet_state", "visible") not in {"visible", "hidden", "veryHidden"}:
                continue
            raw_rows = []
            try:
                for row_number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                    if not values or not any(clean_text(v) for v in values):
                        continue
                    raw_rows.append((row_number, list(values)))
                    if len(raw_rows) > config.MAX_TABLE_ROWS + 100:
                        raise ReaderError(
                            f"工作表“{sheet.title}”超过 {config.MAX_TABLE_ROWS:,} 行。"
                            "为保证汇总准确，请拆分后再导入。"
                        )
            except ReaderError:
                raise
            except Exception as exc:
                raise ReaderError(f"读取工作表“{sheet.title}”失败：{exc}") from exc
            table = build_table_from_raw(
                raw_rows,
                sheet_name=sheet.title,
                hidden=getattr(sheet, "sheet_state", "visible") != "visible",
            )
            if table:
                tables.append(table)
    finally:
        try:
            workbook.close()
        except Exception:
            pass
    return tables


def _read_xls(path: Path) -> list[dict]:
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover
        raise ReaderError("缺少旧版 Excel 读取组件 xlrd，无法导入 .xls 文件") from exc
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:
        raise ReaderError(f"无法打开 .xls 文件：{exc}") from exc
    tables: list[dict] = []
    try:
        if book.nsheets > config.MAX_SHEETS:
            raise ReaderError(f"文件含 {book.nsheets} 个工作表，超过 {config.MAX_SHEETS} 个，请拆分后再导入")
        for sheet in book.sheets():
            raw_rows = []
            hidden = bool(getattr(sheet, "visibility", 0))
            for row_index in range(sheet.nrows):
                values = []
                for cell in sheet.row(row_index):
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            values.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        except Exception:
                            values.append(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        values.append(bool(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_EMPTY:
                        values.append("")
                    else:
                        values.append(cell.value)
                if not any(clean_text(v) for v in values):
                    continue
                raw_rows.append((row_index + 1, values))
                if len(raw_rows) > config.MAX_TABLE_ROWS + 100:
                    raise ReaderError(
                        f"工作表“{sheet.name}”超过 {config.MAX_TABLE_ROWS:,} 行，请拆分后再导入"
                    )
            table = build_table_from_raw(raw_rows, sheet_name=sheet.name, hidden=hidden)
            if table:
                tables.append(table)
    finally:
        try:
            book.release_resources()
        except Exception:
            pass
    return tables


def _decode_csv(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
    for encoding in ("utf-8-sig", "gb18030", "utf-8", "big5"):
        try:
            text = data.decode(encoding)
            if "\ufffd" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_delimited(path: Path) -> list[dict]:
    text = _decode_csv(path)
    if not text.strip():
        raise ReaderError("文件内容为空")
    sample = text[:8192]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except Exception:
        counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
        delimiter = max(counts, key=counts.get) if max(counts.values()) > 0 else ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except Exception as exc:
        raise ReaderError(f"CSV 内容无法解析：{exc}") from exc
    raw_rows = []
    for idx, values in enumerate(rows, start=1):
        values = [clean_text(v) for v in values]
        if not any(values):
            continue
        raw_rows.append((idx, values))
        if len(raw_rows) > config.MAX_TABLE_ROWS + 100:
            raise ReaderError(f"文件超过 {config.MAX_TABLE_ROWS:,} 行，请拆分后再导入")
    table = build_table_from_raw(raw_rows, sheet_name="CSV 数据")
    if not table:
        raise ReaderError("没有找到“表头 + 数据行”的有效结构，请检查文件内容")
    return [table]


def read_tabular_file(path: Path) -> list[dict]:
    """读取一个 Excel/CSV 文件，返回其中的全部有效数据表。"""
    suffix = path.suffix.lower()
    if suffix not in config.SUPPORTED_TABULAR:
        raise ReaderError(f"暂不支持 {suffix or '该'} 格式，请使用 .xlsx / .xls / .csv")
    if path.stat().st_size > config.MAX_FILE_BYTES:
        raise ReaderError("单个文件超过 80 MB，请拆分后再导入")
    if suffix in {".xlsx", ".xlsm"}:
        tables = _read_xlsx(path)
    elif suffix == ".xls":
        tables = _read_xls(path)
    else:
        tables = _read_delimited(path)
    tables = [table for table in tables if table and table.get("rows")]
    if not tables:
        raise ReaderError("没有找到可导入的数据表（需要表头行和至少一行数据）")
    return tables


def reheader_table(table: dict, new_header_row: int) -> dict:
    """用用户指定的行作为表头，重建表结构。

    仅支持前 12 行内的调整；所有原始行均已保存在 top_rows + data 中。
    """
    combined: dict[int, list[Any]] = {}
    for row in table.get("top_rows", []):
        combined[int(row["n"])] = list(row.get("c") or [])
    for row in table.get("rows", []):
        combined[int(row["n"])] = list(row.get("c") or [])
    raw_rows = [(number, combined[number]) for number in sorted(combined)]
    # 把目标表头之前没有进入 top_rows 的行补齐为可能的标题行，仅保留空行之外的内容。
    rebuilt = build_table_from_raw(
        raw_rows,
        sheet_name=table.get("sheet_name") or "数据",
        hidden=bool(table.get("hidden")),
        warnings=[],
    )
    if not rebuilt:
        raise ReaderError("该行之后没有可用数据，无法作为表头")
    # build 会自动检测；若目标行不是检测结果，则手工按目标行重排。
    if rebuilt.get("header_row") == new_header_row:
        return rebuilt
    target = combined.get(new_header_row)
    if not target:
        raise ReaderError("指定的表头行不在可调整范围内")
    width = max([len(target)] + [len(combined[n]) for n in combined if n > new_header_row])
    width = min(max(width, 1), config.MAX_TABLE_COLS)
    headers = []
    seen = {}
    for col in range(width):
        raw = target[col] if col < len(target) else ""
        label = clean_text(raw) or f"第 {col + 1} 列"
        seen[label] = seen.get(label, 0) + 1
        headers.append(f"{label}（{seen[label]}）" if seen[label] > 1 else label)
    data = []
    for number in sorted(combined):
        if number <= new_header_row:
            continue
        cells = [_cell_value(v) for v in combined[number][:width]]
        cells += [""] * (width - len(cells))
        if any(cells):
            data.append({"n": number, "c": cells})
    if not data:
        raise ReaderError("该行之后没有数据行")
    top_rows = []
    for number in sorted(combined)[:12]:
        cells = [_cell_value(v) for v in combined[number][:width]]
        cells += [""] * (width - len(cells))
        top_rows.append({"n": number, "c": cells})
    result = dict(table)
    result.update({
        "headers": headers,
        "header_row": new_header_row,
        "rows": data,
        "top_rows": top_rows,
        "warnings": list(table.get("warnings") or []) + ["已手动调整表头行"],
    })
    return result
