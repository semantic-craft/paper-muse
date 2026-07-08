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
