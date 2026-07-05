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
