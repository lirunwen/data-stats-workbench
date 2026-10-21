"""离线升级包校验、安装、版本切换与回退。

升级包（.zip / .hcup）结构：
    update.json
    payload/app.py
    payload/version.json
    payload/backend/...
    payload/web/...

只接受本产品、版本号更高、来源版本兼容、所有文件哈希校验通过的包。
升级只写入安装目录的 versions/<version>/，用户数据目录完全不动。
"""
from __future__ import annotations

import compileall
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, db
from .errors import AppError
from .utils import clean_text, human_size, now_iso

log = logging.getLogger(__name__)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PACKAGE_RE = re.compile(r"^upd_[A-Za-z0-9]{20,40}$")
MAX_PACKAGE_BYTES = 120 * 1024 * 1024
MAX_UNCOMPRESSED = 300 * 1024 * 1024
MAX_FILES = 2000


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value or "")
    if not match:
        raise ValueError("版本号格式应为 x.y.z")
    return tuple(int(part) for part in match.groups())


def _versions_dir() -> Path:
    if not config.INSTALL_ROOT:
        raise AppError("当前是源码开发模式，离线升级仅在安装版中可用", 409, "not_installed")
    return config.INSTALL_ROOT / "program" / "versions"


def _active_file() -> Path:
    assert config.INSTALL_ROOT is not None
    return config.INSTALL_ROOT / "program" / "active.txt"


def _read_active_version() -> str:
    if not config.INSTALL_ROOT:
        return config.APP_VERSION
    try:
        value = (_active_file()).read_text(encoding="utf-8").strip()
        _version_tuple(value)
        return value
    except Exception:
        return config.APP_VERSION


def _write_active(version: str) -> None:
    assert config.INSTALL_ROOT is not None
    target = _active_file()
    temp = target.with_name(f"active.{secrets.token_hex(4)}.tmp")
    temp.write_text(version + "\n", encoding="utf-8")
    os.replace(temp, target)


def _safe_rel(path: str) -> str:
    path = path.replace("\\", "/").strip()
    if not path or path.startswith("/") or ".." in Path(path).parts or re.match(r"^[A-Za-z]:", path):
        raise ValueError(f"升级包包含非法路径：{path}")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"升级包包含非法路径：{path}")
    return path


def _load_manifest(zip_file: zipfile.ZipFile) -> dict:
    try:
        info = zip_file.getinfo("update.json")
    except KeyError as exc:
        raise AppError("升级包缺少 update.json，可能不是本产品的升级包", 400, "invalid_package") from exc
    if info.file_size > 2 * 1024 * 1024:
        raise AppError("升级包清单过大", 400, "invalid_package")
    try:
        manifest = json.loads(zip_file.read(info).decode("utf-8"))
    except Exception as exc:
        raise AppError("升级包清单无法解析", 400, "invalid_package") from exc
    if manifest.get("product") != config.APP_PRODUCT:
        raise AppError("这个升级包不属于衡策经营数据工作台", 400, "wrong_product")
    if manifest.get("format") != 1:
        raise AppError("升级包格式版本不受支持，请使用新版完整安装包", 400, "unsupported_format")
    try:
        version = str(manifest.get("version") or "")
        _version_tuple(version)
    except ValueError as exc:
        raise AppError("升级包版本号无效", 400, "invalid_version") from exc
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise AppError("升级包没有文件清单", 400, "invalid_package")
    if len(files) > MAX_FILES:
        raise AppError("升级包文件数量过多", 400, "invalid_package")
    seen = set()
    total_size = 0
    for item in files:
        if not isinstance(item, dict):
            raise AppError("升级包文件清单格式不正确", 400, "invalid_package")
        try:
            rel = _safe_rel(str(item.get("path") or ""))
            digest = str(item.get("sha256") or "")
            size = int(item.get("size") or 0)
        except Exception as exc:
            raise AppError(str(exc), 400, "invalid_package") from exc
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise AppError(f"文件 {rel} 缺少有效哈希", 400, "invalid_package")
        if rel in seen:
            raise AppError(f"升级包包含重复文件：{rel}", 400, "invalid_package")
        if size < 0:
            raise AppError("升级包文件大小无效", 400, "invalid_package")
        seen.add(rel)
        total_size += size
    if total_size > MAX_UNCOMPRESSED:
        raise AppError("升级包解压后体积过大", 400, "invalid_package")
    required = {"app.py", "version.json"}
    missing = required - seen
    if missing:
        raise AppError("升级包缺少核心文件：" + "、".join(sorted(missing)), 400, "invalid_package")
    if not any(path.startswith("backend/") for path in seen) or not any(path.startswith("web/") for path in seen):
        raise AppError("升级包代码不完整（缺少 backend 或 web）", 400, "invalid_package")
    manifest["version"] = version
    manifest["files"] = files
    manifest["_total_size"] = total_size
    return manifest


