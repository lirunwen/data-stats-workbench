"""HTTP API：仅监听本机回环地址，供离线前端调用。"""
from __future__ import annotations

import io
import json
import logging
import os
import platform
import secrets
import shutil
import sqlite3
import threading
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request, send_file

from . import config, db, demo_service, import_service, records_service, template_service, updater
from .dashboard_service import get_dashboard
from .errors import AppError
from .utils import clean_text, now_iso

log = logging.getLogger(__name__)
api = Blueprint("api", __name__, url_prefix="/api")


def _json_body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise AppError("请求内容格式不正确，请刷新页面后重试", 400, "invalid_json")
    return payload


def _ok(**kwargs):
    return jsonify({"ok": True, **kwargs})


@api.before_request
def _guard_local_requests():
    """防止外部网页借浏览器向本机接口发起跨站写入。"""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("已拒绝跨站请求", 403, "cross_site_blocked")
        origin = request.headers.get("Origin")
        if origin:
            try:
                host = urlparse(origin).hostname
            except Exception:
                host = None
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise AppError("已拒绝非本机来源的写入请求", 403, "cross_origin_blocked")


@api.get("/health")
def health():
    return _ok(
        product=config.APP_PRODUCT,
        name=config.APP_NAME,
        version=config.APP_VERSION,
        status="ok",
        local_only=True,
        time=now_iso(),
    )


