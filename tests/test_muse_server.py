"""muse_server 薄层测试：#4 的 has_profile 透出（无画像「发现力打折」的服务端判据）。
scan_bg 的外部依赖（LLM/检索/落盘）全部 monkeypatch 掉，只验画像参照系有无的置位与透传。"""

import json
import os
from pathlib import Path

import pytest

import blindspot
import muse_server
from muse_server import ScanReq


class _Turn:
    def __init__(self, role, utterance):
        self.role = role
        self.utterance = utterance


def _fake_python(path, version="Python 3.12.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
    path.chmod(0o755)


def _stub_scan_externals(monkeypatch, captured):
    """把 scan_bg 里会联网/落盘的接线全部换成惰性桩，只保留 has_profile 判据路径。"""
    monkeypatch.setattr(muse_server, "load_api_key", lambda **k: None)
    monkeypatch.setattr(blindspot, "real_providers", lambda: {"x": (lambda p: "")})
    monkeypatch.setattr(blindspot, "pick_decompose_llm", lambda provs: (lambda p: ""))
    monkeypatch.setattr(blindspot, "real_en_search", lambda: (lambda q: []))
    monkeypatch.setattr(blindspot, "real_cnki_search", lambda: (lambda q: []))
    monkeypatch.setattr(blindspot, "real_own_search", lambda: (lambda q: []))

    def fake_run_scan(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(blindspot, "run_scan", fake_run_scan)


def _clear_runtime_env(monkeypatch):
    monkeypatch.setattr(muse_server, "RELEASE_MODE", False)
    for name in (
        "PAPER_MUSE_SERVER_ROOT",
        "PAPER_MUSE_APP_DATA_DIR",
        "PAPER_MUSE_CONFIG_DIR",
        "PAPER_MUSE_CACHE_DIR",
        "PAPER_MUSE_RUNTIME_DIR",
        "PAPER_MUSE_LOGS_DIR",
        "PAPER_MUSE_OUTPUT_DIR",
        "PAPER_MUSE_SECRETS_FILE",
        "PAPER_MUSE_SIDECAR_PYTHON",
        "PAPER_MUSE_SIDECAR_SCRIPT",
        "PAPER_MUSE_PAPERQA_PYTHON",
        "PAPER_MUSE_PAPERQA_LLM",
        "PAPER_MUSE_PAPERQA_SUMMARY_LLM",
        "PAPER_MUSE_PAPERQA_AGENT_LLM",
        "PAPER_MUSE_PAPERQA_EMBEDDING",
        "PAPER_MUSE_PDF_DIR",
        "PAPER_MUSE_ZOTERO_PDF_DIR",
        "PQA_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def _clear_provider_env(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_BASE",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
        "PERPLEXITY_API_KEY",
        "JINA_API_KEY",
        "ENCODER_API_TYPE",
        "ENCODER_API_KEY",
        "ENCODER_API_BASE",
        "ENCODER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_release_runtime_requires_explicit_server_root_and_dirs(monkeypatch):
    _clear_runtime_env(monkeypatch)

    with pytest.raises(RuntimeError, match="server-root"):
        muse_server.configure_runtime_paths(release_mode=True)

    monkeypatch.setenv("PAPER_MUSE_SERVER_ROOT", str(muse_server.ROOT))
    with pytest.raises(RuntimeError, match="PAPER_MUSE_APP_DATA_DIR"):
        muse_server.configure_runtime_paths(release_mode=True)


def test_release_runtime_paths_drive_secrets_and_results(monkeypatch, tmp_path):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setattr(muse_server, "SERVER_ROOT", muse_server.ROOT)
    monkeypatch.chdir(muse_server.ROOT)
    server_root = tmp_path / "server"
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    runtime_dir = tmp_path / "runtime"
    logs_dir = tmp_path / "logs"
    server_root.mkdir()
    (server_root / "secrets.toml.example").write_text("DEEPSEEK_API_KEY=\"sk-YOUR_DEEPSEEK_KEY\"\n",
                                                       encoding="utf-8")

    muse_server.configure_runtime_paths(
        server_root=server_root,
        app_data_dir=data_dir,
        config_dir=config_dir,
        cache_dir=cache_dir,
        runtime_dir=runtime_dir,
        logs_dir=logs_dir,
        release_mode=True,
    )

    assert os.environ["PAPER_MUSE_SERVER_ROOT"] == str(server_root.resolve())
    assert os.environ["PAPER_MUSE_APP_DATA_DIR"] == str(data_dir.resolve())
    assert os.environ["PAPER_MUSE_CONFIG_DIR"] == str(config_dir.resolve())
    assert os.environ["PAPER_MUSE_CACHE_DIR"] == str(cache_dir.resolve())
    assert os.environ["PAPER_MUSE_RUNTIME_DIR"] == str(runtime_dir.resolve())
    assert os.environ["PAPER_MUSE_LOGS_DIR"] == str(logs_dir.resolve())
    assert muse_server._secrets_path() == config_dir.resolve() / "secrets.toml"
    assert muse_server._results_base() == data_dir.resolve() / "results"
    assert Path.cwd() == server_root.resolve()
    assert data_dir.is_dir() and config_dir.is_dir() and cache_dir.is_dir()
    assert runtime_dir.is_dir() and logs_dir.is_dir()
    assert (config_dir / "secrets.toml.example").exists()


def test_setup_status_reports_missing_required_keys(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    _clear_provider_env(monkeypatch)
    secrets = tmp_path / "empty-secrets.toml"
    secrets.write_text("", encoding="utf-8")
    monkeypatch.setenv("PAPER_MUSE_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("PAPER_MUSE_CONFIG_DIR", str(tmp_path / "config"))
    client = TestClient(muse_server.app)

    body = client.get("/setup/status").json()

    assert body["setup_required"] is True
    assert "DEEPSEEK_API_KEY" in body["missing_required_keys"]
    assert body["paths"]["secrets_file"] == str(secrets.resolve())
    assert "首次设置未完成" in body["message"]


def test_topic_suggest_uses_newest_markdown_heading(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    older = tmp_path / "old.md"
    newer = tmp_path / "nested" / "new.md"
    newer.parent.mkdir()
    older.write_text("# 旧主题\n", encoding="utf-8")
    newer.write_text("\n# 新主题\n", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setenv("PAPER_MUSE_OUTPUT_DIR", str(tmp_path))
    client = TestClient(muse_server.app)

    body = client.get("/topic/suggest").json()

    assert body["topic"] == "新主题"
    assert body["path"] == str(newer)


def test_topic_suggest_is_empty_without_output_dir(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("PAPER_MUSE_OUTPUT_DIR", raising=False)
    client = TestClient(muse_server.app)

    assert client.get("/topic/suggest").json() == {"topic": "", "path": None}


def test_release_health_reports_runtime_missing(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    _clear_provider_env(monkeypatch)
    secrets = tmp_path / "empty-secrets.toml"
    secrets.write_text("", encoding="utf-8")
    monkeypatch.setenv("PAPER_MUSE_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(tmp_path / "runtime"))
    client = TestClient(muse_server.app)

    body = client.get("/release/health").json()

    assert body["state"] == "runtime_missing"
    assert body["blocking"] is True
    assert body["components"]["runtime"]["state"] == "runtime_missing"
    assert body["components"]["sidecar"]["state"] == "missing"


def test_release_health_reports_missing_required_key(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    _clear_provider_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    _fake_python(runtime_dir / "main" / "bin" / "python")
    secrets = tmp_path / "empty-secrets.toml"
    secrets.write_text("", encoding="utf-8")
    monkeypatch.setenv("PAPER_MUSE_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    client = TestClient(muse_server.app)

    body = client.get("/release/health").json()

    assert body["state"] == "missing_required_key"
    assert "DEEPSEEK_API_KEY" in body["components"]["setup"]["missing_required_keys"]


def test_release_health_reports_optional_degraded_not_blocking(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    _fake_python(runtime_dir / "main" / "bin" / "python")
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-realish")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-realish")
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setattr(muse_server.shutil, "which", lambda _command: None)
    client = TestClient(muse_server.app)

    body = client.get("/release/health").json()

    assert body["state"] == "ready_degraded"
    assert body["blocking"] is False
    assert body["components"]["optional_capabilities"]["cnki"]["state"] == "unavailable"
    assert body["components"]["sidecar"]["state"] == "missing"


def test_release_health_warns_on_developer_path_in_release_mode(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    _fake_python(runtime_dir / "main" / "bin" / "python")
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-realish")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-realish")
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setattr(muse_server, "RELEASE_MODE", True)
    monkeypatch.setattr(muse_server.shutil, "which", lambda _command: "/usr/bin/true")
    client = TestClient(muse_server.app)

    body = client.get("/release/health").json()

    assert body["state"] == "developer_path"
    assert body["blocking"] is True
    assert body["components"]["developer_paths"]["warnings"]


def test_release_health_allows_staged_server_root_in_release_mode(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    server_root = tmp_path / "server"
    server_root.mkdir()
    (server_root / "muse_server.py").write_text("# staged server\n", encoding="utf-8")
    (server_root / "secrets.toml.example").write_text("", encoding="utf-8")
    _fake_python(runtime_dir / "main" / "bin" / "python")
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-realish")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-realish")
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setattr(muse_server, "ROOT", server_root)
    monkeypatch.setattr(muse_server, "SERVER_ROOT", server_root)
    monkeypatch.setattr(muse_server, "RELEASE_MODE", True)
    monkeypatch.setattr(muse_server.shutil, "which", lambda _command: "/usr/bin/true")
    client = TestClient(muse_server.app)

    body = client.get("/release/health").json()

    assert body["state"] == "ready_degraded"
    assert body["blocking"] is False
    assert body["components"]["developer_paths"] == {"state": "ok", "warnings": []}


def test_sidecar_status_endpoint_reports_missing_and_failed(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    client = TestClient(muse_server.app)

    missing = client.get("/sidecar/status").json()
    assert missing["state"] == "missing"

    runtime_dir.mkdir()
    adversary.sidecar_failed_path(runtime_dir).write_text(
        json.dumps({"error": "checksum mismatch"}), encoding="utf-8"
    )
    failed = client.get("/sidecar/status").json()
    assert failed["state"] == "failed" and "checksum" in failed["message"]

    drafts = client.get("/adversary/drafts", params={"output_dir": str(tmp_path)}).json()
    assert drafts["dir"] == str(tmp_path)


def test_sidecar_bootstrap_endpoint_reports_installing(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("PAPER_MUSE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setattr(muse_server, "_sidecar_bootstrap_bg", lambda _runtime_dir: None)
    client = TestClient(muse_server.app)

    body = client.post("/sidecar/bootstrap").json()

    assert body["ok"] is True
    assert body["sidecar"]["state"] == "installing"


def test_evidence_status_endpoint_reports_optional_paperqa(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        muse_server.paperqa_bridge,
        "paperqa_status",
        lambda pdf_dir=None: {"state": "pdf_dir_missing", "optional": True, "pdf_dir": pdf_dir},
    )
    client = TestClient(muse_server.app)

    body = client.get("/evidence/status", params={"pdf_dir": str(tmp_path)}).json()

    assert body["state"] == "pdf_dir_missing"
    assert body["pdf_dir"] == str(tmp_path)


def test_evidence_ask_uses_current_scan_output_dir(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    captured = {}
    monkeypatch.setattr(muse_server, "load_api_key", lambda **_kw: None)

    def fake_ask(question, **kwargs):
        captured["question"] = question
        captured.update(kwargs)
        return {"ok": True, "question": question, "status": {"state": "ready"}}

    monkeypatch.setattr(muse_server.paperqa_bridge, "ask_self_library", fake_ask)
    with muse_server.SCAN_LOCK:
        muse_server.SCAN.update(output_dir=str(tmp_path / "paper"))
    client = TestClient(muse_server.app)

    body = client.post(
        "/evidence/ask",
        json={
            "question": "自有库里有没有反例？",
            "card_id": 7,
            "card_name": "形式可逆与实质不可逆",
            "pdf_dir": str(tmp_path / "pdfs"),
            "timeout": 45,
        },
    ).json()

    assert body["ok"] is True
    assert captured["question"] == "自有库里有没有反例？"
    assert captured["pdf_dir"] == str(tmp_path / "pdfs")
    assert captured["output_dir"] == str(tmp_path / "paper")
    assert captured["timeout"] == 45
    assert captured["target"] == {
        "kind": "card",
        "id": "7",
        "name": "形式可逆与实质不可逆",
    }


def test_evidence_ref_can_be_read_by_id_without_parsing_markdown(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    expected = {"id": "evr_example", "source": {"title": "Local Paper"}}
    monkeypatch.setattr(
        muse_server.paperqa_bridge,
        "read_evidence",
        lambda output_dir, evidence_id: expected
        if output_dir == str(tmp_path) and evidence_id == "evr_example"
        else None,
    )
    client = TestClient(muse_server.app)

    body = client.get(
        "/evidence/evr_example", params={"output_dir": str(tmp_path)}
    ).json()

    assert body == expected
    missing = client.get(
        "/evidence/evr_missing", params={"output_dir": str(tmp_path)}
    )
    assert missing.status_code == 404


def test_session_returns_setup_required_when_keys_missing(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_runtime_env(monkeypatch)
    _clear_provider_env(monkeypatch)
    secrets = tmp_path / "empty-secrets.toml"
    secrets.write_text("", encoding="utf-8")
    monkeypatch.setenv("PAPER_MUSE_SECRETS_FILE", str(secrets))
    client = TestClient(muse_server.app)

    resp = client.post("/session", json={"topic": "平台数据权力"})

    assert resp.status_code == 428
    assert "首次设置未完成" in resp.json()["detail"]


def test_session_accepts_openai_model_without_deepseek(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-realish")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-realish")
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setattr(muse_server, "warm_start_bg", lambda _req: None)
    muse_server.SESSION.update(phase="idle", runner=None, error=None)
    client = TestClient(muse_server.app)

    resp = client.post(
        "/session",
        json={"topic": "平台数据权力：机制", "card_name": "平台数据权力", "model": "openai"},
    )

    assert resp.status_code == 200
    assert resp.json()["model"] == "openai"
    assert muse_server.SESSION["model"] == "openai"
    assert muse_server.SESSION["card_name"] == "平台数据权力"


def test_session_blocked_and_unclobbered_while_runner_locked(monkeypatch, tmp_path):
    """回归：/session 的相位守卫 + 状态置位须与 /step、/report 同锁。RUNNER_LOCK 被在飞的
    /step/report 持有时，新 /session 应 409 且绝不改写 SESSION——否则会把在飞会话的 runner
    置 None、相位被 /step 的 finally 覆写回 ready → 后续 /step 拿 None runner 崩 500。"""
    from fastapi.testclient import TestClient

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-realish")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-realish")
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setattr(muse_server, "warm_start_bg", lambda _req: None)
    sentinel = object()
    muse_server.SESSION.update(phase="ready", runner=sentinel, topic="旧主题", model="openai")
    client = TestClient(muse_server.app)

    assert muse_server.RUNNER_LOCK.acquire(blocking=False)   # 模拟在飞的 /step 持锁
    try:
        resp = client.post("/session", json={"topic": "新主题", "model": "openai"})
    finally:
        muse_server.RUNNER_LOCK.release()

    assert resp.status_code == 409
    # SESSION 未被改写：在飞会话的 runner/相位/主题原封不动（修复前会被置 warming/None/新主题）
    assert muse_server.SESSION["phase"] == "ready"
    assert muse_server.SESSION["runner"] is sentinel
    assert muse_server.SESSION["topic"] == "旧主题"
    muse_server.SESSION.update(phase="idle", runner=None, topic="")   # 清理，勿污染后续测试


def test_build_rm_defaults_to_bounded_snippets_fulltext_opt_in(monkeypatch):
    """回归：性能 PRD P0「默认有界摘要、全文仅按需」。build_rm 的基础 Tavily 默认
    include_raw_content=False（不拉原始全文）；全文是显式增强路径——req.fulltext=True
    才叠加 JinaFullTextRM。修复前基础 Tavily 无条件写死 True，每轮圆桌都付全文成本。"""
    from knowledge_storm.rm import JinaFullTextRM

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("JINA_API_KEY", "jina-test")

    base = muse_server.build_rm(muse_server.SessionReq(topic="平台责任"), k=5)
    assert base.include_raw_content is False          # 默认有界摘要
    assert base.include_raw_content is not True

    wrapped = muse_server.build_rm(
        muse_server.SessionReq(topic="平台责任", fulltext=True), k=5)
    assert isinstance(wrapped, JinaFullTextRM)         # 全文=显式 opt-in
    assert wrapped.base_rm.include_raw_content is False  # 底层仍有界，Jina 才是全文那层


def test_scan_bg_sets_has_profile_true_and_feeds_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    blindspot.save_researcher_profile({"field": "中文法学·数据法", "stance": "", "familiar": ""})
    captured = {}
    _stub_scan_externals(monkeypatch, captured)
    muse_server.SCAN.update(output_dir=str(tmp_path / "out"), has_profile=False)

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    assert muse_server.SCAN["has_profile"] is True
    assert muse_server.SCAN["phase"] == "done"
    # 画像作为参照系真喂进了扫描
    assert "中文法学·数据法" in captured["profile"]


def test_scan_bg_sets_has_profile_false_without_researcher(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # 空 XDG → 无 researcher.md
    captured = {}
    _stub_scan_externals(monkeypatch, captured)
    muse_server.SCAN.update(output_dir=str(tmp_path / "out"), has_profile=True)  # 故意反向，验被纠正

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle="卡在跨学科切入"))

    assert muse_server.SCAN["has_profile"] is False   # 无画像 → 前端出「发现力打折」
    assert captured["profile"] == ""                  # 无参照系
    assert captured["puzzle"] == "卡在跨学科切入"      # 困惑仍单独喂扫描


def test_scan_bg_replaces_streamed_cards_with_final_cards(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(muse_server, "load_api_key", lambda **k: None)
    monkeypatch.setattr(blindspot, "real_providers", lambda: {"x": (lambda p: "")})
    monkeypatch.setattr(blindspot, "pick_decompose_llm", lambda provs: (lambda p: ""))
    monkeypatch.setattr(blindspot, "real_en_search", lambda: (lambda q: []))
    monkeypatch.setattr(blindspot, "real_cnki_search", lambda: (lambda q: []))
    monkeypatch.setattr(blindspot, "real_own_search", lambda: (lambda q: []))
    muse_server.SCAN.update(output_dir=str(tmp_path / "out"), cards=[], phase="idle", version=0)

    def fake_run_scan(**kw):
        kw["on_card"]({"name": "streamed"})
        return [{"name": "final", "source_models": ["x"], "outlier": False}]

    monkeypatch.setattr(blindspot, "run_scan", fake_run_scan)

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    assert muse_server.SCAN["phase"] == "done"
    assert muse_server.SCAN["cards"] == [{"name": "final", "source_models": ["x"], "outlier": False}]


def test_scan_bg_activates_knowledge_storm_embedding_proximity(monkeypatch, tmp_path):
    import knowledge_storm.encoder as encoder_module

    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    monkeypatch.setenv("ENCODER_API_KEY", "encoder-test-key")
    _stub_scan_externals(monkeypatch, {})
    # 本测隔离 Proximity 语义去重，卡型配额视为齐备（#80 由独立测覆盖）。
    monkeypatch.setattr(
        blindspot, "card_type_quota_status",
        lambda cards: {"state": "ready", "missing_card_types": [], "message": ""})

    class FakeEncoder:
        def encode(self, texts):
            assert "反馈调节" in texts[0]
            return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr(encoder_module, "Encoder", FakeEncoder)

    def fake_run_scan(**kwargs):
        cards = [
            {"type": "学科视角", "name": "控制论视角", "mechanism": "反馈调节",
             "source_models": ["deepseek"]},
            {"type": "理论框架", "name": "制度经济学", "mechanism": "交易成本",
             "source_models": ["gemini"]},
        ]
        return blindspot.finalize_card_quality(cards, embedding_fn=kwargs["embedding_fn"])

    monkeypatch.setattr(blindspot, "run_scan", fake_run_scan)
    muse_server.SCAN.update(
        output_dir=str(tmp_path / "out"), cards=[], phase="idle", version=0,
    )

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    assert muse_server.SCAN["phase"] == "done"
    assert all(card["proximity_basis"] == "embedding" for card in muse_server.SCAN["cards"])
    assert muse_server.SCAN["degradation"] == []


def test_scan_bg_degrades_to_lexical_proximity_without_encoder_key(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import run_manifest

    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ENCODER_API_TYPE", "openai")
    _stub_scan_externals(monkeypatch, {})
    # 本测隔离 Proximity 语义去重，卡型配额视为齐备（#80 由独立测覆盖）。
    monkeypatch.setattr(
        blindspot, "card_type_quota_status",
        lambda cards: {"state": "ready", "missing_card_types": [], "message": ""})

    def fake_run_scan(**kwargs):
        cards = [
            {"type": "学科视角", "name": "控制论视角", "mechanism": "反馈调节",
             "source_models": ["deepseek"]},
            {"type": "理论框架", "name": "制度经济学", "mechanism": "交易成本",
             "source_models": ["gemini"]},
        ]
        return blindspot.finalize_card_quality(cards, embedding_fn=kwargs["embedding_fn"])

    monkeypatch.setattr(blindspot, "run_scan", fake_run_scan)
    output_dir = tmp_path / "out"
    muse_server.SCAN.update(
        output_dir=str(output_dir), cards=[], phase="idle", version=0, degradation=[],
    )

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    marker = "Proximity 语义去重降级：缺少 encoder key，使用 lexical-fallback"
    assert muse_server.SCAN["phase"] == "done"
    assert all(card["proximity_basis"] == "lexical-fallback" for card in muse_server.SCAN["cards"])
    assert muse_server.SCAN["degradation"] == [marker]
    assert TestClient(muse_server.app).get("/scan/status").json()["degradation"] == [marker]
    assert run_manifest.read(output_dir)[-1]["degradation"] == [marker]


def test_scan_bg_exposes_card_type_degradation_and_records_manifest(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import run_manifest

    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    captured = {}
    _stub_scan_externals(monkeypatch, captured)
    # 本测隔离三类卡配额，Proximity 语义去重视为正常（#78 由独立测覆盖）。
    monkeypatch.setattr(muse_server, "_scan_embedding", lambda: (None, []))
    cards = [
        {"type": "学科视角", "name": "学科卡", "evidence": []},
        {"type": "理论框架", "name": "理论卡", "evidence": []},
    ]
    monkeypatch.setattr(blindspot, "run_scan", lambda **kwargs: cards)
    output_dir = tmp_path / "out"
    muse_server.SCAN.update(
        output_dir=str(output_dir), cards=[], phase="idle", version=0,
        card_type_status=None,
    )

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    expected = {
        "state": "degraded",
        "missing_card_types": ["研究方法"],
        "message": "卡型配额降级：缺少研究方法",
    }
    assert muse_server.SCAN["card_type_status"] == expected
    body = TestClient(muse_server.app).get("/scan/status").json()
    assert body["card_type_status"] == expected
    assert run_manifest.read(output_dir)[-1]["degradation"] == [expected["message"]]


def test_scan_bg_records_no_card_type_degradation_when_quota_is_complete(monkeypatch, tmp_path):
    import run_manifest

    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _stub_scan_externals(monkeypatch, {})
    # 本测隔离三类卡配额，Proximity 语义去重视为正常（#78 由独立测覆盖）。
    monkeypatch.setattr(muse_server, "_scan_embedding", lambda: (None, []))
    cards = [
        {"type": card_type, "name": card_type, "evidence": []}
        for card_type in blindspot.CARD_TYPES
    ]
    monkeypatch.setattr(blindspot, "run_scan", lambda **kwargs: cards)
    output_dir = tmp_path / "out"
    muse_server.SCAN.update(
        output_dir=str(output_dir), cards=[], phase="idle", version=0,
        card_type_status=None,
    )

    muse_server.scan_bg(ScanReq(topic="平台数据权力", puzzle=""))

    assert muse_server.SCAN["card_type_status"]["state"] == "ready"
    assert run_manifest.read(output_dir)[-1]["degradation"] == []


def test_scan_status_exposes_has_profile_field(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    client = TestClient(muse_server.app)
    body = client.get("/scan/status").json()
    assert "has_profile" in body and isinstance(body["has_profile"], bool)


def test_scan_status_returns_unchanged_for_current_version(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(muse_server.app)
    evidence = {
        "id": "evr_status",
        "source": {"title": "Study", "url": "https://example.test/study"},
        "retrieval": {"provider": "in-memory", "query": "query"},
        "verification": {"status": "provider-retrieved", "degraded": False},
    }
    zh_status = {
        "provider": "cnki",
        "state": "authentication-required",
        "hits": None,
        "message": "browser session missing",
    }
    with muse_server.SCAN_LOCK:
        muse_server.SCAN.update(
            phase="scanning",
            topic="t",
            cards=[
                {
                    "name": "A",
                    "evidence": [evidence],
                    "zh_status": zh_status,
                    "novelty_reason": "中文面不可用，不判定金标",
                }
            ],
            output_dir=str(tmp_path),
            error=None,
            has_profile=False,
            version=42,
        )
    body = client.get("/scan/status?since=42").json()
    assert body["unchanged"] is True and body["version"] == 42
    assert "cards" not in body
    changed = client.get("/scan/status?since=41").json()
    assert changed["unchanged"] is False
    assert changed["cards"][0]["evidence"] == [evidence]
    assert changed["cards"][0]["zh_status"] == zh_status
    assert "不判定金标" in changed["cards"][0]["novelty_reason"]
    restarted_or_stale_client = client.get("/scan/status?since=43").json()
    assert restarted_or_stale_client["unchanged"] is False


def test_roundtable_status_filters_empty_turns(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(muse_server.app)
    muse_server.SESSION.update(
        phase="ready",
        topic="t",
        runner=type("Runner", (), {
            "conversation_history": [
                _Turn("Background discussion expert", ""),
                _Turn("Background discussion moderator", "有效问题？"),
                _Turn("Background discussion expert", "   "),
            ]
        })(),
        progress=[],
        output_dir=str(tmp_path),
        error=None,
    )

    body = client.get("/status").json()

    assert body["turns"] == [{"role": "Background discussion moderator", "utterance": "有效问题？"}]


def test_roundtable_step_filters_empty_returned_turn(tmp_path):
    from fastapi.testclient import TestClient

    class _Runner:
        conversation_history = []

        def step(self, user_utterance=None):
            return _Turn("Background discussion expert", "")

    client = TestClient(muse_server.app)
    muse_server.SESSION.update(
        phase="ready",
        topic="t",
        runner=_Runner(),
        progress=[],
        output_dir=str(tmp_path),
        error=None,
    )

    body = client.post("/step", json={}).json()

    assert body == {"turns": []}


def test_warm_start_seeds_card_evidence_into_knowledge_base(monkeypatch):
    """#47：/session 携带 card evidence → 热身后 seed 进 runner.knowledge_base，
    证据身份（evidence_id）随 knowledge_base.to_dict 落 instance_dump。"""
    from knowledge_storm.dataclass import KnowledgeBase
    from evidence import ProviderRecord, evidence_ref_from_record

    ref = evidence_ref_from_record(
        ProviderRecord(source_id="S", title="卡片文献", url="https://doi.org/x", version="",
                       source_kind="scholarly-work", relation="supports", identity="",
                       locator_kind="url", locator_value="https://doi.org/x", exact="论据"),
        "openalex", "问式")
    kb = KnowledgeBase(topic="t", knowledge_base_lm=None,
                       node_expansion_trigger_count=10, encoder=object())

    class _Runner:
        def __init__(self):
            self.knowledge_base = kb
            self.conversation_history = []

        def warm_start(self):
            self.conversation_history = [_Turn("Background discussion moderator", "热身完成")]

    monkeypatch.setattr(muse_server, "build_runner", lambda req: _Runner())
    muse_server.warm_start_bg(muse_server.SessionReq(topic="主题", card_id=1, evidence=[ref]))

    assert muse_server.SESSION["phase"] == "ready"
    ids = {v["meta"]["evidence_id"] for v in kb.to_dict()["info_uuid_to_info_dict"].values()}
    assert ref["id"] in ids


def test_scan_feedback_records_event_and_derives_suppression(tmp_path):
    """#50：/scan/feedback 记不可变事件，并由投影重建 angle-feedback 抑制面。"""
    from fastapi.testclient import TestClient
    import feedback_events

    muse_server.SCAN.update(
        phase="done", output_dir=str(tmp_path),
        cards=[{"id": 1, "name": "控制论视角", "evidence": [{"id": "evr_1"}]}])
    client = TestClient(muse_server.app)

    assert client.post("/scan/feedback", json={"name": "控制论视角", "verdict": "已知"}).json() == {"ok": True}

    events = feedback_events.read_events(tmp_path)
    assert len(events) == 1
    assert events[0]["verdict"] == "已知" and events[0]["evidence_ids"] == ["evr_1"]
    assert blindspot.normalize_name("控制论视角") in blindspot.load_suppressed(str(tmp_path))


def test_adversary_bg_writes_run_manifest_with_code_version(monkeypatch, tmp_path):
    """#49：对抗幕跑完落 run-manifest.jsonl，含 kind/code_version/evidence 关联，且无秘密。"""
    import run_manifest

    monkeypatch.setattr(muse_server, "load_api_key", lambda **k: None)
    monkeypatch.setattr(adversary, "real_review_llm", lambda *a, **k: (
        lambda p: json.dumps({"failures": [{"statement": "自治缺强制力", "type": "机制缺环",
                                            "severity": "致命"}]})))
    monkeypatch.setattr(adversary, "real_falsify_search",
                        lambda: (lambda claim, failures: {"sources": [], "en_hits": 0, "zh_hits": 0}))
    monkeypatch.setattr(adversary, "real_library_search",
                        lambda **k: (lambda claim, failures: {"evidence": [], "degraded": False}))
    monkeypatch.setattr(run_manifest, "code_version", lambda: "testsha")

    muse_server.ADV.update(phase="reviewing", mode="line", output_dir=str(tmp_path),
                           version=0, source=None, source_version=0, claims=[], error=None)
    muse_server.adversary_bg(AdversaryReq(mode="line", line="平台自治可替代执法"))

    runs = run_manifest.read(tmp_path)
    assert len(runs) == 1
    m = runs[0]
    assert m["kind"] == "adversary" and m["code_version"] == "testsha"
    assert "failure-points.md" in m["artifacts"]
    assert "***redacted***" not in json.dumps(m)          # 本例无秘密
    assert "平台自治可替代执法" == muse_server.ADV["source"]  # 引擎照常跑完


def test_warm_start_without_evidence_does_not_seed(monkeypatch):
    """#47：不带 evidence 的圆桌照常启动，不 seed（缺证据不拖垮热身）。"""
    from knowledge_storm.dataclass import KnowledgeBase

    kb = KnowledgeBase(topic="t", knowledge_base_lm=None,
                       node_expansion_trigger_count=10, encoder=object())

    class _Runner:
        def __init__(self):
            self.knowledge_base = kb
            self.conversation_history = []

        def warm_start(self):
            self.conversation_history = [_Turn("Background discussion moderator", "热身完成")]

    monkeypatch.setattr(muse_server, "build_runner", lambda req: _Runner())
    muse_server.warm_start_bg(muse_server.SessionReq(topic="主题"))

    assert muse_server.SESSION["phase"] == "ready"
    assert kb.info_uuid_to_info_dict == {}


def test_roundtable_report_filters_empty_turns_in_conversation_md(tmp_path):
    class _KnowledgeBase:
        def reorganize(self):
            pass

    class _Runner:
        conversation_history = [
            _Turn("Background discussion expert", ""),
            _Turn("Background discussion moderator", "有效问题？"),
            _Turn("Background discussion expert", "   "),
            _Turn("Background discussion expert", "有效回答。"),
        ]
        knowledge_base = _KnowledgeBase()

        def generate_report(self):
            return "# 报告\n"

        def to_dict(self):
            return {}

        def dump_logging_and_reset(self):
            return {}

    muse_server.SESSION.update(
        phase="ready",
        topic="圆桌主题",
        card_name="圆桌主题",
        runner=_Runner(),
        progress=[],
        output_dir=str(tmp_path / "costorm_topic"),
        error=None,
    )

    muse_server.report()

    text = (tmp_path / "costorm_topic" / "conversation.md").read_text(encoding="utf-8")
    assert "有效问题？" in text and "有效回答。" in text
    assert "**Background discussion expert**: \n" not in text


def test_roundtable_report_appends_idempotent_mcii_action_using_failure_point(tmp_path):
    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    qfile = muse / "questions.md"
    qfile.write_text(
        "# 拷问弹药：圆桌主题\n\n## 原卡\n- 旧问题？\n\n"
        "### 行动\n- 障碍：卡片最强反驳\n\n"
        "## 圆桌深挖：圆桌主题扩展\n- 扩展主题旧问题？\n",
        encoding="utf-8",
    )
    (muse / "failure-points.md").write_text(
        "# 失败点\n\n## 主张 1：圆桌主题扩展（构思幕卡片送入）\n\n"
        "### [1a] 别卡的失败点\n- 裁决：**未决**\n\n"
        "## 主张 2：圆桌主题（构思幕卡片送入）\n\n"
        "### [2a] 缺少反例检验\n- 裁决：**未决**\n",
        encoding="utf-8",
    )

    class _KnowledgeBase:
        def reorganize(self):
            pass

    class _Runner:
        conversation_history = [_Turn("Background discussion moderator", "圆桌形成了什么新问题？")]
        knowledge_base = _KnowledgeBase()

        def generate_report(self):
            return "# 圆桌报告\n\n## 共识\n"

        def to_dict(self):
            return {}

        def dump_logging_and_reset(self):
            return {}

    muse_server.SESSION.update(
        phase="ready",
        topic="圆桌主题",
        card_name="圆桌主题",
        model="deepseek",
        runner=_Runner(),
        progress=[],
        output_dir=str(tmp_path / "costorm_topic"),
        error=None,
    )

    muse_server.report()
    first = qfile.read_text(encoding="utf-8")
    muse_server.report()
    second = qfile.read_text(encoding="utf-8")

    assert "## 原卡\n- 旧问题？" in first
    assert "## 圆桌深挖：圆桌主题扩展\n- 扩展主题旧问题？" in first
    assert "## 圆桌深挖：圆桌主题\n- 圆桌形成了什么新问题？" in first
    assert "- 目标（理想论证）：把「圆桌主题」的圆桌共识收敛为可写入论文的中心论证" in first
    assert "- 障碍：缺少反例检验" in first  # failure-points 优先于扫描卡 steelman
    assert "- if–then 验收门槛：" in first
    assert second == first
    assert second.splitlines().count("## 圆桌深挖：圆桌主题") == 1


def test_roundtable_report_uses_explicit_card_name_with_separator_for_obstacle(tmp_path):
    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    qfile = muse / "questions.md"
    qfile.write_text(
        "# 拷问弹药\n\n## 卡名：副标题\n\n"
        "### 行动\n- 障碍：扫描侧反驳\n",
        encoding="utf-8",
    )
    (muse / "failure-points.md").write_text(
        "# 失败点\n\n## 主张 1：卡名：副标题（构思幕卡片送入）\n\n"
        "### [1a] 分隔符卡片的失败点\n- 裁决：**未决**\n",
        encoding="utf-8",
    )

    class _KnowledgeBase:
        def reorganize(self):
            pass

    class _Runner:
        conversation_history = [_Turn("Background discussion moderator", "还需要追问什么？")]
        knowledge_base = _KnowledgeBase()

        def generate_report(self):
            return "# 圆桌报告\n"

        def to_dict(self):
            return {}

        def dump_logging_and_reset(self):
            return {}

    muse_server.SESSION.update(
        phase="ready",
        topic="卡名：副标题：机制说明",
        card_name="卡名：副标题",
        model="deepseek",
        runner=_Runner(),
        progress=[],
        output_dir=str(tmp_path / "costorm_topic"),
        error=None,
    )

    muse_server.report()

    assert "- 障碍：分隔符卡片的失败点" in qfile.read_text(encoding="utf-8")


def test_roundtable_report_adds_later_questions_after_action_only_report(tmp_path):
    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    qfile = muse / "questions.md"
    qfile.write_text("# 拷问弹药\n", encoding="utf-8")

    class _KnowledgeBase:
        def reorganize(self):
            pass

    class _Runner:
        conversation_history = [_Turn("Background discussion moderator", "本轮只有陈述。")]
        knowledge_base = _KnowledgeBase()

        def generate_report(self):
            return "# 圆桌报告\n"

        def to_dict(self):
            return {}

        def dump_logging_and_reset(self):
            return {}

    runner = _Runner()
    muse_server.SESSION.update(
        phase="ready",
        topic="同一主题",
        card_name="同一主题",
        model="deepseek",
        runner=runner,
        progress=[],
        output_dir=str(tmp_path / "costorm_topic"),
        error=None,
    )

    muse_server.report()
    runner.conversation_history = [
        _Turn("Background discussion moderator", "后来形成了什么关键问题？")
    ]
    muse_server.report()
    text = qfile.read_text(encoding="utf-8")

    assert "- 后来形成了什么关键问题？" in text
    assert text.splitlines().count("## 圆桌深挖：同一主题") == 1
    assert text.count("- 目标（理想论证）：把「同一主题」") == 1


# ---- 对抗幕接口（#10）：只 stub LLM/检索叶子，放真 run_review 跑通 server→引擎 全链 ----
import json
from pathlib import Path

import adversary
from muse_server import AdversaryReq


def _stub_adv_leaves(monkeypatch, redteam_json):
    monkeypatch.setattr(muse_server, "load_api_key", lambda **k: None)
    monkeypatch.setattr(adversary, "real_review_llm", lambda: (lambda p: redteam_json))
    # 证伪检索 sidecar 换成空池桩（不起 .venv-gptr 子进程）→ 无据 → 未决
    monkeypatch.setattr(adversary, "real_falsify_search",
                        lambda: (lambda claim, failures: {"sources": [], "en_hits": 0, "zh_hits": 0}))


def test_adversary_bg_line_mode_writes_failure_points_undecided(monkeypatch, tmp_path):
    # 无稿模式：主线句直接受审；检索空 → 无据 → 未决·不放行（§12 条 5），真落 failure-points.md
    redteam = json.dumps({"failures": [
        {"statement": "反例未排除", "type": "反例", "severity": "重大"},
        {"statement": "机制方向可能相反", "type": "机制缺环", "severity": "致命"}]})
    _stub_adv_leaves(monkeypatch, redteam)
    muse_server.ADV.update(phase="reviewing", mode="line", claims=[],
                           output_dir=str(tmp_path / "out"), error=None, topic="t")

    muse_server.adversary_bg(AdversaryReq(mode="line", line="算法透明度必然提升司法公正"))

    assert muse_server.ADV["phase"] == "done"
    claims = muse_server.ADV["claims"]
    assert len(claims) == 1 and claims[0]["from"] == "input" and claims[0]["span"] is None
    assert all(f["verdict"] == "未决" for f in claims[0]["failures"])
    fp = (tmp_path / "out" / "docs" / "agents" / "muse" / "failure-points.md")
    assert fp.exists() and "不放行" in fp.read_text(encoding="utf-8")


def test_adversary_bg_draft_mode_reads_md_and_locates_span(monkeypatch, tmp_path):
    draft = tmp_path / "初稿.md"
    quote = "数据确权是破解平台数据垄断的前提"
    draft.write_text(f"引言。\n本文主张：{quote}。\n下文展开。", encoding="utf-8")
    review = json.dumps({"claims": [{"text": "确权是前提", "quote": quote}]})
    # 有稿：review_llm 先抽主张（回 claims），红队再攻击——同一 stub 按调用序返回
    replies = [
        review,
        json.dumps({"failures": [{"statement": "反例", "severity": "重大"}]}),
        json.dumps({"stance": "存疑", "argument": "需要补证。", "needed_evidence": "中文法文献"}),
        json.dumps({"decision": "维持", "reason": "无证据，维持未决。", "revision": "补证"}),
    ]
    monkeypatch.setattr(muse_server, "load_api_key", lambda **k: None)
    monkeypatch.setattr(adversary, "real_review_llm", lambda: (lambda p: replies.pop(0)))
    monkeypatch.setattr(adversary, "real_falsify_search",
                        lambda: (lambda claim, failures: {"sources": [], "en_hits": 0, "zh_hits": 0}))
    muse_server.ADV.update(phase="reviewing", mode="draft", claims=[],
                           output_dir=str(tmp_path), error=None, topic="初稿.md")

    muse_server.adversary_bg(AdversaryReq(mode="draft", draft="初稿.md"))

    assert muse_server.ADV["phase"] == "done"
    c = muse_server.ADV["claims"][0]
    assert c["from"] == "draft" and c["span"] is not None   # 稿面跨度就位（②高亮靠它）
    assert c["failures"][0]["author_rebuttal"]["stance"] == "存疑"
    assert c["failures"][0]["meta_review"]["final_verdict"] == "未决"


def test_list_drafts_skips_generated_products(tmp_path):
    (tmp_path / "01_成品稿").mkdir()
    (tmp_path / "01_成品稿" / "初稿.md").write_text("x", encoding="utf-8")
    (tmp_path / "提纲.md").write_text("y", encoding="utf-8")
    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    (muse / "perspectives.md").write_text("z", encoding="utf-8")  # 产物，不是草稿
    names = {d["name"] for d in muse_server._list_drafts(str(tmp_path))}
    assert "提纲.md" in names and str(Path("01_成品稿") / "初稿.md") in names
    assert not any("perspectives" in n for n in names)


def test_start_adversary_validates_bad_mode_and_empty(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(muse_server.app)
    assert client.post("/adversary", json={"mode": "bogus"}).status_code == 400
    assert client.post("/adversary", json={"mode": "line", "line": "   "}).status_code == 400
    assert client.post("/adversary", json={"mode": "draft", "draft": ""}).status_code == 400
    # status 形状（未起审查时 idle）
    body = client.get("/adversary/status").json()
    assert body["phase"] in ("idle", "reviewing", "done", "error") and "claims" in body


def test_adversary_status_does_not_resend_known_source(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(muse_server.app)
    with muse_server.ADV_LOCK:
        muse_server.ADV.update(
            phase="reviewing", mode="draft", topic="draft.md",
            claims=[{"id": 1, "text": "c", "failures": []}],
            source="很长的草稿", source_version=3, version=5,
            output_dir=str(tmp_path), error=None,
        )
    body = client.get("/adversary/status?since=3").json()
    assert body["unchanged"] is False and body["source"] is None
    assert body["claims"][0]["text"] == "c"
    unchanged = client.get("/adversary/status?since=5").json()
    assert unchanged["unchanged"] is True and "claims" not in unchanged and "source" not in unchanged
    stale_client = client.get("/adversary/status?since=6").json()
    assert stale_client["unchanged"] is False


def test_perf_status_exposes_observability_counters():
    from fastapi.testclient import TestClient

    blindspot.reset_retrieval_cache_stats()
    adversary.reset_sidecar_stats()
    client = TestClient(muse_server.app)

    body = client.get("/perf/status").json()

    assert body["retrieval_cache"]["hits"] == 0
    assert body["retrieval_cache"]["misses"] == 0
    assert body["retrieval_cache"]["stores"] == 0
    assert body["retrieval_cache"]["errors"] == 0
    assert body["retrieval_cache"]["by_retriever"] == {}
    assert body["sidecar"] == {"single_invocations": 0, "batch_invocations": 0, "claims_requested": 0}
    assert body["llm_cache"]["available"] is False


def _seed_graph_artifacts(tmp_path):
    d = tmp_path / "docs" / "agents" / "muse"
    d.mkdir(parents=True, exist_ok=True)
    (d / "perspectives.md").write_text(
        "# 切入点卡片：平台数据\n\n## 熵增视角（学科视角｜交叉空白｜🥇英热中冷）\n- 机制：x\n",
        encoding="utf-8")
    ref = {"id": "evr_1", "source": {"title": "Entropy", "url": "https://doi.org/x"},
           "retrieval": {"provider": "openalex"}, "relation": "context",
           "verification": {"status": "provider-retrieved"}}
    (d / "sources.md").write_text(
        "# 文献锚点：平台数据\n\n- [熵增视角] Entropy — https://doi.org/x\n"
        "  - EvidenceRef-JSON: " + json.dumps(ref, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (d / "failure-points.md").write_text(
        "# 失败点\n\n## 主张 1：熵是隐喻（构思幕卡片送入）\n\n### [f2] 已有文献做过\n"
        "- 类型：新颖性｜严重度：重大｜裁决：**已证伪**\n"
        "- [证伪] Prior — https://doi.org/p · EvidenceRef `evr_2`\n", encoding="utf-8")


def test_evidence_graph_endpoint_projects_cards_and_claims(tmp_path):
    from fastapi.testclient import TestClient

    _seed_graph_artifacts(tmp_path)
    client = TestClient(muse_server.app)
    r = client.get("/evidence/graph", params={"output_dir": str(tmp_path)})

    assert r.status_code == 200
    data = r.json()
    assert [c["id"] for c in data["cards"]] == ["card:熵增视角"]
    assert [c["id"] for c in data["claims"]] == ["claim:1"]
    card_view = data["views"]["card:熵增视角"]
    assert [e["ref_id"] for e in card_view["context"]] == ["evr_1"]
    claim_view = data["views"]["claim:1"]
    assert claim_view["failures"][0]["failure"]["id"] == "failure:f2"
    assert [e["ref_id"] for e in claim_view["failures"][0]["refutes"]] == ["evr_2"]


def test_evidence_graph_route_not_shadowed_by_evidence_id(tmp_path):
    # "graph" 必须命中 /evidence/graph，而非被 /evidence/{evidence_id} 当作 id → 404
    from fastapi.testclient import TestClient

    client = TestClient(muse_server.app)
    r = client.get("/evidence/graph", params={"output_dir": str(tmp_path)})
    assert r.status_code == 200                       # 空目录 → 空投影，非 404
    assert r.json()["cards"] == [] and r.json()["claims"] == []
