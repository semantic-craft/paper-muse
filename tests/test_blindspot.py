import json

import pytest

from blindspot import (
    normalize_name,
    dedupe_cards,
    mark_outliers,
    apply_suppression,
    classify_novelty,
    extract_json,
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
