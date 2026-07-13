import json
from urllib.parse import parse_qs, urlparse

import pytest

from blindspot import (
    normalize_name,
    dedupe_cards,
    finalize_card_quality,
    mark_outliers,
    run_quality_tournament,
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


def _confirmed_cnki_empty():
    return {
        "hits": 0,
        "results": [],
        "evidence": [],
        "status": {"provider": "cnki", "state": "empty", "hits": 0},
    }


def test_normalize_name_strips_noise():
    assert normalize_name("  交易成本 理论（TCE） ") == normalize_name("交易成本理论(tce)")


def test_dedupe_merges_source_models():
    cards = dedupe_cards([_card("交易成本理论", "deepseek"), _card("交易成本理论（TCE）", "gemini")])
    assert len(cards) == 1
    assert set(cards[0]["source_models"]) == {"deepseek", "gemini"}


def test_dedupe_merges_angle_name_variants():
    cards = dedupe_cards(
        [
            _card("进化生物学", "deepseek"),
            _card("物种进化生物学（Evolutionary Biology）视角", "gemini"),
        ]
    )

    assert len(cards) == 1
    assert set(cards[0]["source_models"]) == {"deepseek", "gemini"}


def test_finalize_quality_embedding_merges_near_duplicates():
    cards = [
        _card("制度扩散理论", "deepseek"),
        _card("政策扩散框架", "gemini"),
        _card("交易成本理论", "openai"),
    ]

    def fake_embed(texts):
        assert len(texts) == 3
        return [[1, 0], [0.98, 0.02], [0, 1]]

    merged = finalize_card_quality(cards, embedding_fn=fake_embed, embedding_threshold=0.9)

    assert [c["name"] for c in merged] == ["制度扩散理论", "交易成本理论"]
    assert set(merged[0]["source_models"]) == {"deepseek", "gemini"}
    assert merged[0]["cluster_size"] == 2
    assert merged[0]["merged_angles"] == ["政策扩散框架"]
    assert merged[0]["outlier"] is False


def test_mark_outliers_only_single_proposer():
    cards = dedupe_cards([_card("A", "deepseek"), _card("A", "gemini"), _card("B", "openai")])
    cards = mark_outliers(cards)
    by = {c["name"]: c["outlier"] for c in cards}
    assert by["B"] is True and by["A"] is False
    assert "质量分" in cards[1]["outlier_reason"]


def test_mark_outliers_requires_high_quality_and_isolation():
    cards = [
        _card("英热中冷强卡", "deepseek", novelty="交叉空白", gold=True, en_hits=12, zh_hits=0),
        _card("主流弱卡", "gemini", novelty="主流", gold=False, en_hits=1, zh_hits=9),
    ]
    mark_outliers(cards)

    assert cards[0]["quality_score"] > cards[1]["quality_score"]
    assert cards[0]["outlier"] is True
    assert cards[1]["outlier"] is False
    # 确定性路径不产生真 Elo：没跑 tournament → elo_score 为 None（#51）
    assert cards[0]["elo_score"] is None and cards[1]["elo_score"] is None


def test_apply_suppression_filters_known():
    cards = [_card("A"), _card("B")]
    kept = apply_suppression(cards, suppressed={normalize_name("A")})
    assert [c["name"] for c in kept] == ["B"]


def test_apply_suppression_filters_angle_variants():
    cards = [_card("物种进化生物学（Evolutionary Biology）视角"), _card("交易成本理论")]
    kept = apply_suppression(cards, suppressed={normalize_name("进化生物学")})

    assert [c["name"] for c in kept] == ["交易成本理论"]


def test_extract_json_repairs_cjk_quote_and_missing_brace():
    # 冒烟实证的 deepseek 坏法：结尾英文引号写成 ” 且丢最后的 }（未闭合）
    bad = '{"fundamentals": ["问题一？", "激励对象是谁？”]'
    got = extract_json(bad)
    assert isinstance(got["fundamentals"], list) and len(got["fundamentals"]) == 2
    assert got["fundamentals"][0] == "问题一？"


def test_pick_decompose_llm_prefers_fast_stable():
    from blindspot import pick_decompose_llm
    ds, oa, gm = object(), object(), object()
    # 三家全在 → gemini（最快最稳，避 deepseek）
    assert pick_decompose_llm({"deepseek": ds, "openai": oa, "gemini": gm}) is gm
    # gemini 缺 → 退 openai，而非 deepseek
    assert pick_decompose_llm({"deepseek": ds, "openai": oa}) is oa
    # 只剩 deepseek → 用它
    assert pick_decompose_llm({"deepseek": ds}) is ds
    # 全自定义 → 回退第一个
    cx = object()
    assert pick_decompose_llm({"custom": cx}) is cx


def test_zh_name_core_shapes():
    from blindspot import _zh_name_core
    assert _zh_name_core("信息论与控制论（Cybernetics）视角") == "信息论与控制论"
    assert _zh_name_core("实验法学：图灵测试变体（The Intellectual Turing Test）") == "图灵测试变体"
    assert _zh_name_core("法律符号学（Legal Semiotics）") == "法律符号学"
    assert _zh_name_core("Purely English") == "Purely English"  # 中文核心为空 → 回退原名


def test_academic_en_search_uses_openalex_count_without_s2_key(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv("S2_API_KEY", raising=False)
    B.reset_retrieval_cache_stats()
    seen = []

    def fake_http(url, headers=None, timeout=20):
        seen.append(url)
        assert "api.openalex.org/works" in url
        assert parse_qs(urlparse(url).query)["search"] == [
            "Has anyone studied 平台数据?"
        ]
        return {
            "meta": {"count": 42},
            "results": [{"title": "OpenAlex paper", "doi": "https://doi.org/oa"}],
        }

    monkeypatch.setattr(B, "_http_json", fake_http)
    out = B.real_en_search(k=2)("平台数据")

    assert out["hits"] == 42
    assert out["results"] == [{"title": "OpenAlex paper", "url": "https://doi.org/oa"}]
    assert out["source"] == "openalex"
    assert out["query"] == "Has anyone studied 平台数据?"
    assert out["evidence"][0]["retrieval"]["query"] == "Has anyone studied 平台数据?"
    assert "semantic_scholar" in out["degraded"]
    assert len(seen) == 1


def test_academic_en_search_combines_s2_and_openalex(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "s2-test")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    B.reset_retrieval_cache_stats()
    seen = []

    def fake_http(url, headers=None, timeout=20):
        seen.append((url, headers or {}))
        if "semanticscholar.org" in url:
            assert (headers or {}).get("x-api-key") == "s2-test"
            assert parse_qs(urlparse(url).query)["query"] == [
                "Has anyone studied 平台数据?"
            ]
            return {
                "total": 7,
                "data": [{"title": "S2 paper", "url": "https://s2"}],
            }
        if "api.openalex.org/works" in url:
            assert parse_qs(urlparse(url).query)["search"] == [
                "Has anyone studied 平台数据?"
            ]
            return {
                "meta": {"count": 11},
                "results": [{"title": "OpenAlex paper", "id": "https://openalex/W1"}],
            }
        raise AssertionError(url)

    monkeypatch.setattr(B, "_http_json", fake_http)
    out = B.real_en_search(k=3)("平台数据")

    assert out["hits"] == 11
    assert out["source"] == "semantic_scholar+openalex"
    assert out["degraded"] is None
    assert [r["url"] for r in out["results"]] == ["https://s2", "https://openalex/W1"]
    assert len(seen) == 2


def test_novelty_uses_academic_total_not_anchor_count():
    import blindspot as B

    card = {"name": "复杂系统治理"}
    B._novelty_for(
        card,
        "平台治理",
        en_search=lambda _q: {
            "hits": 47,
            "results": [{"title": "A", "url": "u1"}, {"title": "B", "url": "u2"}],
            "evidence": [
                {
                    "id": "evr_a",
                    "source": {"title": "A", "url": "u1"},
                    "retrieval": {"provider": "openalex", "query": "q"},
                    "verification": {"status": "provider-retrieved", "degraded": False},
                }
            ],
            "source": "openalex",
            "degraded": "semantic_scholar: missing key",
        },
        zh_search=lambda _q: _confirmed_cnki_empty(),
        own_search=None,
        zh_keyword="著作权",
    )

    assert card["en_hits"] == 47
    assert card["en_source"] == "openalex"
    assert card["en_degraded"] == "semantic_scholar: missing key"
    assert card["evidence"][0]["id"] == "evr_a"
    assert card["gold"] is True


def test_cnki_empty_result_is_zero_hits(monkeypatch, tmp_path):
    # EMPTY_RESULT（零命中）≠ 未检：回 [] 让交叉空白/金矿判据可触发。
    # 真实形态（冒烟实证）：stdout 为空、YAML 错误块走 stderr、exit 66
    import blindspot as B
    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()

    class R:
        stdout = ""
        stderr = "ok: false\nerror:\n  code: EMPTY_RESULT\n  message: cnki search returned no data\n"

    monkeypatch.setattr(B.subprocess, "run", lambda *a, **k: R)
    result = B.real_cnki_search()("任意查询")
    assert result["hits"] == 0
    assert result["evidence"] == []
    assert result["status"]["state"] == "empty"


def test_cnki_results_are_normalized_to_evidence_refs(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "ok": True,
                "data": [
                    {
                        "id": "CJFD-2026-1",
                        "title": "平台治理研究",
                        "url": "https://kns.cnki.net/kcms/detail/CJFD-2026-1",
                        "year": "2026",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(B.subprocess, "run", lambda *args, **kwargs: Result)

    result = B.real_cnki_search(limit=3)("平台治理")

    assert result["hits"] == 1
    assert result["status"]["state"] == "ok"
    ref = result["evidence"][0]
    assert ref["source"]["kind"] == "cnki-record"
    assert ref["source"]["version"] == "2026"
    assert ref["retrieval"]["provider"] == "cnki"
    assert ref["retrieval"]["query"] == "平台治理"
    assert ref["relation"] == "discovery"


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        ("NO_BROWSER_SESSION", "authentication-required"),
        ("Browser session is required.", "authentication-required"),
        ("RATE_LIMIT", "rate-limited"),
        ("not-json", "bad-payload"),
        ("timeout", "timeout"),
        ("missing-command", "unavailable"),
    ],
)
def test_cnki_failures_have_distinct_structured_states(
    monkeypatch, tmp_path, failure, expected_state
):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()

    class R:
        stdout = ""
        stderr = f"ok: false\nerror:\n  code: {failure}\n"

    def run(*args, **kwargs):
        if failure == "timeout":
            raise B.subprocess.TimeoutExpired(args[0], timeout=90)
        if failure == "missing-command":
            raise FileNotFoundError("opencli not found")
        if failure == "not-json":
            R.stderr = "not-json"
        return R

    monkeypatch.setattr(B.subprocess, "run", run)

    result = B.real_cnki_search()("任意查询")

    assert result["status"]["state"] == expected_state
    assert result["hits"] == 0
    assert result["status"]["hits"] is None


def test_run_scan_streaming_merge_and_enrich(tmp_path, monkeypatch):
    """流式两段出卡：快家先上墙（慢家未返回时 on_card 已发），重名合并翻离群，徽标异步补挂全齐。"""
    import threading as th

    import blindspot as B

    gate = th.Event()

    def fake_enum(topic, fundamentals, profile, tag, call, puzzle=""):
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
        en_search=lambda q: {
            "hits": 4,
            "results": [{"title": "t", "url": "u"}],
            "evidence": [{
                "id": "evr_en",
                "source": {"title": "t", "url": "u"},
                "retrieval": {"provider": "openalex", "query": q},
                "verification": {"status": "provider-retrieved", "degraded": False},
            }],
            "source": "openalex",
            "degraded": None,
        },
        zh_search=lambda q: _confirmed_cnki_empty(),
        own_search=lambda q: {
            "hits": 1,
            "results": [],
            "evidence": [],
            "status": {"provider": "zsearch", "state": "ok", "hits": 1},
        },
        on_card=on_card)

    assert emitted == ["A", "B", "C"]
    assert [c["id"] for c in cards] == [1, 2, 3]
    a, b, _c = cards
    assert set(a["source_models"]) == {"fast", "slow"}  # 重名只并来源
    assert a["outlier"] is False and b["outlier"] is True
    assert a["en_hits"] == 4 and a["zh_hits"] == 0 and a["own_hits"] == 1
    assert a["novelty"] == "交叉空白" and a["gold"] is True  # en 热 × zh 真零 = 金标
    assert len(a["evidence"]) == 1  # en 面 EvidenceRef 贯穿到卡


def test_emitted_card_carries_merge_target_keys_from_preset(tmp_path, monkeypatch):
    """回归：上墙后原地更新「只换值不加键」。_merge_card_into 会写 merged_angles / feasibility，
    这两键必须在 fresh 预置里就存在——否则第二家枚举出同角度卡时给已上墙的活卡片新增键，
    与 /scan/status 并发序列化撞 RuntimeError: dictionary changed size during iteration。
    只用单家：在 on_card（卡刚上墙、任何合并之前）即断言两键已存在，确定性、无时序依赖。"""
    import blindspot as B

    monkeypatch.setattr(B, "decompose_topic", lambda *a: ["f1"])
    monkeypatch.setattr(B, "_topic_zh_keyword", lambda *a: "kw")
    keys_at_emit = {}

    def on_card(c):
        keys_at_emit[c["name"]] = set(c.keys())

    B.run_scan(
        "主题", "", str(tmp_path),
        providers={"m": lambda p: json.dumps({"cards": [
            {"type": "学科视角", "name": "熵增视角", "mechanism": "m",
             "why_nonobvious": "w", "steelman": "s", "questions": ["q"]}]})},
        decompose_llm=lambda p: "",
        en_search=lambda q: [], zh_search=lambda q: [], own_search=None,
        on_card=on_card)

    assert "merged_angles" in keys_at_emit["熵增视角"]
    assert "feasibility" in keys_at_emit["熵增视角"]


def test_run_scan_merges_angle_variants_in_live_wall(tmp_path, monkeypatch):
    import blindspot as B

    monkeypatch.setattr(B, "decompose_topic", lambda *a: ["f1"])
    monkeypatch.setattr(B, "_topic_zh_keyword", lambda *a: "著作权")
    emitted = []
    cards = B.run_scan(
        "主题", "", str(tmp_path),
        providers={
            "deepseek": lambda p: json.dumps({"cards": [
                {"type": "学科视角", "name": "进化生物学", "mechanism": "m",
                 "why_nonobvious": "w", "steelman": "s", "questions": ["q"]}
            ]}),
            "gemini": lambda p: json.dumps({"cards": [
                {"type": "学科视角", "name": "物种进化生物学视角", "mechanism": "m",
                 "why_nonobvious": "w", "steelman": "s", "questions": ["q2"]}
            ]}),
        },
        decompose_llm=lambda p: "",
        en_search=lambda q: [],
        zh_search=lambda q: [],
        own_search=None,
        on_card=emitted.append,
    )

    assert len(emitted) == 1
    assert len(cards) == 1
    assert set(cards[0]["source_models"]) == {"deepseek", "gemini"}
    assert cards[0]["outlier"] is False


def test_run_scan_suppression_blocks_emit(tmp_path, monkeypatch):
    import blindspot as B

    muse = tmp_path / "docs" / "agents" / "muse"
    muse.mkdir(parents=True)
    (muse / "angle-feedback.json").write_text(
        json.dumps({normalize_name("B"): {"name": "B", "verdict": "已知"}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(B, "enumerate_cards",
                        lambda topic, f, p, tag, call, puzzle="": [_card("A", tag), _card("B", tag)])
    monkeypatch.setattr(B, "decompose_topic", lambda *a: ["f1"])
    monkeypatch.setattr(B, "_topic_zh_keyword", lambda *a: None)
    emitted = []
    cards = B.run_scan("主题", "", str(tmp_path), providers={"m": lambda p: ""},
                       decompose_llm=lambda p: "", en_search=lambda q: [],
                       zh_search=lambda q: _confirmed_cnki_empty(), own_search=None,
                       on_card=emitted.append)
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
    assert "这个任务到底要解决什么问题" in llm.prompts[0]


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


from blindspot import normalize_vs_profile


def test_normalize_vs_profile_coerces_labels_and_drops_junk():
    raw = [
        {"element": "立场", "note": "你默认\n权利本位"},   # 中文标签 → key，note 换行折叠
        {"element": "field", "note": "跨到信息论"},          # 直接给键也接受
        {"element": "血型", "note": "非画像要素"},           # 未知 element → 丢
        {"element": "熟悉", "note": "   "},                  # note 空 → 丢
        "not-a-dict",                                        # 非对象 → 丢
    ]
    out = normalize_vs_profile(raw)
    assert out == [
        {"element": "stance", "note": "你默认 权利本位"},
        {"element": "field", "note": "跨到信息论"},
    ]
    # 单对象也收进列表；非列表/非对象回 []
    assert normalize_vs_profile({"element": "领域", "note": "n"}) == [{"element": "field", "note": "n"}]
    assert normalize_vs_profile(None) == [] and normalize_vs_profile("x") == []


def _enum_reply(vs_profile):
    card = {"type": "学科视角", "name": "熵与信息", "mechanism": "m",
            "why_nonobvious": "w", "steelman": "s", "questions": ["q"]}
    if vs_profile is not None:
        card["vs_profile"] = vs_profile
    return json.dumps({"cards": [card]})


def test_enumerate_cards_emits_normalized_vs_profile_with_profile():
    llm = FakeLLM([_enum_reply([{"element": "立场", "note": "你受法教义学训练"}])])
    cards = enumerate_cards("平台责任", ["根1"], "立场：权利本位", "gemini", llm)
    assert cards[0]["vs_profile"] == [{"element": "stance", "note": "你受法教义学训练"}]
    # 有画像时提示词才要 vs_profile，schema 里带该字段
    assert "vs_profile" in ENUM_SCHEMA_HINT
    assert "相对画像哪一条" in llm.prompts[0]


def test_enumerate_cards_strips_vs_profile_without_profile():
    # 无画像：即便 LLM 硬塞 vs_profile，也剥掉（无参照系不许凭空绑），且提示词不索取
    llm = FakeLLM([_enum_reply([{"element": "立场", "note": "凭空绑"}])])
    cards = enumerate_cards("平台责任", ["根1"], "", "gemini", llm)
    assert "vs_profile" not in cards[0]
    assert "相对画像哪一条" not in llm.prompts[0]
    assert "未提供" in llm.prompts[0]


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
        en_search=lambda q: {
            "hits": 4,
            "results": [{"title": "T", "url": "https://e.com/1"}],
            "evidence": [
                {
                    "id": "evr_e2e",
                    "source": {"title": "T", "url": "https://e.com/1"},
                    "retrieval": {"provider": "in-memory", "query": q},
                    "verification": {"status": "provider-retrieved", "degraded": False},
                }
            ],
            "source": "in-memory",
            "degraded": None,
        },
        zh_search=lambda q: _confirmed_cnki_empty(),
        own_search=lambda q: (
            {"hits": 2, "results": [], "evidence": [],
             "status": {"provider": "zsearch", "state": "ok", "hits": 2}}
            if "交易成本" in q else
            {"hits": 0, "results": [], "evidence": [],
             "status": {"provider": "zsearch", "state": "empty", "hits": 0}}
        ),
        on_card=emitted.append,
    )
    # 去重后 5 张；交易成本双模型共识，离群需同时满足孤立 + 质量分本轮高位
    assert len(cards) == 5 and len(emitted) == 5
    byname = {c["name"]: c for c in cards}
    assert byname["交易成本"]["outlier"] is False
    assert byname["文书量化"]["outlier"] is True and byname["STS"]["outlier"] is False
    assert "质量分" in byname["文书量化"]["outlier_reason"]
    # 自有语料面独立于新颖性判据：own_hits 记数，学界空白×自有有藏 → 已藏未用
    assert byname["交易成本"]["own_hits"] == 2 and byname["STS"]["own_hits"] == 0
    # 新颖性：en=4, zh=0 → 交叉空白 + 金标
    assert byname["交易成本"]["novelty"] == "交叉空白" and byname["交易成本"]["gold"] is True
    assert byname["交易成本"]["evidence"][0]["id"] == "evr_e2e"
    # 三类齐备
    assert {c["type"] for c in cards} == set(CARD_TYPES)
    # 落盘四件
    d = tmp_path / "docs" / "agents" / "muse"
    assert (d / "perspectives.md").exists() and (d / "questions.md").exists()
    assert (d / "sources.md").exists() and (d / "profile.md").read_text(encoding="utf-8") == "画像"
    questions_text = (d / "questions.md").read_text(encoding="utf-8")
    assert "## 交易成本\n- q1" in questions_text  # 既有拷问条目保持原样
    assert "### 行动" in questions_text
    assert "- 目标（理想论证）：把「交易成本」从切入点推进为可辩护的论文论证" in questions_text
    assert "- 障碍：s" in questions_text  # 优先取该卡的最强反驳 steelman
    assert "- if–then 验收门槛：" in questions_text
    sources_text = (d / "sources.md").read_text(encoding="utf-8")
    assert "evr_e2e" in sources_text
    metadata = [
        json.loads(line.removeprefix("  - EvidenceRef-JSON: "))
        for line in sources_text.splitlines()
        if line.startswith("  - EvidenceRef-JSON: ")
    ]
    assert byname["交易成本"]["evidence"][0] in metadata


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


def test_run_scan_only_treats_confirmed_cnki_empty_as_gold(tmp_path):
    card_reply = json.dumps(
        {
            "cards": [
                {
                    "type": "理论框架",
                    "name": "制度空白",
                    "mechanism": "m",
                    "why_nonobvious": "w",
                    "steelman": "s",
                    "questions": ["q"],
                }
            ]
        }
    )
    en_payload = {
        "hits": 12,
        "results": [{"title": "English", "url": "https://en.example"}],
        "evidence": [
            {
                "id": "evr_en",
                "source": {"title": "English", "url": "https://en.example"},
                "retrieval": {"provider": "openalex"},
                "verification": {"status": "provider-retrieved"},
            }
        ],
        "status": {"state": "ok"},
    }
    own_payload = {
        "hits": 1,
        "results": [{"title": "Owned", "url": "zotero://owned"}],
        "evidence": [
            {
                "id": "evr_own",
                "source": {"title": "Owned", "url": "zotero://owned"},
                "retrieval": {"provider": "zsearch"},
                "verification": {"status": "unresolved"},
            }
        ],
        "status": {"state": "ok", "provider": "zsearch"},
    }

    def scan(output_dir, zh_payload):
        return run_scan(
            topic="平台责任",
            profile="",
            output_dir=str(output_dir),
            providers={"deepseek": lambda _prompt: card_reply},
            decompose_llm=lambda _prompt: json.dumps({"fundamentals": ["f"]}),
            en_search=lambda _query: en_payload,
            zh_search=lambda _query: zh_payload,
            own_search=lambda _query: own_payload,
            on_card=lambda _card: None,
        )[0]

    confirmed = scan(
        tmp_path / "confirmed",
        {
            "hits": 0,
            "evidence": [],
            "status": {"state": "empty", "provider": "cnki"},
        },
    )
    degraded = scan(
        tmp_path / "degraded",
        {
            "hits": 0,
            "evidence": [],
            "status": {
                "state": "authentication-required",
                "provider": "cnki",
                "message": "browser session missing",
            },
        },
    )
    unknown = scan(tmp_path / "unknown", [])

    assert confirmed["zh_hits"] == 0
    assert confirmed["gold"] is True
    assert confirmed["own_hits"] == 1
    assert confirmed["zh_status"]["state"] == "empty"
    assert confirmed["own_status"]["state"] == "ok"
    assert {ref["id"] for ref in confirmed["evidence"]} == {"evr_en", "evr_own"}
    assert "已确认零命中" in confirmed["novelty_reason"]

    assert degraded["zh_hits"] is None
    assert degraded["novelty"] == "中文面未检"
    assert degraded["gold"] is False
    assert degraded["zh_status"]["state"] == "authentication-required"
    assert "不判定中文真零或金标" in degraded["novelty_reason"]
    assert unknown["zh_hits"] is None
    assert unknown["gold"] is False
    assert unknown["zh_status"]["state"] == "unknown"


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


# ---- 检索缓存（#19）----

def test_cached_search_normalizes_query_and_tracks_stats(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()
    calls = []

    def search(q):
        calls.append(q)
        return [{"title": "T", "url": "https://e"}]

    cached = B._cached_search("en", 5, "test", ttl=60, search=search)

    assert cached(" 平台   责任 ") == [{"title": "T", "url": "https://e"}]
    assert cached("平台 责任") == [{"title": "T", "url": "https://e"}]
    assert calls == [" 平台   责任 "]
    assert B.retrieval_cache_stats()["en"] == {"hits": 1, "misses": 1, "stores": 1}


def test_cache_base_dir_honors_paper_muse_cache_dir_exact(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    assert B._cache_base_dir() == tmp_path / "cache"


def test_cnki_true_empty_is_cached_but_session_errors_are_not(monkeypatch, tmp_path):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()
    calls = []

    class Empty:
        stdout = ""
        stderr = "ok: false\nerror:\n  code: EMPTY_RESULT\n"

    def empty_run(*args, **kwargs):
        calls.append("empty")
        return Empty

    monkeypatch.setattr(B.subprocess, "run", empty_run)
    search = B.real_cnki_search(limit=3)
    assert search("不存在的题")["status"]["state"] == "empty"
    assert search("不存在的题")["status"]["state"] == "empty"
    assert calls == ["empty"]
    assert B.retrieval_cache_stats()["cnki"] == {"hits": 1, "misses": 1, "stores": 1}

    class NoSession:
        stdout = ""
        stderr = "ok: false\nerror:\n  code: NO_BROWSER_SESSION\n"

    calls.clear()

    def no_session_run(*args, **kwargs):
        calls.append("no-session")
        return NoSession

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache2"))
    B.reset_retrieval_cache_stats()
    monkeypatch.setattr(B.subprocess, "run", no_session_run)
    search = B.real_cnki_search(limit=3)
    assert search("需要浏览器")["status"]["state"] == "authentication-required"
    assert search("需要浏览器")["status"]["state"] == "authentication-required"
    assert calls == ["no-session", "no-session"]
    assert B.retrieval_cache_stats()["cnki"] == {"hits": 0, "misses": 2, "stores": 0}


def test_zsearch_keeps_fast_cached_count_and_returns_context_evidence(
    monkeypatch, tmp_path
):
    import blindspot as B
    from urllib import error

    from zotero_local import ZoteroLocalAdapter

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            [
                {
                    "key": "ABCD1234",
                    "title": "我的平台治理论文",
                    "url": "zotero://select/library/items/ABCD1234",
                }
            ],
            ensure_ascii=False,
        )

    def run(*args, **kwargs):
        calls.append(args[0])
        return Result

    monkeypatch.setattr(B.subprocess, "run", run)
    def unavailable(_request, timeout):
        raise error.URLError("fixture: Zotero unavailable")

    search = B.real_own_search(
        limit=3, zotero_adapter=ZoteroLocalAdapter(opener=unavailable)
    )

    first = search("平台治理")
    second = search("平台治理")

    assert first["hits"] == second["hits"] == 1
    assert first["status"]["state"] == "ok"
    assert calls == [["zsearch", "query", "平台治理", "-k", "3", "--json"]]
    assert B.retrieval_cache_stats()["own"] == {"hits": 1, "misses": 1, "stores": 1}
    ref = first["evidence"][0]
    assert ref["source"]["kind"] == "library-document"
    assert ref["retrieval"]["provider"] == "zsearch"
    assert ref["retrieval"]["source_id"] == "ABCD1234"
    assert ref["relation"] == "context"
    assert ref["verification"]["status"] == "unresolved"
    assert ref["verification"]["degraded"] is True


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        ("timeout", "timeout"),
        ("missing-command", "unavailable"),
        ("bad-json", "bad-payload"),
    ],
)
def test_zsearch_failures_are_structured_and_not_cached(
    monkeypatch, tmp_path, failure, expected_state
):
    import blindspot as B

    monkeypatch.setenv("PAPER_MUSE_CACHE_DIR", str(tmp_path / "cache"))
    B.reset_retrieval_cache_stats()
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = "not-json"

    def run(*args, **kwargs):
        calls.append(args[0])
        if failure == "timeout":
            raise B.subprocess.TimeoutExpired(args[0], timeout=30)
        if failure == "missing-command":
            raise FileNotFoundError("zsearch not found")
        return Result

    monkeypatch.setattr(B.subprocess, "run", run)
    search = B.real_own_search(limit=3)

    assert search("平台治理")["status"]["state"] == expected_state
    assert search("平台治理")["status"]["state"] == expected_state
    assert len(calls) == 2
    assert B.retrieval_cache_stats()["own"] == {"hits": 0, "misses": 2, "stores": 0}


# ---- 机器级画像 researcher.md（ADR-0001 / #3）----
from blindspot import (
    load_researcher_profile,
    save_researcher_profile,
    profile_text_from_dict,
    profile_dict_from_text,
    researcher_md_path,
)


def test_researcher_md_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert researcher_md_path() == tmp_path / "paper-muse" / "researcher.md"


def test_researcher_md_path_honors_paper_muse_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_MUSE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert researcher_md_path() == tmp_path / "config" / "researcher.md"


def test_profile_text_dict_roundtrip_excludes_empty_and_puzzle():
    d = {"field": "中文法学", "stance": "权利本位", "familiar": "比例原则"}
    text = profile_text_from_dict(d)
    assert text == "领域：中文法学\n立场：权利本位\n熟悉：比例原则"
    assert profile_dict_from_text(text) == d
    # 空字段不落行；未知标签（如手改混入的「困惑」）解析时忽略，不进画像三要素
    assert profile_text_from_dict({"field": "X", "stance": "", "familiar": ""}) == "领域：X"
    assert profile_dict_from_text("困惑：想不通\n领域：X") == {"field": "X", "stance": "", "familiar": ""}


def test_researcher_profile_save_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPER_MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_researcher_profile() == {"field": "", "stance": "", "familiar": ""}  # 缺文件回全空
    save_researcher_profile({"field": "数据法", "stance": "法教义学", "familiar": "反垄断法"})
    assert load_researcher_profile() == {"field": "数据法", "stance": "法教义学", "familiar": "反垄断法"}


def test_profile_value_with_newline_stays_single_line_no_injection():
    """值内换行归一为单空格：既不截断续行，也不让续行伪装成别的字段标签（审核 F2/F3）。"""
    # 续行以「立场：」开头——归一前会串改 stance；归一后整段留在 familiar
    d = {"field": "数据法", "stance": "", "familiar": "反垄断\n立场：钓鱼"}
    text = profile_text_from_dict(d)
    assert "\n立场：" not in text                      # 不产生第二个可被误解析的标签行
    back = profile_dict_from_text(text)
    assert back == {"field": "数据法", "stance": "", "familiar": "反垄断 立场：钓鱼"}


def test_run_scan_puzzle_feeds_prompts_but_not_profile_md(tmp_path):
    """困惑入 decompose/enumerate 提示词，但绝不落 profile.md（困惑不污染画像，#3 验收）。"""
    seen = []

    def capture_provider(prompt):
        seen.append(prompt)
        return json.dumps({"cards": [
            {"type": "理论框架", "name": "X", "mechanism": "m", "why_nonobvious": "w",
             "steelman": "s", "questions": ["q"]}]})

    def capture_decompose(prompt):
        seen.append(prompt)
        return json.dumps({"fundamentals": ["f"]})

    run_scan(
        topic="平台数据", profile="领域：中文法学", puzzle="怕落进数据确权红海",
        output_dir=str(tmp_path), providers={"deepseek": capture_provider},
        decompose_llm=capture_decompose,
        en_search=lambda q: [], zh_search=lambda q: [], on_card=lambda c: None,
    )
    blob = "\n".join(seen)
    assert "怕落进数据确权红海" in blob          # 困惑喂进提示词
    profile_md = (tmp_path / "docs" / "agents" / "muse" / "profile.md").read_text(encoding="utf-8")
    assert profile_md == "领域：中文法学"          # 快照 = 画像，困惑不在其中
    assert "怕落进" not in profile_md


# ---- #51 校准 quality / Proximity / 真 Elo ----

def test_finalize_records_proximity_basis_full_semantics():
    """Proximity 用完整卡片语义并记录 basis：无 embedding_fn → lexical-fallback（不再仅名称）。"""
    cards = [_card("控制论视角", "m1", mechanism="反馈调节"),
             _card("博弈论方法", "m2", mechanism="策略均衡")]
    out = finalize_card_quality([dict(c) for c in cards])
    assert all(c["proximity_basis"] == "lexical-fallback" for c in out)
    # 真 embedding_fn → basis=embedding
    out2 = finalize_card_quality([dict(c) for c in cards],
                                 embedding_fn=lambda texts: [[float(len(t)), 1.0] for t in texts])
    assert all(c["proximity_basis"] == "embedding" for c in out2)


def test_quality_tournament_only_competitors_get_elo_and_matches_recorded():
    """真 pairwise tournament：只有实际参赛的卡获得 elo_score；每场 match 有理由记录。"""
    cards = [_card(n, "m1") for n in ("A", "B", "C")]
    mark_outliers(cards)                       # 先有确定性 quality_score
    for c in cards:
        assert c["elo_score"] is None          # 赛前无真 Elo
    matches = []
    # judge：名字靠前者胜（确定性）
    judge = lambda a, b: {"winner": "a" if a["name"] < b["name"] else "b",
                          "reason": f"{a['name']} vs {b['name']}"}
    competitors = run_quality_tournament(cards, judge, max_candidates=3, on_match=matches.append)
    assert len(competitors) == 3
    assert all(isinstance(c["elo_score"], int) and c["tournament_matches"] > 0 for c in competitors)
    assert cards[0]["elo_score"] > cards[2]["elo_score"]      # A 全胜 > C 全败
    assert len(matches) == 3 and all(m["reason"] for m in matches)


def test_quality_tournament_respects_candidate_and_match_budget():
    cards = [_card(str(i), "m1") for i in range(10)]
    mark_outliers(cards)
    n_matches = []
    run_quality_tournament(cards, lambda a, b: {"winner": "tie"},
                           max_candidates=4, max_matches=2, on_match=n_matches.append)
    assert len(n_matches) == 2                                # 预算封顶（墙钟/费用代理）
    scored = [c for c in cards if isinstance(c.get("elo_score"), int)]
    assert len(scored) <= 4                                   # 候选集有界


def test_quality_tournament_judge_error_skips_match_not_crash():
    cards = [_card("A", "m1"), _card("B", "m1")]
    mark_outliers(cards)

    def boom(a, b):
        raise RuntimeError("judge down")

    competitors = run_quality_tournament(cards, boom, max_candidates=2)
    assert competitors == [] and all(c["elo_score"] is None for c in cards)   # 无有效对局→无 Elo


def test_gold_outlier_selectivity_not_all_labels_full():
    """固定样例：应为/不应为 gold-outlier 各有——防「标签全满而测试仍绿」回归。"""
    cards = [
        _card("英热中冷孤例", "m1", novelty="交叉空白", gold=True, en_hits=12, zh_hits=0),   # 应 outlier
        _card("主流共识", "m2", novelty="主流", gold=False, en_hits=1),
        _card("主流共识", "m3", novelty="主流", gold=False, en_hits=1),                       # 双模型共识 → 不 outlier
    ]
    merged = finalize_card_quality(cards)
    golds = [c for c in merged if c.get("gold")]
    outliers = [c for c in merged if c.get("outlier")]
    assert 0 < len(golds) < len(merged)        # 不是全 gold
    assert 0 < len(outliers) < len(merged)     # 不是全 outlier