def _verify_zip(zip_file: zipfile.ZipFile, manifest: dict) -> None:
    names = set(zip_file.namelist())
    expected = {"update.json"} | {f"payload/{item['path']}" for item in manifest["files"]}
    extra = names - expected - {name for name in names if name.endswith("/")}
    if extra:
        example = sorted(extra)[:3]
        raise AppError("升级包包含清单外文件：" + "、".join(example), 400, "unexpected_file")
    for item in manifest["files"]:
        rel = item["path"]
        member = f"payload/{rel}"
        if member not in names:
            raise AppError(f"升级包缺少文件：{rel}", 400, "invalid_package")
        info = zip_file.getinfo(member)
        if info.file_size != int(item.get("size") or 0):
            raise AppError(f"文件大小校验失败：{rel}", 400, "checksum_failed")
        if info.file_size > 8 * 1024 * 1024 and not rel.startswith(("web/", "backend/", "docs/")):
            raise AppError(f"升级包文件异常：{rel}", 400, "invalid_package")
        digest = hashlib.sha256()
        with zip_file.open(member) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise AppError(f"文件哈希校验失败：{rel}，升级包可能损坏", 400, "checksum_failed")


def inspect_package(path: Path, original_name: str) -> dict:
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise AppError("升级包超过 120 MB", 400, "package_too_large")
    try:
        with zipfile.ZipFile(path) as zip_file:
            manifest = _load_manifest(zip_file)
            version = manifest["version"]
            if version == config.APP_VERSION:
                raise AppError("升级包与当前版本相同，无需升级", 409, "same_version")
            try:
                if _version_tuple(version) < _version_tuple(config.APP_VERSION):
                    raise AppError("不能通过升级包降级，请在版本管理中回退", 409, "older_version")
            except ValueError:
                pass
            min_from = str(manifest.get("min_from") or "1.0.0")
            try:
                if _version_tuple(config.APP_VERSION) < _version_tuple(min_from):
                    raise AppError(
                        f"当前版本 {config.APP_VERSION} 过低，请先安装完整包（此升级包要求 {min_from}+）",
                        409, "version_incompatible",
                    )
            except ValueError:
                raise AppError("升级包的最低版本要求格式不正确", 400, "invalid_package")
            _verify_zip(zip_file, manifest)
            notes = clean_text(manifest.get("notes") or "")[:4000]
            released = clean_text(manifest.get("released") or "")[:40]
            file_count = len(manifest["files"])
            total_size = manifest["_total_size"]
    except zipfile.BadZipFile as exc:
        raise AppError("升级包不是有效的 ZIP 文件", 400, "invalid_package") from exc
    package_id = "upd_" + secrets.token_urlsafe(20).replace("-", "").replace("_", "")[:24]
    config.ensure_dirs()
    packages_dir = config.UPDATE_DIR / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    target = packages_dir / f"{package_id}.zip"
    shutil.copy2(path, target)
    meta = {
        "package_id": package_id,
        "version": version,
        "min_from": min_from,
        "released": released,
        "notes": notes,
        "file_count": file_count,
        "size": path.stat().st_size,
        "uncompressed_size": total_size,
        "original_name": original_name,
        "inspected_at": now_iso(),
        "path": str(target),
    }
    (packages_dir / f"{package_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta.pop("path", None)
    return meta


def _load_package_meta(package_id: str) -> dict:
    if not PACKAGE_RE.fullmatch(package_id or ""):
        raise AppError("升级包标识无效", 400)
    meta_path = config.UPDATE_DIR / "packages" / f"{package_id}.json"
    if not meta_path.exists():
        raise AppError("升级包不存在或已被清理，请重新选择", 404, "package_missing")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        package_path = Path(meta["path"])
        package_path.relative_to(config.UPDATE_DIR / "packages")
    except Exception as exc:
        raise AppError("升级包元数据损坏", 400) from exc
    if not package_path.exists():
        raise AppError("升级包文件已丢失，请重新选择", 404)
    return meta


def _extract_payload(package_path: Path, destination: Path) -> dict:
    with zipfile.ZipFile(package_path) as zip_file:
        manifest = _load_manifest(zip_file)
        _verify_zip(zip_file, manifest)
        destination.mkdir(parents=True, exist_ok=False)
        for item in manifest["files"]:
            rel = _safe_rel(item["path"])
            target = destination / Path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(f"payload/{rel}") as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        try:
            version_json = json.loads((destination / "version.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise AppError("新版本 version.json 无效", 400, "invalid_package") from exc
        if version_json.get("version") != manifest["version"]:
            raise AppError("版本清单与 version.json 不一致，升级包已被修改", 400, "invalid_package")
        if version_json.get("product") != config.APP_PRODUCT:
            raise AppError("升级包产品标识不一致", 400, "invalid_package")
    return manifest


def apply_package(package_id: str) -> dict:
    if not config.INSTALL_ROOT:
        raise AppError(
            "当前是源码开发模式，不会覆盖程序文件。请在 Windows 安装版中使用离线升级。",
            409, "not_installed",
        )
    active = _read_active_version()
    if active != config.APP_VERSION:
        raise AppError(
            f"新版本 {active} 已准备好，请先重启应用后再继续升级",
            409, "restart_required",
        )
    meta = _load_package_meta(package_id)
    version = meta["version"]
    try:
        if _version_tuple(version) <= _version_tuple(config.APP_VERSION):
            raise AppError("升级包版本不高于当前版本，不能安装", 409, "version_incompatible")
        min_from = str(meta.get("min_from") or "1.0.0")
        if _version_tuple(config.APP_VERSION) < _version_tuple(min_from):
            raise AppError("当前版本过低，不能安装这个升级包", 409, "version_incompatible")
    except ValueError:
        raise AppError("升级包版本号无效", 400, "invalid_version")
    versions = _versions_dir()
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / version
    if target.exists():
        raise AppError(f"版本 {version} 已经存在，不能重复安装", 409, "version_exists")
    package_path = Path(meta["path"])
    stage = versions / f".stage-{package_id}"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    try:
        manifest = _extract_payload(package_path, stage)
        if manifest["version"] != version:
            raise AppError("升级包版本校验失败", 400, "invalid_package")
        # 语法检查：避免把无法启动的 Python 版本设为启动版本。
        if not compileall.compile_file(str(stage / "app.py"), quiet=2):
            raise AppError("新版本 app.py 存在语法错误，升级包未安装", 400, "invalid_package")
        if not compileall.compile_dir(str(stage / "backend"), quiet=2):
            raise AppError("新版本后端代码存在语法错误，升级包未安装", 400, "invalid_package")
        os.replace(stage, target)
    except AppError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        log.exception("安装离线升级包失败")
        raise AppError(f"安装升级包失败：{exc}", 500, "apply_failed") from exc

    backup = None
    try:
        backup = db.backup_database(f"pre-update-{version}")
        _write_active(version)
    except Exception as exc:
        log.exception("切换版本失败")
        shutil.rmtree(target, ignore_errors=True)
        raise AppError(f"切换版本失败，原版本未被替换：{exc}", 500, "apply_failed") from exc
    _cleanup_versions(keep=4)
    _cleanup_package(package_id)
    return {
        "version": version,
        "restart_required": True,
        "backup": backup.name if backup else "",
        "message": f"版本 {version} 已安装，请重启应用完成切换",
    }


def _cleanup_versions(keep: int = 4) -> None:
    try:
        versions = _versions_dir()
        active = _read_active_version()
        folders = [
            folder for folder in versions.iterdir()
            if folder.is_dir() and SEMVER_RE.fullmatch(folder.name)
        ]
        folders.sort(key=lambda p: _version_tuple(p.name), reverse=True)
        kept = 0
        for folder in folders:
            if folder.name == active:
                continue
            kept += 1
            if kept >= keep:
                shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        log.exception("清理旧版本失败")


def _cleanup_package(package_id: str) -> None:
    folder = config.UPDATE_DIR / "packages"
    for path in (folder / f"{package_id}.zip", folder / f"{package_id}.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def list_versions() -> dict:
    active = _read_active_version()
    versions = []
    if config.INSTALL_ROOT and _versions_dir().exists():
        for folder in sorted(_versions_dir().iterdir(), key=lambda p: p.name, reverse=True):
            if not folder.is_dir() or not SEMVER_RE.fullmatch(folder.name):
                continue
            released = ""
            try:
                info = json.loads((folder / "version.json").read_text(encoding="utf-8"))
                released = info.get("released", "")
            except Exception:
                pass
            versions.append({
                "version": folder.name,
                "active": folder.name == active,
                "running": folder.name == config.APP_VERSION,
                "released": released,
                "size": sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()),
            })
    return {
        "installed": bool(config.INSTALL_ROOT),
        "running_version": config.APP_VERSION,
        "active_version": active,
        "restart_required": active != config.APP_VERSION,
        "versions": versions,
        "install_root": str(config.INSTALL_ROOT) if config.INSTALL_ROOT else "",
    }


def status() -> dict:
    data = list_versions()
    data.update({
        "product": config.APP_PRODUCT,
        "name": config.APP_NAME,
        "update_format": 1,
        "package_extensions": [".zip", ".hcup"],
        "data_dir": str(config.DATA_DIR),
    })
    return data


def activate_version(version: str) -> dict:
    if not config.INSTALL_ROOT:
        raise AppError("当前是源码开发模式，无法切换版本", 409, "not_installed")
    try:
        _version_tuple(version)
    except ValueError as exc:
        raise AppError("版本号格式无效", 400) from exc
    target = _versions_dir() / version
    if not target.is_dir():
        raise AppError("该版本不存在", 404, "version_not_found")
    try:
        info = json.loads((target / "version.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppError("目标版本文件不完整", 400) from exc
    if info.get("product") != config.APP_PRODUCT:
        raise AppError("目标版本不是本产品", 400)
    current_active = _read_active_version()
    if version == current_active:
        raise AppError("该版本已经是当前启动版本", 409, "same_version")
    if version != config.APP_VERSION or current_active != version:
        db.backup_database(f"pre-switch-{version}")
    _write_active(version)
    if version == config.APP_VERSION:
        return {
            "version": version,
            "restart_required": False,
            "message": f"已取消待生效版本，继续使用当前运行的 v{version}",
        }
    return {"version": version, "restart_required": True, "message": f"已切换到 {version}，请重启应用"}


def schedule_restart() -> dict:
    if not config.INSTALL_ROOT:
        raise AppError("当前是源码开发模式，请重启程序", 409, "not_installed")
    active = _read_active_version()
    if active == config.APP_VERSION:
        raise AppError("没有等待生效的新版本", 409, "no_pending_version")
    launcher = None
    for candidate in (
        config.INSTALL_ROOT / "Start-HengCe.vbs",
        config.INSTALL_ROOT / "Start-HengCe-Console.cmd",
        config.INSTALL_ROOT / "启动经营数据工作台.vbs",
    ):
        if candidate.exists():
            launcher = candidate
            break
    if not launcher:
        return {
            "scheduled": False,
            "message": "新版本已准备就绪，请关闭窗口后重新打开应用",
        }
    helper = config.UPDATE_DIR / "restart_update.py"
    helper.write_text(
        "import os, time\n"
        "time.sleep(3)\n"
        f"os.startfile({str(launcher)!r})\n",
        encoding="utf-8",
    )
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [sys.executable, str(helper)],
            cwd=str(config.INSTALL_ROOT),
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception:
        log.exception("启动重启助手失败")
        return {
            "scheduled": False,
            "message": "新版本已准备就绪，请关闭窗口后重新打开应用",
        }
    threading.Timer(1.2, lambda: os._exit(0)).start()
    return {
        "scheduled": True,
        "message": "正在重启应用，新版本即将打开，请稍候",
    }
