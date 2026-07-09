"""
盲区扫描引擎（spec v2 §4）：第一性原理拆解 → 多模型三类卡枚举 →
去重/离群/抑制 → 新颖性三角定位（英文 web + zsearch 中文/自有语料）→ 流式出卡 → 落盘。

纯引擎，LM 与检索全部依赖注入，便于离线测试；真实接线见文件末尾 real_* 系列与 CLI。
"""

import json
import logging
import os
import re
import subprocess
import threading
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from urllib import parse, request

import json_repair

# ---- persona（用户提供原文后整体替换本常量）----
FIRST_PRINCIPLES_PERSONA = (
    "你是第一性原理思考者：回到问题的根本，把问题拆解到最小可验证单元，"
    "永远追问『为什么成立』而不是『怎么做』；拒绝沿袭现成框架的惯性。"
)

CARD_TYPES = ["学科视角", "理论框架", "研究方法"]

_CACHE_VERSION = "retrieval-v1"
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


# ---- 提示词 + 拆解与枚举（注入式 LM）----

ENUM_SCHEMA_HINT = (
    '只输出 JSON：{"cards": [{"type": "学科视角|理论框架|研究方法", "name": "...", '
    '"mechanism": "一句话机制", "why_nonobvious": "为什么对该研究者非显而易见", '
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


def _en_payload(en):
    if isinstance(en, dict):
        results = en.get("results") or en.get("anchors") or []
        return {
            "hits": int(en.get("hits") or len(results)),
            "results": results,
            "source": en.get("source"),
            "degraded": en.get("degraded"),
        }
    results = en or []
    return {"hits": len(results), "results": results, "source": None, "degraded": None}


def _novelty_for(card, topic, en_search, zh_search, own_search=None,
                 zh_gate=None, own_gate=None, zh_keyword=None):
    """新颖性三面定位，逐面原地回填（卡已上墙，字段更新靠 /scan/status 轮询快照可见）。
    顺序：en → own（秒级）先挂，zh（CNKI 走浏览器，慢且必须串行）最后定 novelty/gold。"""
    query = f"{card['name']} {topic}"
    try:
        en = en_search(query) or []
    except Exception as e:
        logging.warning(f"en_search 失败（en_hits=0 计）[{card['name']}]：{e}")
        en = []
    en_payload = _en_payload(en)
    card["en_hits"] = en_payload["hits"]
    card["en_source"] = en_payload["source"]
    card["en_degraded"] = en_payload["degraded"]
    card["anchors"] = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in en_payload["results"][:3]]
    if own_search is None:
        card["own_hits"] = None
    else:
        try:
            with (own_gate or nullcontext()):
                card["own_hits"] = len(own_search(query) or [])  # 自有语料面 = zsearch
        except Exception as e:
            logging.warning(f"own_search 失败（own_hits 置空）[{card['name']}]：{e}")
            card["own_hits"] = None
    kw = zh_keyword() if callable(zh_keyword) else zh_keyword
    zq = f"{_zh_name_core(card['name'])} {kw or topic}"
    try:
        with (zh_gate or nullcontext()):
            zh = zh_search(zq)  # 中文学界面 = CNKI（新颖性判据）
        zh_hits = len(zh or [])  # []＝真零命中（交叉空白/金矿判据靠它触发）
    except Exception as e:
        # 降级必须留痕：中文面是新颖性判据，静默吞掉会让「未检」无从诊断
        logging.warning(f"zh_search 失败（降级中文面未检）[{card['name']}] q={zq!r}：{e}")
        zh_hits = None  # 中文面未检，明示不装懂
    card["novelty"], card["gold"] = classify_novelty(en_payload["hits"], zh_hits)
    card["zh_hits"] = zh_hits
    return card


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
            f"- 为什么非显而易见：{c['why_nonobvious']}",
            f"- 最强反驳：{c['steelman']}",
        ]
        if c.get("feasibility"):
            lines.append(f"- 可行性/数据：{c['feasibility']}")
        lines.append(f"- 提出方：{'、'.join(c['source_models'])}；英文命中 {c.get('en_hits')}，中文学界命中 {c.get('zh_hits')}，自有库命中 {c.get('own_hits')}")
        qlines += [f"\n## {c['name']}"] + [f"- {q}" for q in c.get("questions", [])]
        for a in c.get("anchors", []):
            slines.append(f"- [{c['name']}] {a['title']} — {a['url']}")
    (d / "perspectives.md").write_text("\n".join(lines), encoding="utf-8")
    (d / "questions.md").write_text("\n".join(qlines), encoding="utf-8")
    (d / "sources.md").write_text("\n".join(slines), encoding="utf-8")


def run_scan(topic, profile, output_dir, providers, decompose_llm,
             en_search, zh_search, on_card, own_search=None, puzzle="", on_update=None):
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
                key = normalize_name(c["name"])
                if key in suppressed:
                    continue
                if key in wall:
                    prev = wall[key]
                    prev["source_models"] = sorted(set(prev["source_models"]) | set(c["source_models"]))
                    touched.append(prev)
                else:
                    c = dict(c)
                    # 徽标字段占位：上墙后原地更新只换值不加键（快照序列化安全）
                    c.update(id=len(order) + 1, outlier=None, novelty=None, gold=None,
                             en_hits=None, en_source=None, en_degraded=None,
                             zh_hits=None, own_hits=None, anchors=[])
                    wall[key] = c
                    order.append(key)
                    fresh.append(c)
        for c in fresh:
            on_card(c)
            def enrich(card=c):
                _novelty_for(card, topic, en_search, zh_search, own_search,
                             zh_gate, own_gate, zh_keyword)
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

    all_cards = [wall[k] for k in order]
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


