# 盲区扫描引擎（两幕剧子计划 1/4）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 无头实现 spec v2 §4 的盲区扫描：第一性原理拆解 → 多模型三类卡枚举 → 去重/离群/抑制 → 新颖性三角定位（英文 web + zsearch 中文/自有语料）→ 流式出卡 → 七件套落盘 + muse_server `/scan` 接口。

**Architecture:** 新模块 `blindspot.py`（纯引擎，依赖注入 LM/检索便于离线测试）+ `muse_server.py` 加 `/scan` 三接口（独立于圆桌 SESSION 的 SCAN 状态，轮询全量卡片列表即"增量"——ponytail：1 秒轮询 + 全量列表，不上 SSE）。真实 LM 走 litellm（已是依赖），英文检索复用已合入的 `PerplexitySearchRM`，中文/自有面 subprocess 调 `zsearch`。

**Tech Stack:** Python 3.11（`.venv/bin/python`）、litellm、requests、pytest（已装）、FastAPI（已装）、zsearch CLI（`/opt/homebrew/bin/zsearch`，已装）。

** 上游文档：** spec = `docs/superpowers/specs/2026-07-05-muse-two-act-design.md` §4/§8/§12。姊妹计划（后续另立）：2/4 web 画布 UI、3/4 对抗幕引擎、4/4 圆桌钉死席位。

**分支：** 在 main 上新建 `feat/blindspot-engine`（第一个任务里做）。

**File Structure：**
- Create: `blindspot.py` — 引擎：数据模型、prompts、拆解/枚举/合并/新颖性、落盘、`run_scan()`、`__main__` CLI
- Modify: `muse_server.py` — `/scan` `/scan/status` `/scan/feedback` + SCAN 状态
- Create: `tests/test_blindspot.py` — 全离线单测（注入假 LM/假检索）
- （不动 app/、不动 knowledge_storm/ 既有代码）

**persona 说明：** 第一性原理提示词目前用要旨转述版（回到根本/拆最小可验证单元/答为什么）；用户提供原文后只需替换 `blindspot.py` 顶部 `FIRST_PRINCIPLES_PERSONA` 常量。

---

### Task 1: 分支 + blindspot.py 骨架（纯函数层，TDD）

**Files:**
- Create: `blindspot.py`
- Create: `tests/test_blindspot.py`

- [ ] **Step 1: 建分支**

```bash
cd /Users/xianweizhang/Projects/paper-muse && git checkout -b feat/blindspot-engine
```

- [ ] **Step 2: 写失败测试**（`tests/test_blindspot.py`，全离线）

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_blindspot.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindspot'`（conftest 已把仓库根加进 sys.path，故直接 import 顶层模块可行）。

- [ ] **Step 4: 实现骨架**（`blindspot.py`）

```python
"""
盲区扫描引擎（spec v2 §4）：第一性原理拆解 → 多模型三类卡枚举 →
去重/离群/抑制 → 新颖性三角定位（英文 web + zsearch 中文/自有语料）→ 流式出卡 → 落盘。

纯引擎，LM 与检索全部依赖注入，便于离线测试；真实接线见文件末尾 real_* 系列与 CLI。
"""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

# ---- persona（用户提供原文后整体替换本常量）----
FIRST_PRINCIPLES_PERSONA = (
    "你是第一性原理思考者：回到问题的根本，把问题拆解到最小可验证单元，"
    "永远追问『为什么成立』而不是『怎么做』；拒绝沿袭现成框架的惯性。"
)

CARD_TYPES = ["学科视角", "理论框架", "研究方法"]


# ---- 纯函数层 ----

def normalize_name(name: str) -> str:
    s = re.sub(r"[\s（）()【】\[\]\-—·]", "", name.strip().lower())
    return s


def extract_json(text: str):
    """从可能带说明文字/代码围栏的模型输出里抠出第一个 JSON 对象。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"输出中未找到 JSON：{text[:200]}")
    return json.loads(m.group(0))


def dedupe_cards(cards: list) -> list:
    merged = {}
    for c in cards:
        key = normalize_name(c["name"])
        if key in merged:
            models = merged[key]["source_models"]
            merged[key]["source_models"] = sorted(set(models) | set(c["source_models"]))
        else:
            merged[key] = dict(c)
    return list(merged.values())


def mark_outliers(cards: list) -> list:
    for c in cards:
        c["outlier"] = len(c["source_models"]) == 1
    return cards


def apply_suppression(cards: list, suppressed: set) -> list:
    return [c for c in cards if normalize_name(c["name"]) not in suppressed]


def classify_novelty(en_hits, zh_hits):
    """→ (分类, 是否金标)。zh_hits=None 表示 zsearch 不可用（明示未检，不装懂）。"""
    if zh_hits is None:
        return ("中文面未检", False)
    if zh_hits >= 3:
        return ("主流", False)
    if zh_hits >= 1:
        return ("边缘有人做", False)
    # zh_hits == 0
    gold = en_hits >= 3  # 英热中冷 = 引入型创新机会
    return ("交叉空白", gold)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_blindspot.py -v
```

