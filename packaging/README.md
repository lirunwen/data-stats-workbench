# Windows 离线分发与升级构建

目标运行环境：**Windows 10/11 x64，办公室内网，可完全断网，普通用户权限。**

本目录负责三件事：

1. 把源码 + Python 3.12 embeddable + 全部依赖组装成免安装运行包；
2. 提供稳定的启动器、安装脚本、诊断脚本与紧急回退脚本；
3. 生成可被应用内「离线升级」功能识别的升级包。

## 1. 最终用户拿到的结构

```text
HengCeWorkbench\
├─ Start-HengCe.vbs              静默启动（桌面快捷方式指向它）
├─ Start-HengCe-Console.cmd      诊断启动（保留控制台）
├─ Install-HengCe.cmd            复制到 %LOCALAPPDATA%，创建快捷方式
├─ Diagnostics.cmd               环境自检
├─ Recovery-HengCe.cmd           新版本无法启动时紧急回退
├─ install.ps1                   安装逻辑（无需管理员权限）
├─ README.txt                    给文员的 Windows 使用说明
├─ THIRD_PARTY_NOTICES.txt       第三方组件许可
├─ HengCe.ico
├─ launcher.py                   稳定启动器，不属于任何业务版本
├─ recovery.py                   紧急回退逻辑
├─ data\                         首次启动创建，升级不覆盖
├─ runtime\
│  └─ python\                    嵌入式 Python + wheelhouse 解包依赖
└─ program\
   ├─ active.txt                 当前启动版本，如 1.0.0
   └─ versions\
      ├─ 1.0.0\                  业务源码（app.py / backend / web / version.json）
      └─ 1.0.1\                  离线升级后新增的版本
```

**为什么版本目录与启动器分离：**

- 升级不需要覆盖正在运行的 exe；新版本解压到独立目录，写入 `active.txt` 后下次启动生效；
- 旧版本保留，可在设置页或紧急回退脚本中切回；
- 数据目录完全独立，升级不触碰数据库、备份、日志和异常报告。

## 2. 准备构建资产（只需在联网构建机执行）

### 2.1 Python 嵌入式运行时

下载官方 Windows x64 embeddable：

```text
https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
```

不要解压后再压缩；构建脚本会处理 `python312._pth` 与 `Lib\site-packages`。

### 2.2 离线 wheelhouse

在任意平台的构建机（本脚本跨平台可用）执行：

```bash
mkdir wheels
python -m pip download \
  -r packaging/windows/requirements-win.txt \
  --dest wheels \
  --platform win_amd64 \
  --python-version 3.12 \
  --implementation cp \
  --abi cp312 \
  --only-binary=:all:
```

wheels 目录中应包含 CPython 3.12 / win_amd64 的 MarkupSafe 原生 wheel，以及纯 Python 的 Flask、waitress、openpyxl、xlrd、python-oxmsg 等。运行时依赖全部使用宽松许可。

### 2.3 生成便携包

```bash
python packaging/build_portable.py \
  --embed-zip /path/to/python-3.12.10-embed-amd64.zip \
  --wheels /path/to/wheels \
  --out dist
```

产物：

```text
dist/HengCeWorkbench-Windows-x64-v<version>.zip
dist/build/HengCeWorkbench/                  # 可检查的未压缩目录
```

建议在 Windows 11 目标机上完成一次发布验收：

- 双击 `Start-HengCe.vbs`，Edge / Chrome 自动打开；
- 「Diagnostics.cmd」中的自检全部通过；
- 导入 `.xlsx`、`.msg`（含 Excel 附件）测试；
- 在设置页做一次手动备份；
- 关闭浏览器后，从「设置与数据安全 → 退出工作台」停止后台服务；
- 重新双击启动。

## 3. 版本与离线升级包

### 3.1 版本号

修改根目录 `version.json`：

```json
{
  "product": "hengce-workbench",
  "name": "衡策经营数据工作台",
  "version": "1.0.1",
  "released": "2026-11-01",
  "schema_version": 1,
  "update_format": 1
}
```

- `version` 必须是 `x.y.z`，且高于目标机当前版本；
- 数据库结构变更时提升 `schema_version`，并在 `backend/db.py` 的迁移列表中补充新版本；
- `product` 不能改，否则升级包会被目标机拒绝。

### 3.2 生成升级包

```bash
python packaging/make_update.py \
  --notes "优化导入校验提示；修复部门筛选；新增月度对比。" \
  --min-from 1.0.0 \
  --out dist/updates
```

