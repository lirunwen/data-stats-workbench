#!/usr/bin/env python3
"""离线升级机制测试：版本目录、哈希校验、安装、备份、待重启、取消待生效。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP = Path(tempfile.mkdtemp(prefix="hengce-update-test-"))
INSTALL = TEMP / "install"
SOURCE = TEMP / "source-1.0.1"
DATA = INSTALL / "data"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def prepare():
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    (INSTALL / "program" / "versions" / version).mkdir(parents=True)
    (INSTALL / "program" / "active.txt").parent.mkdir(parents=True, exist_ok=True)
    (INSTALL / "program" / "active.txt").write_text(version + "\n", encoding="utf-8")
    (INSTALL / "program" / "versions" / version / "version.json").write_text(
        json.dumps({"product": "hengce-workbench", "version": version, "released": "2026-10-21"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (INSTALL / "Start-HengCe.vbs").write_text("' placeholder\n", encoding="ascii")
    shutil.copytree(
        ROOT, SOURCE,
        ignore=shutil.ignore_patterns("data", "dist", ".git", "__pycache__", "*.pyc", ".venv"),
    )
    info_path = SOURCE / "version.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["version"] = "1.0.1"
    info["released"] = "2026-11-01"
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    package_dir = TEMP / "packages"
    result = subprocess.run(
        [sys.executable, "packaging/make_update.py", "--notes", "自动测试升级包", "--out", str(package_dir)],
        cwd=SOURCE, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return package_dir / "HengCe-Update-v1.0.1.zip"


def main():
    package_path = prepare()
    os.environ["HENGCE_DATA_DIR"] = str(DATA)
    os.environ["HENGCE_INSTALL_ROOT"] = str(INSTALL)
    os.environ["HENGCE_VERSION_DIR"] = str(INSTALL / "program" / "versions" / "1.0.0")
    os.environ["HENGCE_PACKAGED"] = "1"
    sys.path.insert(0, str(ROOT))
    from backend import config, db, updater
    from backend.errors import AppError

    config.configure(DATA)
    db.init_db()
    print("[1] 升级包校验")
    meta = updater.inspect_package(package_path, package_path.name)
    check(meta["version"] == "1.0.1", "识别目标版本")
    check(meta["file_count"] >= 20, "文件清单完整")
    check(meta["min_from"] == "1.0.0", "最低来源版本")
    check(updater.status()["active_version"] == "1.0.0", "初始启动版本")

    print("[2] 篡改检测")
    tampered = TEMP / "tampered.zip"
    with zipfile.ZipFile(package_path) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "payload/app.py":
                content = content + b"\n# tampered\n"
            target.writestr(info, content)
    try:
        updater.inspect_package(tampered, tampered.name)
        raise AssertionError("tampered package accepted")
    except AppError as exc:
        check(exc.code in {"checksum_failed", "invalid_package"}, "篡改的升级包被拒绝")

    print("[3] 安装与备份")
    result = updater.apply_package(meta["package_id"])
    check(result["version"] == "1.0.1", "安装新版本")
    active = (INSTALL / "program" / "active.txt").read_text(encoding="utf-8").strip()
    check(active == "1.0.1", "切换 active.txt")
    check((INSTALL / "program" / "versions" / "1.0.1" / "app.py").exists(), "新版本目录落盘")
    check((INSTALL / "program" / "versions" / "1.0.0" / "version.json").exists(), "旧版本保留")
    check(bool(list((DATA / "backup").glob("hengce-pre-update-*.db"))), "升级前备份数据库")
    status = updater.status()
    check(status["restart_required"] is True and status["active_version"] == "1.0.1", "提示重启")
    check(status["running_version"] == "1.0.0", "运行版本仍是旧版")

    print("[4] 取消待生效版本")
    cancel = updater.activate_version("1.0.0")
    check(cancel["restart_required"] is False, "取消重启提示")
    check((INSTALL / "program" / "active.txt").read_text(encoding="utf-8").strip() == "1.0.0", "恢复原启动版本")
    check(updater.status()["restart_required"] is False, "状态恢复")

    print("[5] 清理与状态")
    check(not list((DATA / "updates" / "packages").glob("*.zip")), "安装后清理升级包副本")
    print("\n升级机制自检通过")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
    finally:
        shutil.rmtree(TEMP, ignore_errors=True)
