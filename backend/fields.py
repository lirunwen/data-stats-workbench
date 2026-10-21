"""经营流水的标准字段与中文表头同义词。"""
from __future__ import annotations

FIELD_DEFS = [
    {
        "key": "date",
        "label": "业务日期",
        "required": True,
        "type": "date",
        "hint": "交易或记账发生的日期",
        "aliases": [
            "业务日期", "交易日期", "记账日期", "发生日期", "入账日期", "日期", "时间", "业务时间",
            "交易时间", "月份", "年月", "期间", "账期", "所属月份", "统计月份",
            "date", "businessdate", "transactiondate", "bookdate", "month", "period",
        ],
    },
    {
        "key": "amount",
        "label": "金额",
        "required": True,
        "type": "money",
        "hint": "同一列填写金额，或使用收入/支出双列",
        "aliases": [
            "金额", "发生额", "交易金额", "含税金额", "价税合计", "合计金额", "收支金额", "本次金额",
            "实收金额", "实付金额", "收入金额", "支出金额", "收款金额", "付款金额", "amount", "money", "total",
        ],
    },
    {
        "key": "category",
        "label": "经营类别",
        "required": False,
        "type": "text",
        "hint": "如产品销售收入、市场推广费；留空会进入“未分类”",
        "aliases": [
            "经营类别", "业务类别", "费用类别", "收入类别", "费用类型", "收入类型", "科目", "会计科目",
            "经营科目", "类别", "项目", "业务类型", "费用项目", "收入项目", "category", "account", "item",
        ],
    },
    {
        "key": "department",
        "label": "部门",
        "required": False,
        "type": "text",
        "hint": "用于部门经营分析",
        "aliases": [
            "部门", "归属部门", "核算部门", "业务部门", "事业部", "门店", "机构", "团队", "科室",
            "department", "dept", "team", "branch",
        ],
    },
    {
        "key": "counterparty",
        "label": "往来单位",
        "required": False,
        "type": "text",
        "hint": "客户、供应商或交易对方",
        "aliases": [
            "往来单位", "对方单位", "交易对方", "客户名称", "供应商名称", "客户", "供应商", "往来客户",
            "往来供应商", "单位名称", "对方", "counterparty", "customer", "vendor", "supplier",
        ],
    },
    {
        "key": "payment_method",
        "label": "结算方式",
        "required": False,
        "type": "text",
        "hint": "银行转账、现金、承兑等",
        "aliases": [
            "结算方式", "支付方式", "付款方式", "收款方式", "结算", "支付渠道", "收付方式", "银行账户",
            "paymentmethod", "payment", "settlement",
        ],
    },
    {
        "key": "doc_no",
        "label": "单据编号",
        "required": False,
        "type": "text",
        "hint": "有单据编号时，会结合编号、金额和类别判重",
        "aliases": [
            "单据编号", "单据号", "凭证编号", "凭证号", "订单号", "合同号", "流水号", "发票号", "发票号码",
            "业务编号", "单号", "编号", "docno", "documentno", "voucher", "invoiceno", "orderno",
        ],
    },
    {
        "key": "note",
        "label": "摘要 / 备注",
        "required": False,
        "type": "text",
        "hint": "用途、事由或说明",
        "aliases": [
            "摘要", "备注", "说明", "用途", "事由", "业务摘要", "交易说明", "附言", "note", "memo",
            "description", "remark", "summary",
        ],
    },
]

FIELD_MAP = {item["key"]: item for item in FIELD_DEFS}
FIELD_LABELS = {item["key"]: item["label"] for item in FIELD_DEFS}
REQUIRED_FIELDS = [item["key"] for item in FIELD_DEFS if item["required"]]

DIRECTION_ALIASES = [
    "收支类型", "收支方向", "收支类别", "收付类型", "收付方向", "借贷方向", "借贷", "方向", "交易方向",
    "业务方向", "资金方向", "收支", "direction", "inout", "type",
]

AMOUNT_INCOME_ALIASES = {
    "收入金额", "收入额", "收款金额", "实收金额", "贷方金额", "进账金额", "收入",
    "incomeamount", "revenueamount", "creditamount",
}
AMOUNT_EXPENSE_ALIASES = {
    "支出金额", "支出额", "付款金额", "实付金额", "借方金额", "出账金额", "支出",
    "expenseamount", "costamount", "debitamount",
}
