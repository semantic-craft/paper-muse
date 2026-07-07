import json

import pytest

from blindspot import (
    normalize_name,
    dedupe_cards,
    mark_outliers,
    apply_suppression,
    classify_novelty,
    extract_json,
    CARD_TYPES,
)


def _card(name, model="m1", **kw):
    base = {
        "type": "理论框架",
        "name": name,
        "mechanism": "机制",
        "why_nonobvious": "为什么",
        "steelman": "最强反驳",
        "questions": ["q1"],
        "source_models": [model],
    }
    base.update(kw)
    return base


def test_normalize_name_strips_noise():
    assert normalize_name("  交易成本 理论（TCE） ") == normalize_name("交易成本理论(tce)")


def test_dedupe_merges_source_models():
    cards = dedupe_cards([_card("交易成本理论", "deepseek"), _card("交易成本理论（TCE）", "gemini")])
    assert len(cards) == 1
    assert set(cards[0]["source_models"]) == {"deepseek", "gemini"}


def test_mark_outliers_only_single_proposer():
    cards = dedupe_cards([_card("A", "deepseek"), _card("A", "gemini"), _card("B", "openai")])
    cards = mark_outliers(cards)
    by = {c["name"]: c["outlier"] for c in cards}
    assert by["B"] is True and by["A"] is False


def test_apply_suppression_filters_known():
    cards = [_card("A"), _card("B")]
    kept = apply_suppression(cards, suppressed={normalize_name("A")})
    assert [c["name"] for c in kept] == ["B"]


def test_extract_json_repairs_cjk_quote_and_missing_brace():
    # 冒烟实证的 deepseek 坏法：结尾英文引号写成 ” 且丢最后的 }（未闭合）
    bad = '{"fundamentals": ["问题一？", "激励对象是谁？”]'
    got = extract_json(bad)
    assert isinstance(got["fundamentals"], list) and len(got["fundamentals"]) == 2
    assert got["fundamentals"][0] == "问题一？"


def test_zh_name_core_shapes():
    from blindspot import _zh_name_core
    assert _zh_name_core("信息论与控制论（Cybernetics）视角") == "信息论与控制论"
    assert _zh_name_core("实验法学：图灵测试变体（The Intellectual Turing Test）") == "图灵测试变体"
    assert _zh_name_core("法律符号学（Legal Semiotics）") == "法律符号学"
    assert _zh_name_core("Purely English") == "Purely English"  # 中文核心为空 → 回退原名


def test_cnki_empty_result_is_zero_hits(monkeypatch):
    # EMPTY_RESULT（零命中）≠ 未检：回 [] 让交叉空白/金矿判据可触发。
    # 真实形态（冒烟实证）：stdout 为空、YAML 错误块走 stderr、exit 66
    import blindspot as B

    class R:
        stdout = ""
        stderr = "ok: false\nerror:\n  code: EMPTY_RESULT\n  message: cnki search returned no data\n"

    monkeypatch.setattr(B.subprocess, "run", lambda *a, **k: R)
    assert B.real_cnki_search()("任意查询") == []


def test_cnki_session_error_still_raises(monkeypatch):
    import blindspot as B

    class R:
        stdout = ""
        stderr = "ok: false\nerror:\n  code: NO_BROWSER_SESSION\n"

    monkeypatch.setattr(B.subprocess, "run", lambda *a, **k: R)
    with pytest.raises(RuntimeError):
        B.real_cnki_search()("任意查询")


def test_run_scan_streaming_merge_and_enrich(tmp_path, monkeypatch):
    """流式两段出卡：快家先上墙（慢家未返回时 on_card 已发），重名合并翻离群，徽标异步补挂全齐。"""
    import threading as th

    import blindspot as B

    gate = th.Event()

    def fake_enum(topic, fundamentals, profile, tag, call):
        if tag == "fast":
            return [_card("A", "fast"), _card("B", "fast")]
        assert gate.wait(timeout=5), "快家的卡迟迟没上墙——流式失效"
        return [_card("A", "slow"), _card("C", "slow")]

    monkeypatch.setattr(B, "enumerate_cards", fake_enum)
    monkeypatch.setattr(B, "decompose_topic", lambda *a: ["f1"])
    monkeypatch.setattr(B, "_topic_zh_keyword", lambda *a: "著作权")
    emitted = []

    def on_card(c):
        emitted.append(c["name"])
        if len(emitted) == 2:
            gate.set()  # 快家两张上墙后才放行慢家

    cards = B.run_scan(
        "主题", "", str(tmp_path),
        providers={"fast": lambda p: "", "slow": lambda p: ""},
        decompose_llm=lambda p: "",
        en_search=lambda q: [{"title": "t", "url": "u"}] * 4,
        zh_search=lambda q: [],
        own_search=lambda q: [{"title": "t", "url": "u"}],
        on_card=on_card)

    assert emitted == ["A", "B", "C"]
    assert [c["id"] for c in cards] == [1, 2, 3]
    a, b, _c = cards
    assert set(a["source_models"]) == {"fast", "slow"}  # 重名只并来源
    assert a["outlier"] is False and b["outlier"] is True
    assert a["en_hits"] == 4 and a["zh_hits"] == 0 and a["own_hits"] == 1
    assert a["novelty"] == "交叉空白" and a["gold"] is True  # en 热 × zh 真零 = 金标
    assert len(a["anchors"]) == 3


