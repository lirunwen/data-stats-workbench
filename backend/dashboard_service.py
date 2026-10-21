"""经营总览：KPI、趋势、构成、部门表现与数据质量。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import db
from .errors import AppError
from .utils import (
    FieldError,
    add_months,
    month_sequence,
    percent_change,
    period_ranges,
)
from .records_service import get_filter_options


def _totals(conn: Any, start: str, end: str) -> dict:
    row = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN direction = 'income' THEN amount_cents ELSE 0 END), 0) AS income,
             COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount_cents ELSE 0 END), 0) AS expense,
             COUNT(*) AS count
           FROM records WHERE business_date BETWEEN ? AND ?""",
        (start, end),
    ).fetchone()
    income = int(row["income"] or 0)
    expense = int(row["expense"] or 0)
    return {"income": income, "expense": expense, "net": income - expense, "count": int(row["count"] or 0)}


def _kpi(key: str, label: str, current: int, previous: int, *, is_money: bool = True) -> dict:
    return {
        "key": key,
        "label": label,
        "value": current if not is_money else round(current / 100, 2),
        "value_cents": current if is_money else None,
        "change_pct": percent_change(current, previous),
        "previous": previous if not is_money else round(previous / 100, 2),
        "unit": "元" if is_money else "笔",
    }


def _trend(conn: Any, end_month: str) -> list[dict]:
    months = month_sequence(end_month, 12)
    start = f"{months[0]}-01"
    end_day = add_months(date.fromisoformat(f"{months[-1]}-01"), 1) - timedelta(days=1)
    rows = conn.execute(
        """SELECT substr(business_date, 1, 7) AS ym, direction, SUM(amount_cents) AS amount,
                  COUNT(*) AS count
           FROM records WHERE business_date BETWEEN ? AND ? GROUP BY ym, direction""",
        (start, end_day.isoformat()),
    ).fetchall()
    bucket = {month: {"income": 0, "expense": 0, "count": 0} for month in months}
    for row in rows:
        if row["ym"] not in bucket:
            continue
        bucket[row["ym"]][row["direction"]] = int(row["amount"] or 0)
        bucket[row["ym"]]["count"] += int(row["count"] or 0)
    result = []
    for month in months:
        values = bucket[month]
        year, mon = month.split("-")
        result.append({
            "month": month,
            "label": f"{int(mon)}月",
            "year": int(year),
            "income_cents": values["income"],
            "expense_cents": values["expense"],
            "net_cents": values["income"] - values["expense"],
            "count": values["count"],
        })
    return result


def _category_breakdown(conn: Any, start: str, end: str, limit: int = 7) -> dict[str, dict]:
    """一次扫描同时算出收入与支出的类别构成。

    原实现按方向各查一次，同一段日期范围被扫两遍；这里合并为一条
    GROUP BY category, direction，方向拆分放到 Python 侧。
    """
    rows = conn.execute(
        """SELECT category, direction, SUM(amount_cents) AS amount, COUNT(*) AS count
           FROM records WHERE business_date BETWEEN ? AND ? AND direction IN ('income', 'expense')
           GROUP BY category, direction""",
        (start, end),
    ).fetchall()

    buckets: dict[str, list[dict]] = {"income": [], "expense": []}
    for row in rows:
        direction = row["direction"]
        if direction not in buckets:
            continue
        buckets[direction].append({
            "name": row["category"],
            "amount_cents": int(row["amount"] or 0),
            "count": int(row["count"] or 0),
        })

    result: dict[str, dict] = {}
    for direction, items in buckets.items():
        items.sort(key=lambda item: item["amount_cents"], reverse=True)
        total = sum(item["amount_cents"] for item in items)
        if len(items) > limit:
            rest = items[limit - 1:]
            items = items[:limit - 1]
            items.append({
                "name": "其他类别",
                "amount_cents": sum(item["amount_cents"] for item in rest),
                "count": sum(item["count"] for item in rest),
                "is_other": True,
            })
        for item in items:
            item["share"] = round(item["amount_cents"] / total * 100, 1) if total else 0
        result[direction] = {"items": items, "total_cents": total}
    return result


