"""经营流水查询、增删改查与 Excel 导出。"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

from . import config, db
from .errors import AppError
from .fields import FIELD_DEFS, FIELD_LABELS
from .utils import (
    FieldError,
    build_fingerprint,
    clean_text,
    csv_safe,
    direction_label,
    new_id,
    normalize_direction,
    now_iso,
    parse_date_input,
    parse_money_cents,
)

SORT_OPTIONS = {
    "date_desc": "business_date DESC, created_at DESC",
    "date_asc": "business_date ASC, created_at ASC",
    "amount_desc": "amount_cents DESC, business_date DESC",
    "amount_asc": "amount_cents ASC, business_date DESC",
    "updated_desc": "updated_at DESC",
}

LIKE_FIELDS = (
    "business_date", "category", "department", "counterparty", "payment_method",
    "doc_no", "note", "source_file",
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_filters(args: Any, *, for_export: bool = False) -> dict:
    params = {
        "start": None,
        "end": None,
        "direction": None,
        "category": None,
        "department": None,
        "q": None,
        "source_type": None,
        "batch_id": None,
        "sort": "date_desc",
        "page": 1,
        "page_size": 20 if not for_export else 100000,
    }
    start = clean_text(args.get("start"))
    end = clean_text(args.get("end"))
    if start:
        try:
            params["start"] = parse_date_input(start, "开始日期")
        except FieldError as exc:
            raise AppError(str(exc), 400, "invalid_filter") from exc
    if end:
        try:
            params["end"] = parse_date_input(end, "结束日期")
        except FieldError as exc:
            raise AppError(str(exc), 400, "invalid_filter") from exc
    if params["start"] and params["end"] and params["start"] > params["end"]:
        raise AppError("开始日期不能晚于结束日期", 400, "invalid_filter")

    direction = clean_text(args.get("direction"))
    if direction in {"income", "expense"}:
        params["direction"] = direction
    category = clean_text(args.get("category"))
    params["category"] = category[:80] if category else None
    department = clean_text(args.get("department"))
    params["department"] = department[:80] if department else None
    q = clean_text(args.get("q"))
    params["q"] = q[:100] if q else None
    source_type = clean_text(args.get("source"))
    if source_type in {"manual", "import"}:
        params["source_type"] = source_type
    batch_id = clean_text(args.get("batch_id"))
    params["batch_id"] = batch_id[:80] if batch_id else None

    sort = clean_text(args.get("sort"))
    if sort in SORT_OPTIONS:
        params["sort"] = sort
    if not for_export:
        try:
            params["page"] = max(1, int(args.get("page") or 1))
        except (TypeError, ValueError):
            params["page"] = 1
        try:
            page_size = int(args.get("page_size") or 20)
        except (TypeError, ValueError):
            page_size = 20
        params["page_size"] = page_size if page_size in {10, 20, 50, 100} else 20
    return params


def build_where(filters: dict) -> tuple[str, list[Any]]:
    clauses = []
    values: list[Any] = []
    if filters.get("start"):
        clauses.append("business_date >= ?")
        values.append(filters["start"])
    if filters.get("end"):
        clauses.append("business_date <= ?")
        values.append(filters["end"])
    if filters.get("direction"):
        clauses.append("direction = ?")
        values.append(filters["direction"])
    if filters.get("category"):
        clauses.append("category = ?")
        values.append(filters["category"])
    if filters.get("department"):
        clauses.append("department = ?")
        values.append(filters["department"])
    if filters.get("source_type"):
        clauses.append("source_type = ?")
        values.append(filters["source_type"])
    if filters.get("batch_id"):
        clauses.append("batch_id = ?")
        values.append(filters["batch_id"])
    if filters.get("q"):
        like = f"%{_escape_like(filters['q'])}%"
        clauses.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in LIKE_FIELDS) + ")")
        values.extend([like] * len(LIKE_FIELDS))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", values


def record_to_dict(row: Any) -> dict:
    item = dict(row)
    item["amount"] = round(item["amount_cents"] / 100, 2)
    item["direction_label"] = direction_label(item["direction"])
    item["source_label"] = "批量导入" if item["source_type"] == "import" else "手工录入"
    item["source_location"] = ""
    if item["source_file"]:
        location = item["source_file"]
        if item.get("source_sheet"):
            location += f" · {item['source_sheet']}"
        if item.get("source_row"):
            location += f" · 第 {item['source_row']} 行"
        item["source_location"] = location
    item.pop("fingerprint", None)
    return item


def list_records(filters: dict) -> dict:
    conn = db.get_db()
    where, values = build_where(filters)
    total = conn.execute(f"SELECT COUNT(*) FROM records{where}", values).fetchone()[0]
    totals_rows = conn.execute(
        f"SELECT direction, SUM(amount_cents) AS amount, COUNT(*) AS count FROM records{where} GROUP BY direction",
        values,
    ).fetchall()
    income = next((int(r["amount"] or 0) for r in totals_rows if r["direction"] == "income"), 0)
    expense = next((int(r["amount"] or 0) for r in totals_rows if r["direction"] == "expense"), 0)
    page_size = filters["page_size"]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(filters["page"], pages)
    offset = (page - 1) * page_size
    order = SORT_OPTIONS.get(filters.get("sort"), SORT_OPTIONS["date_desc"])
    rows = conn.execute(
        f"SELECT * FROM records{where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*values, page_size, offset],
    ).fetchall()
    return {
        "items": [record_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "summary": {
            "income_cents": income,
            "expense_cents": expense,
            "net_cents": income - expense,
            "count": total,
        },
        "filters": filters,
    }


def get_filter_options() -> dict:
    conn = db.get_db()
    categories = [
        {"name": row["category"], "count": row["count"]}
        for row in conn.execute(
            "SELECT category, COUNT(*) AS count FROM records GROUP BY category ORDER BY count DESC, category ASC"
        )
    ]
    departments = [
        {"name": row["department"], "count": row["count"]}
        for row in conn.execute(
            "SELECT department, COUNT(*) AS count FROM records GROUP BY department ORDER BY count DESC, department ASC"
        )
    ]
    years = [
        row["year"]
        for row in conn.execute(
            "SELECT DISTINCT substr(business_date, 1, 4) AS year FROM records ORDER BY year DESC"
        )
    ]
    range_row = conn.execute(
        "SELECT MIN(business_date) AS min_date, MAX(business_date) AS max_date, COUNT(*) AS total, "
        "SUM(CASE WHEN is_demo = 1 THEN 1 ELSE 0 END) AS demo_count FROM records"
    ).fetchone()
    batch_count = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    return {
        "categories": categories,
        "departments": departments,
        "years": years,
        "min_date": range_row["min_date"],
        "max_date": range_row["max_date"],
        "total": range_row["total"],
        "demo_count": range_row["demo_count"] or 0,
        "batch_count": batch_count,
    }


def get_record(record_id: str) -> dict:
    row = db.get_db().execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise AppError("记录不存在或已删除", 404, "record_not_found")
    return record_to_dict(row)


def _normalize_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise AppError("提交的数据格式不正确", 400)
    errors: dict[str, str] = {}
    try:
        business_date = parse_date_input(payload.get("business_date"), "业务日期")
    except FieldError as exc:
        business_date = ""
        errors["business_date"] = str(exc)
    try:
        direction = normalize_direction(payload.get("direction"))
    except FieldError as exc:
        direction = ""
        errors["direction"] = str(exc)
    amount_cents = 0
    try:
        amount_cents, _ = parse_money_cents(payload.get("amount"))
    except FieldError as exc:
        errors["amount"] = str(exc)
    values = {}
    for item in FIELD_DEFS:
        key = item["key"]
        if key in {"date", "amount"}:
            continue
        raw = payload.get(key, "")
        text = clean_text(raw, multiline=(key == "note"))
        limit = config.FIELD_LIMITS.get(key, 120)
        if len(text) > limit:
            errors[key] = f"{item['label']}不能超过 {limit} 个字符"
        values[key] = text
    if not values.get("category"):
        values["category"] = "未分类"
    if not values.get("department"):
        values["department"] = "未指定"
    if errors:
        raise AppError("请检查表单中的必填项和格式", 422, "validation_error", {"fields": errors})
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
    return {
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
    }


def create_record(payload: dict) -> dict:
    data = _normalize_payload(payload)
    conn = db.get_db()
    duplicate = conn.execute(
        "SELECT id FROM records WHERE fingerprint = ?", (data["fingerprint"],)
    ).fetchone()
    if duplicate:
        raise AppError(
            "系统中已有内容相同的记录，未重复添加。可修改单据编号或备注后重试。",
            409, "duplicate_record",
            {"existing_id": duplicate["id"]},
        )
    record_id = new_id("r")
    timestamp = now_iso()
    try:
        with db.transaction() as tx:
            tx.execute(
                """INSERT INTO records(id, business_date, direction, category, department, counterparty,
                       payment_method, doc_no, note, amount_cents, fingerprint, source_type, batch_id,
                       source_file, source_sheet, source_row, is_demo, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', NULL, '', '', NULL, 0, ?, ?)""",
                (
                    record_id, data["business_date"], data["direction"], data["category"],
                    data["department"], data["counterparty"], data["payment_method"], data["doc_no"],
                    data["note"], data["amount_cents"], data["fingerprint"], timestamp, timestamp,
                ),
            )
            db.audit(
                "create", "record", record_id,
                f"新增{direction_label(data['direction'])}记录 {data['amount_cents'] / 100:.2f} 元",
                conn=tx,
            )
    except Exception as exc:
        if "UNIQUE constraint failed: records.fingerprint" in str(exc):
            raise AppError("系统中已有内容相同的记录，未重复添加", 409, "duplicate_record") from exc
        raise
    return get_record(record_id)