def test_run_scan_suppression_blocks_emit(tmp_path, monkeypatch):
    import blindspot as B

    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    (muse / "angle-feedback.json").write_text(
        json.dumps({normalize_name("B"): {"name": "B", "verdict": "已知"}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(B, "enumerate_cards",
                        lambda topic, f, p, tag, call: [_card("A", tag), _card("B", tag)])
    monkeypatch.setattr(B, "decompose_topic", lambda *a: ["f1"])
    monkeypatch.setattr(B, "_topic_zh_keyword", lambda *a: None)
    emitted = []
    cards = B.run_scan("主题", "", str(tmp_path), providers={"m": lambda p: ""},
                       decompose_llm=lambda p: "", en_search=lambda q: [],
                       zh_search=lambda q: [], own_search=None, on_card=emitted.append)
    assert [c["name"] for c in cards] == ["A"] and emitted[0]["name"] == "A"
    assert cards[0]["own_hits"] is None and cards[0]["novelty"] == "交叉空白"


def test_classify_novelty_quadrants():
    # (en_hits, zh_hits) -> 分类；金标 = 英热中冷
    assert classify_novelty(en_hits=5, zh_hits=6) == ("主流", False)
    assert classify_novelty(en_hits=2, zh_hits=1) == ("边缘有人做", False)
    assert classify_novelty(en_hits=0, zh_hits=0) == ("交叉空白", False)
    assert classify_novelty(en_hits=4, zh_hits=0) == ("交叉空白", True)
    assert classify_novelty(en_hits=3, zh_hits=None) == ("中文面未检", False)


def test_extract_json_from_noisy_output():
    noisy = '好的，以下是结果：\n```json\n{"cards": [{"name": "X"}]}\n```\n希望有帮助'
    assert extract_json(noisy) == {"cards": [{"name": "X"}]}


from blindspot import decompose_topic, enumerate_cards, ENUM_SCHEMA_HINT


class FakeLLM:
    """记录 prompt、按队列吐回复。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_decompose_returns_fundamentals_and_uses_persona():
    llm = FakeLLM([json.dumps({"fundamentals": ["根1", "根2", "根3"]})])
    out = decompose_topic("平台责任", profile="我熟悉法教义学", llm_call=llm)
    assert out == ["根1", "根2", "根3"]
    assert "第一性原理" in llm.prompts[0] and "我熟悉法教义学" in llm.prompts[0]


def test_enumerate_cards_parses_and_tags_model():
    reply = json.dumps(
        {
            "cards": [
                {
                    "type": "研究方法",
                    "name": "裁判文书量化",
                    "mechanism": "m",
                    "why_nonobvious": "w",
                    "steelman": "s",
                    "feasibility": "裁判文书网",
                    "questions": ["q1", "q2"],
                }
            ]
        }
    )
    llm = FakeLLM([reply])
    cards = enumerate_cards(
        topic="平台责任",
        fundamentals=["根1"],
        profile="",
        model_tag="deepseek",
        llm_call=llm,
    )
    assert cards[0]["source_models"] == ["deepseek"]
    assert cards[0]["type"] == "研究方法" and cards[0]["feasibility"] == "裁判文书网"
    # 提示词必须包含三类配额与 schema 约定
    assert all(t in llm.prompts[0] for t in ("学科视角", "理论框架", "研究方法"))
    assert ENUM_SCHEMA_HINT in llm.prompts[0]


def test_enumerate_cards_drops_malformed_entries():
    reply = json.dumps({"cards": [{"type": "理论框架", "name": "X", "mechanism": "m",
                                    "why_nonobvious": "w", "steelman": "s", "questions": ["q"]},
                                   {"name": "缺字段"}]})
    llm = FakeLLM([reply])
    cards = enumerate_cards("t", ["f"], "", "gemini", llm)
    assert len(cards) == 1 and cards[0]["name"] == "X"


from blindspot import run_scan, load_suppressed, record_feedback


def test_run_scan_end_to_end_offline(tmp_path):
    replies = {
        "decompose": json.dumps({"fundamentals": ["根1"]}),
        "deepseek": json.dumps({"cards": [
            {"type": "理论框架", "name": "交易成本", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q1"]},
            {"type": "研究方法", "name": "文书量化", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "feasibility": "裁判文书网", "questions": ["q2"]},
            {"type": "学科视角", "name": "组织社会学", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q3"]}]}),
        "gemini": json.dumps({"cards": [
            {"type": "理论框架", "name": "交易成本（TCE）", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q1"]},
            {"type": "学科视角", "name": "STS", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q4"]},
            {"type": "研究方法", "name": "比较法样本", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "feasibility": "域外判例库", "questions": ["q5"]}]}),
    }

    def llm_for(tag):
        return lambda prompt: replies[tag if tag in replies else "decompose"]

    emitted = []
    cards = run_scan(
        topic="平台责任",
        profile="画像",
        output_dir=str(tmp_path),
        providers={"deepseek": llm_for("deepseek"), "gemini": llm_for("gemini")},
        decompose_llm=llm_for("decompose"),
        en_search=lambda q: [{"title": "T", "url": "https://e.com/1"}] * 4,
        zh_search=lambda q: [],
        own_search=lambda q: [1, 2] if "交易成本" in q else [],
        on_card=emitted.append,
    )
    # 去重后 5 张；交易成本双模型共识、其余离群
    assert len(cards) == 5 and len(emitted) == 5
    byname = {c["name"]: c for c in cards}
    assert byname["交易成本"]["outlier"] is False and byname["STS"]["outlier"] is True
    # 自有语料面独立于新颖性判据：own_hits 记数，学界空白×自有有藏 → 已藏未用
    assert byname["交易成本"]["own_hits"] == 2 and byname["STS"]["own_hits"] == 0
    # 新颖性：en=4, zh=0 → 交叉空白 + 金标
    assert byname["交易成本"]["novelty"] == "交叉空白" and byname["交易成本"]["gold"] is True
    assert byname["交易成本"]["anchors"][0]["url"] == "https://e.com/1"
    # 三类齐备
    assert {c["type"] for c in cards} == set(CARD_TYPES)
    # 落盘四件
    d = tmp_path / "docs" / "agents" / "muse"
    assert (d / "perspectives.md").exists() and (d / "questions.md").exists()
    assert (d / "sources.md").exists() and (d / "profile.md").read_text(encoding="utf-8") == "画像"


def test_feedback_roundtrip_and_suppression(tmp_path):
    d = tmp_path / "docs" / "agents" / "muse"
    record_feedback(str(tmp_path), name="交易成本", verdict="已知")
    record_feedback(str(tmp_path), name="STS", verdict="新且值得深挖")
    sup = load_suppressed(str(tmp_path))
    assert normalize_name("交易成本") in sup and normalize_name("STS") not in sup
    data = json.loads((d / "angle-feedback.json").read_text(encoding="utf-8"))
    assert data[normalize_name("STS")]["verdict"] == "新且值得深挖"


def test_run_scan_zh_search_failure_degrades(tmp_path):
    def boom(q):
        raise RuntimeError("zsearch down")

    cards = run_scan(
        topic="t", profile="", output_dir=str(tmp_path),
        providers={"deepseek": lambda p: json.dumps({"cards": [
            {"type": "理论框架", "name": "X", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q"]}]})},
        decompose_llm=lambda p: json.dumps({"fundamentals": ["f"]}),
        en_search=lambda q: [], zh_search=boom, on_card=lambda c: None,
    )
    assert cards[0]["novelty"] == "中文面未检"


def test_extract_json_multi_object_picks_payload():
    # 思考前奏 + 正文 + 尾注三个对象：贪婪 {.*} 会整体解析失败，平衡扫描应取最大的正文对象
    noisy = '思考: {"step": 1} 好的，结果：{"cards": [{"name": "X", "type": "理论框架"}]} 附注 {"n": 2}'
    assert "cards" in extract_json(noisy)


def test_run_scan_all_providers_failing_raises(tmp_path):
    def bad_provider(prompt):
        raise RuntimeError("auth failed")

    with pytest.raises(RuntimeError, match="所有模型枚举均失败"):
        run_scan(
            topic="t", profile="", output_dir=str(tmp_path),
            providers={"deepseek": bad_provider, "gemini": bad_provider},
            decompose_llm=lambda p: json.dumps({"fundamentals": ["f"]}),
            en_search=lambda q: [], zh_search=lambda q: [], on_card=lambda c: None,
        )
