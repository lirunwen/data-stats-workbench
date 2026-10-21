"""应用路径、版本与运行参数。

程序目录与数据目录严格分离：
- 程序目录：安装包解压后的 versions/<版本>，升级时整目录替换；
- 数据目录：Windows 默认位于 %LOCALAPPDATA%\\DataStatsWorkbench\\data，升级不受影响。
开发模式默认使用项目下的 data/，测试可通过 DATASTATS_DATA_DIR 隔离。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = APP_ROOT / "web"

_PRODUCT = "data-stats-workbench"
_NAME = "数据统计工作台"
_VERSION = "1.0.0"
_RELEASED = ""
_SCHEMA_VERSION = 1
try:
    _version_info = json.loads((APP_ROOT / "version.json").read_text(encoding="utf-8"))
    _PRODUCT = _version_info.get("product", _PRODUCT)
    _NAME = _version_info.get("name", _NAME)
    _VERSION = _version_info.get("version", _VERSION)
    _RELEASED = _version_info.get("released", "")
    _SCHEMA_VERSION = int(_version_info.get("schema_version", 1))
except Exception:
    pass

APP_PRODUCT = _PRODUCT
APP_NAME = _NAME
APP_VERSION = _VERSION
APP_RELEASED = _RELEASED
SCHEMA_VERSION = _SCHEMA_VERSION
VERSION_DIR = Path(os.environ.get("DATASTATS_VERSION_DIR") or APP_ROOT)
INSTALL_ROOT = Path(os.environ["DATASTATS_INSTALL_ROOT"]) if os.environ.get("DATASTATS_INSTALL_ROOT") else None
IS_INSTALLED = bool(INSTALL_ROOT)
PACKAGED = bool(os.environ.get("DATASTATS_PACKAGED"))

_DEFAULT_DATA_DIR = APP_ROOT / "data"
DATA_DIR = Path(os.environ.get("DATASTATS_DATA_DIR") or _DEFAULT_DATA_DIR)
DB_PATH = DATA_DIR / "workbench.db"
STAGING_DIR = DATA_DIR / "staging"
BACKUP_DIR = DATA_DIR / "backup"
LOG_DIR = DATA_DIR / "logs"
REPORT_DIR = DATA_DIR / "reports"
UPDATE_DIR = DATA_DIR / "updates"


def configure(data_dir: str | Path | None = None) -> None:
    """在应用启动前设定数据目录（测试与便携模式使用）。"""
    global DATA_DIR, DB_PATH, STAGING_DIR, BACKUP_DIR, LOG_DIR, REPORT_DIR, UPDATE_DIR
    if data_dir:
        DATA_DIR = Path(data_dir).expanduser().resolve()
    elif os.environ.get("DATASTATS_DATA_DIR"):
        DATA_DIR = Path(os.environ["DATASTATS_DATA_DIR"]).expanduser().resolve()
    DB_PATH = DATA_DIR / "workbench.db"
    STAGING_DIR = DATA_DIR / "staging"
    BACKUP_DIR = DATA_DIR / "backup"
    LOG_DIR = DATA_DIR / "logs"
    REPORT_DIR = DATA_DIR / "reports"
    UPDATE_DIR = DATA_DIR / "updates"


def ensure_dirs() -> None:
    for folder in (DATA_DIR, STAGING_DIR, BACKUP_DIR, LOG_DIR, REPORT_DIR, UPDATE_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# 只接受这些格式；不支持的格式给出明确提示，而不是静默失败。
SUPPORTED_TABULAR = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt"}
SUPPORTED_EMAIL = {".msg", ".oft", ".eml"}
SUPPORTED_UPLOAD = SUPPORTED_TABULAR | SUPPORTED_EMAIL | {".pst", ".ost"}

MAX_UPLOAD_BYTES = 240 * 1024 * 1024
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_ATTACHMENTS_PER_EMAIL = 30
MAX_FILES_PER_IMPORT = 120
MAX_TABLE_ROWS = 50000
MAX_TABLE_COLS = 80
MAX_SHEETS = 30
PREVIEW_ROWS = 50
SESSION_TTL_SECONDS = 24 * 3600

FIELD_LIMITS = {
    "category": 80,
    "department": 80,
    "counterparty": 160,
    "payment_method": 80,
    "doc_no": 120,
    "note": 1000,
}

DEFAULT_SETTINGS = {
    "org_name": "",
    "default_duplicate_policy": "skip",
    "remember_mapping": True,
    "auto_cleanup_staging": True,
    "week_starts_monday": True,
}
