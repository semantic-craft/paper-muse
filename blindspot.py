"""
盲区扫描引擎（spec v2 §4）：第一性原理拆解 → 多模型三类卡枚举 →
去重/离群/抑制 → 新颖性三角定位（英文 web + zsearch 中文/自有语料）→ 流式出卡 → 落盘。

纯引擎，LM 与检索全部依赖注入，便于离线测试；真实接线见文件末尾 real_* 系列与 CLI。
"""

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import threading
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from urllib import parse, request

import json_repair

from evidence import (
    EvidenceProviderError,
    EvidenceGateway,
    FunctionEvidenceProvider,
    ProviderRecord,
    ProviderSearchResult,
)
from prompt_assets import FIRST_PRINCIPLES_PERSONA, SCAN_METHOD_PROMPT
from zotero_local import ZoteroLocalAdapter

CARD_TYPES = ["学科视角", "理论框架", "研究方法"]

# 卡缺失（或空白）张力时产物/卡面统一的占位串——明示「弱张力」而非丢卡（#88 US5）。
TENSION_WEAK_LABEL = "弱张力/未给出"


def card_type_quota_status(cards: list) -> dict:
    present = {card.get("type") for card in cards}
    missing = [card_type for card_type in CARD_TYPES if card_type not in present]
    if not missing:
        return {"state": "ready", "missing_card_types": [], "message": ""}
    return {
        "state": "degraded",
        "missing_card_types": missing,
        "message": f"卡型配额降级：缺少{'、'.join(missing)}",
    }

# 卡片一旦交给 on_card，就成为 /scan/status 可并发读取的活快照。后续阶段只可更新
# 这里已注册的键值，不能再扩张键集；新增阶段字段只需在此登记一行。
CARD_SNAPSHOT_DEFAULTS = {
    "id": None,
    "outlier": None,
    "novelty": None,
    "gold": None,
    "en_hits": None,
    "en_source": None,
    "en_degraded": None,
    "zh_hits": None,
    "zh_status": None,
    "own_hits": None,
    "own_status": None,
    "own_identity_status": None,
    "novelty_reason": None,
    "evidence": [],
    "merged_angles": [],
    "feasibility": None,
    # 张力（对领域读者：反转了哪个默认前提）。软字段（#88 D1），枚举产出、缺失即弱张力。
    # 与 feasibility 同属「枚举写、后续阶段不改」：在此预置保证键集恒定，
    # _new_card_snapshot 里连同 feasibility 一并保留原值不被默认清零。
    "tension": None,
    "quality_score": None,
    "elo_score": None,
    "outlier_reason": None,
    "proximity_basis": None,
    "cluster_id": None,
    "cluster_size": 1,
    "cluster_similarity": None,
}

_CACHE_VERSION = "retrieval-v3-status"
_CACHE_STATS = {}
_CACHE_LOCK = threading.Lock()