def update_record(record_id: str, payload: dict) -> dict:
    conn = db.get_db()
    current = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if not current:
        raise AppError("记录不存在或已删除", 404, "record_not_found")
    data = _normalize_payload(payload)
    duplicate = conn.execute(
        "SELECT id FROM records WHERE fingerprint = ? AND id <> ?", (data["fingerprint"], record_id)
    ).fetchone()
    if duplicate:
        raise AppError(
            "修改后的内容与另一条记录相同，请检查单据编号或备注。",
            409, "duplicate_record", {"existing_id": duplicate["id"]},
        )
    timestamp = now_iso()
    try:
        with db.transaction() as tx:
            tx.execute(
                """UPDATE records SET business_date = ?, direction = ?, category = ?, department = ?,
                       counterparty = ?, payment_method = ?, doc_no = ?, note = ?, amount_cents = ?,
                       fingerprint = ?, updated_at = ? WHERE id = ?""",
                (
                    data["business_date"], data["direction"], data["category"], data["department"],
                    data["counterparty"], data["payment_method"], data["doc_no"], data["note"],
                    data["amount_cents"], data["fingerprint"], timestamp, record_id,
                ),
            )
            db.audit(
                "update", "record", record_id,
                f"修改为{direction_label(data['direction'])} {data['amount_cents'] / 100:.2f} 元",
                conn=tx,
            )
    except Exception as exc:
        if "UNIQUE constraint failed: records.fingerprint" in str(exc):
            raise AppError("修改后的内容与另一条记录相同", 409, "duplicate_record") from exc
        raise
    return get_record(record_id)


