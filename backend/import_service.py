"""批量导入：会话暂存、字段映射、校验、判重与事务落库。"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from . import config, db, readers
from .email_reader import EmailError, parse_email
from .errors import AppError
from .fields import FIELD_DEFS, FIELD_LABELS
from .mapping import header_signature, suggest_mapping, validate_mapping
from .utils import (
    FieldError,
    build_fingerprint,
    chunked,
    clean_text,
    csv_safe,
    direction_label,
    direction_looks_like,
    human_size,
    new_id,
    normalize_direction,
    now_iso,
    parse_date_value,
    parse_money_cents,
    safe_filename,
    sha256_bytes,
    truncate_text,
    unique_filename,
)

log = logging.getLogger(__name__)
_COMMIT_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,80}$")

STATUS_LABELS = {
    "valid": "正常",
    "warning": "有提示",
    "duplicate": "疑似重复",
    "invalid": "异常",
}


# ---------------------------------------------------------------------------
# 会话存储
# ---------------------------------------------------------------------------

def _session_path(token: str) -> Path:
    if not _TOKEN_RE.fullmatch(token or ""):
        raise AppError("导入会话无效，请重新上传文件", 400, "invalid_session")
    path = config.STAGING_DIR / token / "session.json.gz"
    # 双重确认路径仍位于暂存目录内，避免路径穿越。
    try:
        path.resolve().relative_to(config.STAGING_DIR.resolve())
    except ValueError as exc:
        raise AppError("导入会话路径无效", 400, "invalid_session") from exc
    return path


def create_session_id() -> str:
    return secrets.token_urlsafe(24)


def save_session(session: dict) -> None:
    path = _session_path(session["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("session.json.gz.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as handle:
        json.dump(session, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def load_session(token: str) -> dict:
    path = _session_path(token)
    if not path.exists():
        raise AppError("导入会话不存在或已过期，请重新上传文件", 410, "session_expired")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            session = json.load(handle)
    except Exception as exc:
        log.exception("读取导入会话失败")
        raise AppError("导入会话已损坏，请重新上传文件", 410, "session_corrupt") from exc
    if session.get("id") != token:
        raise AppError("导入会话校验失败，请重新上传文件", 400, "invalid_session")
    return session


def discard_session(token: str) -> None:
    path = _session_path(token)
    folder = path.parent
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def cleanup_stale_sessions(active_token: str | None = None) -> None:
    now = time.time()
    if not config.STAGING_DIR.exists():
        return
    for folder in config.STAGING_DIR.iterdir():
        if not folder.is_dir() or folder.name == active_token:
            continue
        try:
            if now - folder.stat().st_mtime > config.SESSION_TTL_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


# ---------------------------------------------------------------------------
# 上传、邮件附件展开与数据表识别
# ---------------------------------------------------------------------------

def _email_fields(meta: dict, file_name: str) -> dict:
    return {
        "subject": clean_text(meta.get("subject"))[:200],
        "sender": clean_text(meta.get("sender"))[:200],
        "date": clean_text(meta.get("date"))[:60],
        "file_name": file_name,
    }


def _make_source(
    *,
    table: dict,
    file_record: dict,
    file_name: str,
    file_type: str,
    presence: str,
    email_meta: dict | None,
    source_index: int,
) -> dict:
    email_info = _email_fields(email_meta, file_record["name"]) if email_meta else None
    if presence == "body":
        display = f"{clean_text(email_info['subject'])[:50]} · {table['sheet_name']}"
    elif table.get("sheet_name") in {"CSV 数据", ""}:
        display = file_name
    else:
        display = f"{file_name} · {table['sheet_name']}"
    source = {
        "id": f"s{source_index}",
        "file_id": file_record["id"],
        "file_name": file_name,
        "file_type": file_type,
        "sheet_name": table.get("sheet_name") or "数据",
        "display_name": display,
        "presence": presence,
        "email": email_info,
        "headers": table["headers"],
        "header_row": table["header_row"],
        "rows": table["rows"],
        "top_rows": table.get("top_rows") or [],
        "warnings": table.get("warnings") or [],
        "hidden": bool(table.get("hidden")),
        "suggested_mapping": None,
        "mapping_notes": [],
        "mapping_source": "auto",
        "saved_profile": False,
    }
    return source


def _attach_suggestion(source: dict, profile_mapping: dict | None = None) -> None:
    sample = [row["c"] for row in source["rows"][:12]]
    suggestion = suggest_mapping(
        source["headers"], sample, source["file_name"], source["sheet_name"]
    )
    mapping = suggestion["mapping"]
    notes = list(suggestion["notes"])
    mapping_source = "auto"
    unresolved = suggestion.get("unresolved", [])
    if profile_mapping:
        validated, errors = validate_mapping(profile_mapping, source["headers"])
        if not errors:
            mapping = validated
            mapping_source = "profile"
            unresolved = []
            notes.insert(0, "已套用相同表头结构的历史字段映射")
    source["suggested_mapping"] = mapping
    source["mapping_notes"] = notes
    source["mapping_source"] = mapping_source
    source["saved_profile"] = mapping_source == "profile"
    source["unresolved"] = unresolved


def _lookup_profile(headers: list[Any]) -> dict | None:
    if not db.get_setting("remember_mapping", True):
        return None
    signature = header_signature(headers)
    row = db.get_db().execute(
        "SELECT mapping_json FROM mapping_profiles WHERE header_signature = ?", (signature,)
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["mapping_json"])
    except Exception:
        return None


def _add_table_sources(
    session: dict,
    tables: list[dict],
    *,
    file_record: dict,
    file_name: str,
    file_type: str,
    presence: str,
    email_meta: dict | None,
) -> int:
    added = 0
    for table in tables:
        index = len(session["sources"]) + 1
        source = _make_source(
            table=table,
            file_record=file_record,
            file_name=file_name,
            file_type=file_type,
            presence=presence,
            email_meta=email_meta,
            source_index=index,
        )
        _attach_suggestion(source, _lookup_profile(source["headers"]))
        session["sources"].append(source)
        added += 1
    return added


def _process_tabular_file(
    session: dict,
    path: Path,
    *,
    original_name: str,
    file_record: dict,
    presence: str = "file",
    email_meta: dict | None = None,
) -> int:
    try:
        tables = readers.read_tabular_file(path)
    except readers.ReaderError as exc:
        message = str(exc)
        file_record["status"] = "warning" if file_record["source_count"] else file_record["status"]
        file_record["message"] = message
        session["warnings"].append(f"{original_name}：{message}")
        return 0
    except Exception as exc:
        log.exception("读取表格文件失败：%s", path)
        message = f"读取失败：{exc}"
        file_record["status"] = "warning" if file_record["source_count"] else "error"
        file_record["message"] = message
        session["warnings"].append(f"{original_name}：{message}")
        return 0
    added = _add_table_sources(
        session,
        tables,
        file_record=file_record,
        file_name=original_name,
        file_type=path.suffix.lower().lstrip("."),
        presence=presence,
        email_meta=email_meta,
    )
    file_record["source_count"] += added
    file_record["sheet_count"] = len(tables)
    return added


def _process_email_file(
    session: dict,
    path: Path,
    *,
    original_name: str,
    file_record: dict,
    raw_dir: Path,
) -> int:
    try:
        meta = parse_email(path)
    except EmailError as exc:
        file_record["status"] = "error"
        file_record["message"] = str(exc)
        session["warnings"].append(f"{original_name}：{exc}")
        return 0
    except Exception as exc:
        log.exception("解析邮件失败：%s", path)
        file_record["status"] = "error"
        file_record["message"] = f"邮件解析失败：{exc}"
        session["warnings"].append(f"{original_name}：邮件解析失败")
        return 0

    email_meta = {
        "subject": meta["subject"],
        "sender": meta["sender"],
        "date": meta["date"],
    }
    file_record["kind"] = "email"
    file_record["email"] = {
        "subject": meta["subject"],
        "sender": meta["sender"],
        "date": meta["date"],
        "attachment_total": len(meta["attachments"]),
        "body_table_total": len(meta["tables"]),
    }
    file_record["warnings"] = list(meta.get("warnings") or [])
    session["warnings"].extend(f"{original_name}：{w}" for w in meta.get("warnings") or [])

    added = 0
    attachment_records = []
    for attachment in meta["attachments"]:
        name = safe_filename(attachment["name"], "邮件附件")
        suffix = Path(name).suffix.lower()
        attachment_record = {
            "name": name,
            "size": len(attachment["bytes"]),
            "status": "ignored",
            "message": "",
        }
        if suffix not in config.SUPPORTED_TABULAR:
            attachment_record["message"] = f"不是支持的表格格式（{suffix or '无扩展名'}）"
            attachment_records.append(attachment_record)
            continue
        target = unique_filename(raw_dir, f"{file_record['id']}_{name}")
        try:
            target.write_bytes(attachment["bytes"])
        except OSError as exc:
            attachment_record["status"] = "error"
            attachment_record["message"] = f"保存附件失败：{exc}"
            attachment_records.append(attachment_record)
            continue
        before = file_record["source_count"]
        _process_tabular_file(
            session,
            target,
            original_name=name,
            file_record=file_record,
            presence="attachment",
            email_meta=email_meta,
        )
        attachment_record["status"] = "imported" if file_record["source_count"] > before else "error"
        attachment_record["message"] = "" if file_record["source_count"] > before else "未识别到有效数据表"
        attachment_records.append(attachment_record)
        added += file_record["source_count"] - before

    # 邮件正文中的 HTML 表格同样可以作为数据表，适用于把数据直接粘贴到邮件的情况。
    for body_index, table_rows in enumerate(meta["tables"], start=1):
        try:
            raw_rows = []
            for row_number, values in enumerate(table_rows, start=1):
                if any(clean_text(v) for v in values):
                    raw_rows.append((row_number, list(values)))
            table = readers.build_table_from_raw(
                raw_rows, sheet_name=f"正文表格 {body_index}", warnings=[]
            )
        except Exception as exc:
            session["warnings"].append(f"{original_name}：正文表格 {body_index} 无法解析（{exc}）")
            continue
        if not table:
            continue
        added += _add_table_sources(
            session,
            [table],
            file_record=file_record,
            file_name=original_name,
            file_type="email-body",
            presence="body",
            email_meta=email_meta,
        )
        file_record["source_count"] = len(
            [s for s in session["sources"] if s["file_id"] == file_record["id"]]
        )

    file_record["source_count"] = len(
        [s for s in session["sources"] if s["file_id"] == file_record["id"]]
    )
    file_record["attachments"] = attachment_records
    if file_record["source_count"] == 0:
        file_record["status"] = "warning"
        if not file_record["message"]:
            file_record["message"] = "邮件中没有找到可导入的 Excel / CSV 附件或正文表格"
        session["warnings"].append(f"{original_name}：{file_record['message']}")
    else:
        file_record["status"] = "warning" if file_record.get("warnings") else "ok"
        file_record["message"] = f"识别到 {file_record['source_count']} 个可导入数据表"
    return added


def create_import_session(files: Iterable[Any]) -> tuple[dict, list[dict]]:
    """保存上传文件、展开邮件附件并返回可预览的会话（不含完整数据行）。"""
    config.ensure_dirs()
    cleanup_stale_sessions()
    token = create_session_id()
    session_dir = config.STAGING_DIR / token
    raw_dir = session_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "id": token,
        "created_at": now_iso(),
        "status": "staged",
        "batch_id": None,
        "sources": [],
        "files": [],
        "warnings": [],
    }
    file_list = list(files or [])
    if not file_list:
        raise AppError("请选择要导入的文件", 400, "no_files")
    if len(file_list) > config.MAX_FILES_PER_IMPORT:
        raise AppError(f"一次最多导入 {config.MAX_FILES_PER_IMPORT} 个文件，请分批操作", 400, "too_many_files")

    for index, storage in enumerate(file_list, start=1):
        original = safe_filename(getattr(storage, "filename", "") or f"文件{index}")
        suffix = Path(original).suffix.lower()
        file_record = {
            "id": f"f{index}",
            "name": original,
            "size": 0,
            "kind": "excel" if suffix in config.SUPPORTED_TABULAR else "email",
            "status": "ok",
            "message": "",
            "source_count": 0,
            "sheet_count": 0,
            "attachments": [],
            "warnings": [],
            "email": None,
        }
        session["files"].append(file_record)
        if suffix not in config.SUPPORTED_UPLOAD:
            file_record["status"] = "error"
            file_record["message"] = (
                f"不支持的格式“{suffix or '无扩展名'}”。请使用 Excel、CSV 或 Outlook 邮件文件。"
            )
            session["warnings"].append(f"{original}：{file_record['message']}")
            continue
        target = unique_filename(raw_dir, f"{file_record['id']}_{original}")
        try:
            storage.save(str(target))
        except Exception as exc:
            log.exception("保存上传文件失败")
            file_record["status"] = "error"
            file_record["message"] = f"保存文件失败：{exc}"
            session["warnings"].append(f"{original}：保存文件失败")
            continue
        size = target.stat().st_size
        file_record["size"] = size
        if size == 0:
            file_record["status"] = "error"
            file_record["message"] = "文件为空"
            session["warnings"].append(f"{original}：文件为空")
            continue
        if size > config.MAX_FILE_BYTES:
            file_record["status"] = "error"
            file_record["message"] = f"文件超过 {human_size(config.MAX_FILE_BYTES)}，请拆分后再导入"
            session["warnings"].append(f"{original}：文件过大")
            continue

        if suffix in config.SUPPORTED_TABULAR:
            added = _process_tabular_file(
                session, target, original_name=original, file_record=file_record
            )
            if added:
                file_record["status"] = "ok"
                file_record["message"] = f"识别到 {added} 个数据表"
            elif file_record["status"] == "ok":
                file_record["status"] = "warning"
                file_record["message"] = file_record["message"] or "未识别到有效数据表"
        else:
            _process_email_file(session, target, original_name=original, file_record=file_record, raw_dir=raw_dir)

    # 清理暂时未被识别的空目录不重要，保留原文件便于追溯。
    save_session(session)
    cleanup_stale_sessions(active_token=token)
    return session, [source_summary(source) for source in session["sources"]]


def source_summary(source: dict) -> dict:
    rows = source.get("rows") or []
    top_rows = []
    for row in (source.get("top_rows") or [])[:8]:
        top_rows.append({"n": row.get("n"), "c": list(row.get("c") or [])[:config.MAX_TABLE_COLS]})
    return {
        "id": source["id"],
        "file_id": source.get("file_id"),
        "file_name": source.get("file_name"),
        "file_type": source.get("file_type"),
        "sheet_name": source.get("sheet_name"),
        "display_name": source.get("display_name"),
        "presence": source.get("presence", "file"),
        "email": source.get("email"),
        "headers": source.get("headers") or [],
        "header_row": source.get("header_row"),
        "row_count": len(rows),
        "column_count": len(source.get("headers") or []),
        "sample_rows": rows[:6],
        "header_candidates": top_rows,
        "warnings": source.get("warnings") or [],
        "hidden": bool(source.get("hidden")),
        "suggested_mapping": source.get("suggested_mapping"),
        "mapping_notes": source.get("mapping_notes") or [],
        "mapping_source": source.get("mapping_source", "auto"),
        "saved_profile": bool(source.get("saved_profile")),
        "unresolved": source.get("unresolved") or [],
    }


def session_payload(session: dict, token: str | None = None) -> dict:
    return {
        "session_id": token or session["id"],
        "created_at": session.get("created_at"),
        "status": session.get("status"),
        "files": session.get("files") or [],
        "sources": [source_summary(source) for source in session.get("sources") or []],
        "warnings": session.get("warnings") or [],
        "source_count": len(session.get("sources") or []),
    }


def reheader_session(token: str, source_id: str, header_row: int) -> dict:
    session = load_session(token)
    if session.get("status") == "committed":
        raise AppError("本次导入已完成，不能再调整表头", 409, "already_committed")
    source = next((s for s in session["sources"] if s["id"] == source_id), None)
    if not source:
        raise AppError("数据表不存在", 404, "source_not_found")
    try:
        header_row = int(header_row)
    except (TypeError, ValueError) as exc:
        raise AppError("表头行号无效", 400) from exc
    allowed_rows = {int(r.get("n")) for r in (source.get("top_rows") or [])}
    if header_row not in allowed_rows:
        raise AppError("只能在文件前 12 行内调整表头", 400)
    try:
        rebuilt = readers.reheader_table(source, header_row)
    except readers.ReaderError as exc:
        raise AppError(str(exc), 400) from exc
    source.update({
        "headers": rebuilt["headers"],
        "header_row": rebuilt["header_row"],
        "rows": rebuilt["rows"],
        "top_rows": rebuilt["top_rows"],
        "warnings": rebuilt["warnings"],
    })
    _attach_suggestion(source, _lookup_profile(source["headers"]))
    session["sources"] = [source if s["id"] == source_id else s for s in session["sources"]]
    save_session(session)
    return source_summary(source)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _get_cell(cells: list[Any], index: int | None) -> str:
    if not isinstance(index, int) or index < 0 or index >= len(cells):
        return ""
    return clean_text(cells[index], multiline=True)


def _selected_sources(session: dict, selections: list[dict]) -> list[tuple[dict, dict]]:
    if not isinstance(selections, list) or not selections:
        raise AppError("请至少选择一个数据表", 400, "no_sources")
    by_id = {source["id"]: source for source in session.get("sources") or []}
    result = []
    seen = set()
    errors = []
    for item in selections:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if source_id in seen:
            continue
        seen.add(source_id)
        source = by_id.get(source_id)
        if not source:
            errors.append({"source_id": source_id, "errors": ["数据表不存在或已过期"]})
            continue
        mapping, mapping_errors = validate_mapping(item.get("mapping") or {}, source["headers"])
        if mapping_errors:
            errors.append({
                "source_id": source_id,
                "display_name": source.get("display_name"),
                "errors": mapping_errors,
            })
        else:
            result.append((source, mapping))
    if errors:
        raise AppError("字段映射还不完整，请检查后再试", 422, "mapping_incomplete", {"sources": errors})
    if not result:
        raise AppError("没有可校验的数据表", 400, "no_sources")
    return result


def _clean_mapped_fields(source: dict, mapping: dict, cells: list[Any]) -> tuple[dict, list[dict]]:
    values: dict[str, str] = {}
    issues: list[dict] = []
    fields = mapping.get("fields") or {}
    for item in FIELD_DEFS:
        key = item["key"]
        if key == "amount":
            continue
        raw = _get_cell(cells, fields.get(key))
        if key in config.FIELD_LIMITS:
            value, warning = truncate_text(
                raw, item["label"], config.FIELD_LIMITS[key], multiline=(key == "note")
            )
            if warning:
                issues.append({"field": item["label"], "level": "warning", "message": warning})
        else:
            value = clean_text(raw, multiline=(key == "note"))
        values[key] = value
    if not values.get("category"):
        values["category"] = "未分类"
        issues.append({
            "field": "经营类别",
            "level": "warning",
            "message": "未填写经营类别，已归入“未分类”，可在导入后补录",
        })
    if not values.get("department"):
        values["department"] = "未指定"
    return values, issues


def _new_issue(field: str, message: str, level: str = "error") -> dict:
    return {"field": field, "level": level, "message": message}


def _empty_record(source: dict, row: dict, issue: dict) -> dict:
    return {
        "source_row": row["n"],
        "status": "invalid",
        "business_date": "",
        "direction": None,
        "category": "",
        "department": "",
        "counterparty": "",
        "payment_method": "",
        "doc_no": "",
        "note": "",
        "amount_cents": 0,
        "fingerprint": "",
        "issues": [issue],
        "raw": row["c"],
        "duplicate_of": None,
        "existing_id": None,
        "duplicate_action": None,
    }


def _make_record(
    *,
    source: dict,
    row: dict,
    values: dict,
    business_date: str,
    direction: str,
    amount_cents: int,
    issues: list[dict],
    fingerprint: str,
) -> dict:
    return {
        "source_row": row["n"],
        "status": "invalid" if any(i["level"] == "error" for i in issues) else (
            "warning" if issues else "valid"
        ),
        "business_date": business_date,
        "direction": direction,
        "category": values["category"],
        "department": values["department"],
        "counterparty": values["counterparty"],
        "payment_method": values["payment_method"],
        "doc_no": values["doc_no"],
        "note": values["note"],
        "amount_cents": amount_cents,
        "fingerprint": fingerprint,
        "issues": issues,
        "raw": row["c"],
        "duplicate_of": None,
        "existing_id": None,
        "duplicate_action": None,
    }


def _is_zero_or_blank(value: Any) -> bool:
    text = clean_text(value)
    if text in {"", "-", "—", "/", "无"}:
        return True
    try:
        return float(text.replace(",", "").replace("￥", "").replace("¥", "")) == 0
    except Exception:
        return False


def _records_for_source(source: dict, mapping: dict) -> list[dict]:
    records: list[dict] = []
    amount_rule = mapping.get("amount") or {}
    direction_rule = mapping.get("direction") or {}
    today = date.today()
    for row in source.get("rows") or []:
        cells = row.get("c") or []
        values, field_issues = _clean_mapped_fields(source, mapping, cells)
        warnings = [i for i in field_issues if i["level"] == "warning"]
        date_issue = None
        business_date = ""
        date_raw = _get_cell(cells, (mapping.get("fields") or {}).get("date"))
        try:
            business_date, month_only, date_warning = parse_date_value(date_raw)
            if date_warning:
                warnings.append(_new_issue("业务日期", date_warning, "warning"))
            try:
                if date.fromisoformat(business_date) > today.replace(year=today.year + 1):
                    warnings.append(_new_issue("业务日期", "日期在未来一年之后，请确认", "warning"))
            except ValueError:
                pass
        except FieldError as exc:
            date_issue = _new_issue("业务日期", str(exc))
            records.append(_empty_record(source, row, date_issue))
            continue

        if amount_rule.get("mode") == "split":
            income_raw = _get_cell(cells, amount_rule.get("income_column"))
            expense_raw = _get_cell(cells, amount_rule.get("expense_column"))
            branches: list[tuple[str, str]] = []
            if not _is_zero_or_blank(income_raw):
                branches.append(("income", income_raw))
            if not _is_zero_or_blank(expense_raw):
                branches.append(("expense", expense_raw))
            if not branches:
                issue = _new_issue("金额", "收入金额和支出金额均为空或为 0")
                records.append(_empty_record(source, row, issue))
                continue
            split_warning = _new_issue(
                "金额", "同一行同时含收入和支出，已按两笔记录拆分", "warning"
            ) if len(branches) == 2 else None
            for direction, amount_raw in branches:
                branch_issues = list(warnings)
                if split_warning:
                    branch_issues.append(split_warning)
                try:
                    amount_cents, amount_warnings = parse_money_cents(amount_raw)
                    for message in amount_warnings:
                        branch_issues.append(_new_issue("金额", message, "warning"))
                except FieldError as exc:
                    branch_issues.append(_new_issue("金额", str(exc)))
                    records.append(_make_record(
                        source=source, row=row, values=values, business_date=business_date,
                        direction=direction, amount_cents=0, issues=branch_issues, fingerprint="",
                    ))
                    continue
                fingerprint = build_fingerprint(
                    date_text=business_date,
                    direction=direction,
                    amount_cents=amount_cents,
                    category=values["category"],
                    department=values["department"],
                    counterparty=values["counterparty"],
                    note=values["note"],
                    doc_no=values["doc_no"],
                )
                records.append(_make_record(
                    source=source, row=row, values=values, business_date=business_date,
                    direction=direction, amount_cents=amount_cents,
                    issues=branch_issues, fingerprint=fingerprint,
                ))
            continue

        amount_raw = _get_cell(cells, amount_rule.get("column"))
        issues = list(warnings)
        try:
            amount_cents, amount_warnings = parse_money_cents(amount_raw)
            for message in amount_warnings:
                issues.append(_new_issue("金额", message, "warning"))
        except FieldError as exc:
            issues.append(_new_issue("金额", str(exc)))
            records.append(_make_record(
                source=source, row=row, values=values, business_date=business_date,
                direction=None, amount_cents=0, issues=issues, fingerprint="",
            ))
            continue

        direction = None
        if direction_rule.get("mode") == "fixed":
            direction = direction_rule.get("value")
            if direction not in {"income", "expense"}:
                issues.append(_new_issue("收支类型", "没有选择整表对应的收支类型"))
        elif direction_rule.get("mode") == "column":
            raw_direction = _get_cell(cells, direction_rule.get("column"))
            try:
                direction = normalize_direction(raw_direction)
            except FieldError as exc:
                issues.append(_new_issue("收支类型", str(exc)))
        else:
            issues.append(_new_issue("收支类型", "没有指定收支类型的来源"))

        fingerprint = ""
        if direction and not any(i["level"] == "error" for i in issues):
            fingerprint = build_fingerprint(
                date_text=business_date,
                direction=direction,
                amount_cents=amount_cents,
                category=values["category"],
                department=values["department"],
                counterparty=values["counterparty"],
                note=values["note"],
                doc_no=values["doc_no"],
            )
        records.append(_make_record(
            source=source, row=row, values=values, business_date=business_date,
            direction=direction, amount_cents=amount_cents, issues=issues,
            fingerprint=fingerprint,
        ))
    return records


def _existing_records(fingerprints: list[str]) -> dict[str, dict]:
    if not fingerprints:
        return {}
    conn = db.get_db()
    found: dict[str, dict] = {}
    for chunk in chunked(list(dict.fromkeys(fingerprints)), 700):
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""SELECT id, fingerprint, business_date, direction, category, department,
                       counterparty, amount_cents
                FROM records WHERE fingerprint IN ({marks})""",
            chunk,
        ).fetchall()
        for row in rows:
            found[row["fingerprint"]] = dict(row)
    return found


