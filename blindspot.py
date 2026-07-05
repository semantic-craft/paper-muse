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
    s = re.sub(r"[（(][^）)]*[）)]", "", name)  # 括号注记（缩写/译名）整体剔除
    s = re.sub(r"[\s【】\[\]\-—·]", "", s.strip().lower())
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


# ---- 提示词 + 拆解与枚举（注入式 LM）----

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


def _novelty_for(card, topic, en_search, zh_search, own_search=None):
    query = f"{card['name']} {topic}"
    try:
        en = en_search(query) or []
    except Exception:
        en = []
    try:
        zh = zh_search(query)  # 中文学界面 = CNKI（新颖性判据）
        zh_hits = len(zh or [])
    except Exception:
        zh_hits = None  # 中文面未检，明示不装懂
    if own_search is None:
        card["own_hits"] = None
    else:
        try:
            card["own_hits"] = len(own_search(query) or [])  # 自有语料面 = zsearch（unknown-knowns 信号）
        except Exception:
            card["own_hits"] = None
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
             en_search, zh_search, on_card, own_search=None):
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
        _novelty_for(card, topic, en_search, zh_search, own_search)
        with lock:
            all_cards.append(card)
        on_card(card)

    _write_outputs(output_dir, topic, profile, all_cards)
    return all_cards


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


def real_cnki_search(limit: int = 5):
    """中文学界面（新颖性判据）：opencli cnki search，CSSCI 过滤。
    需 Chrome 会话（`opencli browser open <url>` 一次）；无会话/风控抛错 → 上层降级「中文面未检」。
    实测：无会话时 `-f json` 仍输出 YAML 风格错误文本（`ok: false` 块），非合法 JSON——
    json.loads 会直接抛 JSONDecodeError，被这里的调用方（_novelty_for）当普通异常吞掉，
    无需额外分支识别该文本形态。"""

    def search(query):
        r = subprocess.run(
            ["opencli", "cnki", "search", query, "--source_category", "CSSCI",
             "--limit", str(limit), "-f", "json"],
            capture_output=True, text=True, timeout=90)
        data = json.loads(r.stdout)
        if isinstance(data, dict) and not data.get("ok", True):
            raise RuntimeError(data.get("error", {}).get("message", "cnki search failed"))
        rows = data.get("data") if isinstance(data, dict) else data
        return rows or []

    return search


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
              f"{'｜🥇' if c.get('gold') else ''}{'｜🔸离群' if c.get('outlier') else ''}"
              f"｜own={c.get('own_hits')}")

    cards = run_scan(a.topic, a.profile, out, provs,
                     decompose_llm=next(iter(provs.values())),
                     en_search=real_en_search(), zh_search=real_cnki_search(),
                     own_search=real_own_search(), on_card=show)
    print(f"共 {len(cards)} 张卡，产物在 {out}/docs/agents/muse/")