def delete_record(record_id: str) -> dict:
    conn = db.get_db()
    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise AppError("记录不存在或已删除", 404, "record_not_found")
    with db.transaction() as tx:
        tx.execute("DELETE FROM records WHERE id = ?", (record_id,))
        db.audit(
            "delete", "record", record_id,
            f"删除{row['business_date']} {direction_label(row['direction'])} {row['amount_cents'] / 100:.2f} 元",
            conn=tx,
        )
    return {"deleted": 1}


def bulk_delete(ids: list[str]) -> dict:
    if not isinstance(ids, list) or not ids:
        raise AppError("请选择要删除的记录", 400)
    ids = [clean_text(i)[:80] for i in ids if clean_text(i)]
    if len(ids) > 500:
        raise AppError("一次最多删除 500 条记录", 400)
    marks = ",".join("?" for _ in ids)
    with db.transaction() as tx:
        count = tx.execute(f"SELECT COUNT(*) FROM records WHERE id IN ({marks})", ids).fetchone()[0]
        tx.execute(f"DELETE FROM records WHERE id IN ({marks})", ids)
        db.audit("bulk_delete", "record", "", f"批量删除 {count} 条经营流水", conn=tx)
    return {"deleted": count}


def export_filter_label(filters: dict) -> str:
    parts = []
    if filters.get("start") or filters.get("end"):
        parts.append(f"{filters.get('start') or '最早'} 至 {filters.get('end') or '最新'}")
    if filters.get("direction"):
        parts.append(direction_label(filters["direction"]))
    if filters.get("category"):
        parts.append(f"类别：{filters['category']}")
    if filters.get("department"):
        parts.append(f"部门：{filters['department']}")
    if filters.get("q"):
        parts.append(f"搜索：{filters['q']}")
    return "；".join(parts) if parts else "全部数据"


