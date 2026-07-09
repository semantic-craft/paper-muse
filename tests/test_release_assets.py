import json

import pytest

from tools import release_assets


def test_release_assets_stage_manifest_and_scan(tmp_path):
    out = tmp_path / "server"

    release_assets.stage(out)

    assert (out / "muse_server.py").exists()
    assert (out / "webui" / "index.html").exists()
    assert not (out / "secrets.toml").exists()
    assert not (out / "results").exists()
    manifest = json.loads((out / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["entrypoint"] == "muse_server.py"
    assert manifest["runtime"]["platform"] == "macos-arm64"
    assert manifest["runtime"]["archive_type"] == "tar.gz"
    assert manifest["runtime"]["entrypoint"] == "main/bin/python"
    assert manifest["runtime"]["install_dir"] == "main"
    assert manifest["runtime"]["asset_url"]
    assert manifest["runtime"]["sha256"]
    assert manifest["sidecar_runtime"]["optional"] is True
    assert manifest["sidecar_runtime"]["entrypoint"] == "sidecar/bin/python"
    assert manifest["sidecar_runtime"]["install_dir"] == "sidecar"
    assert manifest["sidecar_runtime"]["requirements"] == "requirements-gptr.txt"
    assert "webui/index.html" in manifest["required_paths"]
    assert "tools/runtime_bootstrap.py" in manifest["required_paths"]
    assert any(f["path"] == "muse_server.py" and f["sha256"] for f in manifest["files"])

    release_assets.scan(out)


def test_release_assets_scan_rejects_private_state(tmp_path):
    out = tmp_path / "server"
    release_assets.stage(out)
    (out / "muse-smoke-20260709").mkdir()
    (out / "muse-smoke-20260709" / "artifact.json").write_text("local state", encoding="utf-8")

    with pytest.raises(RuntimeError, match="private release assets"):
        release_assets.scan(out)
