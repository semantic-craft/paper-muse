"""对抗幕·证伪检索 sidecar（issue #8）——**跑在隔离 .venv-gptr**（gpt-researcher 重依赖不进主 venv）。

主引擎（adversary.py，主 venv）经子进程调本脚本：
    .venv-gptr/bin/python gptr_sidecar.py   # stdin: JSON payload → stdout: 末行 JSON result

payload  = {"claim": str, "failures": [{"id","statement"}], "topic"?: str,
            "want_memo"?: bool, "max_results"?: int}
result   = {"ok": bool, "sources": [{"title","url","content"}], "memo": str,
            "en_hits": int, "zh_hits": int|null, "by_retriever": {...}, "error"?: str}
            zh_hits=null ⇒ 中文/自有面全降级（无 CNKI 会话且 zsearch 不可用），明示未检。

嵌 gpt-researcher：多源混跑 tavily（英文 web）+ **custom CNKI**（opencli，需 Chrome 会话）
+ **custom zsearch**（自有语料），report_type=custom_report 出「证伪备忘录」。预算靠 config 上限
（单趟 custom_report 不递归、每子查询命中上限、token 上限）内终止。

本 sidecar **只检索、不下裁决**：证伪/佐证/未决三态由主引擎 `decide_verdict` 代码强制
（抗注入的核心不能挪进这里——见 adversary.py 顶注）。
"""
import asyncio
import json
import os
import re
import subprocess
import sys

# ── 命中计账（双面密度）：每个检索器 search() 的原始返回数累加进来 ──
TALLY = {}  # name -> {"hits": int, "ok": bool}


def _bump(name, n, ok):
    t = TALLY.setdefault(name, {"hits": 0, "ok": False})
    t["hits"] += n
    t["ok"] = t["ok"] or ok


def _fallback_title(url, content):
    """gpt-researcher 的 research source 常无 title → 用正文首行（截断）兜底，再退到域名。"""
    line = next((ln.strip() for ln in (content or "").splitlines() if ln.strip()), "")
    if line:
        return line[:60]
    m = re.search(r"https?://([^/]+)", url or "")
    return (m.group(1).replace("www.", "") if m else (url or "")[:40]) or "来源"


def _zh_core(query: str) -> str:
    """gpt-researcher 生成的子查询可能是整句——CNKI 对长复合查询必空（blindspot 冒烟实证），
    收敛到中文短词（去英文/标点，取前若干汉字段）。"""
    core = re.sub(r"[（(][^）)]*[）)]", "", query)
    core = re.sub(r"[A-Za-z0-9·\-–—“”\"'’‘]+", " ", core)
    core = re.sub(r"[，,、。？?！!：:；;（）()\[\]【】]", " ", core)
    parts = [p for p in core.split() if p]
    return " ".join(parts[:2]) or query.strip()


class CNKIRetriever:
    """中文学界面（新颖性/证伪判据）：opencli cnki search，CSSCI 过滤。
    契约同 blindspot.real_cnki_search：EMPTY_RESULT→[]（真零命中）；无 Chrome 会话/风控→抛错
    （由 tally 记 ok=False = 降级明示）。"""

    def __init__(self, query, query_domains=None):
        self.query = query

    def search(self, max_results=5):
        q = _zh_core(self.query)
        try:
            r = subprocess.run(
                ["opencli", "cnki", "search", q, "--source_category", "CSSCI",
                 "--limit", str(max_results), "-f", "json"],
                capture_output=True, text=True, timeout=90)
        except Exception as e:
            _bump("cnki", 0, False)
            raise RuntimeError(f"cnki subprocess 失败：{e}")
        blob = (r.stdout or "") + (r.stderr or "")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            if "EMPTY_RESULT" in blob:
                _bump("cnki", 0, True)   # 真零命中：会话在、就是没查到
                return []
            _bump("cnki", 0, False)      # 无会话/风控等 → 降级
            raise RuntimeError(blob[:160])
        if isinstance(data, dict) and not data.get("ok", True):
            if (data.get("error") or {}).get("code") == "EMPTY_RESULT":
                _bump("cnki", 0, True)
                return []
            _bump("cnki", 0, False)
            raise RuntimeError((data.get("error") or {}).get("message", "cnki failed"))
        rows = data.get("data") if isinstance(data, dict) else data
        out = [{"title": x.get("title", ""), "href": x.get("url", ""),
                "body": x.get("abstract") or x.get("title", "")} for x in (rows or [])]
        _bump("cnki", len(out), True)
        return out[:max_results]


