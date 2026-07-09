#!/usr/bin/env python3
"""Install the PaperMuse main Python runtime from a verified manifest asset."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse


INSTALL_RECORD = ".paper-muse-runtime.json"


class BootstrapError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_runtime_manifest(path: Path, component: str = "runtime") -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    runtime = data.get(component) or {}
    required = ("platform", "version", "asset_url", "archive_type", "sha256", "entrypoint")
    missing = [key for key in required if not runtime.get(key)]
    if missing:
        raise BootstrapError(f"{component} manifest missing fields: " + ", ".join(missing))
    entrypoint = Path(runtime["entrypoint"])
    if entrypoint.is_absolute() or ".." in entrypoint.parts or len(entrypoint.parts) < 2:
        raise BootstrapError(f"invalid runtime entrypoint: {runtime['entrypoint']}")
    install_dir = runtime.get("install_dir") or entrypoint.parts[0]
    if Path(install_dir).is_absolute() or "/" in install_dir or install_dir in ("", ".", ".."):
        raise BootstrapError(f"invalid runtime install_dir: {install_dir}")
    runtime["install_dir"] = install_dir
    return runtime


def _entrypoint(runtime_dir: Path, manifest: dict) -> Path:
    return runtime_dir / manifest["entrypoint"]


def _install_root(runtime_dir: Path, manifest: dict) -> Path:
    return runtime_dir / manifest["install_dir"]


def _record_path_for(runtime_dir: Path, manifest: dict) -> Path:
    return _install_root(runtime_dir, manifest) / INSTALL_RECORD


def runtime_is_healthy(runtime_dir: Path, manifest: dict) -> bool:
    record = _record_path_for(runtime_dir, manifest)
    python = _entrypoint(runtime_dir, manifest)
    if not record.exists() or not python.exists() or not os.access(python, os.X_OK):
        return False
    try:
        installed = json.loads(record.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if installed.get("version") != manifest["version"] or installed.get("sha256") != manifest["sha256"]:
        return False
    return subprocess.run([str(python), "--version"], capture_output=True, text=True, timeout=10).returncode == 0


def _download(url: str, dest: Path, base_dir: Path | None = None) -> None:
    parsed = urlparse(url)
    if not parsed.scheme and base_dir is not None:
        shutil.copy2(base_dir / url, dest)
        return
    if parsed.scheme == "file":
        shutil.copy2(Path(urllib.request.url2pathname(parsed.path)), dest)
        return
    if parsed.scheme in ("http", "https"):
        with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as f:
            shutil.copyfileobj(resp, f)
        return
    raise BootstrapError(f"unsupported runtime asset URL: {url}")


def _extract(archive: Path, archive_type: str, dest: Path) -> None:
    if archive_type in ("tar.gz", "tgz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="data")
    elif archive_type == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        raise BootstrapError(f"unsupported runtime archive type: {archive_type}")


def _make_executable(path: Path) -> None:
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def bootstrap(manifest_path: Path, runtime_dir: Path, component: str = "runtime") -> str:
    manifest = load_runtime_manifest(manifest_path, component=component)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if runtime_is_healthy(runtime_dir, manifest):
        return "already-installed"

    with tempfile.TemporaryDirectory(prefix=".paper-muse-runtime-", dir=runtime_dir) as tmp_name:
        tmp = Path(tmp_name)
        archive = tmp / "runtime-asset"
        try:
            _download(manifest["asset_url"], archive, base_dir=manifest_path.parent)
            actual = sha256_file(archive)
            if actual != manifest["sha256"]:
                raise BootstrapError(f"runtime checksum mismatch: expected {manifest['sha256']} got {actual}")
            extracted = tmp / "extract"
            extracted.mkdir()
            _extract(archive, manifest["archive_type"], extracted)
            staged_main = extracted / manifest["install_dir"]
            staged_python = extracted / manifest["entrypoint"]
            _make_executable(staged_python)
            if not staged_python.exists() or not os.access(staged_python, os.X_OK):
                raise BootstrapError(f"runtime archive missing executable {manifest['entrypoint']}")
            if subprocess.run([str(staged_python), "--version"], capture_output=True, text=True, timeout=10).returncode != 0:
                raise BootstrapError("runtime executable failed health check")
            (staged_main / INSTALL_RECORD).write_text(
                json.dumps({
                    "version": manifest["version"],
                    "platform": manifest["platform"],
                    "sha256": manifest["sha256"],
                    "entrypoint": manifest["entrypoint"],
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            final_main = _install_root(runtime_dir, manifest)
            backup = runtime_dir / f".{manifest['install_dir']}.previous"
            if backup.exists():
                shutil.rmtree(backup)
            try:
                if final_main.exists():
                    final_main.rename(backup)
                staged_main.rename(final_main)
            except Exception:
                if backup.exists() and not final_main.exists():
                    backup.rename(final_main)
                raise
            else:
                if backup.exists():
                    shutil.rmtree(backup)
        except Exception:
            if (runtime_dir / ".main.previous").exists():
                shutil.rmtree(runtime_dir / ".main.previous", ignore_errors=True)
            raise
    return "installed"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--manifest", required=True, type=Path)
    boot.add_argument("--runtime-dir", required=True, type=Path)
    boot.add_argument("--component", default="runtime")
    args = parser.parse_args()

    if args.cmd == "bootstrap":
        print(bootstrap(args.manifest, args.runtime_dir, component=args.component))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as e:
        print(f"runtime bootstrap failed: {e}", file=os.sys.stderr)
        raise SystemExit(1)
