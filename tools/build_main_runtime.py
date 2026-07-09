#!/usr/bin/env python3
"""Build a self-contained PaperMuse main runtime archive from the local venv."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SCRUB_PLACEHOLDER = "@PAPER_MUSE_BUILD_PATH@"
BUILD_PATH_BYTE_REPLACEMENTS = {
    b"/private/var/folders": b"/tmp/papermuse-build",
    b"private/var/folders": b"papermuse/build/tmp",
    b"/var/folders": b"/tmp/buildxx",
}
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}


def _python_info(python: Path) -> dict:
    code = (
        "import json, sys, sysconfig; "
        "print(json.dumps({'base_prefix': sys.base_prefix, "
        "'major': sys.version_info.major, 'minor': sys.version_info.minor, "
        "'purelib': sysconfig.get_paths()['purelib']}))"
    )
    return json.loads(subprocess.check_output([str(python), "-c", code], text=True))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_bytecode(root: Path) -> None:
    for cache in list(root.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache)
    for pattern in ("*.pyc", "*.pyo"):
        for path in list(root.rglob(pattern)):
            if path.is_file() or path.is_symlink():
                path.unlink()


def _scrub_text_prefixes(root: Path, prefixes: list[Path | str]) -> None:
    needles = [str(prefix).encode() for prefix in prefixes if str(prefix)]
    replacement = TEXT_SCRUB_PLACEHOLDER.encode()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096] or not any(needle in data for needle in needles):
            continue
        for needle in needles:
            data = data.replace(needle, replacement)
        path.write_bytes(data)


def _replace_build_path_bytes(root: Path) -> None:
    for old, new in BUILD_PATH_BYTE_REPLACEMENTS.items():
        if len(old) != len(new):
            raise RuntimeError(f"build path replacement length mismatch: {old!r}")
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not any(old in data for old in BUILD_PATH_BYTE_REPLACEMENTS):
            continue
        for old, new in BUILD_PATH_BYTE_REPLACEMENTS.items():
            data = data.replace(old, new)
        path.write_bytes(data)


def _rewrite_libpython_id(main: Path, version: str) -> None:
    dylib = main / "lib" / f"libpython{version}.dylib"
    if not dylib.exists():
        return
    tool = shutil.which("install_name_tool")
    if tool is None:
        raise RuntimeError("install_name_tool is required to rewrite libpython install name")
    subprocess.check_call([tool, "-id", f"@rpath/libpython{version}.dylib", str(dylib)])


def _is_macho(path: Path) -> bool:
    try:
        return path.read_bytes()[:4] in MACHO_MAGICS
    except OSError:
        return False


def _strip_macho_files(root: Path) -> None:
    tool = shutil.which("strip")
    if tool is None:
        raise RuntimeError("strip is required to remove build paths from Mach-O files")
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or not _is_macho(path):
            continue
        subprocess.run(
            [tool, "-S", "-x", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _codesign_macho_files(root: Path) -> None:
    tool = shutil.which("codesign")
    if tool is None:
        raise RuntimeError("codesign is required to ad-hoc sign Mach-O runtime files")
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or not _is_macho(path):
            continue
        subprocess.check_call(
            [tool, "--force", "--sign", "-", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def build(venv: Path, output: Path) -> tuple[Path, str]:
    python = venv / "bin" / "python"
    info = _python_info(python)
    version = f"{info['major']}.{info['minor']}"
    with tempfile.TemporaryDirectory(prefix="papermuse-runtime-") as tmp_name:
        tmp = Path(tmp_name)
        main = tmp / "main"
        shutil.copytree(info["base_prefix"], main, symlinks=True)
        target_site = main / "lib" / f"python{version}" / "site-packages"
        if target_site.exists():
            shutil.rmtree(target_site)
        shutil.copytree(info["purelib"], target_site, symlinks=True)
        python_link = main / "bin" / "python"
        if not python_link.exists():
            python_link.symlink_to(f"python{version}")
        _remove_bytecode(main)
        _scrub_text_prefixes(main, [
            ROOT,
            venv.resolve(),
            Path(info["base_prefix"]).resolve(),
            Path(info["purelib"]).resolve(),
            Path.home(),
            "/private/var/folders",
            "/var/folders",
        ])
        _rewrite_libpython_id(main, version)
        _strip_macho_files(main)
        _replace_build_path_bytes(main)
        _codesign_macho_files(main)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        with tarfile.open(output, "w:gz") as tf:
            tf.add(main, arcname="main", filter=_tar_filter)
    return output, _sha256(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PaperMuse main runtime tar.gz")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "runtime" / "papermuse-main-runtime-macos-arm64.tar.gz",
    )
    args = parser.parse_args()

    output, digest = build(args.venv, args.output)
    print(f"{output}")
    print(f"sha256={digest}")
    print(f"export PAPER_MUSE_MAIN_RUNTIME_FILE={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