def _storage_usage() -> dict:
    def folder_size(path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
        except OSError:
            pass
        return total

    return {
        "data_dir": str(config.DATA_DIR),
        "database_size": db_path_size(config.DB_PATH),
        "backup_size": folder_size(config.BACKUP_DIR),
        "staging_size": folder_size(config.STAGING_DIR),
        "report_size": folder_size(config.REPORT_DIR),
        "log_size": folder_size(config.LOG_DIR),
    }


def db_path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _app_payload() -> dict:
    options = records_service.get_filter_options()
    settings = db.get_settings()
    try:
        update_status = updater.status()
    except Exception:
        log.exception("读取升级状态失败")
        update_status = {"installed": bool(config.INSTALL_ROOT), "running_version": config.APP_VERSION}
    profiles = [
        {
            "signature": row["header_signature"],
            "name": row["name"],
            "headers": json.loads(row["headers_json"] or "[]"),
            "use_count": row["use_count"],
            "updated_at": row["updated_at"],
        }
        for row in db.get_db().execute(
            "SELECT * FROM mapping_profiles ORDER BY updated_at DESC LIMIT 100"
        )
    ]
    return {
        "name": config.APP_NAME,
        "product": config.APP_PRODUCT,
        "version": config.APP_VERSION,
        "released": config.APP_RELEASED,
        "server_date": date.today().isoformat(),
        "server_time": now_iso(),
        "installed": bool(config.INSTALL_ROOT),
        "packaged": bool(config.PACKAGED),
        "data_dir": str(config.DATA_DIR),
        "schema_version": config.SCHEMA_VERSION,
        "options": options,
        "settings": settings,
        "update": update_status,
        "mapping_profiles": profiles,
        "storage": _storage_usage(),
        "limits": {
            "max_file_bytes": config.MAX_FILE_BYTES,
            "max_table_rows": config.MAX_TABLE_ROWS,
            "max_upload_bytes": config.MAX_UPLOAD_BYTES,
            "session_ttl_hours": config.SESSION_TTL_SECONDS // 3600,
        },
        "system": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
    }


@api.get("/bootstrap")
def bootstrap():
    return _ok(app=_app_payload())


@api.get("/options")
def options():
    return _ok(options=records_service.get_filter_options())


@api.get("/import-template")
def import_template():
    content, filename = template_service.build_import_template()
    return send_file(
        io.BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@api.get("/dashboard")
def dashboard():
    preset = clean_text(request.args.get("period") or "year")
    start = clean_text(request.args.get("start")) or None
    end = clean_text(request.args.get("end")) or None
    data = get_dashboard(preset, start, end)
    return _ok(dashboard=data, options=records_service.get_filter_options())


# ---------------------------------------------------------------------------
# 经营流水
# ---------------------------------------------------------------------------

@api.get("/records/export")
def export_records():
    filters = records_service.parse_filters(request.args, for_export=True)
    export_format = clean_text(request.args.get("format") or "xlsx").lower()
    try:
        if export_format == "csv":
            content, filename = records_service.export_csv(filters)
            return send_file(
                io.BytesIO(content), mimetype="text/csv; charset=utf-8",
                as_attachment=True, download_name=filename, max_age=0,
            )
        content, filename = records_service.export_xlsx(filters)
        return send_file(
            io.BytesIO(content),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=filename, max_age=0,
        )
    except ImportError as exc:
        raise AppError("缺少 Excel 导出组件，请联系管理员", 500, "missing_dependency") from exc


@api.get("/records")
def list_records():
    filters = records_service.parse_filters(request.args)
    return _ok(**records_service.list_records(filters))


@api.post("/records")
def create_record():
    record = records_service.create_record(_json_body())
    return _ok(record=record), 201


@api.post("/records/bulk-delete")
def bulk_delete_records():
    payload = _json_body()
    result = records_service.bulk_delete(payload.get("ids") or [])
    return _ok(**result)


@api.get("/records/<record_id>")
def get_record(record_id: str):
    return _ok(record=records_service.get_record(record_id))


@api.put("/records/<record_id>")
def update_record(record_id: str):
    record = records_service.update_record(record_id, _json_body())
    return _ok(record=record)


@api.delete("/records/<record_id>")
def delete_record(record_id: str):
    result = records_service.delete_record(record_id)
    return _ok(**result)


# ---------------------------------------------------------------------------
# 批量导入
# ---------------------------------------------------------------------------

@api.post("/imports/analyze")
def analyze_import():
    files = request.files.getlist("files")
    session, sources = import_service.create_import_session(files)
    payload = import_service.session_payload(session, session["id"])
    return _ok(**payload)


@api.get("/imports/<token>")
def get_import_session(token: str):
    session = import_service.load_session(token)
    return _ok(**import_service.session_payload(session, token))


@api.delete("/imports/<token>")
def discard_import(token: str):
    import_service.discard_session(token)
    return _ok(discarded=True)


@api.patch("/imports/<token>/sources/<source_id>/header")
def change_header(token: str, source_id: str):
    payload = _json_body()
    source = import_service.reheader_session(token, source_id, payload.get("header_row"))
    return _ok(source=source)


def _run_preview(token: str) -> tuple[dict, dict]:
    session = import_service.load_session(token)
    if session.get("status") == "committed":
        raise AppError("本次导入已经提交，不能重复校验", 409, "already_committed")
    payload = _json_body()
    selections = payload.get("sources") or []
    policy = clean_text(payload.get("duplicate_policy") or "skip")
    validation = import_service.prepare_validation(session, selections, policy)
    return session, validation


@api.post("/imports/<token>/preview")
def preview_import(token: str):
    _session, validation = _run_preview(token)
    payload = import_service.validation_payload(validation)
    return _ok(**payload)


@api.post("/imports/<token>/preview-export")
def export_preview_errors(token: str):
    _session, validation = _run_preview(token)
    content = import_service.validation_csv(validation)
    return send_file(
        io.BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"导入校验结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        max_age=0,
    )


@api.post("/imports/<token>/commit")
def commit_import(token: str):
    payload = _json_body()
    batch = import_service.commit_import(
        token,
        payload.get("sources") or [],
        clean_text(payload.get("duplicate_policy") or "skip"),
    )
    return _ok(batch=batch)


@api.get("/batches")
def list_batches():
    conn = db.get_db()
    try:
        page = max(1, int(request.args.get("page") or 1))
        page_size = int(request.args.get("page_size") or 12)
    except (TypeError, ValueError):
        page, page_size = 1, 12
    page_size = page_size if page_size in {10, 12, 20, 50} else 12
    q = clean_text(request.args.get("q"))[:100]
    status = clean_text(request.args.get("status"))
    clauses = []
    values = []
    if q:
        like = f"%{q.replace('%', '\\%').replace('_', '\\_')}%"
        clauses.append(
            """(b.id LIKE ? ESCAPE '\\' OR EXISTS (
                    SELECT 1 FROM batch_sources s WHERE s.batch_id = b.id
                    AND (s.file_name LIKE ? ESCAPE '\\' OR s.sheet_name LIKE ? ESCAPE '\\'
                         OR s.email_subject LIKE ? ESCAPE '\\')))"""
        )
        values.extend([like, like, like, like])
    if status in {"completed", "partial", "duplicate", "failed", "pending"}:
        clauses.append("b.status = ?")
        values.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) FROM batches b{where}", values).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = conn.execute(
        f"""SELECT b.*, (
                 SELECT s.file_name FROM batch_sources s WHERE s.batch_id = b.id ORDER BY s.rowid LIMIT 1
             ) AS first_file,
             (SELECT s.sheet_name FROM batch_sources s WHERE s.batch_id = b.id ORDER BY s.rowid LIMIT 1) AS first_sheet,
             (SELECT COUNT(*) FROM records r WHERE r.batch_id = b.id) AS remaining_records
             FROM batches b{where} ORDER BY b.created_at DESC LIMIT ? OFFSET ?""",
        [*values, page_size, (page - 1) * page_size],
    ).fetchall()
    labels = {
        "completed": "已完成", "partial": "部分导入", "duplicate": "全部重复",
        "failed": "未导入", "pending": "处理中",
    }
    items = []
    for row in rows:
        item = dict(row)
        item.pop("session_token", None)
        item["status_label"] = labels.get(item["status"], item["status"])
        items.append(item)
    return _ok(items=items, total=total, page=page, page_size=page_size, pages=pages)


