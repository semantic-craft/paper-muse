"""#53 证据图投影器测试：重建 / 重复输入去重 / 部分损坏容忍 / 关系查询 / 源更新刷新，
以及 unresolved·degraded·superseded 在图中可见（绝不当 verified）。"""

import json
from pathlib import Path

import evidence_graph


# ---------------------------------------------------------------- 产物写入 helper

def _muse(tmp_path) -> Path:
    d = Path(tmp_path) / "docs" / "agents" / "muse"
    d.mkdir(parents=True, exist_ok=True)
    return d


_PERSPECTIVES = """# 切入点卡片：平台数据权力

## 熵增视角（学科视角｜交叉空白｜🥇英热中冷）
- 机制：把平台数据聚合视为局部负熵积累。
- 最强反驳：熵在社科是修辞不是机制。

## 制度同构（DiMaggio）（理论框架｜边缘有人做｜共识）
- 机制：强制/模仿/规范三种同构压力。
"""

# sources.md 每卡内联 EvidenceRef-JSON（card→evidence 的权威链接源）
_REF1 = {"id": "evr_1", "source": {"title": "Entropy paper", "url": "https://doi.org/x"},
         "locator": {"kind": "url", "value": "https://doi.org/x"},
         "retrieval": {"provider": "openalex"}, "relation": "context",
         "verification": {"status": "provider-retrieved", "degraded": False}}
_SOURCES = (
    "# 文献锚点：平台数据权力\n\n"
    "- [熵增视角] Entropy paper — https://doi.org/x\n"
    "  - EvidenceRef `evr_1` · openalex · provider-retrieved\n"
    "  - EvidenceRef-JSON: " + json.dumps(_REF1, ensure_ascii=False, sort_keys=True) + "\n"
)

_FAILURE_POINTS = """# 失败点（对抗幕·红笔审稿）

> 每条失败点 = 苛刻审稿人的一处「这里会崩」。

## 主张 1：熵是隐喻不是机制（构思幕卡片送入）
> 把数据垄断重述为信息势差的热力学必然。

### [f1] 缺乏可测量的熵定义
- 类型：概念｜严重度：致命｜裁决：**未决**
- 双面密度：英文命中 0，中文/自有命中 0
- **未检得证据 · 不放行**（补检索 / 换词 / 需中文学界会话方可解锁）

### [f2] 已有文献做过同构解释
- 类型：新颖性｜严重度：重大｜裁决：**已证伪**
- [证伪] Prior isomorphism study — https://doi.org/prior · EvidenceRef `evr_2`
"""

_ANNOTATION = {
    "schema_version": 1,
    "target": {"kind": "draft", "id": "", "version": "", "checksum": "abc"},
    "annotations": [{
        "annotation_id": "claim-1",
        "quote": {"exact": "熵是隐喻", "prefix": "", "suffix": ""},
        "position": None,
        "meta": {"claim": "熵是隐喻不是机制", "verdicts": ["未决", "已证伪"],
                 "evidence_ids": ["evr_2"]},
        "attachment": {"status": "unresolved", "start": None, "end": None,
                       "reason": "exact 未命中"},
    }],
}

# fb1（新且值得深挖）被 fb2（已知）supersede —— fb1 应标 superseded、不当 current
_FEEDBACK = "\n".join(json.dumps(e, ensure_ascii=False) for e in [
    {"event_id": "fb1", "version": 1, "ts": "2026-07-01", "run_id": "r1",
     "card_id": "1", "name": "熵增视角", "name_norm": "熵增视角",
     "verdict": "新且值得深挖", "evidence_ids": ["evr_1"], "supersedes": ""},
    {"event_id": "fb2", "version": 2, "ts": "2026-07-02", "run_id": "r2",
     "card_id": "1", "name": "熵增视角", "name_norm": "熵增视角",
     "verdict": "已知", "evidence_ids": [], "supersedes": "fb1"},
]) + "\n"

_MANIFEST = json.dumps({
    "schema_version": 1, "kind": "scan", "run_id": "r1",
    "evidence_ids": ["evr_1"], "degradation": ["semantic_scholar: missing key"],
}, ensure_ascii=False) + "\n"


def _seed_all(tmp_path):
    d = _muse(tmp_path)
    (d / "perspectives.md").write_text(_PERSPECTIVES, encoding="utf-8")
    (d / "sources.md").write_text(_SOURCES, encoding="utf-8")
    (d / "failure-points.md").write_text(_FAILURE_POINTS, encoding="utf-8")
    (d / "annotation-handoff.json").write_text(
        json.dumps(_ANNOTATION, ensure_ascii=False), encoding="utf-8")
    (d / "feedback-events.jsonl").write_text(_FEEDBACK, encoding="utf-8")
    (d / "run-manifest.jsonl").write_text(_MANIFEST, encoding="utf-8")
    (d / "evidence.json").write_text(json.dumps(
        {"schema_version": 1, "evidence": {"evr_1": _REF1}, "bundles": {}},
        ensure_ascii=False), encoding="utf-8")
    return d


