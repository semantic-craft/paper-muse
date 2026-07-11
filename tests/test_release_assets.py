import json

import pytest

from tools import release_assets


def test_release_assets_stage_manifest_and_scan(tmp_path):
    out = tmp_path / "server"

    release_assets.stage(out)

    assert (out / "muse_server.py").exists()
    assert (out / "paperqa_bridge.py").exists()
    assert (out / "evidence.py").exists()
    assert (out / "zotero_local.py").exists()
    assert (out / "prompt_assets.py").exists()
    assert (out / "requirements-paperqa.txt").exists()
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
    assert manifest["runtime"]["paperqa_requirements"] == "requirements-paperqa.txt"
    assert manifest["sidecar_runtime"]["optional"] is True
    assert manifest["sidecar_runtime"]["entrypoint"] == "sidecar/bin/python"
    assert manifest["sidecar_runtime"]["install_dir"] == "sidecar"
    assert manifest["sidecar_runtime"]["requirements"] == "requirements-gptr.txt"
    assert "webui/index.html" in manifest["required_paths"]
    assert "paperqa_bridge.py" in manifest["required_paths"]
    assert "evidence.py" in manifest["required_paths"]
    assert "zotero_local.py" in manifest["required_paths"]
    assert "prompt_assets.py" in manifest["required_paths"]
    assert "requirements-paperqa.txt" in manifest["required_paths"]
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


def test_release_assets_can_embed_main_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "main-runtime.tar.gz"
    runtime.write_bytes(b"runtime")
    out = tmp_path / "server"
    monkeypatch.setenv("PAPER_MUSE_MAIN_RUNTIME_FILE", str(runtime))

    release_assets.stage(out)

    manifest = json.loads((out / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime"]["asset_url"] == "runtime/main-runtime.tar.gz"
    assert manifest["runtime"]["sha256"] == release_assets._sha256(runtime)
    assert (out / "runtime" / "main-runtime.tar.gz").read_bytes() == b"runtime"


def test_adversary_ui_renders_rebuttal_and_meta_review():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "author_rebuttal" in html
    assert "meta_review" in html
    assert "作者答辩" in html and "仲裁" in html


def test_adversary_ui_consumes_evidence_ref_and_provider_degradation():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "e.source || {}" in html
    assert "e.relation" in html
    assert "sidecar_degradation" in html
    assert "EvidenceRef" in html


def test_scan_ui_consumes_unified_evidence_refs():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "c.evidence" in html
    assert "verification.status" in html
    assert "证据" in html


def test_scan_ui_exposes_structured_cnki_status_and_novelty_reason():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "c.zh_status" in html
    assert "c.novelty_reason" in html
    assert "新颖性判定" in html


def test_scan_ui_uses_zotero_locator_and_shows_identity_status():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "c.own_identity_status" in html
    assert 'locator.kind === "zotero-select"' in html
    assert "Zotero 身份" in html


def test_card_ui_exposes_recoverable_self_library_evidence_flow():
    html = (release_assets.ROOT / "webui" / "index.html").read_text(encoding="utf-8")

    assert "查询自有 PDF 库" in html
    assert 'fetch("/evidence/ask"' in html
    assert 'setSelfLibraryState(card, epoch, "loading"' in html
    assert 'setSelfLibraryState(card, epoch, "success"' in html
    assert 'setSelfLibraryState(card, epoch, "degraded"' in html
    assert 'setSelfLibraryState(card, epoch, "error"' in html
    assert "const SELF_LIBRARY_STATE = new Map()" in html
    assert "applySelfLibraryState(card)" in html
    assert "SELF_LIBRARY_STATE.clear()" in html
    assert "EvidenceRef" in html


def test_release_launch_does_not_write_bytecode_into_signed_bundle():
    source = (release_assets.ROOT / "app" / "Sources" / "MuseServer.swift").read_text(
        encoding="utf-8"
    )

    assert '"PYTHONDONTWRITEBYTECODE": "1"' in source