Expected: 6 passed。

- [ ] **Step 6: Commit**

```bash
git add blindspot.py tests/test_blindspot.py
git commit -m "feat: 盲区扫描引擎骨架（去重/离群/抑制/新颖性分类纯函数，TDD）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 提示词 + 拆解与枚举（注入式 LM，TDD）

**Files:**
- Modify: `blindspot.py`
- Modify: `tests/test_blindspot.py`

- [ ] **Step 1: 追加失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_blindspot.py -v`
Expected: `ImportError: cannot import name 'decompose_topic'`。

- [ ] **Step 3: 实现**（`blindspot.py` 追加）

```python
ENUM_SCHEMA_HINT = (
    '只输出 JSON：{"cards": [{"type": "学科视角|理论框架|研究方法", "name": "...", '
    '"mechanism": "一句话机制", "why_nonobvious": "为什么对该研究者非显而易见", '
    '"steelman": "最强反驳：哪类审稿人会怎么打", "feasibility": "方法卡必填：数据从哪来", '
    '"questions": ["1-2个拷问句"]}]}'
)

REQUIRED_CARD_FIELDS = {"type", "name", "mechanism", "why_nonobvious", "steelman", "questions"}


def decompose_topic(topic: str, profile: str, llm_call) -> list:
    prompt = (
        f"{FIRST_PRINCIPLES_PERSONA}\n\n"
        f"研究者画像（可能为空）：{profile}\n"
        f"论文主题/困惑：{topic}\n\n"
        "用第一性原理把它拆成 3-5 个根本问题（最小可验证、互相独立、直指本质）。"
        '只输出 JSON：{"fundamentals": ["...", "..."]}'
    )
    return extract_json(llm_call(prompt))["fundamentals"]


def enumerate_cards(topic: str, fundamentals: list, profile: str, model_tag: str, llm_call) -> list:
    prompt = (
        "你要为一篇中文法学论文勘探非显而易见的切入点。跨学科越远越好，但必须论证适配性。\n"
        f"主题：{topic}\n根本问题：{json.dumps(fundamentals, ensure_ascii=False)}\n"
        f"研究者画像（『非显而易见』以此为参照系）：{profile or '未提供'}\n\n"
        "硬性配额：三类卡各至少 2 张——学科视角（其他学科怎么看这个问题）、"
        "理论框架（具体理论及其机制）、研究方法（实证/比较法/计算法学等，必附 feasibility 数据来源）。"
        "卡片之间必须彼此截然不同（学科、方法论、规范/实证、时间尺度错开），拒绝同一角度的变体。\n"
        + ENUM_SCHEMA_HINT
    )
    raw = extract_json(llm_call(prompt)).get("cards", [])
    cards = []
    for c in raw:
        if not REQUIRED_CARD_FIELDS <= set(c):
            continue
        c["source_models"] = [model_tag]
        cards.append(c)
    return cards
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_blindspot.py -v`　Expected: 9 passed。