def _apply_duplicates(
    prepared_sources: list[dict],
    duplicate_policy: str,
) -> dict[str, int]:
    all_records = [
        record for prepared in prepared_sources for record in prepared["records"]
        if record["status"] != "invalid" and record.get("fingerprint")
    ]
    existing = _existing_records([record["fingerprint"] for record in all_records])
    seen: dict[str, tuple[str, int]] = {}
    counts = {"duplicate": 0, "update": 0, "within_batch": 0}
    for prepared in prepared_sources:
        source = prepared["source"]
        for record in prepared["records"]:
            if record["status"] == "invalid":
                continue
            fingerprint = record["fingerprint"]
            if not fingerprint:
                record["status"] = "invalid"
                record["issues"].append(_new_issue("判重", "无法生成判重标识，请检查字段"))
                continue
            location = f"{source.get('display_name')} 第 {record['source_row']} 行"
            if fingerprint in seen:
                first_source, first_row = seen[fingerprint]
                record["status"] = "duplicate"
                record["duplicate_action"] = "skip"
                record["duplicate_of"] = f"{first_source} 第 {first_row} 行"
                record["issues"].append(_new_issue(
                    "重复数据",
                    f"与本次导入的“{first_source} 第 {first_row} 行”重复，已跳过",
                    "warning",
                ))
                counts["duplicate"] += 1
                counts["within_batch"] += 1
                continue
            seen[fingerprint] = (source.get("display_name") or source["id"], record["source_row"])
            if fingerprint in existing:
                previous = existing[fingerprint]
                record["status"] = "duplicate"
                record["existing_id"] = previous["id"]
                action = "update" if duplicate_policy == "update" else "skip"
                record["duplicate_action"] = action
                date_text = previous.get("business_date") or ""
                amount_text = f"{(previous.get('amount_cents') or 0) / 100:,.2f}"
                record["duplicate_of"] = f"已有记录（{date_text}，{direction_label(previous.get('direction') or 'income')} {amount_text} 元）"
                record["issues"].append(_new_issue(
                    "重复数据",
                    "与已有记录重复，" + ("导入时将更新原记录" if action == "update" else "已跳过"),
                    "warning",
                ))
                counts["duplicate"] += 1
                if action == "update":
                    counts["update"] += 1
    return counts


