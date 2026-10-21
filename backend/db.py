"""SQLite 数据层：连接、版本化迁移、事务与备份。"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from flask import g

from . import config
from .utils import now_iso

log = logging.getLogger(__name__)

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'committed',
    session_token TEXT NOT NULL DEFAULT '',
    source_count INTEGER NOT NULL DEFAULT 0,
    total_rows INTEGER NOT NULL DEFAULT 0,
    imported_rows INTEGER NOT NULL DEFAULT 0,
    updated_rows INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    warning_rows INTEGER NOT NULL DEFAULT 0,
    invalid_rows INTEGER NOT NULL DEFAULT 0,
    duplicate_policy TEXT NOT NULL DEFAULT 'skip',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    business_date TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('income', 'expense')),
    category TEXT NOT NULL DEFAULT '未分类',
    department TEXT NOT NULL DEFAULT '未指定',
    counterparty TEXT NOT NULL DEFAULT '',
    payment_method TEXT NOT NULL DEFAULT '',
    doc_no TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    fingerprint TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'manual',
    batch_id TEXT REFERENCES batches(id) ON DELETE SET NULL,
    source_file TEXT NOT NULL DEFAULT '',
    source_sheet TEXT NOT NULL DEFAULT '',
    source_row INTEGER,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_records_fingerprint ON records(fingerprint);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(business_date DESC);
CREATE INDEX IF NOT EXISTS idx_records_direction_date ON records(direction, business_date);
CREATE INDEX IF NOT EXISTS idx_records_category ON records(category);
CREATE INDEX IF NOT EXISTS idx_records_department ON records(department);
CREATE INDEX IF NOT EXISTS idx_records_batch ON records(batch_id);
CREATE INDEX IF NOT EXISTS idx_records_demo ON records(is_demo);

CREATE TABLE IF NOT EXISTS batch_sources (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    file_type TEXT NOT NULL DEFAULT '',
    sheet_name TEXT NOT NULL DEFAULT '',
    email_subject TEXT NOT NULL DEFAULT '',
    email_from TEXT NOT NULL DEFAULT '',
    email_date TEXT NOT NULL DEFAULT '',
    presence TEXT NOT NULL DEFAULT 'attachment',
    header_row INTEGER NOT NULL DEFAULT 1,
    total_rows INTEGER NOT NULL DEFAULT 0,
    imported_rows INTEGER NOT NULL DEFAULT 0,
    updated_rows INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    warning_rows INTEGER NOT NULL DEFAULT 0,
    invalid_rows INTEGER NOT NULL DEFAULT 0,
    mapping_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    error_report TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_sources_batch ON batch_sources(batch_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_batches_session ON batches(session_token) WHERE session_token <> '';

CREATE TABLE IF NOT EXISTS mapping_profiles (
    header_signature TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    headers_json TEXT NOT NULL DEFAULT '[]',
    mapping_json TEXT NOT NULL DEFAULT '{}',
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);

PRAGMA user_version = 1;
"""

MIGRATIONS: dict[int, str] = {1: SCHEMA_V1}


def _configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    db_path = Path(path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20, isolation_level=None)
    return _configure_connection(conn)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
        g.db_conn = g.db
    return g.db


def close_db(_error=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection | None = None, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    connection = conn or get_db()
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def init_db() -> None:
    config.ensure_dirs()
    conn = connect()
    try:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        if current > config.SCHEMA_VERSION:
            raise RuntimeError(
                f"数据文件版本（v{current}）高于当前程序（v{config.SCHEMA_VERSION}），"
                "请升级程序或恢复旧版数据备份。"
            )
        if current < 1:
            conn.executescript(SCHEMA_V1)
            conn.execute(f"PRAGMA user_version = {config.SCHEMA_VERSION}")
            log.info("已初始化数据库架构 v%s", config.SCHEMA_VERSION)
        # 后续版本：按顺序执行 MIGRATIONS[current + 1 ... SCHEMA_VERSION]
        for version in range(max(current, 1) + 1, config.SCHEMA_VERSION + 1):
            sql = MIGRATIONS.get(version)
            if sql:
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {version}")
                log.info("已迁移数据库架构至 v%s", version)
        for key, value in config.DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"数据文件完整性检查未通过：{check}")
    finally:
        conn.close()


def get_settings() -> dict[str, Any]:
    result = dict(config.DEFAULT_SETTINGS)
    try:
        for row in get_db().execute("SELECT key, value FROM settings"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except Exception:
                result[row["key"]] = row["value"]
    except Exception:
        log.exception("读取设置失败")
    return result


def get_setting(key: str, default: Any = None) -> Any:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return config.DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    allowed = set(config.DEFAULT_SETTINGS)
    with transaction() as conn:
        for key, value in values.items():
            if key not in allowed:
                continue
            conn.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )
    return get_settings()


def audit(action: str, entity: str, entity_id: str, summary: str, conn: sqlite3.Connection | None = None) -> None:
    db = conn or get_db()
    try:
        db.execute(
            "INSERT INTO audit_logs(action, entity, entity_id, summary, created_at) VALUES(?, ?, ?, ?, ?)",
            (action, entity, entity_id, summary[:500], now_iso()),
        )
    except Exception:
        log.exception("写入审计日志失败")


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def backup_database(reason: str = "manual", keep_auto: int = 12) -> Path:
    """使用 SQLite 在线备份 API，避免只复制主文件却漏掉 WAL 内容。"""
    config.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_reason = "".join(ch for ch in reason if ch.isalnum() or ch in "-_") or "manual"
    target = config.BACKUP_DIR / f"hengce-{safe_reason}-{stamp}.db"
    source = connect()
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            destination.close()
    finally:
        source.close()
    # 手动/升级前备份与每日备份分开保留，避免互相挤占。
    files = sorted(
        [path for path in config.BACKUP_DIR.glob("hengce-*-*.db")
         if not path.name.startswith("hengce-daily-")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep_auto:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def daily_auto_backup() -> Path | None:
    """每天第一次启动时留一份轻量备份。"""
    config.ensure_dirs()
    target = config.BACKUP_DIR / f"hengce-daily-{date.today().strftime('%Y%m%d')}.db"
    if target.exists():
        return None
    try:
        source = connect()
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        daily = sorted(
            config.BACKUP_DIR.glob("hengce-daily-*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in daily[14:]:
            try:
                old.unlink()
            except OSError:
                pass
        return target
    except Exception:
        log.exception("每日自动备份失败")
        return None