- [ ] **Step 5: Commit**

```bash
git add blindspot.py tests/test_blindspot.py
git commit -m "feat: 盲区扫描拆解与枚举（第一性原理 persona + 三类卡配额 prompt）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: run_scan 编排 + 落盘（全离线集成测试）

**Files:**
- Modify: `blindspot.py`
- Modify: `tests/test_blindspot.py`

- [ ] **Step 1: 追加失败测试**

```python
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
        on_card=emitted.append,
    )
    # 去重后 5 张；交易成本双模型共识、其余离群
    assert len(cards) == 5 and len(emitted) == 5
    byname = {c["name"]: c for c in cards}
    assert byname["交易成本"]["outlier"] is False and byname["STS"]["outlier"] is True
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
```

- [ ] **Step 2: 跑测试确认失败**　Expected: `ImportError: cannot import name 'run_scan'`。

- [ ] **Step 3: 实现**（`blindspot.py` 追加）

```python
MUSE_SUBDIR = os.path.join("docs", "agents", "muse")


def _muse_dir(output_dir: str) -> Path:
    d = Path(output_dir) / MUSE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_suppressed(output_dir: str) -> set:
    f = _muse_dir(output_dir) / "angle-feedback.json"
    if not f.exists():
        return set()
    data = json.loads(f.read_text(encoding="utf-8"))
    return {k for k, v in data.items() if v.get("verdict") == "已知"}


def record_feedback(output_dir: str, name: str, verdict: str):
    f = _muse_dir(output_dir) / "angle-feedback.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    data[normalize_name(name)] = {"name": name, "verdict": verdict}
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _novelty_for(card, topic, en_search, zh_search):
    query = f"{card['name']} {topic}"
    try:
        en = en_search(query) or []
    except Exception:
        en = []
    try:
        zh = zh_search(query)
        zh_hits = len(zh or [])
    except Exception:
        zh_hits = None  # 中文面未检，明示不装懂
    card["novelty"], card["gold"] = classify_novelty(len(en), zh_hits)
    card["zh_hits"] = zh_hits
    card["en_hits"] = len(en)
    card["anchors"] = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in en[:3]]
    return card


def _write_outputs(output_dir, topic, profile, cards):
    d = _muse_dir(output_dir)
    if profile:
        (d / "profile.md").write_text(profile, encoding="utf-8")
    lines = [f"# 切入点卡片：{topic}\n"]
    qlines = [f"# 拷问弹药（grill-with-docs 用）：{topic}\n"]
    slines = [f"# 文献锚点：{topic}\n"]
    for c in sorted(cards, key=lambda x: (not x.get("gold", False), not x.get("outlier", False))):
        badge = "🥇英热中冷" if c.get("gold") else ("🔸离群" if c.get("outlier") else "共识")
        lines += [
            f"\n## {c['name']}（{c['type']}｜{c.get('novelty','?')}｜{badge}）",
            f"- 机制：{c['mechanism']}",
            f"- 为什么非显而易见：{c['why_nonobvious']}",
            f"- 最强反驳：{c['steelman']}",
        ]
        if c.get("feasibility"):
            lines.append(f"- 可行性/数据：{c['feasibility']}")
        lines.append(f"- 提出方：{'、'.join(c['source_models'])}；英文命中 {c.get('en_hits')}，中文命中 {c.get('zh_hits')}")
        qlines += [f"\n## {c['name']}"] + [f"- {q}" for q in c.get("questions", [])]
        for a in c.get("anchors", []):
            slines.append(f"- [{c['name']}] {a['title']} — {a['url']}")
    (d / "perspectives.md").write_text("\n".join(lines), encoding="utf-8")
    (d / "questions.md").write_text("\n".join(qlines), encoding="utf-8")
    (d / "sources.md").write_text("\n".join(slines), encoding="utf-8")