def _departments(conn: Any, start: str, end: str, limit: int = 7) -> list[dict]:
    rows = conn.execute(
        """SELECT department,
                  COALESCE(SUM(CASE WHEN direction = 'income' THEN amount_cents ELSE 0 END), 0) AS income,
                  COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount_cents ELSE 0 END), 0) AS expense,
                  COUNT(*) AS count
           FROM records WHERE business_date BETWEEN ? AND ?
           GROUP BY department ORDER BY (income + expense) DESC""",
        (start, end),
    ).fetchall()
    result = []
    for row in rows[:limit]:
        income = int(row["income"] or 0)
        expense = int(row["expense"] or 0)
        result.append({
            "name": row["department"],
            "income_cents": income,
            "expense_cents": expense,
            "net_cents": income - expense,
            "count": int(row["count"] or 0),
            "share": 0,
        })
    total = sum(item["income_cents"] + item["expense_cents"] for item in result) or 1
    for item in result:
        item["share"] = round((item["income_cents"] + item["expense_cents"]) / total * 100, 1)
    return result


def _quality(conn: Any, start: str, end: str) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN category = '未分类' THEN 1 ELSE 0 END), 0) AS uncategorized,
                  COALESCE(SUM(CASE WHEN department = '未指定' THEN 1 ELSE 0 END), 0) AS no_department,
                  COALESCE(SUM(CASE WHEN doc_no = '' THEN 1 ELSE 0 END), 0) AS no_doc_no
           FROM records WHERE business_date BETWEEN ? AND ?""",
        (start, end),
    ).fetchone()
    total = int(row["total"] or 0)
    since = (date.today() - timedelta(days=30)).isoformat()
    batch_row = conn.execute(
        """SELECT COUNT(*) AS batches, COALESCE(SUM(invalid_rows), 0) AS invalid,
                  COALESCE(SUM(duplicate_rows), 0) AS duplicate,
                  COALESCE(SUM(imported_rows), 0) AS imported,
                  MAX(created_at) AS last_import
           FROM batches WHERE substr(created_at, 1, 10) >= ?""",
        (since,),
    ).fetchone()
    uncategorized = int(row["uncategorized"] or 0)
    classification_rate = round((total - uncategorized) / total * 100, 1) if total else 100.0
    return {
        "records": total,
        "uncategorized": uncategorized,
        "no_department": int(row["no_department"] or 0),
        "no_doc_no": int(row["no_doc_no"] or 0),
        "classification_rate": classification_rate,
        "recent_batches": int(batch_row["batches"] or 0),
        "recent_invalid": int(batch_row["invalid"] or 0),
        "recent_duplicate": int(batch_row["duplicate"] or 0),
        "recent_imported": int(batch_row["imported"] or 0),
        "last_import": batch_row["last_import"],
    }


def _recent_batches(conn: Any, limit: int = 4) -> list[dict]:
    # 原实现先查批次、再为每个批次单独查首个来源（1+N 条 SQL）；
    # 这里用一次 LEFT JOIN 关联「每个批次 rowid 最小的来源」，一条语句取完。
    rows = conn.execute(
        """SELECT b.id, b.status, b.source_count, b.total_rows, b.imported_rows, b.updated_rows,
                  b.duplicate_rows, b.invalid_rows, b.warning_rows, b.created_at, b.is_demo,
                  s.file_name AS first_file, s.sheet_name AS first_sheet
           FROM batches b
           LEFT JOIN batch_sources s
                  ON s.rowid = (SELECT MIN(rowid) FROM batch_sources WHERE batch_id = b.id)
           ORDER BY b.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    status_labels = {
        "completed": "已完成", "partial": "部分导入", "duplicate": "全部重复",
        "failed": "未导入", "pending": "处理中",
    }
    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "status": row["status"],
            "status_label": status_labels.get(row["status"], row["status"]),
            "source_count": row["source_count"],
            "total_rows": row["total_rows"],
            "imported_rows": row["imported_rows"],
            "updated_rows": row["updated_rows"],
            "duplicate_rows": row["duplicate_rows"],
            "invalid_rows": row["invalid_rows"],
            "created_at": row["created_at"],
            "is_demo": bool(row["is_demo"]),
            "first_file": row["first_file"] or "",
            "first_sheet": row["first_sheet"] or "",
        })
    return result


