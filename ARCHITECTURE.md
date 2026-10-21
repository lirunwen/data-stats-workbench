# 衡策经营数据工作台 · 架构设计

版本：1.0.0  
目标环境：Windows 11 x64 单机 / 办公室内网 / 完全离线 / 非技术背景文员  
形态：本地 Web 桌面应用（浏览器界面 + 本机服务 + SQLite）

---

## 1. 设计目标与约束

| 约束 | 设计回应 |
| --- | --- |
| 办公室内网、完全断网 | 全部前后端资源随程序分发，无 CDN / Web Font / 云接口；页面只访问 `127.0.0.1` |
| 不依赖外部环境 | Windows 包内置 Python 3.12 embeddable 与全部依赖；目标机无需 Python / Node / 数据库 / Office |
| 可直接安装 | 免安装便携目录 + 无需管理员的 PowerShell 安装脚本，创建桌面 / 开始菜单快捷方式 |
| 频繁功能调整 | 稳定启动器 + 版本目录 + 离线升级包；业务代码与 Web 资源可独立升级，数据目录不动 |
| 非技术用户 | 中文全链路、智能表头匹配、导入前预览、异常可下载、无术语堆叠 |
| 财务数据准确 | 金额按“分”整数存储；事务写入；日期/金额严格解析；判重前置；异常不落库 |
| 数据不出本机 | SQLite 单文件 + 本地文件仓；只监听回环地址；无遥测 |
| 长期稳定 | WAL、完整性检查、每日备份、升级前备份、单实例、日志、紧急回退脚本 |

**明确不做：**账号体系、多端同步、云端协作、多人并发写入、审批流。
本工作台按“一台电脑、一位文员、一个本地数据目录”设计。

---

## 2. 总体拓扑