def run_scan(topic, profile, output_dir, providers, decompose_llm,
             en_search, zh_search, on_card):
    """providers: {model_tag: llm_call}；on_card(card) 在每张卡出炉（含新颖性）时回调。"""
    fundamentals = decompose_topic(topic, profile, decompose_llm)
    suppressed = load_suppressed(output_dir)

    all_cards, lock = [], threading.Lock()

    def one_provider(tag, call):
        try:
            return enumerate_cards(topic, fundamentals, profile, tag, call)
        except Exception:
            return []  # 单家失败不拖垮扫描

    threads_out = {}
    ts = []
    for tag, call in providers.items():
        t = threading.Thread(target=lambda tg=tag, c=call: threads_out.__setitem__(tg, one_provider(tg, c)))
        t.start()
        ts.append(t)
    for t in ts:
        t.join()

    merged = mark_outliers(dedupe_cards([c for v in threads_out.values() for c in v]))
    merged = apply_suppression(merged, suppressed)

    for card in merged:
        _novelty_for(card, topic, en_search, zh_search)
        with lock:
            all_cards.append(card)
        on_card(card)

    _write_outputs(output_dir, topic, profile, all_cards)
    return all_cards
```

注意与测试的时序约定：本函数按"先全部枚举、再逐卡定新颖性并回调"实现（枚举阶段的增量由 Task 5 服务器层通过 provider 线程完成时间差自然体现；纯引擎保持简单顺序，`on_card` 是唯一流式钩子）。

- [ ] **Step 4: 跑测试确认通过**　Expected: 12 passed。

- [ ] **Step 5: Commit**

```bash
git add blindspot.py tests/test_blindspot.py
git commit -m "feat: run_scan 编排 + 七件套落盘 + 反馈/抑制闭环（全离线集成测试）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 真实接线（litellm 三家 + PerplexitySearchRM + zsearch）+ CLI 冒烟

**Files:**
- Modify: `blindspot.py`

- [ ] **Step 1: zsearch CLI 探察（先看真实接口再写包装）**

```bash
zsearch --help 2>&1 | head -30
zsearch "平台责任" 2>&1 | head -10
```

记录：输出格式（JSON？行文本？）、条数旗标。**按实际输出调整 Step 2 里 `real_zh_search` 的参数与解析**（默认假设：位置参数 query、行文本输出、每行一条；若有 `--json`/`--limit` 更好，用之）。

- [ ] **Step 2: 实现真实接线**（`blindspot.py` 追加）

```python
# ---- 真实接线（引擎之外的薄层）----

def _litellm_call(model, api_key=None, api_base=None):
    import litellm

    def call(prompt):
        kw = {}
        if api_key:
            kw["api_key"] = api_key
        if api_base:
            kw["api_base"] = api_base
        resp = litellm.completion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.9, timeout=60, **kw)
        return resp.choices[0].message.content

    return call


def real_providers():
    """有 key 的都上（spec：可用几家发几家）。模型名与 README 顶部一致。"""
    out = {}
    if os.getenv("DEEPSEEK_API_KEY"):
        out["deepseek"] = _litellm_call(
            "deepseek/deepseek-v4-flash",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            api_base=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"))
    if os.getenv("OPENAI_API_KEY"):
        out["openai"] = _litellm_call("openai/chat-latest", api_key=os.getenv("OPENAI_API_KEY"))
    if os.getenv("GOOGLE_API_KEY"):
        out["gemini"] = _litellm_call("gemini/gemini-3.1-flash-lite", api_key=os.getenv("GOOGLE_API_KEY"))
    return out


def real_en_search(k: int = 5):
    from knowledge_storm.rm import PerplexitySearchRM

    rm = PerplexitySearchRM(k=k)

    def search(query):
        return rm.forward(query)

    return search


def real_zh_search(limit: int = 8):
    # ponytail: 行文本解析，Step 1 探察后按实际旗标修正此处
    def search(query):
        r = subprocess.run(["zsearch", query], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[:200])
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return [{"title": l, "url": ""} for l in lines[:limit]]

    return search


if __name__ == "__main__":
    import argparse
    import time

    from knowledge_storm.utils import load_api_key

    load_api_key(toml_file_path=str(Path(__file__).parent / "secrets.toml"))
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--profile", default="")
    ap.add_argument("--output-dir", default=None)
    a = ap.parse_args()
    out = a.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or f"./results/muse/{a.topic[:20]}"
    t0 = time.time()
    provs = real_providers()
    print(f"providers: {list(provs)}")

    def show(c):
        print(f"[{time.time()-t0:6.1f}s] {c['type']}｜{c['name']}｜{c.get('novelty')}"
              f"{'｜🥇' if c.get('gold') else ''}{'｜🔸离群' if c.get('outlier') else ''}")

    cards = run_scan(a.topic, a.profile, out, provs,
                     decompose_llm=next(iter(provs.values())),
                     en_search=real_en_search(), zh_search=real_zh_search(), on_card=show)
    print(f"共 {len(cards)} 张卡，产物在 {out}/docs/agents/muse/")
```