def get_dashboard(preset: str = "year", start: str | None = None, end: str | None = None) -> dict:
    try:
        period = period_ranges(preset, start, end)
    except (FieldError, ValueError) as exc:
        raise AppError(str(exc), 400, "invalid_period") from exc
    conn = db.get_db()
    current = _totals(conn, period["start"], period["end"])
    if period.get("previous_start") and period.get("previous_end"):
        previous = _totals(conn, period["previous_start"], period["previous_end"])
        has_comparison = True
    else:
        previous = {"income": 0, "expense": 0, "net": 0, "count": 0}
        has_comparison = False

    kpis = [
        _kpi("income", "经营收入", current["income"], previous["income"]),
        _kpi("expense", "经营支出", current["expense"], previous["expense"]),
        _kpi("net", "经营净额", current["net"], previous["net"]),
        _kpi("count", "流水笔数", current["count"], previous["count"], is_money=False),
    ]
    trend = _trend(conn, period["end"][:7])
    breakdown = _category_breakdown(conn, period["start"], period["end"])
    expense_categories = breakdown["expense"]["items"]
    income_categories = breakdown["income"]["items"]
    expense_total = breakdown["expense"]["total_cents"]
    income_total = breakdown["income"]["total_cents"]
    departments = _departments(conn, period["start"], period["end"])
    quality = _quality(conn, period["start"], period["end"])
    recent = _recent_batches(conn)
    # 过滤选项在此处只算一次，由调用方复用（原实现路由层会再算一遍）。
    options = get_filter_options()
    total_records = options["total"] or 0
    range_has_data = current["count"] > 0

    largest = conn.execute(
        """SELECT id, business_date, direction, category, department, counterparty, amount_cents
           FROM records WHERE business_date BETWEEN ? AND ?
           ORDER BY amount_cents DESC LIMIT 1""",
        (period["start"], period["end"]),
    ).fetchone()
    largest_record = None
    if largest:
        largest_record = dict(largest)
        largest_record["amount"] = round(largest_record["amount_cents"] / 100, 2)

    net_margin = round(current["net"] / current["income"] * 100, 1) if current["income"] else 0
    top_income = income_categories[0] if income_categories else None
    top_expense = expense_categories[0] if expense_categories else None
    average_ticket = round(current["income"] / current["count"], 2) if current["count"] else 0

    return {
        "period": period,
        "kpis": kpis,
        "current": current,
        "previous": previous,
        "has_comparison": has_comparison,
        "trend": trend,
        "composition": {
            "expense": expense_categories,
            "income": income_categories,
            "expense_total_cents": expense_total,
            "income_total_cents": income_total,
        },
        "departments": departments,
        "quality": quality,
        "recent_batches": recent,
        "has_data": total_records > 0,
        "range_has_data": range_has_data,
        "total_records": total_records,
        "demo_count": options["demo_count"],
        "insight": {
            "net_margin": net_margin,
            "top_income_category": top_income,
            "top_expense_category": top_expense,
            "average_ticket": average_ticket,
            "largest_record": largest_record,
        },
        "server_date": date.today().isoformat(),
        # 由路由层取出后作为同级 options 返回，避免再调一次 get_filter_options()。
        "options": options,
    }
