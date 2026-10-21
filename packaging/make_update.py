#!/usr/bin/env python3
"""Build an offline update package that the in-app updater can verify/install.

The package is a ZIP:
  update.json
  payload/app.py
  payload/version.json
  payload/backend/...
  payload/web/...

Bump version.json before building. The build refuses a version mismatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCT = "hengce-workbench"
INCLUDE_FILES = ["app.py", "version.json", "requirements.txt"]
INCLUDE_DIRS = ["backend", "web"]
INCLUDE_VENDOR = "vendor"
SKIP_PARTS = {"__pycache__", ".pytest_cache", "data", "tests", "samples"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".DS_Store"}


def payload_files() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)
    for dirname in INCLUDE_DIRS:
        source = ROOT / dirname
        if not source.exists():
            continue
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            files.append(path)
    vendor = ROOT / INCLUDE_VENDOR
    if vendor.is_dir():
        for path in vendor.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() not in SKIP_SUFFIXES:
                files.append(path)
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", default="", help="Update notes shown in the app")
    parser.add_argument("--min-from", default="1.0.0", help="Minimum source version")
    parser.add_argument("--out", default=str(ROOT / "dist" / "updates"))
    parser.add_argument("--extension", default=".zip", choices=[".zip", ".hcup"])
    args = parser.parse_args()
    version_info = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    if version_info.get("product") != PRODUCT:
        raise SystemExit("version.json product mismatch")
    version = str(version_info.get("version") or "")
    if not version or version.count(".") != 2:
        raise SystemExit("version.json must contain semver x.y.z")
    files = payload_files()
    manifest = {
        "format": 1,
        "product": PRODUCT,
        "version": version,
        "min_from": args.min_from,
        "released": version_info.get("released") or date.today().isoformat(),
        "notes": args.notes,
        "files": [],
    }
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        manifest["files"].append({
            "path": rel,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"HengCe-Update-v{version}{args.extension}"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        archive.writestr("update.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in files:
            archive.write(path, arcname=f"payload/{path.relative_to(ROOT).as_posix()}")
    print(f"update package: {target}")
    print(f"version: {version} · files: {len(files)} · size: {target.stat().st_size / 1024:.1f} KB")
    print(f"sha256: {hashlib.sha256(target.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
