import io
import json
import subprocess
import tarfile

import pytest

from tools import runtime_bootstrap


def _runtime_archive(tmp_path, version="Python 3.12.0"):
    archive = tmp_path / "runtime.tar.gz"
    script = f"#!/bin/sh\necho '{version}'\n".encode()
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("main/bin/python")
        info.mode = 0o755
        info.size = len(script)
        tf.addfile(info, io.BytesIO(script))
    return archive


def _broken_runtime_archive(tmp_path):
    archive = tmp_path / "broken-runtime.tar.gz"
    payload = b"not python"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("main/bin/not-python")
        info.mode = 0o755
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return archive


def _manifest(tmp_path, archive):
    data = {
        "runtime": {
            "platform": "macos-arm64",
            "version": "test-runtime-v1",
            "asset_url": archive.resolve().as_uri(),
            "archive_type": "tar.gz",
            "sha256": runtime_bootstrap.sha256_file(archive),
            "entrypoint": "main/bin/python",
            "compatible_app": "test",
            "compatible_server_schema": 1,
        }
    }
    path = tmp_path / "runtime-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_runtime_bootstrap_installs_and_skips_compatible_runtime(tmp_path):
    archive = _runtime_archive(tmp_path)
    manifest = _manifest(tmp_path, archive)
    runtime_dir = tmp_path / "runtime"

    assert runtime_bootstrap.bootstrap(manifest, runtime_dir) == "installed"
    python = runtime_dir / "main" / "bin" / "python"
    assert python.exists()
    assert runtime_bootstrap.runtime_is_healthy(
        runtime_dir, runtime_bootstrap.load_runtime_manifest(manifest)
    )

    archive.unlink()
    assert runtime_bootstrap.bootstrap(manifest, runtime_dir) == "already-installed"


def test_runtime_bootstrap_rejects_checksum_and_cleans_partial_install(tmp_path):
    archive = _runtime_archive(tmp_path)
    manifest = _manifest(tmp_path, archive)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["runtime"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(runtime_bootstrap.BootstrapError, match="checksum mismatch"):
        runtime_bootstrap.bootstrap(manifest, runtime_dir)

    assert not (runtime_dir / "main").exists()
    assert not list(runtime_dir.glob(".paper-muse-runtime-*"))


def test_runtime_bootstrap_cleans_after_download_failure(tmp_path):
    archive = tmp_path / "missing.tar.gz"
    manifest = _manifest(tmp_path, _runtime_archive(tmp_path))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["runtime"]["asset_url"] = archive.resolve().as_uri()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    runtime_dir = tmp_path / "runtime"

    with pytest.raises(FileNotFoundError):
        runtime_bootstrap.bootstrap(manifest, runtime_dir)

    assert not (runtime_dir / "main").exists()
    assert not list(runtime_dir.glob(".paper-muse-runtime-*"))


def test_runtime_bootstrap_bad_archive_keeps_existing_runtime(tmp_path):
    runtime_dir = tmp_path / "runtime"
    first = _runtime_archive(tmp_path, version="Python 3.12.1")
    first_manifest = _manifest(tmp_path, first)
    assert runtime_bootstrap.bootstrap(first_manifest, runtime_dir) == "installed"

    broken = _broken_runtime_archive(tmp_path)
    next_manifest = _manifest(tmp_path, broken)
    data = json.loads(next_manifest.read_text(encoding="utf-8"))
    data["runtime"]["version"] = "test-runtime-v2"
    next_manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(runtime_bootstrap.BootstrapError, match="missing executable"):
        runtime_bootstrap.bootstrap(next_manifest, runtime_dir)

    python = runtime_dir / "main" / "bin" / "python"
    result = subprocess.run([python, "--version"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "Python 3.12.1"
    assert not list(runtime_dir.glob(".paper-muse-runtime-*"))
