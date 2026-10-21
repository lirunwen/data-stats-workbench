#!/usr/bin/env python3
"""数据统计工作台 · 本地启动器。

默认只监听 127.0.0.1，不依赖公网和任何外部资源。
双击启动脚本或运行 `python app.py` 后，会自动在默认浏览器打开工作台。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import URLError

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from backend import config, db, import_service, updater
from backend.api import api
from backend.errors import AppError
from backend.utils import now_iso

log = logging.getLogger("datastats")

PRODUCT = config.APP_PRODUCT


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s · %(message)s")
    file_handler = RotatingFileHandler(
        config.LOG_DIR / "workbench.log", maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)


def _fatal_message(message: str) -> None:
    log.error(message)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "数据统计工作台", 0x10)
        except Exception:
            pass


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(config.WEB_DIR),
        static_url_path="/static",
    )
    app.config.update(
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_BYTES,
        SEND_FILE_MAX_AGE_DEFAULT=0,
        JSON_AS_ASCII=False,
        PROPAGATE_EXCEPTIONS=False,
    )
    app.json.ensure_ascii = False
    app.secret_key = os.urandom(32)
    app.register_blueprint(api)

    @app.before_request
    def restrict_host():
        host = request.host.split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return jsonify({
                "ok": False,
                "error": "本工作台只允许通过本机地址访问",
                "code": "host_not_allowed",
            }), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'"
        )
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store, max-age=0"
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.teardown_appcontext
    def close_connection(_error=None):
        db.close_db()

    @app.get("/")
    def index():
        return send_from_directory(config.WEB_DIR, "index.html", max_age=0)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(config.WEB_DIR / "assets", "favicon.svg", max_age=0)

    @app.errorhandler(AppError)
    def handle_app_error(error: AppError):
        if error.status >= 500:
            log.error("业务异常：%s", error.message, exc_info=True)
        return jsonify(error.to_dict()), error.status

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        if request.path.startswith("/api/"):
            messages = {
                404: "接口不存在，请刷新页面后重试",
                405: "请求方式不正确",
                413: "上传内容过大，请分批导入或拆分文件",
            }
            message = messages.get(error.code, error.description or "请求处理失败")
            return jsonify({"ok": False, "error": message, "code": f"http_{error.code}"}), error.code
        return error

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        log.exception("未处理异常")
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": "处理时发生意外错误，数据未被修改。请重试，或查看 data/logs/workbench.log。",
                "code": "internal_error",
            }), 500
        return "应用发生错误，请查看日志或联系管理员。", 500

    return app


def _runtime_file() -> Path:
    return config.DATA_DIR / "runtime.json"


def _read_runtime_port() -> int | None:
    try:
        payload = json.loads(_runtime_file().read_text(encoding="utf-8"))
        port = int(payload.get("port"))
        return port if 1 <= port <= 65535 else None
    except Exception:
        return None


def _write_runtime(port: int) -> None:
    try:
        path = _runtime_file()
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({
            "product": PRODUCT,
            "port": port,
            "pid": os.getpid(),
            "started_at": now_iso(),
            "version": config.APP_VERSION,
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    except Exception:
        log.exception("写入运行状态失败")


def _remove_runtime() -> None:
    try:
        _runtime_file().unlink(missing_ok=True)
    except OSError:
        pass


def _probe_existing(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.7) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("product") == PRODUCT and payload.get("status") == "ok"
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _find_port(preferred: int) -> tuple[int, bool]:
    if _port_free(preferred):
        return preferred, False
    if _probe_existing(preferred):
        return preferred, True
    for offset in range(1, 30):
        candidate = preferred + offset
        if _port_free(candidate):
            return candidate, False
        if _probe_existing(candidate):
            return candidate, True
    raise RuntimeError("未找到可用端口，请关闭其他程序后重试")


def _open_browser_later(url: str, port: int) -> None:
    # 冷启动时 waitress 可能需要一点时间；先等待 /api/health，再打开浏览器。
    for _ in range(30):
        if _probe_existing(port):
            break
        time.sleep(0.25)
    try:
        webbrowser.open(url)
    except Exception:
        log.exception("自动打开浏览器失败，请手动访问 %s", url)


def main() -> int:
    parser = argparse.ArgumentParser(description="数据统计工作台")
    parser.add_argument("--data-dir", help="指定数据目录（默认程序目录下的 data）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DATASTATS_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--version", action="store_true", help="显示版本并退出")
    args = parser.parse_args()
    if args.version:
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0

    try:
        if args.data_dir:
            config.configure(args.data_dir)
        config.ensure_dirs()
        setup_logging()
        log.info("启动 %s %s", config.APP_NAME, config.APP_VERSION)
        log.info("数据目录：%s", config.DATA_DIR)
        db.init_db()
        try:
            backup = db.daily_auto_backup()
            if backup:
                log.info("每日自动备份：%s", backup.name)
        except Exception:
            log.exception("自动备份失败，应用继续启动")
        import_service.cleanup_stale_sessions()

        previous_port = _read_runtime_port()
        if previous_port and _probe_existing(previous_port):
            url = f"http://127.0.0.1:{previous_port}/"
            log.info("检测到工作台已在本机运行：%s，直接打开浏览器", url)
            if not args.no_browser:
                webbrowser.open(url)
            return 0

        port, existing = _find_port(args.port)
        url = f"http://127.0.0.1:{port}/"
        if existing:
            log.info("检测到工作台已在本机运行：%s，直接打开浏览器", url)
            if not args.no_browser:
                webbrowser.open(url)
            return 0

        app = create_app()
        _write_runtime(port)
        if not args.no_browser:
            threading.Thread(target=_open_browser_later, args=(url, port), daemon=True).start()
        print(f"\n  数据统计工作台已启动\n  访问地址：{url}\n  仅本机可访问，按 Ctrl+C 停止\n")
        try:
            from waitress import serve
        except ImportError as exc:
            raise RuntimeError("缺少 waitress 组件，请使用完整安装包") from exc
        serve(
            app,
            host="127.0.0.1",
            port=port,
            threads=8,
            channel_timeout=180,
            max_request_body_size=config.MAX_UPLOAD_BYTES + 5 * 1024 * 1024,
            clear_untrusted_proxy_headers=True,
            ident="DataStatsWorkbench",
        )
        _remove_runtime()
        return 0
    except KeyboardInterrupt:
        log.info("用户停止应用")
        _remove_runtime()
        return 0
    except Exception as exc:
        log.exception("启动失败")
        _fatal_message(
            f"应用启动失败：{exc}\n\n"
            f"日志位置：{config.LOG_DIR / 'workbench.log'}\n"
            "请确认数据目录可写，然后重新打开应用。"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