- [ ] **Step 3: 真实冒烟（三家全开，一次真跑）**

```bash
.venv/bin/python blindspot.py "生成式人工智能训练数据的著作权例外" --profile "中文法学，熟悉法教义学与利益衡量，困惑：合理使用路径是否已被做烂"
```

Expected：providers 列出可用家数（应 ≥2：deepseek/openai/gemini 视 key）；逐卡打印含类型/新颖性徽标；结束打印总卡数与产物路径；`perspectives.md` 里三类卡齐备、每卡有最强反驳、命中数与锚点。记录首卡耗时与全程耗时（供 §12.2 验收参照；引擎层无首批 20s 硬指标——那是 UI 层轮询语义，但记录基线）。离线全套照跑：`.venv/bin/python -m pytest tests/test_blindspot.py -v` 仍 12 passed。

- [ ] **Step 4: Commit**

```bash
git add blindspot.py
git commit -m "feat: 盲区扫描真实接线（litellm 三家/Perplexity/zsearch）+ CLI 冒烟

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: muse_server /scan 三接口 + curl E2E

**Files:**
- Modify: `muse_server.py`

- [ ] **Step 1: 加 SCAN 状态与接口**（`muse_server.py`；import 区加 `import blindspot`，与 SESSION 平行加：）

```python
SCAN = {"phase": "idle", "topic": None, "cards": [], "output_dir": None, "error": None}
SCAN_LOCK = threading.Lock()


class ScanReq(BaseModel):
    topic: str
    profile: str = ""
    output_dir: str | None = None


class FeedbackReq(BaseModel):
    name: str
    verdict: str  # 已知 | 新但不适用 | 新且值得深挖


def scan_bg(req: ScanReq):
    try:
        provs = blindspot.real_providers()
        if not provs:
            raise RuntimeError("没有任何可用的 LLM key（DEEPSEEK/OPENAI/GOOGLE）")

        def on_card(card):
            with SCAN_LOCK:
                SCAN["cards"].append(card)

        blindspot.run_scan(
            topic=req.topic, profile=req.profile, output_dir=SCAN["output_dir"],
            providers=provs, decompose_llm=next(iter(provs.values())),
            en_search=blindspot.real_en_search(), zh_search=blindspot.real_zh_search(),
            on_card=on_card)
        SCAN["phase"] = "done"
    except Exception:
        SCAN["error"] = traceback.format_exc()
        SCAN["phase"] = "error"


@app.post("/scan")
def start_scan(req: ScanReq):
    if SCAN["phase"] == "scanning":
        raise HTTPException(409, "扫描进行中")
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "主题不能为空")
    base = req.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(ROOT / "results" / "muse" / sanitize_topic(topic))
    SCAN.update(phase="scanning", topic=topic, cards=[], error=None, output_dir=base)
    threading.Thread(target=scan_bg, args=(req,), daemon=True).start()
    return {"ok": True, "output_dir": base}