def _finalize_stats(prepared: dict, duplicate_policy: str) -> None:
    records = prepared["records"]
    invalid = [r for r in records if r["status"] == "invalid"]
    duplicates = [r for r in records if r["status"] == "duplicate"]
    ready = [r for r in records if r["status"] in {"valid", "warning"}]
    warnings = [r for r in records if r["status"] == "warning"]
    updateable = [r for r in duplicates if r.get("duplicate_action") == "update"]
    prepared["stats"] = {
        "record_count": len(records),
        "ready": len(ready),
        "invalid": len(invalid),
        "duplicate": len(duplicates),
        "warning": len(warnings),
        "updateable": len(updateable),
        "importable": len(ready) + (len(updateable) if duplicate_policy == "update" else 0),
    }
    prepared["invalid_count"] = len(invalid)
    prepared["duplicate_count"] = len(duplicates)
    prepared["warning_count"] = len(warnings)
    prepared["updateable_count"] = len(updateable)


def prepare_validation(session: dict, selections: list[dict], duplicate_policy: str = "skip") -> dict:
    if duplicate_policy not in {"skip", "update"}:
        duplicate_policy = "skip"
    selected = _selected_sources(session, selections)
    prepared_sources = []
    for source, mapping in selected:
        records = _records_for_source(source, mapping)
        item = {"source": source, "mapping": mapping, "records": records}
        _finalize_stats(item, duplicate_policy)
        prepared_sources.append(item)
    duplicate_counts = _apply_duplicates(prepared_sources, duplicate_policy)
    for item in prepared_sources:
        _finalize_stats(item, duplicate_policy)
    total_records = sum(item["stats"]["record_count"] for item in prepared_sources)
    summary = {
        "source_count": len(prepared_sources),
        "row_count": sum(len(item["source"].get("rows") or []) for item in prepared_sources),
        "record_count": total_records,
        "ready": sum(item["stats"]["ready"] for item in prepared_sources),
        "invalid": sum(item["stats"]["invalid"] for item in prepared_sources),
        "duplicate": sum(item["stats"]["duplicate"] for item in prepared_sources),
        "warning": sum(item["stats"]["warning"] for item in prepared_sources),
        "updateable": sum(item["stats"]["updateable"] for item in prepared_sources),
        "importable": sum(item["stats"]["importable"] for item in prepared_sources),
        "within_batch_duplicate": duplicate_counts.get("within_batch", 0),
        "update": duplicate_counts.get("update", 0),
    }
    return {
        "summary": summary,
        "sources": prepared_sources,
        "duplicate_policy": duplicate_policy,
    }