@api.get("/batches/<batch_id>")
def batch_detail(batch_id: str):
    batch = import_service.get_batch_payload(batch_id)
    return _ok(batch=batch)


@api.get("/batches/<batch_id>/errors.zip")
def batch_error_archive(batch_id: str):
    batch = import_service.get_batch_payload(batch_id)
    sources = [source for source in batch.get("sources", []) if source.get("error_report")]
    if not sources:
        raise AppError("本次导入没有异常明细可下载", 404, "no_report")
    import io as _io
    archive = _io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        readme = (
            f"衡策经营数据工作台 · 导入异常明细\n"
            f"批次：{batch_id}\n导入时间：{batch.get('created_at', '')}\n"
            f"新增：{batch.get('imported_rows', 0)}  更新：{batch.get('updated_rows', 0)}  "
            f"重复：{batch.get('duplicate_rows', 0)}  异常：{batch.get('invalid_rows', 0)}\n\n"
            "CSV 已使用 UTF-8 BOM 编码，可直接用 Excel 打开。\n"
        )
        zip_file.writestr("README.txt", readme.encode("utf-8"))
        for source in sources:
            path = import_service.batch_error_report_path(batch_id, source["source_id"])
            zip_file.write(path, arcname=f"error_report_{source['source_id']}.csv")
    archive.seek(0)
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"导入异常明细_{batch_id}.zip",
        max_age=0,
    )


@api.get("/batches/<batch_id>/sources/<source_id>/errors.csv")
def batch_error_report(batch_id: str, source_id: str):
    path = import_service.batch_error_report_path(batch_id, source_id)
    return send_file(
        path,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"导入异常明细_{batch_id}_{source_id}.csv",
        max_age=0,
    )


# ---------------------------------------------------------------------------
# 设置、备份、维护
# ---------------------------------------------------------------------------

@api.get("/settings")
def get_settings():
    return _ok(settings=db.get_settings(), app=_app_payload())


@api.put("/settings")
def update_settings():
    payload = _json_body()
    updates = {}
    if "org_name" in payload:
        org_name = clean_text(payload.get("org_name"))
        if len(org_name) > 60:
            raise AppError("单位名称不能超过 60 个字符", 422)
        updates["org_name"] = org_name
    if "default_duplicate_policy" in payload:
        value = clean_text(payload.get("default_duplicate_policy"))
        if value not in {"skip", "update"}:
            raise AppError("重复数据处理方式无效", 422)
        updates["default_duplicate_policy"] = value
    if "remember_mapping" in payload:
        updates["remember_mapping"] = bool(payload.get("remember_mapping"))
    if "auto_cleanup_staging" in payload:
        updates["auto_cleanup_staging"] = bool(payload.get("auto_cleanup_staging"))
    if not updates:
        raise AppError("没有可保存的设置", 400)
    saved = db.save_settings(updates)
    db.audit("settings", "settings", "", "更新应用设置：" + "、".join(updates))
    return _ok(settings=saved)


@api.post("/maintenance/backup")
def create_backup():
    path = db.backup_database("manual")
    content = path.read_bytes()
    db.audit("backup", "database", "", f"手动备份数据库 {path.name}")
    return send_file(
        io.BytesIO(content),
        mimetype="application/x-sqlite3",
        as_attachment=True,
        download_name=f"衡策数据备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
        max_age=0,
    )


