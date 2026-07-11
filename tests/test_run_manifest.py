"""#49 版本化无秘密 run manifest：构造/清洗/序列化稳定/跨流程关联/降级。
时间戳与 run id 全部注入 → 确定性离线测试。"""

import json

import run_manifest as M


def _manifest(**over):
    base = dict(kind="scan", run_id="run_scan_abc", started_at="2026-07-11T00:00:00",
                ended_at="2026-07-11T00:00:20", code_version_="deadbeef", model="deepseek")
    base.update(over)
    kind = base.pop("kind")
    return M.build(kind, **base)


def test_new_run_id_is_deterministic_and_varies():
    assert M.new_run_id("scan", "topic|t0") == M.new_run_id("scan", "topic|t0")
    assert M.new_run_id("scan", "a") != M.new_run_id("scan", "b")
    assert M.new_run_id("scan", "a") != M.new_run_id("adversary", "a")
    assert M.new_run_id("scan", "a").startswith("run_scan_")


def test_build_serialization_is_stable_and_versioned():
    a = _manifest()
    b = _manifest()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)   # 同输入同序列化
    assert a["schema_version"] == M.SCHEMA_VERSION
    assert a["kind"] == "scan" and a["run_id"] == "run_scan_abc"
    assert a["code_version"] == "deadbeef"


def test_scrub_redacts_secret_valued_strings():
    assert M.scrub("key sk-ABCDEFGH1234 tail") == f"key {M.REDACTED} tail"
    assert M.scrub("AIzaSyABCDEFGHIJKLMNOPQRSTUVWX123") == M.REDACTED
    assert M.scrub("ghp_" + "a" * 24) == M.REDACTED
    assert M.scrub("普通说明文本") == "普通说明文本"


def test_scrub_redacts_secret_named_keys_nested():
    dirty = {"model": "deepseek", "auth": {"api_key": "sk-xxxxxxxx", "note": "ok"},
             "list": [{"token": "abc", "safe": 1}]}
    clean = M.scrub(dirty)
    assert clean["model"] == "deepseek"
    assert clean["auth"] == M.REDACTED                      # 键名 auth → 整块 redact
    assert clean["list"][0]["token"] == M.REDACTED
    assert clean["list"][0]["safe"] == 1


def test_build_carries_no_profile_content_only_bool():
    m = _manifest(has_profile=True)
    assert m["has_profile"] is True
    # 画像内容根本没有对应字段可传（白名单），序列化里不出现任何原文
    assert "profile" not in json.dumps(m, ensure_ascii=False).replace("has_profile", "")


def test_build_scrubs_accidental_secret_in_provider_capability():
    m = _manifest(provider_capability={"openai": "ready", "leaked": "sk-DEADBEEF12345678"})
    assert m["provider_capability"]["openai"] == "ready"
    assert m["provider_capability"]["leaked"] == M.REDACTED


def test_append_read_round_trip_is_immutable_append(tmp_path):
    M.append(tmp_path, _manifest(run_id="r1"))
    M.append(tmp_path, _manifest(run_id="r2", kind="adversary"))
    runs = M.read(tmp_path)
    assert [r["run_id"] for r in runs] == ["r1", "r2"]      # 追加、保序
    assert runs[1]["kind"] == "adversary"


def test_cross_flow_correlation_via_parent_and_evidence_ids(tmp_path):
    parent = M.emit("scan", tmp_path, seed="t|t0", started_at="s", ended_at="e",
                    code_version_="v", evidence_ids=["evr_1", "evr_2"])
    M.emit("adversary", tmp_path, seed="t|t1", started_at="s", ended_at="e",
           code_version_="v", parent_run_id=parent["run_id"], evidence_ids=["evr_2"])
    runs = M.read(tmp_path)
    assert runs[1]["parent_run_id"] == parent["run_id"]     # 子流程指回父
    assert runs[0]["evidence_ids"] == ["evr_1", "evr_2"]


def test_degradation_recorded(tmp_path):
    m = M.emit("adversary", tmp_path, seed="t|t2", started_at="s", ended_at="e",
               code_version_="v", degradation=["sidecar missing", "paperqa venv missing"])
    assert m["degradation"] == ["sidecar missing", "paperqa venv missing"]
    assert M.read(tmp_path)[0]["degradation"] == m["degradation"]


def test_read_missing_file_is_empty(tmp_path):
    assert M.read(tmp_path) == []