@app.get("/scan/status")
def scan_status():
    with SCAN_LOCK:
        cards = list(SCAN["cards"])
    return {"phase": SCAN["phase"], "topic": SCAN["topic"], "cards": cards,
            "output_dir": SCAN["output_dir"], "error": SCAN["error"]}


@app.post("/scan/feedback")
def scan_feedback(req: FeedbackReq):
    if not SCAN["output_dir"]:
        raise HTTPException(409, "尚无扫描会话")
    if req.verdict not in ("已知", "新但不适用", "新且值得深挖"):
        raise HTTPException(400, "verdict 必须是 已知/新但不适用/新且值得深挖")
    blindspot.record_feedback(SCAN["output_dir"], req.name, req.verdict)
    return {"ok": True}
```

同时把文件头 docstring 的接口清单补上三个 /scan 接口一行。

- [ ] **Step 2: curl E2E（真实小跑一发）**

```bash
cd /Users/xianweizhang/Projects/paper-muse
lsof -ti tcp:8765 | xargs kill 2>/dev/null; sleep 1
nohup .venv/bin/python muse_server.py --port 8765 > /tmp/muse_scan_e2e.log 2>&1 &
sleep 6
curl -s -X POST http://127.0.0.1:8765/scan -H 'Content-Type: application/json' \
  -d '{"topic":"算法解释义务的可诉性"}'
for i in $(seq 1 40); do sleep 3; S=$(curl -s http://127.0.0.1:8765/scan/status | .venv/bin/python -c "import sys,json;d=json.load(sys.stdin);print(d['phase'],len(d['cards']))"); echo "[$((i*3))s] $S"; case "$S" in done*|error*) break;; esac; done
curl -s -X POST http://127.0.0.1:8765/scan/feedback -H 'Content-Type: application/json' -d '{"name":"任选一张卡的name","verdict":"已知"}'
curl -s http://127.0.0.1:8765/scan/status | .venv/bin/python -c "import sys,json;d=json.load(sys.stdin);print('phase:',d['phase'],'cards:',len(d['cards']));print((d['error'] or '')[-300:])"
lsof -ti tcp:8765 | xargs kill 2>/dev/null
```

Expected：phase 走到 done、cards ≥6；feedback 返回 ok 且 `angle-feedback.json` 出现该条；error 为空。轮询期间应能看到 cards 数量**逐步增长**（新颖性逐卡回调）。

- [ ] **Step 3: 全量回归**

```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: 18 passed（6 旧 + 12 新）。

- [ ] **Step 4: Commit**

```bash
git add muse_server.py
git commit -m "feat: muse_server /scan 三接口（后台扫描/增量轮询/三键反馈）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 对照 spec §12 可测子集验收 + 终审

- [ ] Step 1: 逐条核 §12.3/.4/.7 可自动验证项：三类卡齐备（Task 4 冒烟产物）、离群 ≥1、每卡最强反驳与锚点、密度字段非空或"中文面未检"、抑制表生效（对同 output_dir 再跑一次扫描，确认被标「已知」的卡不再出现——真实二跑）、`docs/agents/muse/` 产物齐备。
- [ ] Step 2: 派终审 code review（范围 main..HEAD），修 Critical/Important 后合并决策交用户。
- [ ] §12.1（质量地板）与 §12.2（20s 首批）留给 UI 子计划与用户真实使用验收，本计划如实标注"未验，移交"。

---

## 附：后续姊妹计划（勿混入本计划）
- **2/4 web 画布 UI**：4 方向 HTML mock → 用户反应 → 实现卡片墙/圆桌流/对抗报告 + WKWebView 接管 App 内容区（含 §12.2 首批 20s 的 UI 语义）。
- **3/4 对抗幕引擎**：有稿/无稿双模式红队循环 + failure-points.md。
- **4/4 圆桌钉死席位**：第一性原理专家 + 跨学科猎人进 Co-STORM 专家列表（persona 原文替换后做）。