def _academic_result(title="", url=""):
    return {"title": title or "", "url": url or ""}


def _semantic_scholar_search(query: str, limit: int):
    key = _s2_api_key()
    if not key:
        raise RuntimeError("SEMANTIC_SCHOLAR_API_KEY/S2_API_KEY missing")
    params = parse.urlencode({"query": query, "limit": limit, "fields": "title,url"})
    data = _http_json(
        f"https://api.semanticscholar.org/graph/v1/paper/search?{params}",
        headers={"x-api-key": key},
    )
    rows = data.get("data") or []
    return {
        "hits": int(data.get("total") or len(rows)),
        "results": [_academic_result(r.get("title"), r.get("url")) for r in rows],
    }


def _openalex_search(query: str, limit: int):
    params = {"search": query, "per-page": limit}
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.getenv("OPENALEX_MAILTO")
    data = _http_json(f"https://api.openalex.org/works?{parse.urlencode(params)}")
    rows = data.get("results") or []
    return {
        "hits": int((data.get("meta") or {}).get("count") or len(rows)),
        "results": [
            _academic_result(r.get("title"), r.get("doi") or r.get("id")) for r in rows
        ],
    }


def _merge_academic_searches(query: str, limit: int):
    academic_query = _owl_academic_query(query)
    results, degraded, sources, totals = [], [], [], []

    for name, search in (
        ("semantic_scholar", _semantic_scholar_search),
        ("openalex", _openalex_search),
    ):
        try:
            payload = search(query, limit)
            sources.append(name)
            totals.append(payload["hits"])
            for item in payload["results"]:
                key = (item.get("url") or item.get("title") or "").lower()
                if key and key not in {r["_key"] for r in results}:
                    results.append({**item, "_key": key})
        except Exception as e:
            degraded.append(f"{name}: {e}")

    return {
        "hits": max(totals) if totals else 0,
        "results": [{k: v for k, v in r.items() if k != "_key"} for r in results[:limit]],
        "source": "+".join(sources) if sources else None,
        "degraded": "; ".join(degraded) if degraded else None,
        "query": academic_query,
    }


def real_en_search(k: int = 5):
    def search(query):
        return _merge_academic_searches(query, k)

    s2_mode = "s2" if _s2_api_key() else "openalex-only"
    return _cached_search("en", k, f"academic:{s2_mode}", ttl=7 * 24 * 3600, search=search)


def real_cnki_search(limit: int = 5):
    """中文学界面（新颖性判据）：opencli cnki search，CSSCI 过滤。
    需 Chrome 会话（`opencli browser open <url>` 一次）；无会话/风控抛错 → 上层降级「中文面未检」。
    错误输出是 YAML 风格文本（`ok: false` 块）非合法 JSON。
    ⚠️ EMPTY_RESULT（零命中）≠ 未检：必须回 []（zh_hits=0），交叉空白/金矿判据靠它触发
    （冒烟 2026-07-07：之前当异常抛导致金标永不可能出现）。"""

    def search(query):
        r = subprocess.run(
            ["opencli", "cnki", "search", query, "--source_category", "CSSCI",
             "--limit", str(limit), "-f", "json"],
            capture_output=True, text=True, timeout=90)
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            blob = (r.stdout or "") + (r.stderr or "")
            if "EMPTY_RESULT" in blob:
                return []  # 真零命中（实测：错误 YAML 走 stderr、stdout 为空、exit 66）
            raise RuntimeError(blob[:200])
        if isinstance(data, dict) and not data.get("ok", True):
            if (data.get("error") or {}).get("code") == "EMPTY_RESULT":
                return []  # 真零命中
            raise RuntimeError(data.get("error", {}).get("message", "cnki search failed"))
        rows = data.get("data") if isinstance(data, dict) else data
        return rows or []

    return _cached_search("cnki", limit, "CSSCI", ttl=3 * 24 * 3600, search=search)


def real_own_search(limit: int = 8):
    """自有语料面（unknown-knowns 信号）：zsearch 本地 Zotero 语义检索。
    实测：无 `zsearch "<query>"` 顶层用法；真实子命令是 `zsearch query <text> -k N --json`，
    输出干净 JSON 数组（元素含 title/url 等字段），故直接 json.loads 取 title/url，
    不走 spec 基准里假设的「行文本、每行一条」解析。"""

    def search(query):
        r = subprocess.run(
            ["zsearch", "query", query, "-k", str(limit), "--json"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[:200])
        rows = json.loads(r.stdout)
        return [{"title": row.get("title", ""), "url": row.get("url", "")} for row in rows[:limit]]

    return _cached_search("own", limit, "zsearch", ttl=7 * 24 * 3600, search=search)


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