def _cache_base_dir() -> Path:
    explicit = os.environ.get("PAPER_MUSE_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "paper-muse"


def _normalize_query_text(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def _bump_cache_stat(name: str, field: str):
    with _CACHE_LOCK:
        stats = _CACHE_STATS.setdefault(name, {"hits": 0, "misses": 0, "stores": 0})
        stats[field] += 1


def reset_retrieval_cache_stats():
    with _CACHE_LOCK:
        _CACHE_STATS.clear()


def retrieval_cache_stats():
    with _CACHE_LOCK:
        by_retriever = deepcopy(_CACHE_STATS)
    totals = {"hits": 0, "misses": 0, "stores": 0, "errors": 0}
    for stats in by_retriever.values():
        for field in ("hits", "misses", "stores"):
            totals[field] += int(stats.get(field, 0) or 0)
    return {**totals, "by_retriever": by_retriever, **by_retriever}


def _cached_search(name: str, limit: int, mode: str, ttl: int, search):
    """Disk-cache stable retrieval results. Exceptions are never cached."""
    try:
        import diskcache
    except Exception:
        return search
    cache = diskcache.Cache(str(_cache_base_dir() / "retrieval"))

    def wrapped(query):
        norm = _normalize_query_text(query)
        key = (_CACHE_VERSION, name, limit, mode, norm)
        if key in cache:
            _bump_cache_stat(name, "hits")
            return deepcopy(cache[key])
        _bump_cache_stat(name, "misses")
        result = search(query)
        cache.set(key, deepcopy(result), expire=ttl)
        _bump_cache_stat(name, "stores")
        return result

    return wrapped


def _http_json(url: str, headers=None, timeout: int = 20):
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")

# 研究者画像三要素：既是机器级 researcher.md 的键，也是卡片 vs_profile 绑定的目标（#4）。
# key = 机器/UI 用的稳定标识；label = 中文面（喂 LLM、researcher.md 文本、UI 标签）。
PROFILE_ELEMENTS = [("field", "领域"), ("stance", "立场"), ("familiar", "熟悉")]
_LABEL_TO_KEY = {label: key for key, label in PROFILE_ELEMENTS}
_PROFILE_KEYS = {key for key, _ in PROFILE_ELEMENTS}


# ---- 纯函数层 ----

def normalize_name(name: str) -> str:
    s = re.sub(r"[（(][^）)]*[）)]", "", name)  # 括号注记（缩写/译名）整体剔除
    s = re.sub(r"[\s【】\[\]\-—·]", "", s.strip().lower())
    return s


def _angle_core(name: str) -> str:
    s = normalize_name(name)
    for suffix in ("研究视角", "理论框架", "方法路径", "分析框架", "学科视角", "视角", "理论", "框架", "方法"):
        if s.endswith(suffix):
            return s[: -len(suffix)] or s
    return s


def _same_angle(left: str, right: str) -> bool:
    a, b = _angle_core(left), _angle_core(right)
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    # ponytail: O(n^2) substring merge is enough for current 10-30 card walls; use embeddings when #6 needs real clusters.
    return len(short) >= 4 and short in long


def _merge_unique(seq, incoming):
    seen = {json.dumps(v, ensure_ascii=False, sort_keys=True) for v in seq}
    out = list(seq)
    for item in incoming:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _new_card_snapshot(card: dict, card_id: int) -> dict:
    """Freeze the complete live-card key set before the card is first emitted."""
    snapshot = dict(card)
    feasibility = snapshot.get("feasibility")
    tension = snapshot.get("tension")
    snapshot.update(deepcopy(CARD_SNAPSHOT_DEFAULTS))
    snapshot["id"] = card_id
    snapshot["feasibility"] = feasibility
    snapshot["tension"] = tension          # 枚举给的张力保留；缺失卡落回 None（弱张力占位）
    return snapshot


def _merge_card_into(base: dict, incoming: dict, similarity=None) -> dict:
    models = set(base.get("source_models") or []) | set(incoming.get("source_models") or [])
    base["source_models"] = sorted(models)
    if incoming.get("name") and incoming["name"] != base.get("name"):
        aliases = base.setdefault("merged_angles", [])
        if incoming["name"] not in aliases:
            aliases.append(incoming["name"])
    base["questions"] = _merge_unique(base.get("questions") or [], incoming.get("questions") or [])
    # 仅当 incoming 真带证据才改写 base["evidence"]：枚举卡本无证据（证据在 enrich 里补），
    # 无谓改写会与不持锁的 enrich 线程对同一 card["evidence"] 竞态、覆盖丢更新。
    if incoming.get("evidence"):
        base["evidence"] = _merge_unique(base.get("evidence") or [], incoming["evidence"])
    if not base.get("feasibility") and incoming.get("feasibility"):
        base["feasibility"] = incoming["feasibility"]
    base["cluster_size"] = int(base.get("cluster_size") or 1) + int(incoming.get("cluster_size") or 1)
    if similarity is not None:
        base["cluster_similarity"] = max(float(base.get("cluster_similarity") or 0.0), round(similarity, 3))
    return base


def extract_json(text: str):
    """从可能带说明文字/代码围栏的模型输出里抠出 JSON 对象。
    优先 ```json 围栏；否则花括号平衡扫描收集全部可解析对象、取最大者
    （贪婪 `\\{.*\\}` 在「思考 {…} + 正文 {…}」多对象输出下会整体解析失败，
    静默丢掉一整家模型——终审 Important #1）。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    candidates = []
    start = text.find("{")
    while start != -1:
        depth = 0
        end = None
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            break
        span = text[start:end]
        try:
            candidates.append((len(span), json.loads(span)))
        except json.JSONDecodeError:
            try:
                # strict=False 容忍字符串内裸控制符（LLM 输出最常见的坏法）
                candidates.append((len(span), json.loads(span, strict=False)))
            except json.JSONDecodeError:
                pass
        start = text.find("{", end)
    if not candidates:
        # 终极兜底：json_repair 修「未闭合/中文引号当界符」类坏 JSON
        # （deepseek 对部分中文 prompt 稳定产出 `…？”]` 缺 `}` 的输出，冒烟实证）
        start = text.find("{")
        if start != -1:
            repaired = json_repair.loads(text[start:])
            if isinstance(repaired, dict) and repaired:
                return repaired
        raise ValueError(f"输出中未找到 JSON：{text[:200]}")
    return max(candidates, key=lambda x: x[0])[1]


def dedupe_cards(cards: list) -> list:
    merged = {}
    for c in cards:
        key = next((k for k in merged if _same_angle(c["name"], merged[k]["name"])), normalize_name(c["name"]))
        if key in merged:
            _merge_card_into(merged[key], c)
        else:
            item = dict(c)
            item.setdefault("cluster_size", 1)
            merged[key] = item
    return list(merged.values())


def _card_quality_points(card: dict) -> float:
    novelty = card.get("novelty")
    points = 0.0
    points += 80 if card.get("gold") else 0
    points += {"交叉空白": 45, "边缘有人做": 20, "中文面未检": -5, "主流": -30}.get(novelty, 0)
    points += min(int(card.get("en_hits") or 0), 20) * 1.5
    points += min(int(card.get("own_hits") or 0), 8) * 2
    if card.get("type") == "研究方法" and card.get("feasibility"):
        points += 8
    points += min(len(card.get("questions") or []), 3) * 2
    return points


def _quality_ratings(cards: list, k: int = 32) -> list:
    """确定性质量分（**不是** Elo：无真实 pairwise debate，只是质量点数的公式换算）。
    首屏便宜、快、不阻塞出卡；真 Elo 见 run_quality_tournament（卡片上墙后异步跑）。"""
    ratings = [1500.0 for _ in cards]
    quality = [_card_quality_points(c) for c in cards]
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            diff = quality[i] - quality[j]
            actual_i = 0.5 if abs(diff) < 4 else (1.0 if diff > 0 else 0.0)
            expected_i = 1 / (1 + 10 ** ((ratings[j] - ratings[i]) / 400))
            delta = k * (actual_i - expected_i)
            ratings[i] += delta
            ratings[j] -= delta
    return [int(round(r)) for r in ratings]


def mark_outliers(cards: list) -> list:
    """确定性 quality_score + 离群判定（首屏便宜路径）。降级证据不参与错误判定：
    离群只看「簇内孤立 + 本轮质量高位」，不因 degraded 命中虚高。elo_score 保持 None——
    只有真跑过 pairwise tournament 的卡才有 elo_score（#51）。"""
    scores = _quality_ratings(cards)
    floor = min(1500, sorted(scores)[len(scores) // 2]) if scores else 1500
    isolated_cards = []
    for i, c in enumerate(cards):
        score = scores[i] if i < len(scores) else 1500
        isolated = len(c.get("source_models") or []) == 1 and int(c.get("cluster_size") or 1) == 1
        high_quality = score >= floor
        if isolated:
            isolated_cards.append((score, c))
        c["quality_score"] = score
        c.setdefault("elo_score", None)   # 无 matches → 不存在真 Elo（仅占位 None）
        c["outlier_reason"] = (
            f"质量分 {score}；"
            f"{'簇内孤立' if isolated else '已有近邻/多模型共识'}；"
            f"{'高于本轮基准' if high_quality else '低于本轮基准'}"
        )
        c["outlier"] = isolated and high_quality
    if isolated_cards and not any(c["outlier"] for c in cards):
        _, fallback = max(isolated_cards, key=lambda item: item[0])
        fallback["outlier"] = True
        fallback["outlier_reason"] += "；孤立卡兜底标亮"
    return cards


def run_quality_tournament(cards: list, judge, *, max_candidates: int = 8,
                           max_matches: int | None = None, on_match=None) -> list:
    """卡片上墙后的**真** pairwise judge tournament（异步跑，不阻塞首卡）。
    judge(card_a, card_b) -> {"winner": "a"|"b"|"tie", "reason": str}。只有实际参赛的卡获得
    elo_score；每场 match/judge/理由/分数变化经 on_match 回调进 manifest。候选集有界
    (max_candidates，取确定性质量分高位)、预算有界 (max_matches 墙钟/费用代理)。

    返回参赛卡子集（带 elo_score + tournament_matches 计数）。judge 抛错的对局跳过、不崩。"""
    ranked = sorted(cards, key=lambda c: c.get("quality_score") or 0, reverse=True)
    field = ranked[:max(0, max_candidates)]
    ratings = {id(c): 1500.0 for c in field}
    played = {id(c): 0 for c in field}
    matches = 0
    for a_i in range(len(field)):
        for b_i in range(a_i + 1, len(field)):
            if max_matches is not None and matches >= max_matches:
                break
            a, b = field[a_i], field[b_i]
            try:
                verdict = judge(a, b) or {}
            except Exception as e:
                logging.warning(f"tournament judge 异常，跳过该对局：{e}")
                continue
            winner = verdict.get("winner")
            score_a = 1.0 if winner == "a" else (0.0 if winner == "b" else 0.5)
            exp_a = 1 / (1 + 10 ** ((ratings[id(b)] - ratings[id(a)]) / 400))
            delta = 32 * (score_a - exp_a)
            ratings[id(a)] += delta
            ratings[id(b)] -= delta
            played[id(a)] += 1
            played[id(b)] += 1
            matches += 1
            if on_match:
                on_match({"a": a.get("name"), "b": b.get("name"), "winner": winner or "tie",
                          "reason": str(verdict.get("reason") or ""),
                          "rating_delta": round(delta, 2)})
        else:
            continue
        break
    competitors = []
    for c in field:
        if played[id(c)] > 0:
            c["elo_score"] = int(round(ratings[id(c)]))
            c["tournament_matches"] = played[id(c)]
            competitors.append(c)
    return competitors


def _card_embedding_text(card: dict) -> str:
    # Proximity 比较完整卡片语义（机制/非显而易见理由/steelman/可行性），不只名称（#51）。
    feasibility = card.get("feasibility")
    parts = [
        card.get("name", ""),
        card.get("type", ""),
        card.get("mechanism", ""),
        card.get("why_nonobvious", ""),
        card.get("steelman", ""),
        feasibility if isinstance(feasibility, str) else "",
    ]
    return "\n".join(str(p) for p in parts if p)


def _text_features(text: str) -> list:
    raw = normalize_name(text)
    features = re.findall(r"[a-z]+", raw)
    for n in (2, 3):
        features += [raw[i:i + n] for i in range(max(0, len(raw) - n + 1))]
    return features or [raw]


def _local_embedding(texts: list, dims: int = 128) -> list:
    vectors = []
    for text in texts:
        vec = [0.0] * dims
        for feat in _text_features(text):
            h = int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=4).digest(), "big")
            vec[h % dims] += 1.0
        vectors.append(vec)
    return vectors


def _cosine(left, right) -> float:
    a = [float(x) for x in left]
    b = [float(x) for x in right]
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _card_vectors(cards: list, embedding_fn=None):
    # Proximity 完整语义（机制/why/steelman/可行性）走真 embedding；无真 embedding 时退回
    # 本地 hash 仅按名称——crude n-gram 对整段语义会把共享套话的卡误并，故名称基更稳，
    # 并由 finalize 记 proximity_basis="lexical-fallback" 明示这是降级路径（#51）。
    texts = [_card_embedding_text(c) for c in cards] if embedding_fn else [c.get("name", "") for c in cards]
    try:
        vectors = (embedding_fn or _local_embedding)(texts)
    except Exception as e:
        logging.warning(f"embedding 去重失败，退回名称去重：{e}")
        return None
    if len(vectors) != len(cards):
        logging.warning("embedding 去重失败：向量数量与卡片数量不一致")
        return None
    return vectors


def finalize_card_quality(cards: list, embedding_fn=None, embedding_threshold: float = 0.88) -> list:
    """Final async #6 pass: embedding-neighbor merge, then deterministic quality outlier basis.
    Proximity 用完整卡片语义（#51）；记录 proximity_basis：真 embedding vs 本地 hash 退化 vs 无。"""
    merged = dedupe_cards(cards)
    vectors = _card_vectors(merged, embedding_fn)
    basis = ("embedding" if (embedding_fn and vectors) else
             ("lexical-fallback" if vectors else "none"))
    for card in merged:
        card["proximity_basis"] = basis
    if vectors:
        kept, kept_vecs = [], []
        for card, vec in zip(merged, vectors):
            best_i, best_sim = None, 0.0
            for i, old_vec in enumerate(kept_vecs):
                sim = _cosine(vec, old_vec)
                if sim > best_sim:
                    best_i, best_sim = i, sim
            if best_i is not None and best_sim >= embedding_threshold:
                _merge_card_into(kept[best_i], card, similarity=best_sim)
            else:
                card.setdefault("cluster_size", 1)
                kept.append(card)
                kept_vecs.append(vec)
        merged = kept
    for i, card in enumerate(merged, start=1):
        card["cluster_id"] = i
        card.setdefault("cluster_size", 1)
    mark_outliers(merged)
    return merged


def apply_suppression(cards: list, suppressed: set) -> list:
    return [
        c
        for c in cards
        if not any(_same_angle(c["name"], known) for known in suppressed)
    ]


def classify_novelty(en_hits, zh_hits):
    """→ (分类, 是否金标)。zh_hits=None 表示 CNKI 未确认（明示未检，不装懂）。"""
    if zh_hits is None:
        return ("中文面未检", False)
    if zh_hits >= 3:
        return ("主流", False)
    if zh_hits >= 1:
        return ("边缘有人做", False)
    # zh_hits == 0
    gold = en_hits >= 3  # 英热中冷 = 引入型创新机会
    return ("交叉空白", gold)


# ---- 提示词 + 拆解与枚举（注入式 LM）----

ENUM_SCHEMA_HINT = (
    '只输出 JSON：{"cards": [{"type": "学科视角|理论框架|研究方法", "name": "...", '
    '"mechanism": "一句话机制", "why_nonobvious": "为什么对该研究者非显而易见", '
    '"tension": "反转了法学界哪个默认前提（对领域读者的张力，与 why_nonobvious 分开；软字段，没有可省）", '
    '"vs_profile": [{"element": "领域|立场|熟悉", "note": "相对画像这一条为何非显而易见（有画像才填）"}], '
    '"steelman": "最强反驳：哪类审稿人会怎么打", "feasibility": "方法卡必填：数据从哪来", '
    '"questions": ["1-2个拷问句"]}]}'
)

REQUIRED_CARD_FIELDS = {"type", "name", "mechanism", "why_nonobvious", "steelman", "questions"}


def normalize_vs_profile(raw) -> list:
    """卡片 vs_profile → 规整的 [{element, note}]（#4 结构化「因你」）。
    element 归一为画像键（field/stance/familiar，容错 LLM 写中文标签或直接写键两种）；
    note 空白折叠。element 不在三要素内或 note 空 → 丢弃该条（绝不编造绑定）。
    单对象也收（LLM 常只绑一条），统一进列表；无有效绑定回 []。"""
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        el = str(item.get("element", "")).strip()
        key = el if el in _PROFILE_KEYS else _LABEL_TO_KEY.get(el)
        note = re.sub(r"\s+", " ", str(item.get("note", ""))).strip()
        if key and note:
            out.append({"element": key, "note": note})
    return out


def decompose_topic(topic: str, profile: str, llm_call, puzzle: str = "") -> list:
    prompt = (
        f"{FIRST_PRINCIPLES_PERSONA}\n\n"
        f"研究者画像（可能为空）：{profile}\n"
        f"论文主题：{topic}\n"
        + (f"本次困惑（研究者这一篇想不通什么）：{puzzle}\n" if puzzle else "")
        + "\n用第一性原理把它拆成 3-5 个根本问题（最小可验证、互相独立、直指本质）。"
        '只输出 JSON：{"fundamentals": ["...", "..."]}'
    )
    # 拆解是全扫描的单点：一次坏输出不该整扫崩死，重试一次再抛
    try:
        return extract_json(llm_call(prompt))["fundamentals"]
    except (ValueError, KeyError):
        return extract_json(llm_call(prompt))["fundamentals"]


def enumerate_cards(topic: str, fundamentals: list, profile: str, model_tag: str, llm_call,
                    puzzle: str = "") -> list:
    has_profile = bool((profile or "").strip())
    profile_line = (
        f"研究者画像（『非显而易见』以此为参照系）：{profile}\n" if has_profile
        else "研究者画像：未提供（无参照系，vs_profile 留空）\n"
    )
    vs_line = (
        "每张卡必须给出 vs_profile：指明它相对画像哪一条（领域/立场/熟悉）构成非显而易见，附一句为什么。\n"
        if has_profile else ""
    )
    prompt = (
        "你要为一篇中文法学论文勘探非显而易见的切入点。跨学科越远越好，但必须论证适配性。\n"
        f"主题：{topic}\n根本问题：{json.dumps(fundamentals, ensure_ascii=False)}\n"
        + (f"本次困惑（扫描应朝此发力，切入点优先解此惑）：{puzzle}\n" if puzzle else "")
        + profile_line + vs_line + "\n"
        + SCAN_METHOD_PROMPT + "\n\n"
        "硬性配额：三类卡各至少 2 张——学科视角（其他学科怎么看这个问题）、"
        "理论框架（具体理论及其机制）、研究方法（实证/比较法/计算法学等，必附 feasibility 数据来源）。"
        "卡片之间必须彼此截然不同（学科、方法论、规范/实证、时间尺度错开），拒绝同一角度的变体。\n"
        + ENUM_SCHEMA_HINT
    )
    raw = extract_json(llm_call(prompt)).get("cards", [])
    cards = []
    dropped = 0
    for c in raw:
        if not REQUIRED_CARD_FIELDS <= set(c):
            dropped += 1
            continue
        c["source_models"] = [model_tag]
        # 张力软透传：折叠空白，空则删键（缺失≠空串冒充张力）；缺 tension 不丢卡（软字段 #88 D1）。
        # 落墙时 _new_card_snapshot 预置 tension=None，缺失卡即以弱张力占位。
        tension = re.sub(r"\s+", " ", str(c.get("tension", ""))).strip()
        if tension:
            c["tension"] = tension
        else:
            c.pop("tension", None)
        # vs_profile 只在有画像时有意义（无画像＝无参照系，不让 LLM 凭空绑），归一后按有无回填
        vp = normalize_vs_profile(c.get("vs_profile")) if has_profile else []
        if vp:
            c["vs_profile"] = vp
        else:
            c.pop("vs_profile", None)
        cards.append(c)
    if dropped:
        logging.info(f"enumerate_cards[{model_tag}]: 丢弃缺字段卡 {dropped}/{len(raw)}")
    return cards


# ---- run_scan 编排 + 落盘 ----

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


def _zh_name_core(name: str) -> str:
    """卡名 → 知网可检索的中文核心词。
    冒烟实证（2026-07-07）：CNKI 对「全名（英文）＋整句主题」长复合查询必空且 ~42s/次，
    「控制论 著作权」两短词才有真命中——查询必须收敛到中文短词。"""
    core = re.sub(r"[（(][^）)]*[）)]", "", name)      # 去括号注（多为英文名）
    core = re.sub(r"[A-Za-z0-9·\-–—'']+", "", core)   # 去英文/数字残留
    core = re.sub(r"\s+", "", core).strip("：:，,、 ")
    if "：" in core:
        core = core.split("：")[-1]                    # 取冒号后最具体段（计算法学：X → X）
    core = re.sub(r"视角$", "", core) or core          # 去卡型后缀
    return core or name


def _topic_zh_keyword(topic: str, llm_call):
    """主题 → 1 个知网检索核心词（「谁已经用 X 研究过 Y」的 Y）。失败回 None 并留痕。"""
    prompt = (f"论文主题：{topic}\n"
              "在知网检索该主题相关文献时，最核心的一个检索词是什么？要求：2-6 个汉字、"
              "领域概念词（学科名/制度名/理论名，如「著作权」「不正当竞争」），"
              '不要对象描述词、不要照抄主题。只输出 JSON：{"keyword": "..."}')
    try:
        kw = re.sub(r"\s+", "", str(extract_json(llm_call(prompt)).get("keyword", "")))
        logging.info(f"zh 检索词：{kw!r}")  # 全零命中时诊断靠它
        return kw or None
    except Exception as e:
        logging.warning(f"主题检索词提取失败（zh 查询退回整题）：{e}")
        return None


def _retrieval_payload(result, provider=None):
    # 只认 EvidenceGateway.search() 的公开 dict 契约；非 dict（None/空列表/检索异常回退）
    # 一律归一为空面，不再吞非空裸列表或私有 anchors 键（#52 contract 收口）。
    result = result if isinstance(result, dict) else {}
    results = result.get("results") or []
    hits = int(result.get("hits", len(results)))
    return {
        "hits": hits,
        "results": results,
        "evidence": result.get("evidence") or [],
        "source": result.get("source"),
        "degraded": result.get("degraded"),
        "status": result.get("status") or {
            "provider": provider,
            "state": (
                "unknown" if provider == "cnki" else ("empty" if hits == 0 else "ok")
            ),
            "hits": hits,
            "message": None,
        },
        "identity_status": result.get("identity_status"),
    }


def _novelty_for(card, topic, en_search, zh_search, own_search=None,
                 zh_gate=None, own_gate=None, zh_keyword=None, on_face_update=None):
    """新颖性三面定位，逐面原地回填（卡已上墙，字段更新靠 /scan/status 轮询快照可见）。
    顺序：en → own（秒级）先挂，zh（CNKI 走浏览器，慢且必须串行）最后定 novelty/gold。"""
    query = f"{card['name']} {topic}"
    try:
        en = en_search(query) or []
    except Exception as e:
        logging.warning(f"en_search 失败（en_hits=0 计）[{card['name']}]：{e}")
        en = []
    en_payload = _retrieval_payload(en, provider="academic")
    card["en_hits"] = en_payload["hits"]
    card["en_source"] = en_payload["source"]
    card["en_degraded"] = en_payload["degraded"]
    card["evidence"] = en_payload["evidence"]
    if own_search is None:
        card["own_hits"] = None
        card["own_status"] = None
        card["own_identity_status"] = None
    else:
        try:
            with (own_gate or nullcontext()):
                own_result = own_search(query)
                own_payload = _retrieval_payload(own_result, provider="zsearch")
            card["own_hits"] = own_payload["hits"]
            card["own_status"] = own_payload["status"]
            card["own_identity_status"] = own_payload["identity_status"]
            card["evidence"] = _merge_unique(card["evidence"], own_payload["evidence"])
            if on_face_update:
                on_face_update(card)
            if (
                own_payload["status"].get("state") in {"ok", "empty"}
                and callable(getattr(own_search, "enrich", None))
            ):
                enriched = _retrieval_payload(
                    own_search.enrich(own_result), provider="zsearch"
                )
                card["own_identity_status"] = enriched["identity_status"]
                card["evidence"] = _merge_unique(
                    en_payload["evidence"], enriched["evidence"]
                )
                if on_face_update:
                    on_face_update(card)
        except Exception as e:
            logging.warning(f"own_search 失败（own_hits 置空）[{card['name']}]：{e}")
            card["own_hits"] = None
            card["own_status"] = {
                "provider": "zsearch",
                "state": "error",
                "hits": None,
                "message": str(e),
            }
            card["own_identity_status"] = None
    kw = zh_keyword() if callable(zh_keyword) else zh_keyword
    zq = f"{_zh_name_core(card['name'])} {kw or topic}"
    try:
        with (zh_gate or nullcontext()):
            zh_payload = _retrieval_payload(zh_search(zq), provider="cnki")
        card["zh_status"] = zh_payload["status"]
        card["evidence"] = _merge_unique(card["evidence"], zh_payload["evidence"])
        if zh_payload["status"].get("state") in {"ok", "empty"}:
            zh_hits = zh_payload["hits"]
        else:
            zh_hits = None
    except Exception as e:
        # 降级必须留痕：中文面是新颖性判据，静默吞掉会让「未检」无从诊断
        logging.warning(f"zh_search 失败（降级中文面未检）[{card['name']}] q={zq!r}：{e}")
        zh_hits = None  # 中文面未检，明示不装懂
        card["zh_status"] = {
            "provider": "cnki",
            "state": "error",
            "hits": None,
            "message": str(e),
        }
    card["novelty"], card["gold"] = classify_novelty(en_payload["hits"], zh_hits)
    card["zh_hits"] = zh_hits
    zh_state = (card.get("zh_status") or {}).get("state", "error")
    if zh_hits is None:
        card["novelty_reason"] = f"中文面状态 {zh_state}，不判定中文真零或金标"
    elif zh_state == "empty":
        card["novelty_reason"] = "CNKI 已确认零命中；允许交叉空白与金标判定"
    else:
        card["novelty_reason"] = f"CNKI 已确认 {zh_hits} 条命中"
    return card


def format_mcii_action(goal: str, obstacle: str, if_then: str) -> list[str]:
    """Render the additive MCII action contract shared by scan and roundtable outputs."""
    return [
        "### 行动",
        f"- 目标（理想论证）：{goal}",
        f"- 障碍：{obstacle}",
        f"- if–then 验收门槛：{if_then}",
    ]


def _write_outputs(output_dir, topic, profile, cards):
    d = _muse_dir(output_dir)
    if profile:
        (d / "profile.md").write_text(profile, encoding="utf-8")
    lines = [f"# 切入点卡片：{topic}\n"]
    qlines = [f"# 拷问弹药（grill-with-docs 用）：{topic}\n"]
    slines = [f"# 文献锚点：{topic}\n"]
    for c in sorted(cards, key=lambda x: (not x.get("gold", False), not x.get("outlier", False))):
        badges = []
        if c.get("gold"):
            badges.append("🥇英热中冷")
        if c.get("outlier"):
            badges.append("🔸离群")
        if c.get("own_hits") and c.get("novelty") in ("交叉空白", "边缘有人做"):
            badges.append("📚已藏未用")
        badge = "｜".join(badges) or "共识"
        lines += [
            f"\n## {c['name']}（{c['type']}｜{c.get('novelty','?')}｜{badge}）",
            f"- 机制：{c['mechanism']}",
            # 双参照系成对：why_nonobvious=对研究者，tension=对领域读者；缺张力明示弱张力（#88 US11/US5）
            f"- 为什么非显而易见：{c['why_nonobvious']}",
            f"- 反转的领域默认前提：{c.get('tension') or TENSION_WEAK_LABEL}",
            f"- 最强反驳：{c['steelman']}",
        ]
        if c.get("outlier_reason"):
            lines.append(f"- 离群依据：{c['outlier_reason']}")
        if c.get("feasibility"):
            lines.append(f"- 可行性/数据：{c['feasibility']}")
        lines.append(f"- 提出方：{'、'.join(c['source_models'])}；英文命中 {c.get('en_hits')}，中文学界命中 {c.get('zh_hits')}，自有库命中 {c.get('own_hits')}")
        questions = c.get("questions", [])
        first_question = next((str(q).strip() for q in questions if str(q).strip()), "本卡的核心拷问")
        goal = (
            f"把「{c['name']}」从切入点推进为可辩护的论文论证，"
            f"并证成其机制：{c['mechanism']}"
        )
        obstacle = str(c.get("steelman") or "尚未形成最强反驳").strip()
        if_then = (
            f"如果能针对上述障碍补入至少一条可定位证据，并正面回答「{first_question}」，"
            "则进入圆桌深挖；否则保留为待证切入点。"
        )
        qlines += (
            [f"\n## {c['name']}"]
            + [f"- {q}" for q in questions]
            + [""]
            + format_mcii_action(goal, obstacle, if_then)
        )
        if c.get("evidence"):
            for ref in c["evidence"]:
                source = ref.get("source") or {}
                retrieval = ref.get("retrieval") or {}
                verification = ref.get("verification") or {}
                slines += [
                    f"- [{c['name']}] {source.get('title', '')} — {source.get('url', '')}",
                    f"  - EvidenceRef `{ref.get('id', '')}` · {retrieval.get('provider', '?')}"
                    f" · {verification.get('status', 'unknown')}",
                    "  - EvidenceRef-JSON: "
                    + json.dumps(
                        ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                ]
    (d / "perspectives.md").write_text("\n".join(lines), encoding="utf-8")
    (d / "questions.md").write_text("\n".join(qlines), encoding="utf-8")
    (d / "sources.md").write_text("\n".join(slines), encoding="utf-8")


def run_scan(topic, profile, output_dir, providers, decompose_llm,
             en_search, zh_search, on_card, own_search=None, puzzle="", on_update=None,
             embedding_fn=None):
    """providers: {model_tag: llm_call}。流式两段出卡（spec §4.1/§9，冒烟 #15）：
    单家枚举完成即合并上墙（on_card，无徽标），新颖性三面每卡起线程异步补挂——
    卡对象原地更新（只换预置键的值，不加键），/scan/status 轮询快照自然可见。
    返回时全部补挂完成，产物落盘。
    profile=研究者画像（稳定，机器级源头物化的只读串）；puzzle=本次困惑（一次性输入，
    喂扫描但不落 profile.md、不进画像，见 ADR-0001 / CONTEXT.md）。"""
    fundamentals = decompose_topic(topic, profile, decompose_llm, puzzle)
    suppressed = load_suppressed(output_dir)

    wall, order = {}, []
    lock = threading.Lock()
    zh_gate = threading.Semaphore(1)    # CNKI 共用一个浏览器会话，必须串行
    own_gate = threading.Semaphore(3)   # zsearch 子进程限并发
    enrich_ts = []

    # 主题检索词后台先备着（只有 zh 面等它；en/own 不受影响）
    kw_holder = {}
    kw_t = threading.Thread(
        target=lambda: kw_holder.update(kw=_topic_zh_keyword(topic, decompose_llm)), daemon=True)
    kw_t.start()

    def zh_keyword():
        kw_t.join(timeout=60)
        return kw_holder.get("kw")

    def emit_family(cards):
        """单家枚举结果并入墙：新卡上墙＋起新颖性线程；重名卡只并 source_models。"""
        fresh, touched = [], []
        with lock:
            for c in cards:
                if any(_same_angle(c["name"], known) for known in suppressed):
                    continue
                key = next((k for k in wall if _same_angle(c["name"], wall[k]["name"])), normalize_name(c["name"]))
                if key in wall:
                    prev = wall[key]
                    _merge_card_into(prev, c)
                    touched.append(prev)
                else:
                    c = _new_card_snapshot(c, len(order) + 1)
                    wall[key] = c
                    order.append(key)
                    fresh.append(c)
        for c in fresh:
            on_card(c)
            def enrich(card=c):
                _novelty_for(card, topic, en_search, zh_search, own_search,
                             zh_gate, own_gate, zh_keyword, on_update)
                if on_update:
                    on_update(card)
            t = threading.Thread(target=enrich, daemon=True)
            t.start()
            enrich_ts.append(t)
        if on_update:
            for c in touched:
                on_update(c)

    threads_out = {}

    def one_provider(tag, call):
        try:
            cards = enumerate_cards(topic, fundamentals, profile, tag, call, puzzle)
        except Exception as e:
            # 单家失败不拖垮扫描，但必须留痕——否则三方合议静默降为两方，离群徽标失真
            logging.error(f"provider[{tag}] 枚举失败：{e}")
            cards = []
        threads_out[tag] = cards
        emit_family(cards)  # 最快家先上墙，其余家增量合并（spec §4.1）

    ts = [threading.Thread(target=one_provider, args=(tag, call)) for tag, call in providers.items()]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    # 全军覆没不许装成功——空墙必须以 error 面目示人（终审 Important #2）
    if providers and not any(threads_out.values()):
        raise RuntimeError("所有模型枚举均失败或零卡——检查 key/模型名/输出解析（stderr 有各家错误日志）")

    # 离群 = 全家到齐后仅一家提出（早标会闪烁误导，故与其他徽标同为异步补挂）
    with lock:
        mark_outliers([wall[k] for k in order])
    if on_update:
        on_update(None)

    for t in enrich_ts:
        t.join()

    all_cards = finalize_card_quality([wall[k] for k in order], embedding_fn=embedding_fn)
    for i, card in enumerate(all_cards, start=1):
        card["id"] = i
    if on_update:
        on_update(None)
    _write_outputs(output_dir, topic, profile, all_cards)
    return all_cards


# ---- 真实接线（引擎之外的薄层）----

# 研究者画像 = 机器级配置，跨所有论文/扫描复用（ADR-0001）。三要素（PROFILE_ELEMENTS，见顶部）、
# 不含困惑（CONTEXT.md）。release 下存 `${PAPER_MUSE_CONFIG_DIR}/researcher.md`，
# 开发默认存 `${XDG_CONFIG_HOME:-~/.config}/paper-muse/researcher.md`，
# 扫描时物化只读快照为论文 profile.md。键值块格式与 profile.md 一致 → 下游（grill-with-docs 等）读法无感。


def researcher_md_path() -> Path:
    explicit = os.environ.get("PAPER_MUSE_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser() / "researcher.md"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "paper-muse" / "researcher.md"


def profile_text_from_dict(d: dict) -> str:
    """三要素 dict → 键值块文本（喂扫描 = researcher.md 内容 = profile.md 快照，同一份）。
    每要素严格单行：值内空白（含换行）归一为单空格，否则往返时续行会被丢弃、
    甚至若续行以「立场：」等标签开头会串改别的字段（审核 F2/F3）。"""
    def _oneline(v):
        return re.sub(r"\s+", " ", (v or "")).strip()
    lines = [f"{label}：{_oneline(d.get(key))}"
             for key, label in PROFILE_ELEMENTS if _oneline(d.get(key))]
    return "\n".join(lines)


def profile_dict_from_text(text: str) -> dict:
    """键值块文本 → 三要素 dict（容错手改：全/半角冒号、未知行忽略）。"""
    d = {key: "" for key, _ in PROFILE_ELEMENTS}
    for line in (text or "").splitlines():
        for sep in ("：", ":"):
            if sep in line:
                label, val = line.split(sep, 1)
                key = _LABEL_TO_KEY.get(label.strip())
                if key:
                    d[key] = val.strip()
                break
    return d


def load_researcher_profile() -> dict:
    """读机器级画像 → 三要素 dict（缺文件回全空，供首填/无画像「发现力打折」）。"""
    p = researcher_md_path()
    if not p.exists():
        return {key: "" for key, _ in PROFILE_ELEMENTS}
    return profile_dict_from_text(p.read_text(encoding="utf-8"))


def save_researcher_profile(d: dict) -> None:
    """写机器级画像（首版单向源头：机器级→论文快照）。不进 git、跨机由用户软链。"""
    p = researcher_md_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile_text_from_dict(d) + "\n", encoding="utf-8")


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
            timeout=60, **kw)
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
        # 显式钉官方端点：用户 shell 全局导出的 OPENAI_BASE_URL（kimi 代理）会被 litellm
        # 读走，把官方 key 发往代理 → AuthenticationError（冒烟 2026-07-07 实证）
        out["openai"] = _litellm_call("openai/chat-latest", api_key=os.getenv("OPENAI_API_KEY"),
                                      api_base="https://api.openai.com/v1")
    if os.getenv("GOOGLE_API_KEY"):
        out["gemini"] = _litellm_call("gemini/gemini-3.1-flash-lite", api_key=os.getenv("GOOGLE_API_KEY"))
    return out


# 第一性拆解是全扫描根基，且是首批卡的串行前置——挑快而稳的家，避开 deepseek
# （实测 2026-07-07：decompose 耗时 gemini 2.5s / openai 5.2s / deepseek 7.9s，
#  且 deepseek 对中文 prompt 有坏 JSON 重试风险，会把首批推到 ~38s）。
DECOMPOSE_PREFERENCE = ("gemini", "openai", "deepseek")


def pick_decompose_llm(provs: dict):
    for tag in DECOMPOSE_PREFERENCE:
        if tag in provs:
            return provs[tag]
    return next(iter(provs.values()))  # 全是自定义 provider → 回退第一个


def _owl_academic_query(query: str) -> str:
    return f"Has anyone studied {query.strip()}?"


def _s2_api_key():
    return os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")


def _semantic_scholar_search(query: str, limit: int):
    key = _s2_api_key()
    if not key:
        raise RuntimeError("SEMANTIC_SCHOLAR_API_KEY/S2_API_KEY missing")
    params = parse.urlencode(
        {"query": query, "limit": limit, "fields": "paperId,title,url,publicationDate"}
    )
    data = _http_json(
        f"https://api.semanticscholar.org/graph/v1/paper/search?{params}",
        headers={"x-api-key": key},
    )
    rows = data.get("data") or []
    return ProviderSearchResult(
        total=int(data.get("total") or len(rows)),
        records=tuple(
            ProviderRecord(
                source_id=r.get("paperId") or r.get("url") or r.get("title") or "",
                title=r.get("title") or "",
                url=r.get("url") or "",
                version=r.get("publicationDate") or "",
            )
            for r in rows
        ),
    )


def _openalex_search(query: str, limit: int):
    params = {"search": query, "per-page": limit}
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.getenv("OPENALEX_MAILTO")
    data = _http_json(f"https://api.openalex.org/works?{parse.urlencode(params)}")
    rows = data.get("results") or []
    return ProviderSearchResult(
        total=int((data.get("meta") or {}).get("count") or len(rows)),
        records=tuple(
            ProviderRecord(
                source_id=r.get("id") or r.get("doi") or r.get("title") or "",
                title=r.get("title") or "",
                url=r.get("doi") or r.get("id") or "",
                version=r.get("updated_date") or "",
            )
            for r in rows
        ),
    )


def _merge_academic_searches(query: str, limit: int):
    academic_query = _owl_academic_query(query)
    gateway = EvidenceGateway(
        (
            FunctionEvidenceProvider("semantic_scholar", _semantic_scholar_search),
            FunctionEvidenceProvider("openalex", _openalex_search),
        )
    )
    return gateway.search(academic_query, limit)


def real_en_search(k: int = 5):
    def search(query):
        return _merge_academic_searches(query, k)

    s2_mode = "s2" if _s2_api_key() else "openalex-only"
    return _cached_search("en", k, f"academic:{s2_mode}", ttl=7 * 24 * 3600, search=search)


def real_cnki_search(limit: int = 5):
    """中文学界面（新颖性判据）：opencli cnki search，CSSCI 过滤。
    需 Chrome 会话（`opencli browser open <url>` 一次）；无会话/风控抛错 → 上层降级「中文面未检」。
    错误输出是 YAML 风格文本（`ok: false` 块）非合法 JSON。
    ⚠️ EMPTY_RESULT（零命中）≠ 未检：必须回结构化 empty（zh_hits=0），交叉空白/金矿判据靠它触发
    （冒烟 2026-07-07：之前当异常抛导致金标永不可能出现）。"""

    def failure_state(blob: str):
        value = blob.upper()
        if any(
            token in value
            for token in (
                "NO_BROWSER_SESSION",
                "BROWSER SESSION",
                "SESSION IS REQUIRED",
                "AUTH",
                "LOGIN",
                "403",
            )
        ):
            return "authentication-required"
        if any(token in value for token in ("RATE_LIMIT", "TOO_MANY_REQUESTS", "429")):
            return "rate-limited"
        return "bad-payload"

    def provider_search(query):
        try:
            r = subprocess.run(
                ["opencli", "cnki", "search", query, "--source_category", "CSSCI",
                 "--limit", str(limit), "-f", "json"],
                capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired as e:
            raise EvidenceProviderError("timeout", "cnki search timed out") from e
        except FileNotFoundError as e:
            raise EvidenceProviderError("unavailable", "opencli not found") from e
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            blob = (r.stdout or "") + (r.stderr or "")
            if "EMPTY_RESULT" in blob:
                return ProviderSearchResult(total=0, records=())
            raise EvidenceProviderError(failure_state(blob), blob[:200])
        if isinstance(data, dict) and not data.get("ok", True):
            error = data.get("error") or {}
            if error.get("code") == "EMPTY_RESULT":
                return ProviderSearchResult(total=0, records=())
            raise EvidenceProviderError(
                failure_state(f"{error.get('code', '')} {error.get('message', '')}"),
                error.get("message", "cnki search failed"),
            )
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise EvidenceProviderError("bad-payload", "cnki search data must be a list")
        return ProviderSearchResult(
            total=len(rows),
            records=tuple(
                ProviderRecord(
                    source_id=row.get("id") or row.get("url") or row.get("title") or "",
                    title=row.get("title") or row.get("name") or "",
                    url=row.get("url") or row.get("link") or "",
                    version=str(row.get("year") or row.get("date") or ""),
                    source_kind="cnki-record",
                )
                for row in rows
                if isinstance(row, dict)
            ),
        )

    cached = _cached_search(
        "cnki", limit, "CSSCI", ttl=3 * 24 * 3600, search=provider_search
    )
    gateway = EvidenceGateway(
        (FunctionEvidenceProvider("cnki", lambda query, _limit: cached(query)),)
    )
    return lambda query: gateway.search(query, limit)


class OwnEvidenceSearch:
    """Return zsearch counts immediately; enrich Zotero identity as a second phase."""

    def __init__(self, cached_search, limit, zotero_adapter):
        self.cached_search = cached_search
        self.limit = limit
        self.zotero_adapter = zotero_adapter
        self.identity_cache = {}
        self.pending = {}
        self.lock = threading.Lock()

    def __call__(self, query):
        def provider_search(value, _limit):
            batch = self.cached_search(value)
            with self.lock:
                self.pending[value] = batch
            return batch

        gateway = EvidenceGateway(
            (FunctionEvidenceProvider("zsearch", provider_search),)
        )
        payload = gateway.search(query, self.limit)
        payload["identity_status"] = None
        return payload

    def enrich(self, payload):
        query = payload["query"]
        with self.lock:
            batch = self.pending.pop(query, None)
        if batch is None:
            batch = self.cached_search(query)
        key = tuple((record.source_id, record.url) for record in batch.records)
        with self.lock:
            enrichment = self.identity_cache.get(key)
        if enrichment is None:
            enrichment = self.zotero_adapter.enrich(batch.records)
            if enrichment.status["state"] in {"ok", "empty"}:
                with self.lock:
                    self.identity_cache[key] = enrichment

        enriched_gateway = EvidenceGateway(
            (
                FunctionEvidenceProvider(
                    "zsearch",
                    lambda _query, _limit: ProviderSearchResult(
                        total=batch.total, records=enrichment.records
                    ),
                ),
            )
        )
        # own_hits 的 top-k 只约束 zsearch parent；Zotero 关联对象必须完整保留。
        result = enriched_gateway.search(query, max(self.limit, len(enrichment.records)))
        result["status"] = payload["status"]
        result["statuses"] = payload["statuses"]
        result["degraded"] = payload["degraded"]
        result["identity_status"] = enrichment.status
        return result


def real_own_search(limit: int = 8, zotero_adapter=None):
    """自有语料面（unknown-knowns 信号）：zsearch 本地 Zotero 语义检索。
    实测：无 `zsearch "<query>"` 顶层用法；真实子命令是 `zsearch query <text> -k N --json`，
    输出干净 JSON 数组（元素含 title/url/key 等字段）。第一阶段立即返回 own_hits 与
    unresolved/context EvidenceRef；第二阶段由 OwnEvidenceSearch.enrich() 只读补全 Zotero 身份。"""

    def provider_search(query):
        try:
            r = subprocess.run(
                ["zsearch", "query", query, "-k", str(limit), "--json"],
                capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise EvidenceProviderError("timeout", "zsearch timed out") from e
        except FileNotFoundError as e:
            raise EvidenceProviderError("unavailable", "zsearch not found") from e
        if r.returncode != 0:
            raise EvidenceProviderError("unavailable", (r.stderr or "zsearch failed")[:200])
        try:
            rows = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise EvidenceProviderError("bad-payload", "zsearch returned invalid JSON") from e
        if not isinstance(rows, list):
            raise EvidenceProviderError("bad-payload", "zsearch result must be a list")
        return ProviderSearchResult(
            total=len(rows),
            records=tuple(
                ProviderRecord(
                    source_id=row.get("key") or row.get("url") or row.get("title") or "",
                    title=row.get("title") or "",
                    url=row.get("url") or "",
                    source_kind="library-document",
                    relation="context",
                    verification_status="unresolved",
                )
                for row in rows[:limit]
                if isinstance(row, dict)
            ),
        )

    cached = _cached_search(
        "own", limit, "zsearch", ttl=7 * 24 * 3600, search=provider_search
    )
    return OwnEvidenceSearch(cached, limit, zotero_adapter or ZoteroLocalAdapter())


if __name__ == "__main__":
    import argparse
    import time

    from knowledge_storm.utils import load_api_key

    load_api_key(toml_file_path=str(Path(__file__).parent / "secrets.toml"))
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--profile", default="")
    ap.add_argument("--puzzle", default="")
    ap.add_argument("--output-dir", default=None)
    a = ap.parse_args()
    out = a.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or f"./results/muse/{a.topic[:20]}"
    t0 = time.time()
    provs = real_providers()
    print(f"providers: {list(provs)}")

    def show(c):
        print(f"[{time.time()-t0:6.1f}s] {c['type']}｜{c['name']}｜{c.get('novelty')}"
              f"{'｜🥇' if c.get('gold') else ''}{'｜🔸离群' if c.get('outlier') else ''}"
              f"｜own={c.get('own_hits')}")

    cards = run_scan(a.topic, a.profile, out, provs,
                     decompose_llm=pick_decompose_llm(provs),
                     en_search=real_en_search(), zh_search=real_cnki_search(),
                     own_search=real_own_search(), on_card=show, puzzle=a.puzzle)
    print(f"共 {len(cards)} 张卡，产物在 {out}/docs/agents/muse/")