def build_export_rows(filters: dict) -> tuple[list[dict], dict]:
    conn = db.get_db()
    where, values = build_where(filters)
    total = conn.execute(f"SELECT COUNT(*) FROM records{where}", values).fetchone()[0]
    if total > 100000:
        raise AppError("本次导出超过 10 万条，请缩小日期范围后再导出", 400, "export_too_large")
    rows = conn.execute(
        f"SELECT * FROM records{where} ORDER BY {SORT_OPTIONS.get(filters.get('sort'), SORT_OPTIONS['date_desc'])}",
        values,
    ).fetchall()
    records = [record_to_dict(row) for row in rows]
    summary = {
        "count": len(records),
        "income_cents": sum(r["amount_cents"] for r in records if r["direction"] == "income"),
        "expense_cents": sum(r["amount_cents"] for r in records if r["direction"] == "expense"),
    }
    summary["net_cents"] = summary["income_cents"] - summary["expense_cents"]
    return records, summary


def _safe_excel_text(value: Any) -> str:
    text = clean_text(value, multiline=True)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_xlsx(filters: dict) -> tuple[bytes, str]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    records, summary = build_export_rows(filters)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "经营流水"
    sheet.sheet_view.showGridLines = False
    headers = ["序号", "业务日期", "收支类型", "经营类别", "部门", "往来单位", "金额（元）",
               "结算方式", "单据编号", "摘要 / 备注", "来源", "来源位置", "录入时间"]
    header_fill = PatternFill("solid", fgColor="173F35")
    header_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="微软雅黑", size=10, color="2C3330")
    thin = Side(style="thin", color="E4E8E2")
    border = Border(bottom=thin)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    sheet.row_dimensions[1].height = 28
    for idx, record in enumerate(records, start=1):
        sheet.append([
            idx,
            record["business_date"],
            record["direction_label"],
            _safe_excel_text(record["category"]),
            _safe_excel_text(record["department"]),
            _safe_excel_text(record["counterparty"]),
            record["amount_cents"] / 100,
            _safe_excel_text(record["payment_method"]),
            _safe_excel_text(record["doc_no"]),
            _safe_excel_text(record["note"]),
            record["source_label"],
            _safe_excel_text(record["source_location"]),
            record["created_at"].replace("T", " ")[:19],
        ])
        row_index = sheet.max_row
        for cell in sheet[row_index]:
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=(cell.column in {10, 12}))
        sheet.cell(row=row_index, column=7).number_format = "#,##0.00"
        sheet.row_dimensions[row_index].height = 22
    widths = [6, 12, 9, 18, 12, 22, 14, 12, 16, 30, 10, 36, 20]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    if records:
        ref = f"A1:M{sheet.max_row}"
        tab = Table(displayName="LedgerTable", ref=ref)
        tab.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False
        )
        sheet.add_table(tab)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = ref

    summary_sheet = workbook.create_sheet("汇总")
    summary_sheet.sheet_view.showGridLines = False
    summary_sheet.column_dimensions["A"].width = 20
    summary_sheet.column_dimensions["B"].width = 22
    summary_sheet["A1"] = "经营数据导出汇总"
    summary_sheet["A1"].font = Font(name="微软雅黑", size=16, bold=True, color="173F35")
    summary_sheet.merge_cells("A1:C1")
    meta_rows = [
        ("导出时间", date.today().isoformat()),
        ("筛选条件", export_filter_label(filters)),
        ("记录笔数", summary["count"]),
        ("收入合计（元）", summary["income_cents"] / 100),
        ("支出合计（元）", summary["expense_cents"] / 100),
        ("经营净额（元）", summary["net_cents"] / 100),
    ]
    for row_index, (label, value) in enumerate(meta_rows, start=3):
        summary_sheet.cell(row=row_index, column=1, value=label).font = Font(
            name="微软雅黑", size=10, bold=True, color="66716B"
        )
        cell = summary_sheet.cell(row=row_index, column=2, value=value)
        cell.font = Font(name="微软雅黑", size=10, color="1D2622")
        if "元" in label:
            cell.number_format = "#,##0.00"
    # 分类汇总
    summary_sheet["A10"] = "经营类别"
    summary_sheet["B10"] = "收入（元）"
    summary_sheet["C10"] = "支出（元）"
    summary_sheet["D10"] = "净额（元）"
    for cell in summary_sheet[10]:
        if cell.value:
            cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F6B5B")
            cell.alignment = Alignment(horizontal="center")
    summary_sheet.column_dimensions["C"].width = 18
    summary_sheet.column_dimensions["D"].width = 18
    categories: dict[str, list[int]] = {}
    for record in records:
        bucket = categories.setdefault(record["category"], [0, 0])
        bucket[0 if record["direction"] == "income" else 1] += record["amount_cents"]
    for row_index, (name, amounts) in enumerate(
        sorted(categories.items(), key=lambda item: sum(item[1]), reverse=True), start=11
    ):
        summary_sheet.cell(row=row_index, column=1, value=_safe_excel_text(name))
        summary_sheet.cell(row=row_index, column=2, value=amounts[0] / 100).number_format = "#,##0.00"
        summary_sheet.cell(row=row_index, column=3, value=amounts[1] / 100).number_format = "#,##0.00"
        summary_sheet.cell(row=row_index, column=4, value=(amounts[0] - amounts[1]) / 100).number_format = "#,##0.00"

    workbook.properties.creator = config.APP_NAME
    workbook.properties.title = "经营流水导出"
    output = io.BytesIO()
    workbook.save(output)
    filename = f"经营流水_{date.today().strftime('%Y%m%d')}.xlsx"
    return output.getvalue(), filename


def export_csv(filters: dict) -> tuple[bytes, str]:
    import csv

    records, _summary = build_export_rows(filters)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["业务日期", "收支类型", "经营类别", "部门", "往来单位", "金额（元）",
                     "结算方式", "单据编号", "摘要/备注", "来源", "来源位置"])
    for record in records:
        writer.writerow([
            record["business_date"],
            record["direction_label"],
            csv_safe(record["category"]),
            csv_safe(record["department"]),
            csv_safe(record["counterparty"]),
            f"{record['amount_cents'] / 100:.2f}",
            csv_safe(record["payment_method"]),
            csv_safe(record["doc_no"]),
            csv_safe(record["note"]),
            record["source_label"],
            csv_safe(record["source_location"]),
        ])
    filename = f"经营流水_{date.today().strftime('%Y%m%d')}.csv"
    return ("\ufeff" + output.getvalue()).encode("utf-8"), filename