@api.post("/maintenance/cleanup")
def cleanup():
    import_service.cleanup_stale_sessions()
    removed = 0
    for folder in config.REPORT_DIR.iterdir() if config.REPORT_DIR.exists() else []:
        try:
            if folder.is_dir() and folder.stat().st_mtime < (datetime.now().timestamp() - 180 * 86400):
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    db.audit("cleanup", "storage", "", f"清理过期报告 {removed} 个")
    return _ok(removed_reports=removed, message="临时文件与过期报告已清理")


@api.get("/audit")
def audit_logs():
    try:
        limit = min(200, max(1, int(request.args.get("limit") or 30)))
    except (TypeError, ValueError):
        limit = 30
    rows = db.get_db().execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return _ok(items=[dict(row) for row in rows])


@api.get("/mapping-profiles")
def mapping_profiles():
    rows = db.get_db().execute(
        "SELECT * FROM mapping_profiles ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    items = []
    for row in rows:
        items.append({
            "signature": row["header_signature"],
            "name": row["name"],
            "headers": json.loads(row["headers_json"] or "[]"),
            "mapping": json.loads(row["mapping_json"] or "{}"),
            "use_count": row["use_count"],
            "updated_at": row["updated_at"],
        })
    return _ok(items=items)


@api.delete("/mapping-profiles/<signature>")
def delete_mapping_profile(signature: str):
    if not all(ch in "0123456789abcdef" for ch in signature.lower()) or len(signature) != 64:
        raise AppError("映射标识无效", 400)
    with db.transaction() as tx:
        row = tx.execute(
            "SELECT name FROM mapping_profiles WHERE header_signature = ?", (signature,)
        ).fetchone()
        if not row:
            raise AppError("该字段映射已不存在", 404)
        tx.execute("DELETE FROM mapping_profiles WHERE header_signature = ?", (signature,))
        db.audit("delete_mapping_profile", "mapping_profile", signature, f"删除字段映射习惯：{row['name']}", conn=tx)
    return _ok(deleted=1)


# ---------------------------------------------------------------------------
# 演示数据
# ---------------------------------------------------------------------------

@api.post("/demo")
def seed_demo():
    result = demo_service.seed_demo()
    return _ok(**result)


@api.delete("/demo")
def clear_demo():
    result = demo_service.clear_demo()
    return _ok(**result)


# ---------------------------------------------------------------------------
# 离线升级
# ---------------------------------------------------------------------------

@api.get("/update/status")
def update_status():
    return _ok(update=updater.status())


@api.post("/update/inspect")
def inspect_update():
    storage = request.files.get("package")
    if not storage or not storage.filename:
        raise AppError("请选择升级包文件", 400, "no_package")
    suffix = Path(storage.filename).suffix.lower()
    if suffix not in {".zip", ".hcup"}:
        raise AppError("升级包应为 .zip 或 .hcup 文件", 400, "invalid_package")
    config.ensure_dirs()
    temp = config.UPDATE_DIR / f"incoming_{secrets.token_hex(10)}.zip"
    try:
        storage.save(str(temp))
        meta = updater.inspect_package(temp, storage.filename)
        db.audit("update_inspect", "application", meta["package_id"], f"校验升级包 {meta['version']}")
        return _ok(package=meta)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


@api.post("/update/apply")
def apply_update():
    payload = _json_body()
    result = updater.apply_package(clean_text(payload.get("package_id")))
    db.audit("update_apply", "application", payload.get("package_id", ""), result["message"])
    return _ok(**result)


@api.post("/update/activate")
def activate_version():
    payload = _json_body()
    result = updater.activate_version(clean_text(payload.get("version")))
    db.audit("update_switch", "application", payload.get("version", ""), result["message"])
    return _ok(**result)


@api.post("/update/restart")
def restart_after_update():
    result = updater.schedule_restart()
    return _ok(**result)


@api.get("/versions")
def versions():
    return _ok(update=updater.status())


@api.post("/application/quit")
def quit_application():
    db.audit("quit", "application", "", "用户退出工作台")
    threading.Timer(1.0, lambda: __import__("os")._exit(0)).start()
    return _ok(message="工作台已退出，可以关闭此页面")