def _record_view(record: dict) -> dict:
    return {
        "row": record["source_row"],
        "status": record["status"],
        "status_label": STATUS_LABELS.get(record["status"], record["status"]),
        "business_date": record["business_date"],
        "direction": record["direction"],
        "direction_label": direction_label(record["direction"]) if record["direction"] else "",
        "category": record["category"],
        "department": record["department"],
        "counterparty": record["counterparty"],
        "amount": f"{record['amount_cents'] / 100:,.2f}" if record["amount_cents"] else "",
        "doc_no": record["doc_no"],
        "note": record["note"],
        "issues": record["issues"],
        "message": record["issues"][0]["message"] if record["issues"] else "",
        "duplicate_of": record.get("duplicate_of"),
        "duplicate_action": record.get("duplicate_action"),
        "raw": [clean_text(v) for v in (record.get("raw") or [])[:12]],
    }


def validation_payload(validation: dict, sample_per_source: int = 18) -> dict:
    sources = []
    for item in validation["sources"]:
        source = item["source"]
        records = item["records"]
        abnormal = [r for r in records if r["status"] in {"invalid", "duplicate"}]
        warnings = [r for r in records if r["status"] == "warning"]
        sample = records[:sample_per_source]
        source_payload = {
            "source_id": source["id"],
            "display_name": source.get("display_name"),
            "file_name": source.get("file_name"),
            "sheet_name": source.get("sheet_name"),
            "presence": source.get("presence"),
            "email": source.get("email"),
            "header_row": source.get("header_row"),
            "row_count": len(source.get("rows") or []),
            "stats": item["stats"],
            "samples": [_record_view(r) for r in sample],
            "issues": [
                {
                    "row": r["source_row"],
                    "status": r["status"],
                    "status_label": STATUS_LABELS.get(r["status"], r["status"]),
                    "message": r["issues"][0]["message"] if r["issues"] else "",
                    "field": (r["issues"][0].get("field") if r["issues"] else ""),
                }
                for r in abnormal[:200]
            ],
            "warnings": [
                {"row": r["source_row"], "message": r["issues"][0]["message"] if r["issues"] else ""}
                for r in warnings[:100]
            ],
            "issue_total": len(abnormal),
            "warning_total": len(warnings),
            "invalid_count": item["invalid_count"],
            "duplicate_count": item["duplicate_count"],
            "warning_count": item["warning_count"],
            "updateable_count": item["updateable_count"],
        }
        sources.append(source_payload)
    summary = dict(validation["summary"])
    summary["source_count"] = len(sources)
    return {
        "summary": summary,
        "sources": sources,
        "duplicate_policy": validation["duplicate_policy"],
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# 异常报告
# ---------------------------------------------------------------------------

_REPORT_HEADERS = ["状态", "行号", "字段", "问题说明", "业务日期", "收支", "经营类别", "部门", "往来单位", "金额（元）", "结算方式", "单据编号", "摘要备注"]


def _report_rows(prepared_sources: list[dict]) -> Iterable[list[str]]:
    for item in prepared_sources:
        source = item["source"]
        for record in item["records"]:
            if record["status"] == "valid":
                continue
            issues = record.get("issues") or []
            messages = "；".join(i["message"] for i in issues if i.get("message"))
            fields = "、".join(dict.fromkeys(i.get("field", "") for i in issues if i.get("field")))
            yield [
                csv_safe(STATUS_LABELS.get(record["status"], record["status"])),
                str(record["source_row"]),
                csv_safe(fields),
                csv_safe(messages),
                record["business_date"],
                direction_label(record["direction"]) if record["direction"] else "",
                csv_safe(record["category"]),
                csv_safe(record["department"]),
                csv_safe(record["counterparty"]),
                f"{record['amount_cents'] / 100:.2f}" if record["amount_cents"] else "",
                csv_safe(record["payment_method"]),
                csv_safe(record["doc_no"]),
                csv_safe(record["note"]),
            ]


def validation_csv(validation: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_REPORT_HEADERS)
    for row in _report_rows(validation["sources"]):
        writer.writerow(row)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _report_rel_path(batch_id: str, source_id: str) -> str:
    """异常明细相对数据目录的路径（存库用）。"""
    return str((config.REPORT_DIR / batch_id / f"{source_id}.csv").relative_to(config.DATA_DIR))


def render_batch_report(source: dict, records: list[dict]) -> bytes:
    """在内存中生成异常明细 CSV。

    写入磁盘由调用方在事务提交后完成：事务内做文件 I/O 会拉长写锁持有时间，
    且回滚时已落盘的文件不会跟着回滚，会留下没有批次归属的孤儿报告。
    只有表头（没有异常行）时返回空 bytes，调用方据此决定不生成文件。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_REPORT_HEADERS)
    written = 0
    item = {"source": source, "records": records}
    for row in _report_rows([item]):
        writer.writerow(row)
        written += 1
    if not written:
        return b""
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def write_report_file(batch_id: str, source_id: str, payload: bytes) -> str:
    """把已渲染好的报告原子写入磁盘，返回存库用的相对路径。"""
    folder = config.REPORT_DIR / batch_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{source_id}.csv"
    temp = folder / f".{source_id}.csv.tmp"
    temp.write_bytes(payload)
    os.replace(temp, target)
    return str(target.relative_to(config.DATA_DIR))


def _flush_reports(batch_id: str, pending: list[tuple[str, bytes]]) -> None:
    """事务提交后落盘。写失败时清空库里的指针，避免界面给出失效的下载链接。"""
    for source_id, payload in pending:
        try:
            write_report_file(batch_id, source_id, payload)
        except Exception:
            log.exception("写入异常明细报告失败：%s / %s", batch_id, source_id)
            try:
                with db.transaction() as tx:
                    tx.execute(
                        "UPDATE batch_sources SET error_report = '' WHERE batch_id = ? AND source_id = ?",
                        (batch_id, source_id),
                    )
            except Exception:
                log.exception("回滚报告指针失败：%s / %s", batch_id, source_id)


# ---------------------------------------------------------------------------
# 提交
# ---------------------------------------------------------------------------

def _batch_status(imported: int, updated: int, invalid: int, duplicate: int) -> str:
    if imported + updated > 0:
        return "partial" if invalid > 0 else "completed"
    if duplicate > 0:
        return "duplicate"
    return "failed"


def _upsert_mapping_profile(source: dict, mapping: dict) -> None:
    if not db.get_setting("remember_mapping", True):
        return
    signature = header_signature(source["headers"])
    if not signature:
        return
    timestamp = now_iso()
    name = source.get("display_name") or source.get("file_name") or "字段映射"
    try:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO mapping_profiles(header_signature, name, headers_json, mapping_json,
                                                use_count, created_at, updated_at)
                   VALUES(?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(header_signature) DO UPDATE SET
                     name = excluded.name,
                     headers_json = excluded.headers_json,
                     mapping_json = excluded.mapping_json,
                     use_count = mapping_profiles.use_count + 1,
                     updated_at = excluded.updated_at""",
                (
                    signature, name[:120],
                    json.dumps(source["headers"], ensure_ascii=False),
                    json.dumps(mapping, ensure_ascii=False),
                    timestamp, timestamp,
                ),
            )
    except Exception:
        log.exception("保存字段映射习惯失败")


