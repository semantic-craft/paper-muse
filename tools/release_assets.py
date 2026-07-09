#!/usr/bin/env python3
"""Stage and scan the public server assets bundled into PaperMuse.app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "runtime-manifest.json"
EMBEDDED_MAIN_RUNTIME = "runtime/main-runtime.tar.gz"

PUBLIC_FILES = [
    "muse_server.py",
    "blindspot.py",
    "prompt_assets.py",
    "adversary.py",
    "paperqa_bridge.py",
    "gptr_sidecar.py",
    "requirements.txt",
    "requirements-paperqa.txt",
    "requirements-gptr.txt",
    "secrets.toml.example",
    "tools/runtime_bootstrap.py",
    "LICENSE",
]
PUBLIC_DIRS = [
    "knowledge_storm",
    "webui",
]
REQUIRED_PATHS = [
    "muse_server.py",
    "blindspot.py",
    "prompt_assets.py",
    "adversary.py",
    "paperqa_bridge.py",
    "knowledge_storm/__init__.py",
    "webui/index.html",
    "tools/runtime_bootstrap.py",
    "requirements.txt",
    "requirements-paperqa.txt",
    MANIFEST,
]

IGNORED_NAMES = {
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
BANNED_COMPONENTS = {
    ".cache",
    ".venv",
    ".venv-gptr",
    "browser-state",
    "cache",
    "cookies",
    "indexes",
    "local",
    "logs",
    "results",
    "smoke",
}
BANNED_COMPONENT_SUBSTRINGS = {
    "smoke",
}
BANNED_FILENAMES = {
    ".env",
    "Cookies",
    "History",
    "Local State",
    "researcher.md",
    "secrets.toml",
}
BANNED_SUFFIXES = {
    ".db",
    ".log",
    ".pt",
    ".sqlite",
    ".sqlite3",
}


def _ignore(_dir, names):
    return sorted(name for name in names if name in IGNORED_NAMES)


def _server_root(path: Path) -> Path:
    app_server = path / "Contents" / "Resources" / "server"
    return app_server if app_server.exists() else path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest(root: Path) -> dict:
    embedded_main = root / EMBEDDED_MAIN_RUNTIME
    main_asset_url = EMBEDDED_MAIN_RUNTIME if embedded_main.exists() else os.environ.get(
        "PAPER_MUSE_MAIN_RUNTIME_URL", "PAPER_MUSE_MAIN_RUNTIME_URL")
    main_sha = _sha256(embedded_main) if embedded_main.exists() else os.environ.get(
        "PAPER_MUSE_MAIN_RUNTIME_SHA256", "PAPER_MUSE_MAIN_RUNTIME_SHA256")
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != MANIFEST):
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "schema": 1,
        "name": "paper-muse-server-assets",
        "entrypoint": "muse_server.py",
        "runtime": {
            "platform": os.environ.get("PAPER_MUSE_MAIN_RUNTIME_PLATFORM", "macos-arm64"),
            "version": os.environ.get("PAPER_MUSE_MAIN_RUNTIME_VERSION", "main-python-3.12-v1"),
            "asset_url": main_asset_url,
            "archive_type": os.environ.get("PAPER_MUSE_MAIN_RUNTIME_ARCHIVE_TYPE", "tar.gz"),
            "sha256": main_sha,
            "entrypoint": "main/bin/python",
            "install_dir": "main",
            "compatible_app": os.environ.get("PAPER_MUSE_APP_VERSION", "dev"),
            "compatible_server_schema": 1,
            "python": "3.12",
            "main_requirements": "requirements.txt",
            "paperqa_requirements": "requirements-paperqa.txt",
            "sidecar_requirements": "requirements-gptr.txt",
        },
        "sidecar_runtime": {
            "optional": True,
            "platform": os.environ.get("PAPER_MUSE_SIDECAR_RUNTIME_PLATFORM", "macos-arm64"),
            "version": os.environ.get("PAPER_MUSE_SIDECAR_RUNTIME_VERSION", "sidecar-python-3.12-v1"),
            "asset_url": os.environ.get("PAPER_MUSE_SIDECAR_RUNTIME_URL", "PAPER_MUSE_SIDECAR_RUNTIME_URL"),
            "archive_type": os.environ.get("PAPER_MUSE_SIDECAR_RUNTIME_ARCHIVE_TYPE", "tar.gz"),
            "sha256": os.environ.get("PAPER_MUSE_SIDECAR_RUNTIME_SHA256", "PAPER_MUSE_SIDECAR_RUNTIME_SHA256"),
            "entrypoint": "sidecar/bin/python",
            "install_dir": "sidecar",
            "compatible_app": os.environ.get("PAPER_MUSE_APP_VERSION", "dev"),
            "compatible_server_schema": 1,
            "python": "3.12",
            "requirements": "requirements-gptr.txt",
        },
        "required_paths": REQUIRED_PATHS,
        "files": files,
    }


def stage(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for rel in PUBLIC_FILES:
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    for rel in PUBLIC_DIRS:
        shutil.copytree(ROOT / rel, output / rel, ignore=_ignore)
    main_runtime_file = os.environ.get("PAPER_MUSE_MAIN_RUNTIME_FILE")
    if main_runtime_file:
        src = Path(main_runtime_file).expanduser()
        if not src.is_absolute():
            src = ROOT / src
        dst = output / EMBEDDED_MAIN_RUNTIME
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (output / MANIFEST).write_text(
        json.dumps(_manifest(output), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    scan(output)


def scan(path: Path) -> None:
    root = _server_root(path)
    if not root.exists():
        raise RuntimeError(f"server assets not found: {root}")

    missing = [rel for rel in REQUIRED_PATHS if not (root / rel).exists()]
    if missing:
        raise RuntimeError("missing required release assets: " + ", ".join(missing))

    offenders = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        parts = set(p.relative_to(root).parts)
        lower_parts = {part.lower() for part in parts}
        if (
            parts & BANNED_FILENAMES
            or lower_parts & BANNED_COMPONENTS
            or any(token in part for part in lower_parts for token in BANNED_COMPONENT_SUBSTRINGS)
            or p.suffix.lower() in BANNED_SUFFIXES
        ):
            offenders.append(rel)
    if offenders:
        raise RuntimeError("private release assets found: " + ", ".join(sorted(offenders)[:20]))

    json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    stage_p = sub.add_parser("stage")
    stage_p.add_argument("--output", required=True, type=Path)
    scan_p = sub.add_parser("scan")
    scan_p.add_argument("path", type=Path)
    args = parser.parse_args()

    if args.cmd == "stage":
        stage(args.output)
    else:
        scan(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
