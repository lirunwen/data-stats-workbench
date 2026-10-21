#!/usr/bin/env python3
"""Windows 安装版的稳定启动器。

它不属于任何业务版本：只负责读取 program/active.txt、准备数据目录、
把版本目录加入 sys.path，然后运行该版本的 app.py。
升级时只替换 program/versions 下的目录。
"""
from __future__ import annotations

import os
import runpy
import sys
import traceback
from pathlib import Path


def fatal(message: str) -> None:
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "数据统计工作台", 0x10)
        else:
            print(message, file=sys.stderr)
    finally:
        sys.exit(1)


def read_version(root: Path) -> str:
    for location in (root / "program" / "active.txt", root / "active.txt"):
        try:
            value = location.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    versions = sorted(
        [path for path in (root / "program" / "versions").iterdir() if path.is_dir()],
        key=lambda path: path.name,
    ) if (root / "program" / "versions").exists() else []
    if versions:
        return versions[-1].name
    fatal("没有找到可启动的程序版本。请重新解压完整安装包。")


def main() -> None:
    root = Path(__file__).resolve().parent
    version = read_version(root)
    version_dir = root / "program" / "versions" / version
    app_file = version_dir / "app.py"
    if not app_file.exists():
        fatal(
            f"启动版本 v{version} 不完整。\n\n"
            "请检查 program/active.txt，或重新安装完整包。\n"
            f"程序目录：{version_dir}"
        )
    data_dir = Path(os.environ.get("DATASTATS_DATA_DIR") or (root / "data")).resolve()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        # 检查数据目录可写，避免启动后才在保存时失败。
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        fatal(
            "数据目录无法写入，应用不能启动。\n\n"
            f"目录：{data_dir}\n原因：{exc}\n\n"
            "请将程序放到有写入权限的位置，或联系管理员。"
        )
    os.environ["DATASTATS_PACKAGED"] = "1"
    os.environ["DATASTATS_INSTALL_ROOT"] = str(root)
    os.environ["DATASTATS_VERSION_DIR"] = str(version_dir)
    os.environ["DATASTATS_DATA_DIR"] = str(data_dir)
    if os.name == "nt":
        os.chdir(version_dir)
    for path in (version_dir / "vendor", version_dir, version_dir / "web"):
        if path.exists():
            sys.path.insert(0, str(path))
    sys.argv = [str(app_file)]
    try:
        runpy.run_path(str(app_file), run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        log_file = data_dir / "logs" / "launcher-error.log"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        fatal(
            "应用启动失败，数据没有被修改。\n\n"
            f"错误：{detail.splitlines()[-1] if detail.splitlines() else '未知错误'}\n"
            f"日志：{log_file}\n\n"
            "请重新打开程序；若持续失败，请联系维护人员。"
        )


if __name__ == "__main__":
    main()