class ZsearchRetriever:
    """自有语料证伪面：zsearch 本地 Zotero 语义检索（无 Chrome 依赖，快）。
    契约同 blindspot.real_own_search：`zsearch query <text> -k N --json` → JSON 数组。"""

    def __init__(self, query, query_domains=None):
        self.query = query

    def search(self, max_results=5):
        try:
            r = subprocess.run(
                ["zsearch", "query", self.query, "-k", str(max_results), "--json"],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                _bump("zsearch", 0, False)
                raise RuntimeError((r.stderr or "")[:160])
            rows = json.loads(r.stdout)
        except Exception as e:
            _bump("zsearch", 0, False)
            raise RuntimeError(f"zsearch 失败：{e}")
        out = [{"title": x.get("title", ""), "href": x.get("url", ""),
                "body": x.get("abstract") or x.get("title", "")} for x in (rows or [])]
        _bump("zsearch", len(out), True)
        return out[:max_results]


def _patch_gptr():
    """把 CNKI/zsearch 注册为 gpt-researcher 的 in-process 检索器：
    ① 放宽 Config 校验名单（parse_retrievers 会拒未知名）；② 解析器映射名→类；
    并把 tavily/cnki/zsearch 全包一层计账（双面密度）。"""
    import gpt_researcher.retrievers.utils as U
    import gpt_researcher.actions.retriever as R

    _names = U.get_all_retriever_names
    U.get_all_retriever_names = lambda: list(_names()) + ["cnki", "zsearch"]

    def counting(name, cls):
        class Counting(cls):
            def search(self, max_results=5):
                res = super().search(max_results=max_results) or []
                if name == "tavily":       # 内建检索器自己不计账，这里补上
                    _bump(name, len(res), True)
                return res
        Counting.__name__ = f"Counting_{name}"
        return Counting

    _gr = R.get_retriever

    def get_retriever(name):
        base = {"cnki": CNKIRetriever, "zsearch": ZsearchRetriever}.get(name) or _gr(name)
        return counting(name, base) if base else base

    R.get_retriever = get_retriever


def _falsify_prompt(claim, failures, topic):
    lines = "\n".join(f"{i + 1}. [{f.get('id', i + 1)}] {f.get('statement', '')}"
                      for i, f in enumerate(failures))
    return (
        "你是最苛刻的匿名学术审稿人，为下面这条中文法学论文的中心主张撰写一份**证伪备忘录**"
        "（falsification memo，证伪导向，不写综述、不复述主张）。\n\n"
        f"{'研究主题：' + topic + chr(10) if topic else ''}"
        f"中心主张：{claim}\n\n"
        f"红队已指出以下最可能让它崩塌的失败点：\n{lines}\n\n"
        "检索中英文学术文献与自有语料，逐个失败点找**证伪（削弱主张）**或**佐证（支持主张）**的证据，"
        "给出带来源 URL 的证据，并点明每条证据支持还是削弱主张。没有证据的失败点，明确写「未检得证据」。"
    )


def _env_setup():
    """gpt-researcher 全走 env 配置。OPENAI_BASE_URL 必须显式钉——否则 shell 全局导出的
    代理 base（kimi 之类）会被读走，官方 key 发往代理即 AuthError（blindspot 冒烟实证）。"""
    os.environ["RETRIEVER"] = os.getenv("GPTR_RETRIEVER", "tavily,cnki,zsearch")
    os.environ["OPENAI_BASE_URL"] = os.getenv("GPTR_OPENAI_BASE_URL", "https://api.openai.com/v1")
    os.environ.setdefault("FAST_LLM", os.getenv("GPTR_FAST_LLM", "openai:gpt-4o-mini"))
    os.environ.setdefault("SMART_LLM", os.getenv("GPTR_SMART_LLM", "openai:gpt-4.1"))
    os.environ.setdefault("STRATEGIC_LLM", os.getenv("GPTR_STRATEGIC_LLM", "openai:gpt-4.1"))
    os.environ.setdefault("EMBEDDING", os.getenv("GPTR_EMBEDDING", "openai:text-embedding-3-small"))
    os.environ.setdefault("LANGUAGE", "chinese")          # 子查询走中文，利 CNKI/zsearch
    os.environ.setdefault("REPORT_SOURCE", "web")
    os.environ.setdefault("CURATE_SOURCES", "false")      # 省一趟 embedding 相关处理
    os.environ.setdefault("MAX_SEARCH_RESULTS_PER_QUERY", os.getenv("GPTR_MAX_RESULTS", "5"))
    os.environ.setdefault("FAST_TOKEN_LIMIT", "2000")
    os.environ.setdefault("SMART_TOKEN_LIMIT", "4000")


async def run(payload):
    claim = (payload.get("claim") or "").strip()
    failures = payload.get("failures") or []
    if not claim:
        return {"ok": False, "sources": [], "memo": "", "en_hits": 0, "zh_hits": None,
                "by_retriever": {}, "error": "empty claim"}
    _env_setup()
    _patch_gptr()
    from gpt_researcher import GPTResearcher

    prompt = _falsify_prompt(claim, failures, payload.get("topic", ""))
    researcher = GPTResearcher(query=prompt, report_type="custom_report", report_source="web")
    await researcher.conduct_research()
    sources = researcher.get_research_sources() or []
    memo = ""
    if payload.get("want_memo", True):
        memo = await researcher.write_report()

    norm = []
    for s in sources:
        url = s.get("url") or s.get("href") or ""
        if not url:
            continue
        content = (s.get("content") or s.get("raw_content") or s.get("body") or "")
        norm.append({"title": (s.get("title") or "").strip() or _fallback_title(url, content),
                     "url": url, "content": content[:1200]})

    en = TALLY.get("tavily", {}).get("hits", 0)
    cnki, zs = TALLY.get("cnki", {}), TALLY.get("zsearch", {})
    zh_ok = cnki.get("ok", False) or zs.get("ok", False)
    zh_hits = (cnki.get("hits", 0) + zs.get("hits", 0)) if zh_ok else None
    return {"ok": True, "sources": norm, "memo": memo, "en_hits": en, "zh_hits": zh_hits,
            "by_retriever": TALLY}


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        result = asyncio.run(run(payload))
    except Exception as e:
        import traceback
        result = {"ok": False, "sources": [], "memo": "", "en_hits": 0, "zh_hits": None,
                  "by_retriever": {}, "error": f"{e}\n{traceback.format_exc()[-800:]}"}
    # 末行 = 结果 JSON（gpt-researcher 的日志走 stdout 时，主引擎只取最后一行 JSON）
    sys.stdout.write("\n__GPTR_RESULT__" + json.dumps(result, ensure_ascii=False) + "\n")
