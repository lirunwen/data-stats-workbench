#!/usr/bin/env python3
"""Assemble the self-contained Windows x64 portable folder and ZIP.

This script is intended for maintainers. It does not download anything:
pass a local Python embeddable ZIP and a wheelhouse prepared on a networked
build machine. The produced runtime is fully offline.

Example:
  python packaging/build_portable.py \
    --embed-zip /path/python-3.12.10-embed-amd64.zip \
    --wheels /path/wheels \
    --out dist
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_ASSETS = ROOT / "packaging" / "windows"
PRODUCT = "data-stats-workbench"
PACKAGE_FOLDER = "DataStatsWorkbench"
VERSION_FILES = [
    "app.py", "version.json", "requirements.txt",
]
VERSION_DIRS = ["backend", "web"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "data", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}


def copy_source_version(target: Path) -> str:
    version_info = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    if version_info.get("product") != PRODUCT:
        raise SystemExit("version.json product mismatch")
    for name in VERSION_FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, target / name)
    for dirname in VERSION_DIRS:
        source_dir = ROOT / dirname
        for item in source_dir.rglob("*"):
            if not item.is_file():
                continue
            if any(part in SKIP_PARTS for part in item.parts):
                continue
            if item.suffix.lower() in SKIP_SUFFIXES:
                continue
            rel = item.relative_to(ROOT)
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
    vendor = ROOT / "vendor"
    if vendor.is_dir():
        shutil.copytree(vendor, target / "vendor", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return str(version_info["version"])


def extract_embed(embed_zip: Path, python_dir: Path) -> None:
    python_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(embed_zip) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = python_dir / info.filename
            if not str(target.resolve()).startswith(str(python_dir.resolve())):
                raise SystemExit(f"unsafe path in embed zip: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    pth = python_dir / "python312._pth"
    pth.write_text(
        "python312.zip\n.\nLib\\site-packages\n\nimport site\n",
        encoding="ascii",
    )
    if not (python_dir / "pythonw.exe").exists():
        raise SystemExit("embedded Python has no pythonw.exe")


def extract_wheel(wheel: Path, site_packages: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if ".data/" in name:
                # Remap purelib/platlib payloads; skip entry-point scripts.
                marker = ".data/purelib/"
                marker2 = ".data/platlib/"
                if marker in name:
                    name = name.split(marker, 1)[1]
                elif marker2 in name:
                    name = name.split(marker2, 1)[1]
                else:
                    continue
            parts = [part for part in name.split("/") if part and part != "."]
            if not parts or any(part == ".." for part in parts):
                continue
            target = site_packages.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def extract_special_sdists(wheels: Path, site_packages: Path) -> list[str]:
    handled = []
    for archive_path in wheels.iterdir():
        name = archive_path.name.lower()
        if name.startswith("red-black-tree-mod-") and name.endswith((".tar.gz", ".zip")):
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as archive:
                    root_name = archive.namelist()[0].split("/")[0]
                    for item in archive.namelist():
                        if item.startswith(f"{root_name}/red_black_") and item.endswith(".py"):
                            data = archive.read(item)
                            (site_packages / Path(item).name).write_bytes(data)
            else:
                with tarfile.open(archive_path, "r:gz") as archive:
                    for member in archive.getmembers():
                        if "/red_black_" in f"/{member.name}" and member.name.endswith(".py") and member.isfile():
                            source = archive.extractfile(member)
                            if source:
                                (site_packages / Path(member.name).name).write_bytes(source.read())
            handled.append(archive_path.name)
        if name.startswith("win_unicode_console-") and name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                for item in archive.namelist():
                    if "/win_unicode_console/" not in "/" + item:
                        continue
                    if item.endswith("/"):
                        continue
                    rel = item.split("/win_unicode_console/", 1)[1]
                    target = site_packages / "win_unicode_console" / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(item))
            handled.append(archive_path.name)
    return handled


def install_runtime(runtime_python: Path, embed_zip: Path, wheels: Path) -> None:
    extract_embed(embed_zip, runtime_python)
    site_packages = runtime_python / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    wheel_files = sorted(wheels.glob("*.whl"))
    if len(wheel_files) < 12:
        raise SystemExit(f"wheelhouse looks incomplete ({len(wheel_files)} wheels)")
    for wheel in wheel_files:
        extract_wheel(wheel, site_packages)
    extract_special_sdists(wheels, site_packages)
    required = [
        "flask", "waitress", "openpyxl", "xlrd", "oxmsg", "olefile", "markupsafe",
    ]
    missing = [item for item in required if not (site_packages / item).exists()]
    if missing:
        raise SystemExit("runtime is missing packages: " + ", ".join(missing))


def copy_windows_assets(build_root: Path) -> None:
    for name in (
        "DataStats.ico", "Start-DataStats.vbs", "Start-DataStats-Console.cmd",
        "Install-DataStats.cmd", "Diagnostics.cmd", "Recovery-DataStats.cmd", "install.ps1", "README.txt",
        "THIRD_PARTY_NOTICES.txt",
    ):
        shutil.copy2(WINDOWS_ASSETS / name, build_root / name)
    shutil.copy2(ROOT / "packaging" / "launcher.py", build_root / "launcher.py")
    shutil.copy2(ROOT / "packaging" / "recovery.py", build_root / "recovery.py")


def make_zip(build_parent: Path, folder_name: str, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in (build_parent / folder_name).rglob("*"):
            if item.is_file():
                archive.write(item, arcname=f"{folder_name}/{item.relative_to(build_parent / folder_name).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embed-zip", required=True)
    parser.add_argument("--wheels", required=True)
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()
    embed_zip = Path(args.embed_zip).resolve()
    wheels = Path(args.wheels).resolve()
    if not embed_zip.exists():
        raise SystemExit("embed zip not found")
    if not wheels.is_dir():
        raise SystemExit("wheelhouse not found")
    out = Path(args.out).resolve()
    build_parent = out / "build"
    if build_parent.exists():
        shutil.rmtree(build_parent)
    build_parent.mkdir(parents=True)
    build_root = build_parent / PACKAGE_FOLDER
    build_root.mkdir()
    version_dir = build_root / "program" / "versions" / "placeholder"
    version_dir.mkdir(parents=True)
    version = copy_source_version(version_dir)
    final_version_dir = version_dir.with_name(version)
    version_dir.rename(final_version_dir)
    (build_root / "program" / "active.txt").write_text(version + "\n", encoding="utf-8")
    (build_root / "data").mkdir()
    copy_windows_assets(build_root)
    runtime_python = build_root / "runtime" / "python"
    install_runtime(runtime_python, embed_zip, wheels)
    license_dir = runtime_python / "Lib" / "site-packages" / "_licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "packaging" / "third_party", license_dir, dirs_exist_ok=True)
    # A small manifest for support/debugging.
    (build_root / "build-info.json").write_text(
        json.dumps({
            "product": PRODUCT,
            "version": version,
            "built_at": date.today().isoformat(),
            "python": "3.12.10 embeddable x64",
            "platform": "Windows 10/11 x64",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    zip_path = out / f"DataStatsWorkbench-Windows-x64-v{version}.zip"
    if not args.no_zip:
        make_zip(build_parent, PACKAGE_FOLDER, zip_path)
    print(f"portable folder: {build_root}")
    if not args.no_zip:
        print(f"zip: {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
