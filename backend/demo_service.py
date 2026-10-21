"""可选的演示数据：首次体验与界面验收使用，可一键清除。"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from . import db
from .errors import AppError
from .utils import build_fingerprint, direction_label, new_id, now_iso

DEMO_BATCH_ID = "b_demo_2026"

INCOME_CATEGORIES = {
    "产品销售收入": 0.60,
    "项目服务收入": 0.22,
    "渠道分销收入": 0.13,
    "其他业务收入": 0.05,
}
EXPENSE_CATEGORIES = {
    "原材料采购": 0.34,
    "员工薪酬": 0.23,
    "市场推广费": 0.14,
    "房租水电": 0.10,
    "差旅交通费": 0.07,
    "税费及附加": 0.06,
    "办公行政费": 0.04,
    "其他支出": 0.02,
}
DEPARTMENTS = ["销售部", "市场部", "产品部", "运营部", "财务部", "行政部"]
DEPARTMENT_BY_CATEGORY = {
    "产品销售收入": ["销售部", "销售部", "销售部", "运营部"],
    "项目服务收入": ["产品部", "产品部", "销售部"],
    "渠道分销收入": ["销售部", "市场部"],
    "其他业务收入": ["运营部", "财务部"],
    "原材料采购": ["产品部", "运营部"],
    "员工薪酬": ["财务部", "行政部"],
    "市场推广费": ["市场部", "市场部", "销售部"],
    "房租水电": ["行政部"],
    "差旅交通费": ["销售部", "市场部", "产品部"],
    "税费及附加": ["财务部"],
    "办公行政费": ["行政部", "财务部"],
    "其他支出": ["运营部", "行政部"],
}
CUSTOMERS = [
    "华东智造科技有限公司", "远山实业集团", "新澜数字科技", "海川供应链有限公司",
    "明途教育科技", "恒越医疗器械", "南岸文化传播", "启辰新能源", "澄明软件",
    "长风商贸", "一格设计事务所", "禾木食品", "星桥物流", "青屿酒店管理",
]
SUPPLIERS = [
    "盛丰原材料有限公司", "嘉禾办公用品", "蓝湾物业服务", "拓新广告传媒",
    "云和差旅服务", "东岸包装材料", "诚达物流", "万景机电设备", "德润咨询",
    "同源信息技术", "城北水电服务", "华信印刷",
]
PAYMENTS = ["银行转账", "银行转账", "银行转账", "网银支付", "银行承兑", "现金"]
NOTES_INCOME = [
    "合同首款到账", "项目阶段验收款", "产品销售回款", "渠道月度结算", "老客户续约款",
    "季度框架订单回款", "服务尾款", "样品订单收款",
]
NOTES_EXPENSE = [
    "本月采购结算", "项目差旅报销", "季度推广投放", "办公场地费用", "团队薪酬发放",
    "设备维保费用", "物流运输费用", "税费缴纳", "客户招待费用", "软件服务续费",
]


def _pick(weighted: dict[str, float], rng: random.Random) -> str:
    names = list(weighted)
    weights = list(weighted.values())
    return rng.choices(names, weights=weights, k=1)[0]


def demo_exists() -> bool:
    row = db.get_db().execute("SELECT 1 FROM batches WHERE id = ?", (DEMO_BATCH_ID,)).fetchone()
    return bool(row)


def seed_demo() -> dict:
    conn = db.get_db()
    if demo_exists():
        count = conn.execute("SELECT COUNT(*) FROM records WHERE is_demo = 1").fetchone()[0]
        return {"created": 0, "count": count, "message": "演示数据已存在"}
    rng = random.Random(20261021)
    today = date.today()
    start = date(today.year - 1, 1, 2)
    rows = []
    income_seq = expense_seq = 0
    current = start
    while current <= today:
        weekday = current.weekday()
        entries = []
        if weekday < 5:
            income_count = rng.choices([0, 1, 2], weights=[15, 58, 27], k=1)[0]
            expense_count = rng.choices([0, 1, 2], weights=[20, 55, 25], k=1)[0]
        else:
            income_count = rng.choices([0, 1], weights=[80, 20], k=1)[0]
            expense_count = rng.choices([0, 1], weights=[88, 12], k=1)[0]
        year_factor = 1.0 if current.year == today.year else 0.78
        month_factor = (0.82 + current.month * 0.035) * year_factor
        for _ in range(income_count):
            income_seq += 1
            category = _pick(INCOME_CATEGORIES, rng)
            if rng.random() < 0.018:
                category = "未分类"
            base = rng.lognormvariate(10.9, 0.64) * month_factor
            amount = max(3200, min(326000, round(base / 100) * 100 - rng.choice([0, 0, 0, 50, 80])))
            if rng.random() < 0.012:
                amount = rng.randint(480000, 860000)
            department = rng.choice(DEPARTMENT_BY_CATEGORY.get(category, DEPARTMENTS))
            if rng.random() < 0.045:
                department = "未指定"
            doc_no = f"YS-{current.year}-{income_seq:04d}" if rng.random() > 0.06 else ""
            customer = rng.choice(CUSTOMERS)
            note = rng.choice(NOTES_INCOME)
            if rng.random() < 0.16:
                note = f"{current.month}月{note}"
            entries.append(("income", category, department, amount * 100, doc_no, customer, note))
        for _ in range(expense_count):
            expense_seq += 1
            category = _pick(EXPENSE_CATEGORIES, rng)
            if rng.random() < 0.024:
                category = "未分类"
            base = rng.lognormvariate(10.15, 0.72) * month_factor
            amount = max(380, min(186000, round(base / 100) * 100))
            if category == "员工薪酬" and current.day in (8, 9, 10):
                amount = rng.randint(180000, 320000)
            department = rng.choice(DEPARTMENT_BY_CATEGORY.get(category, DEPARTMENTS))
            if rng.random() < 0.055:
                department = "未指定"
            doc_no = f"FK-{current.year}-{expense_seq:04d}" if rng.random() > 0.08 else ""
            supplier = rng.choice(SUPPLIERS)
            note = rng.choice(NOTES_EXPENSE)
            if category == "房租水电":
                note = f"{current.month}月{note}"
            entries.append(("expense", category, department, amount * 100, doc_no, supplier, note))
        rng.shuffle(entries)
        for direction, category, department, amount_cents, doc_no, counterparty, note in entries:
            rows.append({
                "business_date": current.isoformat(),
                "direction": direction,
                "category": category,
                "department": department,
                "amount_cents": int(amount_cents),
                "doc_no": doc_no,
                "counterparty": counterparty,
                "note": note,
                "payment_method": rng.choice(PAYMENTS),
                "source_row": len(rows) + 2,
            })
        current += timedelta(days=1)

    if not rows:
        rows = [{
            "business_date": today.isoformat(),
            "direction": "income",
            "category": "产品销售收入",
            "department": "销售部",
            "amount_cents": 12800000,
            "doc_no": f"YS-{today.year}-DEMO",
            "counterparty": "示例客户",
            "note": "示例收入",
            "payment_method": "银行转账",
            "source_row": 2,
        }]
    # 保证单据号唯一，避免演示数据相互判重。
    for index, row in enumerate(rows, start=1):
        if row["doc_no"]:
            row["doc_no"] = f"DEMO-{row['doc_no']}"
        row["fingerprint"] = build_fingerprint(
            date_text=row["business_date"],
            direction=row["direction"],
            amount_cents=row["amount_cents"],
            category=row["category"],
            department=row["department"],
            counterparty=row["counterparty"],
            note=row["note"],
            doc_no=row["doc_no"],
        )
    timestamp = now_iso()
    warnings = sum(1 for row in rows if row["category"] == "未分类" or row["department"] == "未指定")
    with db.transaction() as tx:
        tx.execute(
            """INSERT INTO batches(id, status, session_token, source_count, total_rows, imported_rows,
                   updated_rows, duplicate_rows, warning_rows, invalid_rows, duplicate_policy,
                   duration_ms, is_demo, note, created_at)
               VALUES(?, 'completed', '', 1, ?, ?, 0, 0, ?, 0, 'skip', 0, 1, '内置演示数据', ?)""",
            (DEMO_BATCH_ID, len(rows), len(rows), warnings, timestamp),
        )
        tx.executemany(
            """INSERT OR IGNORE INTO records(id, business_date, direction, category, department,
                   counterparty, payment_method, doc_no, note, amount_cents, fingerprint, source_type,
                   batch_id, source_file, source_sheet, source_row, is_demo, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', ?, '示例数据_2025-2026年经营流水.xlsx',
                       '经营流水', ?, 1, ?, ?)""",
            [
                (
                    new_id("r"), row["business_date"], row["direction"], row["category"],
                    row["department"], row["counterparty"], row["payment_method"], row["doc_no"],
                    row["note"], row["amount_cents"], row["fingerprint"], DEMO_BATCH_ID,
                    row["source_row"], timestamp, timestamp,
                )
                for row in rows
            ],
        )
        inserted = tx.execute("SELECT COUNT(*) FROM records WHERE is_demo = 1").fetchone()[0]
        tx.execute(
            """INSERT INTO batch_sources(id, batch_id, source_id, file_name, file_type, sheet_name,
                   email_subject, email_from, email_date, presence, header_row, total_rows, imported_rows,
                   updated_rows, duplicate_rows, warning_rows, invalid_rows, mapping_json, errors_json,
                   warnings_json, error_report, created_at)
               VALUES(?, ?, 's1', '示例数据_2025-2026年经营流水.xlsx', 'xlsx', '经营流水', '', '', '',
                      'file', 1, ?, ?, 0, 0, ?, 0, '{}', '[]', '[]', '', ?)""",
            (new_id("bs"), DEMO_BATCH_ID, len(rows), inserted, warnings, timestamp),
        )
        db.audit("demo_seed", "batch", DEMO_BATCH_ID, f"载入 {inserted} 条演示流水", conn=tx)
    return {"created": inserted, "count": inserted, "message": f"已载入 {inserted} 条演示数据"}


def clear_demo() -> dict:
    conn = db.get_db()
    with db.transaction() as tx:
        count = tx.execute("SELECT COUNT(*) FROM records WHERE is_demo = 1").fetchone()[0]
        tx.execute("DELETE FROM records WHERE is_demo = 1")
        tx.execute("DELETE FROM batch_sources WHERE batch_id = ?", (DEMO_BATCH_ID,))
        tx.execute("DELETE FROM batches WHERE is_demo = 1")
        db.audit("demo_clear", "batch", DEMO_BATCH_ID, f"清除 {count} 条演示流水", conn=tx)
    return {"deleted": count}


def demo_batch_summary() -> dict | None:
    row = db.get_db().execute(
        "SELECT id, imported_rows, warning_rows, created_at FROM batches WHERE id = ?",
        (DEMO_BATCH_ID,),
    ).fetchone()
    return dict(row) if row else None
