"""对抗幕·证伪检索 sidecar（issue #8）——**跑在隔离 .venv-gptr**（gpt-researcher 重依赖不进主 venv）。

主引擎（adversary.py，主 venv）经子进程调本脚本：
    .venv-gptr/bin/python gptr_sidecar.py   # stdin: JSON payload → stdout: 末行 JSON result

payload  = {"claim": str, "failures": [{"id","statement"}], "topic"?: str,
            "want_memo"?: bool, "max_results"?: int}
result   = {"ok": bool, "sources": [{"title","url","content"}], "memo": str,
            "en_hits": int, "zh_hits": int|null, "by_retriever": {...}, "error"?: str}
            zh_hits=null ⇒ 中文/自有面全降级（无 CNKI 会话且 zsearch 不可用），明示未检。

嵌 gpt-researcher 0.15.1：通过其公开 custom endpoint 契约
（RETRIEVER=tavily,custom + RETRIEVER_ENDPOINT）多源混跑英文 web、CNKI（需 Chrome 会话）
与 zsearch（自有语料），report_type=custom_report 出「证伪备忘录」。本机 loopback endpoint
只聚合两种专门来源，不修改 GPT Researcher 的 retriever registry/parser。预算靠 config 上限终止。

本 sidecar **只检索、不下裁决**：证伪/佐证/未决三态由主引擎 `decide_verdict` 代码强制
（抗注入的核心不能挪进这里——见 adversary.py 顶注）。
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from evidence import ProviderRecord
from zotero_local import ZoteroLocalAdapter

# ── 命中计账（双面密度）：每个检索器 search() 的原始返回数累加进来 ──
TALLY = {}  # name -> {"hits": int, "ok": bool}
TALLY_LOCK = threading.Lock()


def _bump(name, n, ok):
    with TALLY_LOCK:
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
                [
                    "opencli",
                    "cnki",
                    "search",
                    q,
                    "--source_category",
                    "CSSCI",
                    "--limit",
                    str(max_results),
                    "-f",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
        except Exception as e:
            raise RuntimeError(f"cnki subprocess 失败：{e}")
        blob = (r.stdout or "") + (r.stderr or "")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            if "EMPTY_RESULT" in blob:
                return []
            raise RuntimeError(blob[:160])
        if isinstance(data, dict) and not data.get("ok", True):
            if (data.get("error") or {}).get("code") == "EMPTY_RESULT":
                return []
            raise RuntimeError((data.get("error") or {}).get("message", "cnki failed"))
        rows = data.get("data") if isinstance(data, dict) else data
        out = [
            {
                "title": x.get("title", ""),
                "href": x.get("url", ""),
                "body": x.get("abstract") or x.get("title", ""),
                "source_id": x.get("id") or x.get("dbcode") or x.get("url", ""),
                "version": x.get("version") or "",
            }
            for x in (rows or [])
        ]
        return out[:max_results]


class ZsearchRetriever:
    """自有语料证伪面：zsearch 本地 Zotero 语义检索（无 Chrome 依赖，快）。
    契约同 blindspot.real_own_search：`zsearch query <text> -k N --json` → JSON 数组。"""

    def __init__(self, query, query_domains=None, zotero_adapter=None):
        self.query = query
        self.zotero_adapter = zotero_adapter or ZoteroLocalAdapter()

    def search(self, max_results=5):
        try:
            r = subprocess.run(
                ["zsearch", "query", self.query, "-k", str(max_results), "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "")[:160])
            rows = json.loads(r.stdout)
            if not isinstance(rows, list):
                raise ValueError("zsearch output must be a list")
        except Exception as e:
            raise RuntimeError(f"zsearch 失败：{e}")
        rows = [row for row in rows if isinstance(row, dict)]
        records = tuple(
            ProviderRecord(
                source_id=str(
                    row.get("key")
                    or row.get("source_id")
                    or row.get("id")
                    or row.get("url")
                    or ""
                ),
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                version=str(row.get("version") or ""),
                source_kind="library-document",
                relation="context",
                verification_status="unresolved",
            )
            for row in rows
        )
        enrichment = self.zotero_adapter.enrich(records)
        enriched = enrichment.records
        out = []
        for row, original in zip(rows, records):
            attachments = [
                record
                for record in enriched
                if record.native
                and record.native.get("parent_item") == original.source_id
                and record.native.get("attachment_key")
            ]
            direct = next(
                (
                    record
                    for record in enriched
                    if record.source_id == original.source_id
                ),
                original,
            )
            ambiguous_attachment = len(attachments) > 1
            selected = attachments[0] if len(attachments) == 1 else direct
            exact = str(
                row.get("abstract") or row.get("text") or row.get("title") or ""
            )
            raw_locator = str(row.get("url") or selected.locator_value or "")
            item = {
                "title": str(row.get("title") or selected.title),
                "href": str(row.get("url") or selected.locator_value),
                "body": exact,
                "source_id": selected.source_id,
                "version": selected.version,
                "identity": selected.identity,
                "verification_status": (
                    "unresolved"
                    if ambiguous_attachment
                    else selected.verification_status
                ),
                "identity_status": (
                    "ambiguous"
                    if ambiguous_attachment
                    else enrichment.status.get("state", "unknown")
                ),
                "locator": {
                    "kind": selected.locator_kind
                    or (
                        "zotero-select"
                        if raw_locator.startswith("zotero://")
                        else ("url" if raw_locator else "provider-id")
                    ),
                    "value": selected.locator_value
                    or str(row.get("url") or selected.source_id),
                    "exact": exact,
                },
            }
            if selected.native:
                item["native"] = selected.native
            if row.get("index_version"):
                item["index_version"] = str(row["index_version"])
            out.append(item)
        return out[:max_results]


class CustomEvidenceEndpoint:
    """Loopback implementation of GPT Researcher's public custom endpoint contract."""

    def __init__(self):
        self._server = None
        self._thread = None
        self._cache = {}
        self._provenance = {}
        self._lock = threading.Lock()

    @property
    def url(self):
        if self._server is None:
            raise RuntimeError("custom evidence endpoint is not running")
        return f"http://127.0.0.1:{self._server.server_port}/search"

    def begin_claim(self):
        with self._lock, TALLY_LOCK:
            self._cache.clear()
            self._provenance.clear()
            TALLY.clear()

    def _adapter_rows(self, name, retriever, query, max_results):
        try:
            rows = retriever(query).search(max_results=max_results) or []
        except Exception:
            _bump(name, 0, False)
            return []
        _bump(name, len(rows), True)
        normalized = []
        for row in rows:
            if not isinstance(row, dict) or not (row.get("href") or row.get("url")):
                continue
            item = {
                "url": row.get("href") or row.get("url") or "",
                "raw_content": row.get("body") or row.get("raw_content") or "",
                "title": row.get("title") or "",
                "provider": name,
                "source_id": row.get("source_id")
                or row.get("href")
                or row.get("url")
                or "",
            }
            for key in (
                "version",
                "identity",
                "index_version",
                "native",
                "locator",
                "verification_status",
                "identity_status",
            ):
                if row.get(key):
                    item[key] = row[key]
            normalized.append(item)
        return normalized

    def search(self, query, max_results=5):
        key = (" ".join(str(query or "").split()), int(max_results))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return [dict(row) for row in cached]
        with ThreadPoolExecutor(max_workers=2) as executor:
            cnki = executor.submit(
                self._adapter_rows, "cnki", CNKIRetriever, key[0], key[1]
            )
            zsearch = executor.submit(
                self._adapter_rows, "zsearch", ZsearchRetriever, key[0], key[1]
            )
            rows = cnki.result() + zsearch.result()
        with self._lock:
            self._cache[key] = [dict(row) for row in rows]
            for row in rows:
                self._provenance.setdefault(row["url"], dict(row))
        return rows

    def provenance_for(self, url):
        with self._lock:
            value = self._provenance.get(url)
            return dict(value) if value else None

    def snapshot(self):
        with TALLY_LOCK:
            by_retriever = {name: dict(stats) for name, stats in TALLY.items()}
        return {"by_retriever": by_retriever}

    def __enter__(self):
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                params = parse_qs(urlparse(self.path).query)
                query = (params.get("query") or [""])[0]
                try:
                    max_results = int((params.get("max_results") or ["5"])[0])
                except ValueError:
                    max_results = 5
                body = json.dumps(
                    endpoint.search(query, max(1, min(max_results, 20))),
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None


def _falsify_prompt(claim, failures, topic):
    lines = "\n".join(
        f"{i + 1}. [{f.get('id', i + 1)}] {f.get('statement', '')}"
        for i, f in enumerate(failures)
    )
    return (
        "你是最苛刻的匿名学术审稿人，为下面这条中文法学论文的中心主张撰写一份**证伪备忘录**"
        "（falsification memo，证伪导向，不写综述、不复述主张）。\n\n"
        f"{'研究主题：' + topic + chr(10) if topic else ''}"
        f"中心主张：{claim}\n\n"
        f"红队已指出以下最可能让它崩塌的失败点：\n{lines}\n\n"
        "检索中英文学术文献与自有语料，逐个失败点找**证伪（削弱主张）**或**佐证（支持主张）**的证据，"
        "给出带来源 URL 的证据，并点明每条证据支持还是削弱主张。没有证据的失败点，明确写「未检得证据」。"
    )


def _env_setup(endpoint_url=None):
    """gpt-researcher 全走 env 配置。OPENAI_BASE_URL 必须显式钉——否则 shell 全局导出的
    代理 base（kimi 之类）会被读走，官方 key 发往代理即 AuthError（blindspot 冒烟实证）。"""
    web_retriever = os.getenv("GPTR_WEB_RETRIEVER", "tavily")
    os.environ["RETRIEVER"] = f"{web_retriever},custom"
    if endpoint_url:
        os.environ["RETRIEVER_ENDPOINT"] = endpoint_url
        os.environ["RETRIEVER_ARG_MAX_RESULTS"] = os.getenv("GPTR_MAX_RESULTS", "5")
    os.environ["OPENAI_BASE_URL"] = os.getenv(
        "GPTR_OPENAI_BASE_URL", "https://api.openai.com/v1"
    )
    os.environ.setdefault("FAST_LLM", os.getenv("GPTR_FAST_LLM", "openai:gpt-4o-mini"))
    os.environ.setdefault("SMART_LLM", os.getenv("GPTR_SMART_LLM", "openai:gpt-4.1"))
    os.environ.setdefault(
        "STRATEGIC_LLM", os.getenv("GPTR_STRATEGIC_LLM", "openai:gpt-4.1")
    )
    os.environ.setdefault(
        "EMBEDDING", os.getenv("GPTR_EMBEDDING", "openai:text-embedding-3-small")
    )
    os.environ.setdefault("LANGUAGE", "chinese")  # 子查询走中文，利 CNKI/zsearch
    os.environ.setdefault("REPORT_SOURCE", "web")
    os.environ.setdefault("CURATE_SOURCES", "false")  # 省一趟 embedding 相关处理
    os.environ.setdefault(
        "MAX_SEARCH_RESULTS_PER_QUERY", os.getenv("GPTR_MAX_RESULTS", "5")
    )
    os.environ.setdefault("FAST_TOKEN_LIMIT", "2000")
    os.environ.setdefault("SMART_TOKEN_LIMIT", "4000")


def normalize_research_sources(sources, endpoint, web_retriever="tavily"):
    """Retain original locators and provider identity across web/custom results."""
    normalized = []
    for source in sources or []:
        url = source.get("url") or source.get("href") or ""
        if not url:
            continue
        content = (
            source.get("content")
            or source.get("raw_content")
            or source.get("body")
            or ""
        )
        provenance = endpoint.provenance_for(url)
        provider = (provenance or {}).get("provider") or web_retriever
        provenance = provenance or {}
        locator = provenance.get("locator")
        if not isinstance(locator, dict):
            locator_kind = "zotero-select" if url.startswith("zotero://") else "url"
            locator = {"kind": locator_kind, "value": url}
        item = {
            "title": (source.get("title") or provenance.get("title") or "").strip()
            or _fallback_title(url, content),
            "url": url,
            "content": (content or provenance.get("raw_content") or "")[:1200],
            "provider": provider,
            "source_id": provenance.get("source_id") or url,
            "locator": dict(locator),
        }
        for key in (
            "version",
            "identity",
            "index_version",
            "native",
            "verification_status",
            "identity_status",
        ):
            if provenance.get(key):
                item[key] = provenance[key]
        normalized.append(item)
    return normalized


def summarize_provider_statuses(sources, endpoint, web_retriever="tavily"):
    """Report only provider health observable from this public extension seam."""
    by_retriever = endpoint.snapshot()["by_retriever"]
    web_hits = sum(1 for source in sources if source["provider"] == web_retriever)
    by_retriever[web_retriever] = {
        "hits": web_hits,
        "ok": web_hits > 0,
        "state": "ok" if web_hits > 0 else "unknown",
    }
    cnki = by_retriever.setdefault("cnki", {"hits": 0, "ok": False})
    zsearch = by_retriever.setdefault("zsearch", {"hits": 0, "ok": False})
    zh_ok = cnki.get("ok", False) or zsearch.get("ok", False)
    failed = [name for name, state in by_retriever.items() if not state.get("ok")]
    return {
        "by_retriever": by_retriever,
        "en_hits": web_hits,
        "zh_hits": (cnki.get("hits", 0) + zsearch.get("hits", 0) if zh_ok else None),
        "degraded": bool(failed),
        "degradation_reason": (
            "provider unavailable or unverified: " + ", ".join(failed)
            if failed
            else None
        ),
    }


def _error_result(error: str) -> dict:
    """空/降级 result 的单点定义（契约见文件头）。_run_one 空 claim、run 批量异常、
    __main__ 顶层异常三处共用，避免同一 7 键字面量散弹式重复、schema 变更漏改（#18）。"""
    return {"ok": False, "sources": [], "memo": "", "en_hits": 0,
            "zh_hits": None, "by_retriever": {}, "error": error}


async def _run_one(payload, endpoint):
    endpoint.begin_claim()
    claim = (payload.get("claim") or "").strip()
    failures = payload.get("failures") or []
    if not claim:
        return _error_result("empty claim")
    _env_setup(endpoint.url)
    from gpt_researcher import GPTResearcher

    prompt = _falsify_prompt(claim, failures, payload.get("topic", ""))
    researcher = GPTResearcher(
        query=prompt, report_type="custom_report", report_source="web"
    )
    await researcher.conduct_research()
    sources = researcher.get_research_sources() or []
    memo = ""
    if payload.get("want_memo", True):
        memo = await researcher.write_report()

    norm = normalize_research_sources(
        sources,
        endpoint,
        web_retriever=os.getenv("GPTR_WEB_RETRIEVER", "tavily"),
    )

    web_name = os.getenv("GPTR_WEB_RETRIEVER", "tavily")
    providers = summarize_provider_statuses(norm, endpoint, web_retriever=web_name)
    return {
        "ok": True,
        "sources": norm,
        "memo": memo,
        "en_hits": providers["en_hits"],
        "zh_hits": providers["zh_hits"],
        "by_retriever": providers["by_retriever"],
        "extension_seam": "custom-endpoint",
        "degraded": providers["degraded"],
        "degradation_reason": providers["degradation_reason"],
    }


async def run(payload):
    with CustomEvidenceEndpoint() as endpoint:
        if isinstance(payload.get("claims"), list):
            out = []
            for item in payload["claims"]:
                one = {
                    "claim": item.get("claim", ""),
                    "failures": item.get("failures") or [],
                    "topic": payload.get("topic", item.get("topic", "")),
                    "want_memo": payload.get("want_memo", item.get("want_memo", True)),
                }
                try:
                    res = await _run_one(one, endpoint)
                except Exception as e:
                    import traceback

                    res = _error_result(f"{e}\n{traceback.format_exc()[-800:]}")
                res["id"] = item.get("id")
                out.append(res)
            return {"ok": True, "claims": out}
        return await _run_one(payload, endpoint)


def health_check():
    _env_setup()
    import importlib.metadata as metadata
    import gpt_researcher  # noqa: F401
    from gpt_researcher.retrievers import CustomRetriever  # noqa: F401

    return {
        "ok": True,
        "gpt_researcher_version": metadata.version("gpt-researcher"),
        "extension_seam": "custom-endpoint",
    }


if __name__ == "__main__":
    if "--health" in sys.argv:
        try:
            result = health_check()
            status = 0
        except Exception as e:
            result = {"ok": False, "error": str(e)}
            status = 1
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        raise SystemExit(status)
    try:
        payload = json.load(sys.stdin)
        result = asyncio.run(run(payload))
    except Exception as e:
        import traceback

        result = _error_result(f"{e}\n{traceback.format_exc()[-800:]}")
    # 末行 = 结果 JSON（gpt-researcher 的日志走 stdout 时，主引擎只取最后一行 JSON）
    sys.stdout.write(
        "\n__GPTR_RESULT__" + json.dumps(result, ensure_ascii=False) + "\n"
    )
