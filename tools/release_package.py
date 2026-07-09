#!/usr/bin/env python3
"""Build, sign, notarize, and verify a PaperMuse macOS release package."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PROJECT = ROOT / "app" / "PaperMuse.xcodeproj"
APP_PATH = ROOT / "app" / "build-release" / "Build" / "Products" / "Release" / "PaperMuse.app"
DEFAULT_IDENTITY = "Developer ID Application: Xianwei Zhang (LQAVR62TK2)"
MAIN_RUNTIME_URL = "PAPER_MUSE_MAIN_RUNTIME_URL"
MAIN_RUNTIME_SHA = "PAPER_MUSE_MAIN_RUNTIME_SHA256"
NOTARY_PROFILE = "PAPER_MUSE_NOTARY_PROFILE"


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


def _runtime_env_errors(env: dict[str, str]) -> list[str]:
    errors = []
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
    sign_app(args.identity)
    final_zip = notarize_and_package(args.notary_profile, args.output_dir)
    print(final_zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
