"""#50 不可变反馈事件 + 投影 + 离线 replay 指标。全部注入时间戳 → 确定性离线。"""

import feedback_events as F
import blindspot


def test_record_events_are_immutable_append_with_version(tmp_path):
    F.record_event(tmp_path, name="控制论视角", verdict="已知", ts="t1")
    F.record_event(tmp_path, name="博弈论方法", verdict="新且值得深挖", ts="t2",
                   evidence_ids=["evr_1"])
    evs = F.read_events(tmp_path)
    assert [e["version"] for e in evs] == [1, 2]
    assert evs[0]["verdict"] == "已知" and evs[1]["evidence_ids"] == ["evr_1"]
    assert all(e["event_id"].startswith("fev_") for e in evs)


def test_correction_supersedes_without_mutating_history(tmp_path):
    """反馈修正：同角度再记一条覆盖旧判断，历史事件不被篡改。"""
    F.record_event(tmp_path, name="控制论视角", verdict="已知", ts="t1")
    F.record_event(tmp_path, name="控制论视角", verdict="新且值得深挖", ts="t2",
                   supersedes="(t1)")
    evs = F.read_events(tmp_path)
    assert len(evs) == 2 and evs[0]["verdict"] == "已知"     # 旧事件仍在，未被改
    proj = F.project(evs)
    norm = blindspot.normalize_name("控制论视角")
    assert proj["latest"][norm]["verdict"] == "新且值得深挖"  # 投影取最新
    assert norm not in proj["suppressed"]                    # 已翻案，不再抑制


def test_project_splits_suppress_applicability_priority(tmp_path):
    F.record_event(tmp_path, name="A 视角", verdict="已知", ts="t1")
    F.record_event(tmp_path, name="B 视角", verdict="新但不适用", ts="t2", applicability="仅限刑法")
    F.record_event(tmp_path, name="C 视角", verdict="新且值得深挖", ts="t3")
    proj = F.project(F.read_events(tmp_path))
    assert blindspot.normalize_name("A 视角") in proj["suppressed"]
    assert proj["applicability"][blindspot.normalize_name("B 视角")] == "仅限刑法"
    assert proj["priority_boost"][blindspot.normalize_name("C 视角")] == 1


def test_rebuild_angle_feedback_is_derived_from_events_and_suppresses(tmp_path):
    """兼容面：angle-feedback.json 由事件投影重建，blindspot.load_suppressed 照常读到「已知」。"""
    F.record_event(tmp_path, name="控制论视角", verdict="已知", ts="t1")
    F.record_event(tmp_path, name="博弈论方法", verdict="新且值得深挖", ts="t2")
    F.rebuild_angle_feedback(tmp_path)
    suppressed = blindspot.load_suppressed(str(tmp_path))
    assert blindspot.normalize_name("控制论视角") in suppressed
    assert blindspot.normalize_name("博弈论方法") not in suppressed


def test_replay_metrics_prove_feedback_changes_next_round(tmp_path):
    """离线 replay（无付费 API）：标「已知」的角度在下一轮消失 → suppressed_leaked=0；
    重复率、选择性、验证 locator 比例、证据复用可解释。"""
    verified = {"verification": {"status": "provider-retrieved", "degraded": False}, "id": "evr_9"}
    round1 = [
        {"name": "控制论视角", "gold": True, "verdict": "新且值得深挖", "evidence": [verified]},
        {"name": "博弈论方法", "outlier": True, "evidence": []},
    ]
    round2 = [  # 用户把「控制论视角」标已知后重扫：该角度不再出现（抑制生效）
        {"name": "博弈论方法", "outlier": True, "evidence": [verified]},
        {"name": "复杂系统视角", "evidence": []},
    ]
    events = [F.record_event(tmp_path, name="控制论视角", verdict="已知", ts="t1")]
    m = F.replay_metrics([round1, round2], events)
    assert m[0]["first_valuable_pos"] == 0                    # 第一张即有价值
    assert m[0]["gold_selectivity"] == 0.5 and m[0]["outlier_selectivity"] == 0.5
    assert m[0]["verified_locator_ratio"] == 1.0             # 唯一 locator 已验证
    assert m[0]["suppressed_leaked"] == 1                    # 首轮出现（用户据此标「已知」）
    assert m[1]["suppressed_leaked"] == 0                    # 再扫已抑制 → 反馈改变了下一轮
    assert m[1]["repeat_rate"] == 0.5                        # 博弈论方法在两轮都出现
    assert m[1]["evidence_reuse"] == 1                       # evr_9 跨轮复用


def test_record_event_rejects_bad_verdict(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        F.record_event(tmp_path, name="X", verdict="随便", ts="t1")