def get_batch_payload(batch_id: str, include_sources: bool = True) -> dict:
    conn = db.get_db()
    batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise AppError("导入记录不存在", 404, "batch_not_found")
    result = dict(batch)
    result["status_label"] = {
        "completed": "已导入",
        "partial": "部分导入",
        "duplicate": "全部重复",
        "failed": "未导入",
    }.get(result["status"], result["status"])
    if include_sources:
        sources = conn.execute(
            "SELECT * FROM batch_sources WHERE batch_id = ? ORDER BY rowid", (batch_id,)
        ).fetchall()
        result["sources"] = [
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "file_name": row["file_name"],
                "file_type": row["file_type"],
                "sheet_name": row["sheet_name"],
                "email_subject": row["email_subject"],
                "email_from": row["email_from"],
                "presence": row["presence"],
                "header_row": row["header_row"],
                "total_rows": row["total_rows"],
                "imported_rows": row["imported_rows"],
                "updated_rows": row["updated_rows"],
                "duplicate_rows": row["duplicate_rows"],
                "warning_rows": row["warning_rows"],
                "invalid_rows": row["invalid_rows"],
                "error_report": bool(row["error_report"]),
                "issues": json.loads(row["errors_json"] or "[]"),
                "warnings": json.loads(row["warnings_json"] or "[]"),
            }
            for row in sources
        ]
    return result