```text
┌──────────────────────────── 单台 Windows 11 PC（无网络也可运行） ────────────────────────────┐
│                                                                                             │
│  Edge / Chrome                                                      Python 3.12 runtime      │
│  ┌──────────────────────┐        HTTP 127.0.0.1         ┌──────────────────────────────┐     │
│  │ 原生 HTML / CSS / JS │ ◀──────────────────────────▶ │ Flask API + waitress          │     │
│  │ 仪表盘 / 导入 / 台账 │                                │ 业务服务层                     │     │
│  └──────────────────────┘                                └──────────────┬───────────────┘     │
│                                                                          │                    │
│                                         ┌────────────────────────────────┼──────────────────┐ │
│                                         ▼                                ▼                  ▼ │
│                              ┌────────────────────┐          ┌──────────────────┐  ┌──────────────┐
│                              │ SQLite (WAL)       │          │ data/staging     │  │ data/reports │
│                              │ 记录 / 批次 / 设置 │          │ 原文件 / 会话    │  │ 异常 CSV     │
│                              └────────────────────┘          └──────────────────┘  └──────────────┘
│                                         │                                                 │
│                                         ▼                                                 ▼
│                              ┌────────────────────┐                           ┌──────────────────┐
│                              │ data/backup        │                           │ data/updates     │
│                              │ 每日 / 升级前备份  │                           │ 升级包暂存       │
│                              └────────────────────┘                           └──────────────────┘
│                                                                                             │
│  稳定启动器 launcher.py → program/active.txt → program/versions/<version>/app.py            │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**运行链路：**

1. 用户双击 `Start-HengCe.vbs`；
2. 稳定启动器读取 `program/active.txt`，设置环境变量，把版本目录加入 `sys.path`；
3. 启动 `app.py`，初始化 SQLite、日志、备份、导入暂存清理；
4. waitress 只绑定 `127.0.0.1`，默认端口 8765，被占用则自动顺延；
5. 自动打开浏览器；
6. 浏览器页面通过同源 `/api/*` 获取数据，通过 `/static/*` 加载全部本地资源；
7. 单实例检测依赖 `data/runtime.json` + `/api/health`，重复启动只会打开已有页面。

---

## 3. 技术选型

| 层 | 选择 | 理由 |
| --- | --- | --- |
| 运行时 | Python 3.12.10 embeddable | 官方 Windows 免安装运行时；打包可控；无管理员权限要求 |
| Web 服务 | Flask 3 + waitress | 轻量、纯 Python、Windows 稳定、无外部服务 |
| 数据库 | SQLite（stdlib） | 单文件、WAL、事务、无安装；适合单机经营数据 |
| Excel | openpyxl（.xlsx/.xlsm）、xlrd（.xls） | 保留模板样式；不依赖 Office；避免 pandas 的体积与兼容负担 |
| Outlook | python-oxmsg（MIT） | 读取 MSG/OFT 元数据、正文、附件，依赖少且许可宽松 |
| 邮件 | Python 标准库 email | EML 与 MIME 附件解析 |
| 前端 | 原生 HTML + CSS + ES Modules | 无构建链、无 Node 运行时、升级简单、离线最稳 |
| 图表 | 手写 SVG | 无第三方图表库、无外部资源、完全可控 |
| 打包 | 官方 Python embeddable + pip 下载的 Windows wheels | 目标机无 Python；12 MB 级离线 zip |

刻意不使用：

- **Electron / Tauri**：构建链、体积、原生 Office 解析能力都不如“Python + 本地 Web”；
- **pandas**：本工具的数据量与计算逻辑不需要，反而增加打包体积；
- **云数据库 / 在线字体 / CDN**：在断网环境会直接失效；
- **前端 SPA 构建链**：避免“构建产物缺失”导致无法启动，也方便内网直接替换文件；
- **extract-msg（GPL）**：改用 MIT 许可的 python-oxmsg，降低分发许可风险。

---

## 4. 代码模块

```text
app.py
  启动、参数、数据目录、日志、单实例、端口、waitress、浏览器

backend/
  config.py             路径、版本、默认设置、导入限制
  api.py                Flask Blueprint、路由、请求校验、Host/Origin 防护
  db.py                 SQLite 连接、WAL、迁移、设置、审计、在线备份
  utils.py              日期/金额解析、文本清洗、指纹、期间计算、安全 CSV
  readers.py            XLSX/XLSM/XLS/CSV 读取、表头识别、表头手动重设
  email_reader.py       MSG/OFT/EML、HTML 表格、附件提取
  mapping.py            表头同义词、自动映射、映射合法性校验
  import_service.py     会话暂存、逐行校验、判重、预测、事务提交、异常报告
  records_service.py    明细查询、筛选、CRUD、分页、Excel/CSV 导出
  dashboard_service.py  KPI、对比、趋势、分类、部门、数据质量
  template_service.py   标准导入模板生成
  demo_service.py       可选演示数据
  updater.py            离线升级包校验、安装、版本切换、重启
web/
  index.html            应用壳、图标 sprite
  css/app.css           设计系统 + 所有页面
  js/app.js             哈希路由、全局交互
  js/api.js             请求/上传/下载/错误规范
  js/store.js           全局状态
  js/ui.js              格式化、Toast、Modal、帮助
  js/charts.js          SVG 趋势图、环形图、迷你折线
  js/pages/*.js         总览 / 台账 / 导入向导 / 导入记录 / 设置
packaging/
  build_portable.py     组装 Windows 便携包
  make_update.py        生成离线升级包
  launcher.py           稳定启动器
  recovery.py           紧急版本回退
  windows/              安装、诊断、恢复、README、图标、第三方声明
tests/
  smoke.py              70 项端到端后端测试
  updater_selftest.py   升级机制自测
```

---

## 5. 数据存储方案

### 5.1 SQLite 数据库

数据库文件：`data/hengce.db`。连接参数：

- `PRAGMA journal_mode = WAL`：读写并发更稳，写入不阻塞仪表盘读取；
- `PRAGMA synchronous = NORMAL`；
- `PRAGMA foreign_keys = ON`；
- `busy_timeout = 15000`；
- 每个请求独立连接，请求结束关闭；
- 启动时执行 `PRAGMA quick_check`，失败则拒绝启动；
- 版本化迁移：`PRAGMA user_version` + `backend/db.py` 中的迁移表。

#### records

| 字段 | 说明 |
| --- | --- |
| `id` | `r_YYYYMMDDHHMMSS_随机串` |
| `business_date` | ISO 日期 `YYYY-MM-DD` |
| `direction` | `income` / `expense` |
| `category` | 经营类别，缺失写“未分类” |
| `department` | 部门，缺失写“未指定” |
| `counterparty` / `payment_method` / `doc_no` / `note` | 文本，长度受限 |
| `amount_cents` | **整数分**，避免浮点误差 |
| `fingerprint` | SHA-256 判重指纹，唯一索引 |
| `source_type` | `manual` / `import` |
| `batch_id` | 导入批次 |
| `source_file` / `source_sheet` / `source_row` | 行级来源追溯 |
| `is_demo` | 演示数据标记 |
| `created_at` / `updated_at` | 本地时区 ISO 时间 |

索引：日期、方向 + 日期、类别、部门、批次、指纹唯一索引。

#### batches / batch_sources

- `batches`：一次导入的汇总（状态、文件数、数据行数、新增、更新、重复、异常、提示、耗时、策略、演示标记、时间）。
- `batch_sources`：批次内每个数据表（文件名、工作表、邮件主题 / 发件人、来源类型、表头行、各项统计、映射快照、异常摘要、完整异常 CSV 路径）。
- `batches.session_token` 唯一（部分索引），保证重复提交具有幂等性。

#### mapping_profiles

- `header_signature`：标准化表头串的 SHA-256；
- 保存表头数组与映射 JSON；
- 相同表头再次导入时自动套用；
- 用户可在设置页删除记忆。

#### settings / audit_logs

- `settings`：单位名称、默认重复策略、记住映射等；
- `audit_logs`：导入、增删改、备份、升级等操作的可读摘要。

### 5.2 文件目录

```text
data/
├─ hengce.db
├─ hengce.db-wal / -shm
├─ staging/<session>/
│  ├─ session.json.gz      本次上传解析后的表格数据与会话状态
│  └─ raw/                 原始上传文件与邮件附件
├─ reports/<batch>/<source>.csv   异常/重复/提示明细（UTF-8 BOM）
├─ backup/                 每日自动备份、手动、升级前、回退前备份
├─ updates/                升级包副本与暂存目录
└─ logs/hengce.log         运行日志（轮转）
```

- 暂存会话 24 小时后清理。
- 重复导入的源文件不重复落盘。
- 备份策略：手动 / 升级前保留最近 12 份；每日自动备份保留 14 份。
- 导入记录通过 `batch_sources` 保留文件名、工作表、邮件主题、映射与异常报告；
  数据库不长期保存原始邮件和附件，以控制磁盘占用与敏感数据暴露面。

### 5.3 数据目录与程序目录分离

| 场景 | 程序 | 数据 |
| --- | --- | --- |
| 便携运行 | 解压目录 | `<解压目录>\data` |
| 安装运行 | `%LOCALAPPDATA%\HengCeWorkbench\program` | `%LOCALAPPDATA%\HengCeWorkbench\data` |

升级只覆盖 `program/versions`，不会触碰 `data`。
启动器通过 `HENGCE_DATA_DIR`、`HENGCE_INSTALL_ROOT`、`HENGCE_VERSION_DIR` 传递路径。

---

## 6. 导入链路

### 6.1 上传与分析

```text
文件/邮件 → 保存到 staging/raw → 文件类型分派
  ├─ Excel: 每个非空工作表
  ├─ CSV/TSV/TXT: 编码识别 + 分隔符嗅探
  └─ Outlook/EML: 元数据 + 正文表格 + 附件
        └─ 附件 Excel 继续按工作表展开
→ 表头检测 → 前 12 行候选 → 自动映射 → session.json.gz
```

- 支持多文件、多工作表、邮件附件与邮件正文表格。
- 单文件 80 MB、单次上传 240 MB、单表 50,000 行、单表 80 列、单次最多 120 个上传文件。
- `.pst/.ost` 给出明确提示：先在 Outlook 中另存为 `.msg`。
- 服务器只保存安全文件名；附件不可执行、不宏运行。

### 6.2 表头识别

- 扫描前 20 个非空行，基于：
  - 已知中英文表头同义词命中数量；
  - 非空单元格数量与唯一性；
  - 文本 / 数字比例；
  - 下一行是否有数据；
  - 行位置惩罚。
- 用户可在映射页把表头行切换到文件前 12 行中的任意一行；
- 重设表头后重新推断映射，再交用户确认。

### 6.3 自动字段映射

- 字段同义词表覆盖日期、金额、收支、类别、部门、单位、结算、单据号、备注。
- 支持“科目/费用类型/销售收入”等派生表头；
- 没有明确收支表头时，用样例值识别“收入/支出”列；
- 没有收支列时，根据文件名 / 工作表名建议整表收入或整表支出；
- 收入金额 / 支出金额双列会自动进入 split 模式。

### 6.4 逐行标准化与校验

| 规则 | 处理 |
| --- | --- |
| 日期 | 支持 ISO、斜杠、点、中文日期、Excel 序列、仅年月（提示并按当月 1 日） |
| 金额 | 去除 `¥`、`￥`、`$`、千分位、`元`、`万元`；转为整数分；拒绝 0、负数、NaN、超大值 |
| 收支 | 支持收入/支出、收/付、借/贷、in/out 等；无法识别为异常 |
| 类别 | 空值写“未分类”，产生可导入的 warning |
| 部门 | 空值写“未指定”，用于质量分析 |
| 文本长度 | 超长截断并提示；备注保留换行 |
| 未来日期 | 超过未来一年提示 |
| 大额 | 超过 5000 万提示 |
| 双列金额 | 同一行只填一列 → 单条记录；两列都填 → 拆成收入与支出两条记录并提示 |

异常行不进入数据库；warning 行可以导入。
所有异常包含原始行号与可读原因。

### 6.5 判重

指纹在应用层按规范化后的业务字段计算，SHA-256 后加唯一索引。

```text
有单据号：SHA256(年份 + 收支 + 单据编号 + 金额分 + 类别)
无单据号：SHA256(日期 + 收支 + 金额分 + 类别 + 部门 + 往来单位 + 备注)
```

- 有单据号时仍纳入金额和类别，避免把同一单据的多个明细行误合并。
- 可识别批内重复与库内重复。
- 跳过策略：只增加重复计数。
- 更新策略：命中原记录 ID，更新业务字段与来源信息；批内重复仍只会处理第一条。
- 预览阶段即可看到重复数量与动作，不会先写入再回滚。

### 6.6 提交事务

1. 重新读取暂存会话并重新校验（防止预览后状态变化）；
2. `BEGIN IMMEDIATE`；
3. 写入 batch / batch_sources；
4. 新增或更新 records；
5. 为异常/重复/提示行写完整 CSV；
6. 更新批次最终统计；
7. `COMMIT`；
8. 保存字段映射习惯；
9. 标记会话已提交。

中途任何异常回滚事务，已存在的流水不会变。
重复提交由 `session_token` 与导入互斥锁保护。

### 6.7 导入结果和异常

- 结果页显示新增、更新、重复、异常、提示；
- 异常明细 CSV 含行号、字段、原因、原始业务值；
- 导入记录可从异常明细 zip 下载全部数据表的报告；
- 历史批次保留映射快照与统计。

---

## 7. 自动汇总与仪表盘

### 7.1 期间

- 本月 / 本季 / 本年 / 全部 / 自定义；
- KPI 与构成按所选期间；
- 对比期间：
  - 本月 → 上月同期；
  - 本季 → 上季度同期；
  - 本年 → 上年同期；
  - 自定义 → 前一个等长周期；
  - 全部 → 不比较。

### 7.2 仪表盘数据

- KPI：收入、支出、净额、笔数，含上期百分比；
- 趋势：固定 12 个月的月度收入 / 支出 / 净额；
- 构成：当前期间收入、支出分别按类别汇总，取前 7 项，其余合并为“其他类别”；
- 部门：收入、支出、净额、总额排行；
- 质量：分类完整度、未分类、未填部门、近 30 天异常与重复识别；
- 洞察：净利率、最大单笔、近期导入；
- 最近导入：从 batches 读取，可点开历史详情。

所有汇总由 SQLite `GROUP BY` 实时计算，不维护额外的汇总表，避免“汇总与明细不一致”。
数据规模（数月到数年、数万流水）下性能足够。

### 7.3 可视化

- 手写 SVG：分组柱 + 净额折线 + 环形构成 + KPI 迷你折线；
- 纯本地渲染，无第三方图表库；
- 响应式，SVG 附带 ARIA 描述；
- 类别与部门可点击下钻到台账筛选。

---

## 8. 台账查询与导出

- 条件：关键字、收支、类别、部门、日期区间、来源、导入批次；
- 排序：日期、金额、最近修改；
- 分页：10 / 20 / 50 / 100；
- 返回当前筛选的合计（不只是当前页）；
- 手工新增 / 编辑 / 删除 / 批量删除；
- 搜索覆盖类别、部门、往来单位、结算方式、单据号、备注、来源文件；
- Excel 导出：
  - 数据页 + 汇总页；
  - 带表头样式、数字格式、冻结、筛选；
  - 表头文本做公式注入防护；
- CSV 导出带 UTF-8 BOM，便于 Excel 打开。

---

## 9. HTTP API（主要端点）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 / 单实例探测 |
| GET | `/api/bootstrap` | 应用信息、设置、选项、版本状态 |
| GET | `/api/dashboard` | 仪表盘汇总 |
| GET | `/api/records` | 分页筛选 |
| POST | `/api/records` | 新增 |
| PUT/DELETE | `/api/records/<id>` | 修改 / 删除 |
| POST | `/api/records/bulk-delete` | 批量删除 |
| GET | `/api/records/export` | Excel / CSV |
| POST | `/api/imports/analyze` | 上传并展开数据表 |
| PUT | `/api/imports/<token>/sources/<id>/header` | 调整表头行 |
| POST | `/api/imports/<token>/preview` | 校验 + 判重预览 |
| POST | `/api/imports/<token>/preview-export` | 校验明细 CSV |
| POST | `/api/imports/<token>/commit` | 事务导入 |
| GET | `/api/batches` | 导入历史 |
| GET | `/api/batches/<id>` | 批次详情 |
| GET | `/api/batches/<id>/sources/<sid>/errors.csv` | 单个表异常 CSV |
| GET | `/api/batches/<id>/errors.zip` | 全部异常明细 zip |
| GET/PUT | `/api/settings` | 设置 |
| POST | `/api/maintenance/backup` | 手动备份下载 |
| POST | `/api/demo` / DELETE | 演示数据 |
| POST | `/api/update/inspect` | 校验离线升级包 |
| POST | `/api/update/apply` | 安装版本 |
| POST | `/api/update/activate` | 切回 / 取消待生效版本 |
| POST | `/api/update/restart` | 重启并载入新版本 |
| POST | `/api/application/quit` | 退出后台服务 |

统一返回 `{ "ok": true, ... }` 或 `{ "ok": false, "error": "...", "code": "..." }`。

---

## 10. 前端设计

### 10.1 页面与路由

- `#/dashboard`：仪表盘、趋势、构成、部门、最近导入；
- `#/records`：台账筛选、分页、CRUD、导出；
- `#/import`：三步导入向导；
- `#/history`：导入批次与异常；
- `#/settings`：设置、备份、映射记忆、升级、审计。

哈希路由 + 每路由独立挂载点：
**旧页面的异步回调不会写入新页面**，避免路由切换时的数据串页。

### 10.2 视觉与可用性

- 深绿 + 暖金 + 米白的财务感配色；
- 系统字体，无 Web Font；
- 4 列 KPI → 12 列栅格 → 响应式；
- 文员无需理解“字段映射”术语之外的技术概念，界面全部用中文解释；
- 全站使用 Toast、确认弹窗、加载态、空状态、异常态；
- 表单、按钮、表格具备键盘焦点样式；
- 1366 / 1024 / 手机宽度做响应式适配。

### 10.3 安全渲染

- 所有业务文本输出均经过 HTML 转义；
- 图表只渲染数值与已转义标签；
- 用户输入不会拼接进 SQL；
- CSP：`default-src 'self'`；禁止外部脚本与连接；
- 无 `eval`、无内联脚本、无外部库。

---

## 11. 安全模型

### 11.1 网络边界

- 只绑定 `127.0.0.1`，不对局域网开放；
- Host 头只允许 `127.0.0.1` / `localhost` / `::1`；
- 所有写请求检查 `Origin` 与 `Sec-Fetch-Site`，阻断跨站写入；
- 无 CORS。

### 11.2 数据边界

- 上传文件限制类型、大小、数量、行数；
- 文件名净化，不使用用户路径原值落盘；
- SQL 全部参数化；
- 更新包路径穿越防护；
- 文件系统路径二次校验；
- 不执行任何 Office 宏 / 外部程序 / 邮件脚本；
- 不连接互联网。

### 11.3 前端防护

- 内容安全策略（CSP）限制外部资源；
- 所有动态 HTML 字符转义；
- 导出表格做公式注入防护；
- 不使用第三方前端运行时。

> 适用边界：单机、单人、可信办公环境。若需多人访问，应另加反向代理、认证、权限、审计和并发控制，不能直接把监听地址改为 `0.0.0.0`。

---

## 12. 稳定性和数据准确性

1. **金额整数分**：避免 JS / Python 浮点误差。
2. **事务提交**：整批可回滚；异常行不落库。
3. **判重前置**：预览时即揭示重复，避免先写后删。
4. **SQLite 在线备份**：不直接复制 WAL 正在写入的库。
5. **启动完整性检查**：`quick_check`。
6. **每日自动备份**：14 份；手动 / 升级前 12 份。
7. **日志与审计**：导入、增删改、备份、升级有轨迹。
8. **单实例**：`data/runtime.json` + 健康检查，避免两个写入进程。
9. **端口回退**：8765 被占用时自动换端口并打开正确地址。
10. **字段映射快照**：每次导入保留当时的映射，便于解释历史结果。
11. **完整异常报告**：CSV 落盘，不只存在内存。
12. **升级前语法检查 + 数据库备份 + 旧版本保留**。

---

## 13. 离线升级机制

### 13.1 目录

```text
program/
├─ active.txt        正在启动的版本
└─ versions/
   ├─ 1.0.0/
   └─ 1.0.1/
```

稳定启动器 `launcher.py` 位于根目录，不参与业务升级。
它读取 `active.txt`，把版本目录和可选的 `vendor/` 加入 `sys.path`，运行 `app.py`。

### 13.2 升级包格式

```text
update.json
payload/
├─ app.py
├─ version.json
├─ requirements.txt
├─ backend/**
└─ web/**
```

`update.json` 包含：格式版本、产品标识、目标版本、最低来源版本、发布日期、说明、每个文件的相对路径 / 大小 / SHA-256。

### 13.3 安装步骤

1. 用户选择 `.zip` / `.hcup`；
2. 服务端校验产品、格式、版本、`min_from`；
3. 校验清单与每个文件的 SHA-256；
4. 拒绝路径穿越、绝对路径、盘符、清单外文件；
5. 解压到 `program/versions/.stage-*`；
6. 检查 payload 内 `version.json` 与清单版本一致；
7. `compileall` 检查 Python 语法；
8. 移动到 `program/versions/<version>`；
9. 使用 SQLite backup 备份数据库；
10. 原子更新 `program/active.txt`；
11. 提示重启；保留旧版本；
12. 重启助手等待旧进程退出后重新调用启动器。

本流程在 `tests/updater_selftest.py` 中自动验证了版本识别、篡改拒绝、安装、备份、待重启与取消待生效版本。

### 13.4 回退

- 应用仍可启动时：设置 → 版本与离线升级 → 切换旧版本；
- 新版本无法启动时：根目录 `Recovery-HengCe.cmd`；
- 回退会先备份数据库；
- 如果数据库 schema 已经升级到旧版本不支持的版本，应使用升级前备份恢复。

### 13.5 依赖与 schema 升级

- 常规升级包只携带业务代码与 Web 资源，不包含新的 site-packages。
- 纯 Python 新依赖可以放进版本的 `vendor/`，启动器会加入 `sys.path`。
- 原生依赖 / Python 版本升级：重建完整运行时包，按大版本发布。
- 数据库结构变更：在 `backend/db.py` 中增加迁移，提升 `schema_version`；
  启动时按 `PRAGMA user_version` 顺序执行，绝不修改已有数据目录结构之外的内容。
- `update.json.min_from` 用于阻止跨过多代迁移的升级。

### 13.6 信任模型

升级包有完整性哈希，但**没有数字签名**。
在断网环境中，分发渠道本身就是信任边界；建议升级包由维护人员在内网共享盘或 U 盘分发，并保留 SHA-256 清单。

---

## 14. 部署方案

### 14.1 推荐方式

- 生成 `HengCeWorkbench-Windows-x64-v<version>.zip`；
- 复制到目标 Windows 11 电脑；
- 解压到可写目录，双击 `Start-HengCe.vbs`；
- 如需桌面图标，运行 `Install-HengCe.cmd`（无需管理员）。

安装脚本会：

1. 检查工作台是否在运行；
2. 复制程序到 `%LOCALAPPDATA%\HengCeWorkbench`，排除 `data`；
3. 创建桌面和开始菜单快捷方式；
4. 启动工作台。

### 14.2 为什么数据放在 `%LOCALAPPDATA%`

- 普通用户有写权限，避免 UAC；
- 不会随程序目录被覆盖或移动；
- 升级时无需迁移；
- 备份、迁移只需复制 `data` 目录。

### 14.3 兼容性

- Windows 10/11 x64；
- Edge / Chrome 等现代浏览器（Windows 11 自带 Edge）；
- 无需 Office、Python、Node、Java、数据库服务；
- 无网络也能启动和导入。

---

## 15. 测试与验收

### 自动测试

```bash
python tests/smoke.py             # 70 项
python tests/updater_selftest.py  # 升级机制
```

覆盖：

- .xlsx / .xls / GBK CSV / MSG（附件 + 正文表格）/ EML；
- 标题行识别、自动字段映射、双列金额；
- 异常、警告、批内重复、库内重复、更新策略；
- CRUD、筛选、分页、搜索、导出；
- 导入记录、异常 CSV、异常 ZIP；
- 备份、模板、设置、演示数据；
- Host / Origin 安全边界；
- 升级包校验、篡改拒绝、版本切换、数据库备份、取消待生效版本。

### 手工验收建议

1. 在目标 Windows 11 实机验证启动、浏览器打开、端口冲突回退；
2. 用真实业务 Excel 和 Outlook MSG/EML 做一次导入；
3. 校验异常明细、重复策略、导入后看板；
4. 做一次手动备份；
5. 制作一个测试升级包 1.0.1，完成安装、重启、回退；
6. 用 `Recovery-HengCe.cmd` 做一次紧急回退演练。

---

## 16. 边界与后续扩展点

### 当前边界

- 不直接解析 `.pst/.ost`；
- 不支持 `.xlsb`、加密 Office、宏执行；
- 复杂多行表头需在源表整理；
- 单机单用户，无登录 / 权限；
- 无云同步 / 移动端；
- 升级包未签名；
- 依赖变更需要完整运行时包；
- 自动备份当前为“启动时每日一次”，不是实时 CDP。

### 扩展方式

- **新数据字段**：在 `backend/fields.py` 增加定义、在数据库迁移增加列、在前端表单/映射/导出补列；
- **新指标**：在 `dashboard_service` 增加聚合；前端仪表盘增加卡片；
- **新文件格式**：在 `readers` / `email_reader` 增加解析器；
- **新报表**：复用 records 查询和 openpyxl 导出；
- **新页面**：新增 `web/js/pages/*.js` 并注册路由，不需要构建链；
- **升级**：版本号 + `make_update.py`，业务迭代不需要重新安装 Windows 运行时。
