"""表头识别、字段映射建议与映射合法性校验。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from .fields import (
    AMOUNT_EXPENSE_ALIASES,
    AMOUNT_INCOME_ALIASES,
    DIRECTION_ALIASES,
    FIELD_DEFS,
    FIELD_LABELS,
)
from .utils import clean_text, direction_looks_like, sha256_text

_STRIP_RE = re.compile(r"[\s_\-—–·•:：/\\,，.。()（）\[\]【】<>《》#*]+")
_TRAILING_UNIT_RE = re.compile(r"(\(元\)|（元）|人民币元|rmb|cny)$", re.IGNORECASE)


def normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    text = _STRIP_RE.sub("", text)
    text = _TRAILING_UNIT_RE.sub("", text)
    return text


def header_signature(headers: Iterable[Any]) -> str:
    normalized = "\x1f".join(normalize_header(h) for h in headers)
    return sha256_text(normalized)


def _find_header(headers_norm: list[str], aliases: Iterable[str], used: set[int],
                 *, allow_partial: bool = True) -> tuple[int | None, int]:
    """返回 (列索引, 匹配分数)。精确匹配优先，其次同义词包含。"""
    normalized_aliases = sorted({normalize_header(a) for a in aliases if normalize_header(a)}, key=len, reverse=True)
    best_index: int | None = None
    best_score = 0
    for index, header in enumerate(headers_norm):
        if not header or index in used:
            continue
        for alias in normalized_aliases:
            score = 0
            if header == alias:
                score = 120 + min(len(alias), 12)
            elif allow_partial and len(alias) >= 2 and alias in header:
                score = 70 + min(len(alias), 12)
                # “金额说明”“类型备注”之类通常是补充列，降权。
                if header.endswith(("说明", "备注", "描述")):
                    score -= 28
            elif allow_partial and len(header) >= 2 and header in alias:
                score = 55 + min(len(header), 10)
            if score > best_score:
                best_index, best_score = index, score
    return best_index, best_score


def _direction_header(headers_norm: list[str], used: set[int]) -> int | None:
    index, _ = _find_header(headers_norm, DIRECTION_ALIASES, used)
    return index


def _direction_sample_column(headers_norm: list[str], sample_rows: list[list[Any]], used: set[int]) -> int | None:
    """没有明确表头时，用样例值识别“收入/支出”列。"""
    width = len(headers_norm)
    candidates: list[tuple[int, float, int]] = []
    for index in range(width):
        if index in used:
            continue
        values = [row[index] if index < len(row) else "" for row in sample_rows[:12]]
        nonempty = [v for v in values if clean_text(v)]
        if len(nonempty) < 2:
            continue
        hits = sum(1 for value in nonempty if direction_looks_like(value))
        ratio = hits / len(nonempty)
        if ratio >= 0.7:
            candidates.append((index, ratio, hits))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return candidates[0][0]


def _find_split_amount(headers_norm: list[str], used: set[int]) -> dict | None:
    income_index = expense_index = None
    for index, header in enumerate(headers_norm):
        if not header or index in used:
            continue
        if income_index is None and header in {normalize_header(a) for a in AMOUNT_INCOME_ALIASES}:
            income_index = index
        if expense_index is None and header in {normalize_header(a) for a in AMOUNT_EXPENSE_ALIASES}:
            expense_index = index
    if income_index is not None and expense_index is not None and income_index != expense_index:
        return {"mode": "split", "income_column": income_index, "expense_column": expense_index}
    return None


def _name_hint(file_name: str, sheet_name: str) -> str | None:
    text = f"{file_name} {sheet_name}"
    has_income = any(token in text for token in ("收入", "收款", "回款", "进账", "销售"))
    has_expense = any(token in text for token in ("支出", "费用", "付款", "报销", "采购"))
    if has_income and not has_expense:
        return "income"
    if has_expense and not has_income:
        return "expense"
    return None


def suggest_mapping(
    headers: list[Any],
    sample_rows: list[list[Any]] | None = None,
    file_name: str = "",
    sheet_name: str = "",
) -> dict[str, Any]:
    """基于表头同义词和样例值给出可用的初始映射。"""
    sample_rows = sample_rows or []
    headers_norm = [normalize_header(h) for h in headers]
    used: set[int] = set()
    notes: list[str] = []

    split_amount = _find_split_amount(headers_norm, used)
    fields: dict[str, int | None] = {item["key"]: None for item in FIELD_DEFS}

    if split_amount:
        used.update({split_amount["income_column"], split_amount["expense_column"]})
        notes.append("检测到收入、支出两列金额，将自动拆分为两笔记录")
        direction = {"mode": "split", "column": None, "value": None}
    else:
        direction_col = _direction_header(headers_norm, used)
        if direction_col is None:
            direction_col = _direction_sample_column(headers_norm, sample_rows, used)
            if direction_col is not None:
                notes.append("通过样例值识别出收支类型列")
        if direction_col is not None:
            used.add(direction_col)
            direction = {"mode": "column", "column": direction_col, "value": None}
        else:
            guessed = _name_hint(file_name, sheet_name)
            direction = {"mode": "fixed" if guessed else "unset", "column": None, "value": guessed}
            if guessed:
                notes.append("未找到收支类型列，已根据文件名预设为整表" + ("收入" if guessed == "income" else "支出"))

    for item in FIELD_DEFS:
        key = item["key"]
        if key == "amount" and split_amount:
            continue
        index, score = _find_header(headers_norm, item["aliases"], used)
        if index is not None:
            fields[key] = index
            used.add(index)
            if score >= 120:
                notes.append(f"已匹配 {FIELD_LABELS[key]} ←「{clean_text(headers[index])}」")
            else:
                notes.append(f"建议 {FIELD_LABELS[key]} ←「{clean_text(headers[index])}」，请确认")

    if split_amount:
        amount = split_amount
    elif fields.get("amount") is not None:
        amount = {"mode": "single", "column": fields["amount"]}
    else:
        amount = {"mode": "single", "column": None}
    fields.pop("amount", None)

    # 有些“收入金额”单列文件：把金额列识别为金额，并预设为收入。
    if not split_amount and amount.get("column") is not None:
        header = headers_norm[amount["column"]]
        if direction["mode"] == "unset":
            if header in {normalize_header(a) for a in AMOUNT_INCOME_ALIASES}:
                direction = {"mode": "fixed", "column": None, "value": "income"}
                notes.append("“金额”列来自收入金额，已预设为整表收入")
            elif header in {normalize_header(a) for a in AMOUNT_EXPENSE_ALIASES}:
                direction = {"mode": "fixed", "column": None, "value": "expense"}
                notes.append("“金额”列来自支出金额，已预设为整表支出")

    unresolved = []
    if fields.get("date") is None:
        unresolved.append("业务日期")
    if amount.get("column") is None and amount.get("mode") != "split":
        unresolved.append("金额")
    if direction.get("mode") == "unset":
        unresolved.append("收支类型")
    if unresolved:
        notes.append("还需手动指定：" + "、".join(unresolved))

    mapping = {
        "version": 1,
        "fields": fields,
        "amount": amount,
        "direction": direction,
        "header_signature": header_signature(headers),
    }
    return {"mapping": mapping, "notes": notes, "unresolved": unresolved}


def _valid_column(value: Any, width: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < width


def validate_mapping(mapping: dict, headers: list[Any]) -> tuple[dict, list[str]]:
    """返回规范化后的映射和可展示错误列表。"""
    errors: list[str] = []
    width = len(headers)
    if not isinstance(mapping, dict):
        return {"version": 1, "fields": {}, "amount": {"mode": "single", "column": None},
                "direction": {"mode": "unset", "column": None, "value": None}}, ["字段映射格式不正确"]

    raw_fields = mapping.get("fields") or {}
    if not isinstance(raw_fields, dict):
        raw_fields = {}
    fields: dict[str, int | None] = {}
    used: dict[int, str] = {}
    from .fields import FIELD_MAP

    for key, value in raw_fields.items():
        if key not in FIELD_MAP:
            continue
        column = value if value is not None else None
        if column is not None:
            if not _valid_column(column, width):
                errors.append(f"{FIELD_LABELS.get(key, key)} 对应的源列不存在")
                column = None
            else:
                if column in used:
                    errors.append(f"同一源列不能同时映射到“{used[column]}”和“{FIELD_LABELS.get(key, key)}”")
                else:
                    used[column] = FIELD_LABELS.get(key, key)
        fields[key] = column
    for key in FIELD_MAP:
        fields.setdefault(key, None)

    amount_raw = mapping.get("amount") or {}
    amount_mode = amount_raw.get("mode", "single")
    if amount_mode not in {"single", "split"}:
        amount_mode = "single"
    if amount_mode == "split":
        income_column = amount_raw.get("income_column")
        expense_column = amount_raw.get("expense_column")
        if not _valid_column(income_column, width):
            errors.append("请选择正确的“收入金额”列")
            income_column = None
        if not _valid_column(expense_column, width):
            errors.append("请选择正确的“支出金额”列")
            expense_column = None
        if income_column is not None and income_column == expense_column:
            errors.append("收入金额列和支出金额列不能是同一列")
        for column, label in ((income_column, "收入金额"), (expense_column, "支出金额")):
            if column is not None:
                if column in used:
                    errors.append(f"“{label}”列与“{used[column]}”重复使用")
                else:
                    used[column] = label
        amount = {"mode": "split", "income_column": income_column, "expense_column": expense_column}
        direction = {"mode": "split", "column": None, "value": None}
    else:
        column = amount_raw.get("column")
        if not _valid_column(column, width):
            if column is not None:
                errors.append("金额对应的源列不存在")
            column = None
        else:
            if column in used:
                errors.append(f"金额列与“{used[column]}”重复使用")
            else:
                used[column] = "金额"
        amount = {"mode": "single", "column": column}
        direction_raw = mapping.get("direction") or {}
        mode = direction_raw.get("mode", "unset")
        if mode not in {"column", "fixed", "unset"}:
            mode = "unset"
        if mode == "column":
            column = direction_raw.get("column")
            if not _valid_column(column, width):
                errors.append("请选择正确的收支类型列")
                column = None
            elif column in used:
                errors.append(f"收支类型列与“{used[column]}”重复使用")
                column = None
            else:
                used[column] = "收支类型"
            direction = {"mode": "column", "column": column, "value": None}
        elif mode == "fixed":
            value = direction_raw.get("value")
            if value not in {"income", "expense"}:
                errors.append("请选择整表对应的收支类型")
                value = None
            direction = {"mode": "fixed", "column": None, "value": value}
        else:
            direction = {"mode": "unset", "column": None, "value": None}

    if fields.get("date") is None:
        errors.append("请选择“业务日期”对应的源列")
    if amount_mode == "single" and amount.get("column") is None:
        errors.append("请选择“金额”对应的源列")
    if amount_mode == "single" and direction.get("mode") == "unset":
        errors.append("请选择收支类型的来源（按列识别或整表固定）")

    result = {
        "version": 1,
        "fields": fields,
        "amount": amount,
        "direction": direction,
        "header_signature": header_signature(headers),
    }
    # 去重，保持提示顺序。
    errors = list(dict.fromkeys(errors))
    return result, errors


def mapping_summary(mapping: dict, headers: list[Any]) -> list[dict]:
    """给前端展示映射摘要。"""
    fields = mapping.get("fields") or {}
    amount = mapping.get("amount") or {}
    items = []
    for item in FIELD_DEFS:
        key = item["key"]
        if key == "amount":
            if amount.get("mode") == "split":
                source = " + ".join(
                    clean_text(headers[c]) for c in (amount.get("income_column"), amount.get("expense_column"))
                    if isinstance(c, int) and 0 <= c < len(headers)
                )
            else:
                c = amount.get("column")
                source = clean_text(headers[c]) if isinstance(c, int) and 0 <= c < len(headers) else ""
        else:
            c = fields.get(key)
            source = clean_text(headers[c]) if isinstance(c, int) and 0 <= c < len(headers) else ""
        items.append({"key": key, "label": item["label"], "source": source})
    return items