产物：

```text
dist/updates/HengCe-Update-v1.0.1.zip
```

升级包结构：

```text
update.json
payload/
├─ app.py
├─ version.json
├─ requirements.txt
├─ backend/...
└─ web/...
```

`update.json`：

```json
{
  "format": 1,
  "product": "hengce-workbench",
  "version": "1.0.1",
  "min_from": "1.0.0",
  "released": "2026-11-01",
  "notes": "更新说明",
  "files": [
    { "path": "app.py", "size": 9260, "sha256": "…64位十六进制…" }
  ]
}
```

### 3.3 目标机安装升级

1. 设置与数据安全 → 版本与离线升级 → 选择升级包；
2. 应用在本机完成产品、版本、路径、文件数量、哈希、最低版本、Python 语法检查；
3. 新版本落盘到 `program/versions/<version>/`；
4. 自动备份数据库到 `data/backup/`；
5. 写入 `program/active.txt`，提示重启；
6. 打开设置页可看到「立即重启应用」，重启后运行新版本；
7. 旧版本保留，可在版本列表切回。

## 4. 升级安全机制

| 检查 / 保障 | 实现 |
| --- | --- |
| 产品归属 | `update.json.product` 必须等于 `hengce-workbench` |
| 防降级 | 目标版本必须高于当前运行版本 |
| 来源兼容 | 当前版本必须 ≥ `min_from` |
| 文件完整性 | 清单记录每个文件的 size + SHA-256，逐文件校验 |
| 防路径穿越 | 拒绝绝对路径、盘符、`..`、清单外文件 |
| 防损坏 | ZIP 校验 + 全量解压到临时目录后再切换 |
| 防坏代码 | `compileall` 校验 `app.py` 与 `backend/` 语法 |
| 防丢数据 | 切换版本前自动 SQLite 在线备份 |
| 可回退 | 旧版本目录保留；设置页可切换；`Recovery-HengCe.cmd` 可在无法启动时回退 |
| 防重复安装 | 相同版本目录已存在时拒绝覆盖 |
| 防并发切换 | 有 `active.txt` 待生效版本时，拒绝继续安装 |

升级包**未做数字签名**。文件哈希只能证明包没有被损坏，不能证明来源。
请通过本单位可信渠道分发升级包；这一点已写入用户文档。

## 5. 紧急回退

当新版本安装后无法启动（例如运行时环境被破坏、依赖缺失），用户仍可使用根目录的：

```text
Recovery-HengCe.cmd
```

它会：

1. 检测工作台是否仍在运行；
2. 列出 `program/versions` 下的所有版本；
3. 默认选择“不是当前启动版本”的最新版本；
4. 备份数据库到 `data/backup/hengce-recovery-*.db`；
5. 写入新的 `active.txt` 并尝试重新启动。

若数据库已发生不可逆结构升级，回退前请使用 `data/backup/` 中的升级前数据库备份。

## 6. 依赖变化

- **只改业务代码 / 前端**：普通升级包即可。
- **增加纯 Python 依赖**：把它放入运行时 wheelhouse，重新构建完整包；升级包仍不能自带新的 site-packages。也可将纯 Python 模块放入 `vendor/`（构建脚本会把它作为版本目录的一部分进入 `sys.path`，并在升级包中按路径携带）。
- **增加原生扩展 / 修改 Python 版本**：必须重新构建完整 Windows 包，按一次大版本发布处理。
- 更新 `requirements-win.txt`、`requirements.txt` 和 `version.json` 后，重新执行测试。

## 7. 数据位置与升级边界

| 运行方式 | 数据目录 |
| --- | --- |
| 便携运行 | `<程序目录>\data` |
| 安装运行 | `%LOCALAPPDATA%\HengCeWorkbench\data` |

升级只替换 `program/versions`，不会覆盖 `data`。
安装脚本使用 robocopy 时显式排除 `data`，因此重复运行安装脚本不会清掉业务数据。

## 8. 发布前检查清单

```bash
python tests/smoke.py
python tests/updater_selftest.py
```

- [ ] `version.json` 版本号正确，且已高于目标机版本
- [ ] 数据库迁移已加入 `backend/db.py`
- [ ] 升级包清单哈希正确
- [ ] 在 Windows 11 x64 实机完成启动、导入、导出、备份、升级、重启、回退演练
- [ ] 从旧版本数据目录启动新版本，确认数据可读
- [ ] `THIRD_PARTY_NOTICES.txt` 与依赖清单一致
