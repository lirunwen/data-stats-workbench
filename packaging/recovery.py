#!/usr/bin/env python3
"""Emergency version switch for the portable/installed Windows build.

Runs from the installation root with the embedded Python. Useful when a newly
installed version cannot start and the normal UI is therefore unavailable.
It backs up the SQLite database, switches program/active.txt, and relaunches.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PRODUCT = "hengce-workbench"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def semver(value: str):
    match = SEMVER.fullmatch(value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def app_is_running(data_dir: Path) -> bool:
    try:
        runtime = json.loads((data_dir / "runtime.json").read_text(encoding="utf-8"))
        port = int(runtime.get("port"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("product") == PRODUCT and payload.get("status") == "ok"
    except Exception:
        return False


def backup_database(db_path: Path, backup_dir: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"hengce-recovery-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def main() -> int:
    root = Path(__file__).resolve().parent
    program = root / "program"
    versions_dir = program / "versions"
    data_dir = Path(os.environ.get("HENGCE_DATA_DIR") or (root / "data")).resolve()
    if app_is_running(data_dir):
        print("工作台正在运行。请先关闭浏览器页面，并在「设置与数据安全 → 退出工作台」中停止服务。")
        return 1
    versions = sorted(
        [path for path in versions_dir.iterdir() if path.is_dir() and SEMVER.fullmatch(path.name)],
        key=lambda path: semver(path.name),
        reverse=True,
    ) if versions_dir.exists() else []
    if not versions:
        print("没有找到任何程序版本，请重新安装完整包。")
        return 1
    active = ""
    try:
        active = (program / "active.txt").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    candidates = [path for path in versions if path.name != active] or versions
    default = candidates[0].name
    print("衡策经营数据工作台 · 启动版本恢复")
    print("=" * 48)
    print("可用版本：")
    for index, path in enumerate(versions, start=1):
        try:
            info = json.loads((path / "version.json").read_text(encoding="utf-8"))
            released = info.get("released", "")
        except Exception:
            released = ""
        marker = "  ← 当前启动版本" if path.name == active else ""
        print(f"  {index}. v{path.name}  {released}{marker}")
    print()
    print(f"默认切换到上一个版本 v{default}。")
    print("输入序号后回车（直接回车使用默认版本，输入 Q 取消）：")
    choice = input("> ").strip()
    if choice.lower() == "q":
        return 0
    if not choice:
        selected = candidates[0]
    else:
        try:
            selected = versions[int(choice) - 1]
        except (ValueError, IndexError):
            print("序号无效，未做任何修改。")
            return 1
    if not (selected / "app.py").exists() or not (selected / "version.json").exists():
        print(f"v{selected.name} 文件不完整，未做任何修改。")
        return 1
    try:
        info = json.loads((selected / "version.json").read_text(encoding="utf-8"))
        if info.get("product") != PRODUCT:
            raise ValueError("product mismatch")
    except Exception:
        print("目标版本信息无效，未做任何修改。")
        return 1
    backup = backup_database(data_dir / "hengce.db", data_dir / "backup")
    temp = program / "active.txt.tmp"
    temp.write_text(selected.name + "\n", encoding="utf-8")
    os.replace(temp, program / "active.txt")
    print()
    print(f"已切换启动版本为 v{selected.name}。")
    if backup:
        print(f"数据库备份：{backup}")
    print("正在重新打开工作台…")
    launcher = root / "Start-HengCe.vbs"
    try:
        if os.name == "nt" and launcher.exists():
            os.startfile(str(launcher))  # type: ignore[attr-defined]
        else:
            print("请双击 Start-HengCe.vbs 启动。")
    except Exception as exc:
        print(f"自动启动失败：{exc}")
        print("请手动双击 Start-HengCe.vbs。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
