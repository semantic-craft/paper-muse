#!/usr/bin/env python3
"""Build, sign, notarize, and verify a PaperMuse macOS release package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
APP_PROJECT = ROOT / "app" / "PaperMuse.xcodeproj"
APP_PATH = ROOT / "app" / "build-release" / "Build" / "Products" / "Release" / "PaperMuse.app"
DEFAULT_IDENTITY = "Developer ID Application: Xianwei Zhang (LQAVR62TK2)"
MAIN_RUNTIME_URL = "PAPER_MUSE_MAIN_RUNTIME_URL"
MAIN_RUNTIME_SHA = "PAPER_MUSE_MAIN_RUNTIME_SHA256"
MAIN_RUNTIME_FILE = "PAPER_MUSE_MAIN_RUNTIME_FILE"
NOTARY_PROFILE = "PAPER_MUSE_NOTARY_PROFILE"
EMBEDDED_MAIN_RUNTIME = Path("runtime/main-runtime.tar.gz")
MANIFEST = "runtime-manifest.json"
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}


class ReleaseError(RuntimeError):
    pass


def run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int | None = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ReleaseError(f"{' '.join(cmd)} failed\n{detail}")
    return proc.stdout


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def _extract_tar_safe(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            member_path = PurePosixPath(member.name)
            if (
                not member.name
                or member_path.is_absolute()
                or ".." in member_path.parts
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
            ):
                raise ReleaseError(f"unsafe runtime archive member: {member.name}")
            if member.issym() or member.islnk():
                link_path = PurePosixPath(member.linkname)
                if (
                    not member.linkname
                    or link_path.is_absolute()
                    or ".." in link_path.parts
                ):
                    raise ReleaseError(f"unsafe runtime archive link: {member.name}")
        try:
            tf.extractall(dest, filter="fully_trusted")
        except TypeError:
            tf.extractall(dest)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _sign_macho_files(root: Path, identity: str) -> int:
    signed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not _is_macho(path):
            continue
        run([
            "codesign",
            "--force",
            "--options", "runtime",
            "--timestamp",
            "--sign", identity,
            str(path),
        ])
        signed += 1
    return signed


def _refresh_embedded_runtime_manifest(server_root: Path, archive: Path) -> None:
    manifest_path = server_root / MANIFEST
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = archive.relative_to(server_root).as_posix()
    digest = _sha256(archive)
    data["runtime"]["asset_url"] = rel
    data["runtime"]["sha256"] = digest
    for item in data.get("files", []):
        if item.get("path") == rel:
            item["bytes"] = archive.stat().st_size
            item["sha256"] = digest
            break
    else:
        data.setdefault("files", []).append({
            "path": rel,
            "bytes": archive.stat().st_size,
            "sha256": digest,
        })
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sign_embedded_runtime(identity: str) -> int:
    """Developer-ID sign Mach-O files inside the embedded runtime archive."""
    server_root = APP_PATH / "Contents" / "Resources" / "server"
    archive = server_root / EMBEDDED_MAIN_RUNTIME
    if not archive.exists():
        return 0

    with tempfile.TemporaryDirectory(prefix="papermuse-runtime-sign-") as tmp_name:
        tmp = Path(tmp_name)
        extracted = tmp / "runtime"
        extracted.mkdir()
        _extract_tar_safe(archive, extracted)
        signed = _sign_macho_files(extracted, identity)
        rebuilt = tmp / "main-runtime.tar.gz"
        with tarfile.open(rebuilt, "w:gz") as tf:
            for child in sorted(extracted.iterdir()):
                tf.add(child, arcname=child.name, filter=_tar_filter)
        shutil.copy2(rebuilt, archive)

    _refresh_embedded_runtime_manifest(server_root, archive)
    run([sys.executable, "tools/release_assets.py", "scan", str(APP_PATH)])
    return signed


def _runtime_env_errors(env: dict[str, str]) -> list[str]:
    errors = []
    runtime_file = env.get(MAIN_RUNTIME_FILE, "").strip()
    if runtime_file:
        path = Path(runtime_file).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        return [] if path.exists() else [f"{MAIN_RUNTIME_FILE} does not exist: {path}"]
    url = env.get(MAIN_RUNTIME_URL, "").strip()
    sha = env.get(MAIN_RUNTIME_SHA, "").strip()
    if not url or url == MAIN_RUNTIME_URL:
        errors.append(f"{MAIN_RUNTIME_URL} must point to a distributable main runtime archive")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        errors.append(f"{MAIN_RUNTIME_SHA} must be a 64-character sha256 hex digest")
    return errors


def _identity_errors(identity: str) -> list[str]:
    try:
        out = run(["security", "find-identity", "-v", "-p", "codesigning"])
    except ReleaseError as e:
        return [str(e)]
    return [] if identity in out else [f"codesigning identity not found: {identity}"]


def _notary_errors(profile: str | None) -> list[str]:
    if not profile:
        return [f"{NOTARY_PROFILE} or --notary-profile is required for notarization"]
    try:
        run(["xcrun", "notarytool", "history", "--keychain-profile", profile], timeout=30)
    except ReleaseError as e:
        return [f"notary profile failed validation: {profile}\n{e}"]
    return []


def preflight(identity: str, notary_profile: str | None, env: dict[str, str]) -> list[str]:
    errors = []
    for tool in ("xcodebuild", "xcrun", "codesign", "ditto", "spctl"):
        if shutil.which(tool) is None:
            errors.append(f"required tool not found: {tool}")
    if not APP_PROJECT.exists():
        errors.append("app/PaperMuse.xcodeproj is missing; run xcodegen generate --spec app/project.yml --project app")
    errors.extend(_runtime_env_errors(env))
    errors.extend(_identity_errors(identity))
    errors.extend(_notary_errors(notary_profile))
    return errors


def build_app(env: dict[str, str]) -> None:
    if (ROOT / "app" / "build-release").exists():
        shutil.rmtree(ROOT / "app" / "build-release")
    run([
        "xcodebuild",
        "-project", str(APP_PROJECT),
        "-scheme", "PaperMuse",
        "-configuration", "Release",
        "-derivedDataPath", "app/build-release",
        "CODE_SIGNING_ALLOWED=NO",
        "build",
    ], env=env)
    run([sys.executable, "tools/release_assets.py", "scan", str(APP_PATH)])


def sign_app(identity: str) -> None:
    run([
        "codesign",
        "--force",
        "--deep",
        "--options", "runtime",
        "--timestamp",
        "--sign", identity,
        str(APP_PATH),
    ])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP_PATH)])


def notarize_and_package(notary_profile: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    submit_zip = output_dir / "PaperMuse-notary-submit.zip"
    final_zip = output_dir / "PaperMuse-macos-arm64.zip"
    for path in (submit_zip, final_zip):
        path.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--keepParent", str(APP_PATH), str(submit_zip)])
    run(["xcrun", "notarytool", "submit", str(submit_zip), "--keychain-profile", notary_profile, "--wait"])
    run(["xcrun", "stapler", "staple", str(APP_PATH)])
    run(["spctl", "--assess", "--type", "execute", "--verbose=4", str(APP_PATH)])
    run(["ditto", "-c", "-k", "--keepParent", str(APP_PATH), str(final_zip)])
    submit_zip.unlink(missing_ok=True)
    return final_zip


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperMuse macOS release packager")
    parser.add_argument("command", choices=("preflight", "package"))
    parser.add_argument("--identity", default=os.environ.get("PAPER_MUSE_CODESIGN_IDENTITY", DEFAULT_IDENTITY))
    parser.add_argument("--notary-profile", default=os.environ.get(NOTARY_PROFILE))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "release")
    args = parser.parse_args()

    env = os.environ.copy()
    errors = preflight(args.identity, args.notary_profile, env)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.command == "preflight":
        print("release preflight ok")
        return 0

    build_app(env)
    signed_runtime_count = sign_embedded_runtime(args.identity)
    if signed_runtime_count:
        print(f"signed embedded runtime Mach-O files: {signed_runtime_count}")
    sign_app(args.identity)
    final_zip = notarize_and_package(args.notary_profile, args.output_dir)
    print(final_zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
