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
    for name in (
        "PAPER_MUSE_SERVER_ROOT",
        "PAPER_MUSE_APP_DATA_DIR",
        "PAPER_MUSE_CONFIG_DIR",
        "PAPER_MUSE_CACHE_DIR",
        "PAPER_MUSE_RUNTIME_DIR",
        "PAPER_MUSE_LOGS_DIR",
        "PAPER_MUSE_OUTPUT_DIR",
        "PAPER_MUSE_SECRETS_FILE",
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
    with muse_server.SCAN_LOCK:
        muse_server.SCAN.update(phase="scanning", topic="t", cards=[{"name": "A"}],
                                output_dir=str(tmp_path), error=None, has_profile=False,
                                version=42)
    body = client.get("/scan/status?since=42").json()
    assert body["unchanged"] is True and body["version"] == 42
    assert "cards" not in body
    changed = client.get("/scan/status?since=41").json()
    assert changed["unchanged"] is False and changed["cards"] == [{"name": "A"}]
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
        runner=_Runner(),
        progress=[],
        output_dir=str(tmp_path / "costorm_topic"),
        error=None,
    )

    muse_server.report()

    text = (tmp_path / "costorm_topic" / "conversation.md").read_text(encoding="utf-8")
    assert "有效问题？" in text and "有效回答。" in text
    assert "**Background discussion expert**: \n" not in text


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
    replies = [review, json.dumps({"failures": [{"statement": "反例", "severity": "重大"}]})]
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
