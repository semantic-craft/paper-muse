import json
import tarfile

import pytest

from tools import release_package


def _mock_preflight_dependencies(monkeypatch, tmp_path):
    project = tmp_path / "PaperMuse.xcodeproj"
    project.mkdir()
    monkeypatch.setattr(release_package, "APP_PROJECT", project)
    monkeypatch.setattr(release_package.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(release_package, "_identity_errors", lambda _identity: [])
    monkeypatch.setattr(release_package, "_notary_errors", lambda _profile: [])


def test_release_preflight_requires_main_runtime_env(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)

    errors = release_package.preflight("Developer ID Application: Example", "profile", {})

    assert any("PAPER_MUSE_MAIN_RUNTIME_URL" in e for e in errors)
    assert any("PAPER_MUSE_MAIN_RUNTIME_SHA256" in e for e in errors)


def test_release_preflight_accepts_required_inputs(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)
    env = {
        "PAPER_MUSE_MAIN_RUNTIME_URL": "https://downloads.example.test/papermuse-runtime.tar.gz",
        "PAPER_MUSE_MAIN_RUNTIME_SHA256": "a" * 64,
    }

    assert release_package.preflight("Developer ID Application: Example", "profile", env) == []


def test_release_preflight_accepts_embedded_runtime_file(monkeypatch, tmp_path):
    _mock_preflight_dependencies(monkeypatch, tmp_path)
    runtime = tmp_path / "main-runtime.tar.gz"
    runtime.write_bytes(b"runtime")

    assert release_package.preflight(
        "Developer ID Application: Example",
        "profile",
        {"PAPER_MUSE_MAIN_RUNTIME_FILE": str(runtime)},
    ) == []


def test_sign_embedded_runtime_archive_updates_manifest(monkeypatch, tmp_path):
    app = tmp_path / "PaperMuse.app"
    server = app / "Contents" / "Resources" / "server"
    runtime_dir = server / "runtime"
    payload_dir = tmp_path / "payload" / "main" / "bin"
    runtime_dir.mkdir(parents=True)
    payload_dir.mkdir(parents=True)
    (payload_dir / "python3.12").write_bytes(b"\xcf\xfa\xed\xfepayload")
    archive = runtime_dir / "main-runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(tmp_path / "payload" / "main", arcname="main")
    (server / "runtime-manifest.json").write_text(
        json.dumps({
            "runtime": {
                "asset_url": "runtime/main-runtime.tar.gz",
                "sha256": "old",
            },
            "files": [{
                "path": "runtime/main-runtime.tar.gz",
                "bytes": 1,
                "sha256": "old",
            }],
            "required_paths": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(release_package, "APP_PATH", app)
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return ""

    monkeypatch.setattr(release_package, "run", fake_run)

    signed = release_package.sign_embedded_runtime("Developer ID Application: Example")

    assert signed == 1
    assert any(cmd[:2] == ["codesign", "--force"] for cmd in commands)
    assert any(cmd[-3:] == ["tools/release_assets.py", "scan", str(app)] for cmd in commands)
    manifest = json.loads((server / "runtime-manifest.json").read_text(encoding="utf-8"))
    digest = release_package._sha256(archive)
    assert manifest["runtime"]["sha256"] == digest
    assert manifest["files"][0]["bytes"] == archive.stat().st_size
    assert manifest["files"][0]["sha256"] == digest


def test_safe_runtime_extraction_rejects_escaping_link(tmp_path):
    archive = tmp_path / "unsafe-runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        link = tarfile.TarInfo("main/bin/python")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../outside"
        tf.addfile(link)

    with pytest.raises(release_package.ReleaseError, match="unsafe runtime archive link"):
        release_package._extract_tar_safe(archive, tmp_path / "runtime")
