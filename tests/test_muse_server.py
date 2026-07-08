"""muse_server 薄层测试：#4 的 has_profile 透出（无画像「发现力打折」的服务端判据）。
scan_bg 的外部依赖（LLM/检索/落盘）全部 monkeypatch 掉，只验画像参照系有无的置位与透传。"""

import blindspot
import muse_server
from muse_server import ScanReq


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


def test_scan_bg_sets_has_profile_true_and_feeds_profile(monkeypatch, tmp_path):
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

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    client = TestClient(muse_server.app)
    body = client.get("/scan/status").json()
    assert "has_profile" in body and isinstance(body["has_profile"], bool)


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