def commit_import(token: str, selections: list[dict], duplicate_policy: str = "skip") -> dict:
    if duplicate_policy not in {"skip", "update"}:
        duplicate_policy = "skip"
    with _COMMIT_LOCK:
        session = load_session(token)
        conn = db.get_db()
        existing_batch = conn.execute(
            "SELECT id FROM batches WHERE session_token = ?", (token,)
        ).fetchone()
        if session.get("status") == "committed" or existing_batch:
            batch_id = session.get("batch_id") or (existing_batch["id"] if existing_batch else None)
            if batch_id:
                return get_batch_payload(batch_id)
            raise AppError("本次导入已经提交，请刷新查看导入记录", 409, "already_committed")

        started = time.monotonic()
        validation = prepare_validation(session, selections, duplicate_policy)
        summary = validation["summary"]
        if summary["importable"] == 0:
            reason = "没有可导入的有效数据" if summary["duplicate"] == 0 else "没有新增数据，全部为重复或异常行"
            raise AppError(
                reason + "，请根据异常提示修正源文件后重试",
                422,
                "nothing_importable",
                {"summary": summary},
            )

        batch_id = new_id("b")
        created_at = now_iso()
        imported = updated = 0
        batch_sources = []
        pending_reports: list[tuple[str, bytes]] = []
        try:
            with db.transaction() as tx:
                tx.execute(
                    """INSERT INTO batches(id, status, session_token, source_count, total_rows,
                           imported_rows, updated_rows, duplicate_rows, warning_rows, invalid_rows,
                           duplicate_policy, duration_ms, is_demo, note, created_at)
                       VALUES(?, 'pending', ?, ?, ?, 0, 0, ?, ?, ?, ?, 0, 0, '', ?)""",
                    (
                        batch_id, token, summary["source_count"], summary["row_count"],
                        summary["duplicate"], summary["warning"], summary["invalid"],
                        duplicate_policy, created_at,
                    ),
                )
                for item in validation["sources"]:
                    source = item["source"]
                    source_imported = source_updated = source_duplicate = 0
                    errors_preview = []
                    warnings_preview = []
                    for record in item["records"]:
                        if record["status"] == "invalid":
                            if record["issues"]:
                                errors_preview.append({
                                    "row": record["source_row"],
                                    "message": record["issues"][0]["message"],
                                    "field": record["issues"][0].get("field"),
                                })
                            continue
                        if record["status"] == "duplicate":
                            if record.get("duplicate_action") == "update" and record.get("existing_id"):
                                tx.execute(
                                    """UPDATE records SET business_date = ?, direction = ?, category = ?,
                                           department = ?, counterparty = ?, payment_method = ?, doc_no = ?,
                                           note = ?, amount_cents = ?, fingerprint = ?, source_type = 'import',
                                           batch_id = ?, source_file = ?, source_sheet = ?, source_row = ?,
                                           is_demo = 0, updated_at = ?
                                       WHERE id = ?""",
                                    (
                                        record["business_date"], record["direction"], record["category"],
                                        record["department"], record["counterparty"], record["payment_method"],
                                        record["doc_no"], record["note"], record["amount_cents"],
                                        record["fingerprint"], batch_id, source.get("file_name", ""),
                                        source.get("sheet_name", ""), record["source_row"], created_at,
                                        record["existing_id"],
                                    ),
                                )
                                updated += 1
                                source_updated += 1
                            else:
                                source_duplicate += 1
                            continue
                        record_id = new_id("r")
                        tx.execute(
                            """INSERT INTO records(id, business_date, direction, category, department,
                                   counterparty, payment_method, doc_no, note, amount_cents, fingerprint,
                                   source_type, batch_id, source_file, source_sheet, source_row, is_demo,
                                   created_at, updated_at)
                               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', ?, ?, ?, ?, 0, ?, ?)
                               ON CONFLICT(fingerprint) DO NOTHING""",
                            (
                                record_id, record["business_date"], record["direction"], record["category"],
                                record["department"], record["counterparty"], record["payment_method"],
                                record["doc_no"], record["note"], record["amount_cents"], record["fingerprint"],
                                batch_id, source.get("file_name", ""), source.get("sheet_name", ""),
                                record["source_row"], created_at, created_at,
                            ),
                        )
                        if tx.execute(
                            "SELECT 1 FROM records WHERE id = ?", (record_id,)
                        ).fetchone():
                            imported += 1
                            source_imported += 1
                        else:
                            if duplicate_policy == "update":
                                tx.execute(
                                    """UPDATE records SET business_date = ?, category = ?, department = ?,
                                           counterparty = ?, payment_method = ?, doc_no = ?, note = ?,
                                           amount_cents = ?, source_type = 'import', batch_id = ?,
                                           source_file = ?, source_sheet = ?, source_row = ?, is_demo = 0,
                                           updated_at = ? WHERE fingerprint = ?""",
                                    (
                                        record["business_date"], record["category"], record["department"],
                                        record["counterparty"], record["payment_method"], record["doc_no"],
                                        record["note"], record["amount_cents"], batch_id,
                                        source.get("file_name", ""), source.get("sheet_name", ""),
                                        record["source_row"], created_at, record["fingerprint"],
                                    ),
                                )
                                updated += 1
                                source_updated += 1
                            else:
                                source_duplicate += 1
                        if record["status"] == "warning":
                            warnings_preview.append({
                                "row": record["source_row"],
                                "message": record["issues"][0]["message"] if record["issues"] else "",
                            })
                    # 为异常/重复/提示行落一份完整 CSV，历史记录中可下载。
                    # 这里只渲染到内存，落盘放到事务提交之后（见 _flush_reports）。
                    abnormal = [r for r in item["records"] if r["status"] != "valid"]
                    error_report = ""
                    if abnormal:
                        report_payload = render_batch_report(source, item["records"])
                        if report_payload:
                            error_report = _report_rel_path(batch_id, source["id"])
                            pending_reports.append((source["id"], report_payload))
                    tx.execute(
                        """INSERT INTO batch_sources(id, batch_id, source_id, file_name, file_type,
                               sheet_name, email_subject, email_from, email_date, presence, header_row,
                               total_rows, imported_rows, updated_rows, duplicate_rows, warning_rows,
                               invalid_rows, mapping_json, errors_json, warnings_json, error_report,
                               created_at)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            new_id("bs"), batch_id, source["id"], source.get("file_name", ""),
                            source.get("file_type", ""), source.get("sheet_name", ""),
                            (source.get("email") or {}).get("subject", ""),
                            (source.get("email") or {}).get("sender", ""),
                            (source.get("email") or {}).get("date", ""),
                            source.get("presence", "file"), source.get("header_row") or 1,
                            len(source.get("rows") or []), source_imported, source_updated,
                            item["duplicate_count"], item["warning_count"], item["invalid_count"],
                            json.dumps(item["mapping"], ensure_ascii=False),
                            json.dumps(errors_preview[:200], ensure_ascii=False),
                            json.dumps(warnings_preview[:200], ensure_ascii=False),
                            error_report, created_at,
                        ),
                    )
                    batch_sources.append(source["id"])
                status = _batch_status(imported, updated, summary["invalid"], summary["duplicate"])
                duration_ms = int((time.monotonic() - started) * 1000)
                tx.execute(
                    """UPDATE batches SET status = ?, imported_rows = ?, updated_rows = ?,
                           duration_ms = ? WHERE id = ?""",
                    (status, imported, updated, duration_ms, batch_id),
                )
                db.audit(
                    "import", "batch", batch_id,
                    f"导入 {summary['source_count']} 个数据表，新增 {imported} 条，更新 {updated} 条，重复 {summary['duplicate']} 条，异常 {summary['invalid']} 条",
                    conn=tx,
                )
        except AppError:
            raise
        except Exception as exc:
            log.exception("提交导入失败")
            raise AppError(f"保存导入数据失败：{exc}", 500, "commit_failed") from exc

        # 事务已提交，此时再落盘异常明细；写失败只会清空指针，不影响已入库的数据。
        _flush_reports(batch_id, pending_reports)

        for item in validation["sources"]:
            _upsert_mapping_profile(item["source"], item["mapping"])

        session["status"] = "committed"
        session["batch_id"] = batch_id
        try:
            save_session(session)
        except Exception:
            log.exception("更新导入会话状态失败")
        return get_batch_payload(batch_id)


def batch_error_report_path(batch_id: str, source_id: str) -> Path:
    row = db.get_db().execute(
        "SELECT error_report FROM batch_sources WHERE batch_id = ? AND source_id = ?",
        (batch_id, source_id),
    ).fetchone()
    if not row or not row["error_report"]:
        raise AppError("该数据表没有异常明细可下载", 404, "no_report")
    path = (config.DATA_DIR / row["error_report"]).resolve()
    try:
        path.relative_to(config.REPORT_DIR.resolve())
    except ValueError as exc:
        raise AppError("报告路径无效", 400) from exc
    if not path.exists():
        raise AppError("异常明细文件已被清理", 404, "report_missing")
    return path