def _by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def _has_edge(graph, source, target, relation):
    return any(e["source"] == source and e["target"] == target
              and e["relation"] == relation for e in graph["edges"])


# ---------------------------------------------------------------- 节点/边投影

def test_build_graph_projects_all_node_kinds(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert kinds == {"topic", "card", "claim", "failure", "evidence",
                     "annotation", "feedback"}
    nodes = _by_id(graph)
    assert nodes["topic:平台数据权力"]["title"] == "平台数据权力"
    # 带括号卡名（制度同构（DiMaggio））join key 靠 normalize_name 落到 制度同构
    assert "card:熵增视角" in nodes and "card:制度同构" in nodes
    assert nodes["card:熵增视角"]["novelty"] == "交叉空白"


def test_edges_carry_prescribed_relations(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    # card → topic : derived-from
    assert _has_edge(graph, "card:熵增视角", "topic:平台数据权力", "derived-from")
    # evidence → card : context（sources.md 内联 ref.relation）
    assert _has_edge(graph, "evidence:evr_1", "card:熵增视角", "context")
    # failure → claim : refutes；evidence → failure : refutes（证伪 stance）
    assert _has_edge(graph, "failure:f2", "claim:1", "refutes")
    assert _has_edge(graph, "evidence:evr_2", "failure:f2", "refutes")
    # annotation → claim : annotates
    assert _has_edge(graph, "annotation:claim-1", "claim:1", "annotates")
    # feedback → card : deepens
    assert _has_edge(graph, "feedback:fb1", "card:熵增视角", "deepens")
    relations = {e["relation"] for e in graph["edges"]}
    assert relations <= {"supports", "refutes", "context",
                         "derived-from", "annotates", "deepens"}


# ---------------------------------------------------------------- 幂等重建

def test_rebuild_is_idempotent(tmp_path):
    _seed_all(tmp_path)
    first = evidence_graph.build_graph(tmp_path)
    second = evidence_graph.build_graph(tmp_path)
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
        json.dumps(second, sort_keys=True, ensure_ascii=False)


def test_build_does_not_mutate_sources(tmp_path):
    d = _seed_all(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    names_before = sorted(p.name for p in d.iterdir())
    evidence_graph.build_graph(tmp_path)
    after = {p.name: p.read_bytes() for p in d.iterdir()}
    assert before == after                                   # 逐字节不改源
    assert sorted(p.name for p in d.iterdir()) == names_before  # 不新增文件


# ---------------------------------------------------------------- 重复输入去重

def test_duplicate_evidence_across_sources_dedupes_to_one_node(tmp_path):
    # evr_1 同时出现在 sources.md、evidence.json、feedback、manifest → 只一个节点
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    evr1 = [n for n in graph["nodes"] if n.get("ref_id") == "evr_1"]
    assert len(evr1) == 1
    # 详情来自 sources.md/evidence.json 的完整 ref（非身份桩）
    assert evr1[0]["title"] == "Entropy paper" and evr1[0]["provider"] == "openalex"


def test_duplicate_build_stable_edge_count(tmp_path):
    _seed_all(tmp_path)
    g1 = evidence_graph.build_graph(tmp_path)
    # 再喂一次相同产物（重复输入）不应改变图规模
    g2 = evidence_graph.build_graph(tmp_path)
    assert len(g1["edges"]) == len(g2["edges"])
    assert len(g1["nodes"]) == len(g2["nodes"])


# ---------------------------------------------------------------- 部分损坏容忍

def test_partial_corruption_keeps_other_sources(tmp_path):
    d = _seed_all(tmp_path)
    (d / "evidence.json").write_text("{ this is not json", encoding="utf-8")  # 坏 JSON
    # perspectives.md 混入一条不符契约的卡头（缺 ｜｜ 结构）→ 该卡跳过，其余保留
    (d / "perspectives.md").write_text(
        _PERSPECTIVES + "\n## 半行坏卡头没有元数据\n- 机制：x\n", encoding="utf-8")
    graph = evidence_graph.build_graph(tmp_path)      # 不抛异常
    nodes = _by_id(graph)
    assert "card:熵增视角" in nodes                    # 好卡仍在
    assert "card:半行坏卡头没有元数据" not in nodes       # 坏卡头被跳过
    # evidence.json 坏掉，但 sources.md 内联 ref 仍撑起 evr_1 详情
    evr1 = [n for n in graph["nodes"] if n.get("ref_id") == "evr_1"]
    assert len(evr1) == 1 and evr1[0]["title"] == "Entropy paper"


def test_missing_files_yield_empty_graph(tmp_path):
    _muse(tmp_path)                                    # 空目录，无任何产物
    graph = evidence_graph.build_graph(tmp_path)
    assert graph["nodes"] == [] and graph["edges"] == []


def test_corrupt_jsonl_line_skipped(tmp_path):
    d = _seed_all(tmp_path)
    (d / "feedback-events.jsonl").write_text(
        _FEEDBACK + "{ broken jsonl line\n", encoding="utf-8")
    graph = evidence_graph.build_graph(tmp_path)
    fb = [n for n in graph["nodes"] if n["kind"] == "feedback"]
    assert {n["id"] for n in fb} == {"feedback:fb1", "feedback:fb2"}  # 坏行跳过


# ---------------------------------------------------------------- 关系查询

def test_evidence_for_card_groups_support_context_feedback(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    view = evidence_graph.evidence_for_card(graph, "熵增视角")   # 原始名，自动 normalize
    assert view["card"]["id"] == "card:熵增视角"
    assert [e["ref_id"] for e in view["context"]] == ["evr_1"]
    assert view["supports"] == [] and view["refutes"] == []
    # 后续判断（反馈）两条都在，superseded 可辨
    verdicts = {f["verdict"]: f["superseded"] for f in view["feedback"]}
    assert verdicts == {"新且值得深挖": True, "已知": False}


def test_evidence_for_claim_groups_failures_and_annotations(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    view = evidence_graph.evidence_for_claim(graph, "1")
    assert view["claim"]["id"] == "claim:1"
    fids = {b["failure"]["id"] for b in view["failures"]}
    assert fids == {"failure:f1", "failure:f2"}
    f2 = next(b for b in view["failures"] if b["failure"]["id"] == "failure:f2")
    assert [e["ref_id"] for e in f2["refutes"]] == ["evr_2"]
    assert [a["id"] for a in view["annotations"]] == ["annotation:claim-1"]


def test_query_unknown_id_returns_empty_shell(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    assert evidence_graph.evidence_for_card(graph, "不存在的卡")["card"] is None
    assert evidence_graph.evidence_for_claim(graph, "999")["claim"] is None


# ---------------------------------------------------------------- 源更新后刷新

def test_refresh_reflects_appended_feedback(tmp_path):
    d = _seed_all(tmp_path)
    before = evidence_graph.build_graph(tmp_path)
    assert len([n for n in before["nodes"] if n["kind"] == "feedback"]) == 2
    # 追加一条新反馈事件（不可变追加流）→ 重建应反映
    (d / "feedback-events.jsonl").write_text(_FEEDBACK + json.dumps({
        "event_id": "fb3", "version": 3, "ts": "2026-07-03", "run_id": "r3",
        "card_id": "2", "name": "制度同构（DiMaggio）", "name_norm": "制度同构",
        "verdict": "新且值得深挖", "evidence_ids": [], "supersedes": "",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    after = evidence_graph.build_graph(tmp_path)
    assert len([n for n in after["nodes"] if n["kind"] == "feedback"]) == 3
    assert _has_edge(after, "feedback:fb3", "card:制度同构", "deepens")


def test_refresh_reflects_new_card(tmp_path):
    d = _seed_all(tmp_path)
    (d / "perspectives.md").write_text(
        _PERSPECTIVES + "\n## 福柯规训（学科视角｜交叉空白｜🔸离群）\n- 机制：可见性架构。\n",
        encoding="utf-8")
    graph = evidence_graph.build_graph(tmp_path)
    assert "card:福柯规训" in _by_id(graph)


# ---------------------------------------------------------------- 不当 verified

def test_unresolved_degraded_superseded_visible(tmp_path):
    _seed_all(tmp_path)
    graph = evidence_graph.build_graph(tmp_path)
    nodes = _by_id(graph)
    # 未决失败点：unresolved=True
    assert nodes["failure:f1"]["unresolved"] is True
    assert nodes["failure:f1"]["verdict"] == "未决"
    # 降级 provider：manifest degradation 标到证据节点 + meta 汇总
    assert nodes["evidence:evr_1"]["degraded"] is True
    assert "semantic_scholar: missing key" in nodes["evidence:evr_1"]["degraded_reasons"]
    assert graph["meta"]["degraded_providers"] == ["semantic_scholar: missing key"]
    # 被修正反馈：superseded=True（不当 current）
    assert nodes["feedback:fb1"]["superseded"] is True
    assert nodes["feedback:fb2"]["superseded"] is False
    # 批注定位符未决：attachment_status 可见
    assert nodes["annotation:claim-1"]["attachment_status"] == "unresolved"
