"""Optional PaperQA2 bridge for self-library evidence questions.

PaperQA has a fast-moving dependency tree, so Paper Muse calls it through a
separate Python interpreter instead of importing it into the main runtime.
Missing PaperQA runtime is a feature degradation, not an app startup failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from evidence import (
    CorpusAnswer,
    EvidenceGateway,
    FunctionCorpusProvider,
    ProviderRecord,
)

ROOT = Path(__file__).resolve().parent
RESULT_MARK = "__PAPERQA_RESULT__"
DEFAULT_TIMEOUT = 900
ARTIFACT_LOCK = threading.RLock()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


def default_python_path() -> Path:
    explicit = _env_path("PAPER_MUSE_PAPERQA_PYTHON")
    return explicit if explicit else ROOT / ".venv-paperqa" / "bin" / "python"


def default_pdf_dir() -> Path | None:
    return _env_path("PAPER_MUSE_PDF_DIR") or _env_path("PAPER_MUSE_ZOTERO_PDF_DIR")


def _parse_marker(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARK):
            try:
                return json.loads(line[len(RESULT_MARK) :])
            except json.JSONDecodeError:
                return None
    return None


def _emit(payload: dict):
    print(RESULT_MARK + json.dumps(payload, ensure_ascii=False))


def paperqa_status(
    python: str | Path | None = None, pdf_dir: str | Path | None = None
) -> dict:
    """Return optional capability state without importing PaperQA in this process."""
    py = Path(python).expanduser().resolve() if python else default_python_path()
    papers = Path(pdf_dir).expanduser().resolve() if pdf_dir else default_pdf_dir()
    base = {
        "optional": True,
        "python": str(py),
        "pdf_dir": str(papers) if papers else None,
        "pqa_home": os.environ.get("PQA_HOME"),
        "installed": False,
        "ready": False,
    }
    if not py.exists():
        return {
            **base,
            "state": "missing",
            "message": "PaperQA runtime 未安装；按 requirements-paperqa.txt 创建 .venv-paperqa 后启用",
        }
    if not os.access(py, os.X_OK):
        return {
            **base,
            "state": "failed",
            "installed": True,
            "message": "PaperQA python 不可执行",
        }

    try:
        version = subprocess.run(
            [str(py), "--version"], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        return {**base, "state": "failed", "installed": True, "message": str(e)}
    if version.returncode != 0:
        return {
            **base,
            "state": "failed",
            "installed": True,
            "message": (version.stderr or version.stdout or "python --version failed")[
                -500:
            ],
        }

    try:
        health = subprocess.run(
            [str(py), str(Path(__file__).resolve()), "--health"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        return {**base, "state": "failed", "installed": True, "message": str(e)}
    payload = _parse_marker(health.stdout)
    if health.returncode != 0 or not payload or payload.get("ok") is not True:
        message = (payload or {}).get("error") or (
            health.stderr or health.stdout or "PaperQA health check failed"
        )
        return {
            **base,
            "state": "failed",
            "installed": True,
            "message": str(message)[-500:],
        }

    base = {**base, "installed": True, "paperqa_version": payload.get("version")}
    if papers is None:
        return {
            **base,
            "state": "pdf_dir_missing",
            "message": "未配置 PAPER_MUSE_PDF_DIR / PAPER_MUSE_ZOTERO_PDF_DIR",
        }
    if not papers.is_dir():
        return {
            **base,
            "state": "pdf_dir_missing",
            "message": f"PDF 目录不存在：{papers}",
        }
    return {**base, "state": "ready", "ready": True, "message": "PaperQA ready"}


def _degraded(question: str, status: dict, message: str | None = None,
              *, state: str | None = None) -> dict:
    # state/message 覆盖内部并入 status，免每个失败分支手工拼 {**status, "state":…, "message":…}
    # （#21：ask_self_library 六处失败分支曾各拼一遍，易漏、易漂移）。
    if state is not None:
        status = {**status, "state": state}
    if message is not None:
        status = {**status, "message": message}
    state_map = {
        "missing": "unavailable",
        "pdf_dir_missing": "unavailable",
        "failed": "error",
    }
    state = state_map.get(str(status.get("state")), str(status.get("state") or "error"))
    bundle_id = (
        "evb_"
        + hashlib.sha256(
            f"paperqa\n{question.casefold()}\n{state}".encode("utf-8")
        ).hexdigest()[:24]
    )
    return {
        "id": bundle_id,
        "ok": False,
        "degraded": True,
        "question": question,
        "answer": "",
        "formatted_answer": "",
        "evidence": [],
        "status": {
            "provider": "paperqa",
            "state": state,
            "query": question,
            "hits": None,
            "message": message or status.get("message") or "PaperQA unavailable",
        },
        "capability": status,
        "provider_version": str(status.get("paperqa_version") or ""),
        "index_version": "",
        "message": message or status.get("message") or "PaperQA unavailable",
    }


def _clip(value, limit: int = 2000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _get(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _context_item(value) -> dict:
    text_value = _get(value, "text")
    text_obj = (
        text_value
        if text_value is not None and not isinstance(text_value, str)
        else None
    )
    doc = _get(text_obj, "doc") if text_obj is not None else None
    text_name = _get(text_obj, "name") if text_obj is not None else None
    docname = _get(doc, "docname")
    dockey = _get(doc, "dockey")
    citation = _get(doc, "citation")
    url = (
        _get(doc, "url")
        or _get(doc, "doi_url")
        or _get(doc, "pdf_url")
        or _get(doc, "file_location")
    )
    doi = _get(doc, "doi")
    page = (
        _get(value, "page")
        or _get(text_obj, "page")
        or _get(text_obj, "page_num")
        or _get(text_obj, "page_number")
    )
    if page is None and text_name:
        match = re.search(r"\bpages?\s+(\d+)", str(text_name), flags=re.IGNORECASE)
        page = int(match.group(1)) if match else None
    item = {}
    for key, candidate in (
        ("name", _get(value, "name") or text_name),
        ("text_name", _get(value, "text_name")),
        ("title", _get(value, "title") or _get(doc, "title") or docname),
        ("citation", _get(value, "citation") or citation),
        ("docname", _get(value, "docname") or docname),
        ("dockey", _get(value, "dockey") or dockey),
        ("url", _get(value, "url") or url),
        ("doi", _get(value, "doi") or doi),
        ("page", page),
        ("pages", _get(value, "pages") or _get(text_obj, "pages")),
        ("exact", _get(value, "exact") or _get(text_obj, "exact")),
        ("prefix", _get(value, "prefix") or _get(text_obj, "prefix")),
        ("suffix", _get(value, "suffix") or _get(text_obj, "suffix")),
        ("source_identity", _get(value, "source_identity")),
        ("source_version", _get(value, "source_version") or _get(doc, "content_hash")),
        ("index_version", _get(value, "index_version")),
    ):
        if candidate not in (None, ""):
            item[key] = candidate if key == "page" else _clip(candidate, 500)
    if dockey and not item.get("source_identity"):
        item["source_identity"] = f"paperqa:{dockey}"
    other = _get(doc, "other", {}) or {}
    zotero = (
        _get(value, "zotero")
        or _get(value, "native")
        or (other.get("zotero") if isinstance(other, dict) else None)
    )
    if isinstance(zotero, dict):
        item["zotero"] = zotero
    text = (
        _get(text_obj, "text")
        if text_obj is not None
        else text_value or _get(value, "summary") or _get(value, "context")
    )
    if text:
        item["text"] = _clip(text, 2000)
    summary = _get(value, "context") if text_obj is not None else None
    if summary:
        item["summary"] = _clip(summary, 2000)
    if not item:
        item["text"] = _clip(value, 2000)
    return item


def _extract_references(context: list) -> list:
    refs, seen = [], set()
    for item in context:
        title = (
            item.get("title")
            or item.get("citation")
            or item.get("name")
            or item.get("text_name")
        )
        url = item.get("url") or item.get("doi") or item.get("dockey")
        if not title and not url:
            continue
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"title": title or "(unknown source)", "url": url or ""})
    return refs


def answer_to_payload(answer_response, *, question: str, pdf_dir: str | Path) -> dict:
    session = _get(answer_response, "session")
    if session is None:
        candidate = _get(answer_response, "answer")
        if (
            candidate is not None
            and not isinstance(candidate, (str, bytes))
            and _get(candidate, "question")
        ):
            session = candidate
    root = session or answer_response
    contexts = _get(root, "contexts")
    if not isinstance(contexts, (list, tuple)):
        # 锁定版 paper-qa==2026.3.18 只发 contexts（复数）；旧单数 context 迁移壳已删（#52）。
        contexts = []
    context = [_context_item(c) for c in contexts]
    raw_status = _get(answer_response, "status", "success")
    agent_status = str(getattr(raw_status, "value", raw_status) or "success").lower()
    return {
        "ok": True,
        "degraded": False,
        "agent_status": agent_status,
        "question": str(_get(root, "question", question) or question),
        "pdf_dir": str(Path(pdf_dir).expanduser().resolve()),
        "formatted_answer": _clip(_get(root, "formatted_answer"), 12000),
        "answer": _clip(_get(root, "answer"), 12000),
        "context": context,
        "references": _extract_references(context),
    }


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _paperqa_record(item: dict, index: int) -> ProviderRecord:
    native = item.get("zotero") if isinstance(item.get("zotero"), dict) else None
    source_id = str(
        item.get("dockey")
        or item.get("docname")
        or item.get("name")
        or item.get("url")
        or f"context-{index}"
    )
    title = str(
        item.get("title")
        or item.get("citation")
        or item.get("text_name")
        or item.get("name")
        or source_id
    )
    url = str(item.get("url") or item.get("doi") or "")
    identity = str(item.get("source_identity") or "")
    if not identity and native:
        library_type = native.get("library_type", "user")
        library_id = native.get("library_id", 0)
        attachment = native.get("attachment_key") or source_id
        identity = f"zotero:{library_type}:{library_id}:attachment:{attachment}"
    page = _int_or_none(item.get("page") or item.get("pages"))
    return ProviderRecord(
        source_id=source_id,
        title=title,
        url=url,
        version=str(item.get("source_version") or item.get("version") or ""),
        source_kind="library-document",
        relation="supports",
        identity=identity,
        locator_kind="pdf-page" if page is not None else None,
        locator_value=str(page) if page is not None else url or source_id,
        exact=str(item.get("exact") or item.get("text") or ""),
        prefix=str(item.get("prefix") or ""),
        suffix=str(item.get("suffix") or ""),
        page=page,
        native=native,
    )


def paperqa_payload_to_bundle(payload: dict, *, question: str, status: dict) -> dict:
    """Normalize the isolated PaperQA payload before it reaches API callers."""
    raw_context = (
        payload.get("context") if isinstance(payload.get("context"), list) else []
    )
    references = (
        payload.get("references") if isinstance(payload.get("references"), list) else []
    )
    # sidecar 路径里 references 与 context 分开返回、chunk 可能缺 url，需从 references 回填。
    # 但 _extract_references 跳过无题无址项并按 (title,url) 去重，故 references 与 context
    # 下标错位——旧代码按下标回填会把后一篇文献的 url 挂到无址的孤儿 chunk 上（张冠李戴、
    # 契约保真破坏）。改按标题建索引精确匹配：无标题或标题对不上则不回填（宁缺毋滥）。
    ref_url_by_title = {}
    for r in references:
        if isinstance(r, dict) and r.get("title") and r.get("url"):
            ref_url_by_title.setdefault(r["title"], r["url"])
    context = []
    for raw_item in raw_context:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if not item.get("url") and item.get("title") in ref_url_by_title:
            item["url"] = ref_url_by_title[item["title"]]
        context.append(item)
    answer = CorpusAnswer(
        answer=str(payload.get("answer") or ""),
        formatted_answer=str(payload.get("formatted_answer") or ""),
        records=tuple(
            _paperqa_record(item, i)
            for i, item in enumerate(context)
            if isinstance(item, dict)
        ),
        provider_version=str(
            status.get("paperqa_version") or payload.get("paperqa_version") or ""
        ),
        index_version=str(
            payload.get("index_version")
            or next(
                (
                    item.get("index_version")
                    for item in context
                    if isinstance(item, dict) and item.get("index_version")
                ),
                "",
            )
        ),
    )
    gateway = EvidenceGateway(
        (),
        corpus_providers=(FunctionCorpusProvider("paperqa", lambda _question: answer),),
    )
    return gateway.ask_corpus(question)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _paperqa_settings(pdf_dir: Path):
    from paperqa import Settings

    llm = (os.environ.get("PAPER_MUSE_PAPERQA_LLM") or "").strip()
    summary_llm = (os.environ.get("PAPER_MUSE_PAPERQA_SUMMARY_LLM") or llm).strip()
    agent_llm = (os.environ.get("PAPER_MUSE_PAPERQA_AGENT_LLM") or llm).strip()
    embedding = (os.environ.get("PAPER_MUSE_PAPERQA_EMBEDDING") or "").strip()
    agent = {
        "index": {
            "paper_directory": str(pdf_dir),
            "use_absolute_paper_directory": True,
            "recurse_subdirectories": True,
        }
    }
    if agent_llm:
        agent["agent_llm"] = agent_llm
    kwargs = {
        "temperature": _float_env("PAPER_MUSE_PAPERQA_TEMPERATURE", 0.5),
        "agent": agent,
    }
    if llm:
        kwargs["llm"] = llm
    if summary_llm:
        kwargs["summary_llm"] = summary_llm
    if embedding:
        kwargs["embedding"] = embedding
    return Settings(**kwargs)


def _run_paperqa_question(question: str, paper_dir: str | Path) -> dict:
    from paperqa import ask

    pdf_dir = Path(paper_dir).expanduser().resolve()
    response = ask(question, settings=_paperqa_settings(pdf_dir))
    return answer_to_payload(response, question=question, pdf_dir=pdf_dir)


def _sources_path(output_dir: str | Path) -> Path:
    return (
        Path(output_dir).expanduser().resolve()
        / "docs"
        / "agents"
        / "muse"
        / "sources.md"
    )


def _evidence_store_path(output_dir: str | Path) -> Path:
    return (
        Path(output_dir).expanduser().resolve()
        / "docs"
        / "agents"
        / "muse"
        / "evidence.json"
    )


def _atomic_write_text(path: Path, text: str):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def persist_evidence_bundle(output_dir: str | Path, payload: dict) -> Path:
    """Upsert provider-neutral evidence and bundle metadata by stable id."""
    path = _evidence_store_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT_LOCK:
        try:
            store = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid evidence store: {path}") from exc
        evidence = (
            store.get("evidence") if isinstance(store.get("evidence"), dict) else {}
        )
        bundles = store.get("bundles") if isinstance(store.get("bundles"), dict) else {}
        refs = [
            ref
            for ref in payload.get("evidence", [])
            if isinstance(ref, dict) and ref.get("id")
        ]
        for ref in refs:
            evidence[ref["id"]] = ref
        bundle_id = str(payload.get("id") or "")
        if bundle_id:
            bundles[bundle_id] = {
                "question": payload.get("question", ""),
                "target": payload.get("target"),
                "answer": payload.get("answer", ""),
                "formatted_answer": payload.get("formatted_answer", ""),
                "evidence_ids": [ref["id"] for ref in refs],
                "status": payload.get("status", {}),
                "provider_version": payload.get("provider_version", ""),
                "index_version": payload.get("index_version", ""),
            }
        _atomic_write_text(
            path,
            json.dumps(
                {"schema_version": 1, "evidence": evidence, "bundles": bundles},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    return path


def read_evidence(output_dir: str | Path, evidence_id: str) -> dict | None:
    """Read one structured EvidenceRef without reparsing sources.md."""
    path = _evidence_store_path(output_dir)
    with ARTIFACT_LOCK:
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    evidence = store.get("evidence") if isinstance(store, dict) else None
    return evidence.get(evidence_id) if isinstance(evidence, dict) else None


def append_sources_contract(output_dir: str | Path, payload: dict) -> Path:
    with ARTIFACT_LOCK:
        return _append_sources_contract_unlocked(output_dir, payload)


def _append_sources_contract_unlocked(output_dir: str | Path, payload: dict) -> Path:
    """Append PaperQA answer into the existing docs/agents/muse/sources.md contract."""
    path = _sources_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8").rstrip()
    else:
        text = "# 文献锚点\n"
    bundle_id = str(payload.get("id") or "")
    marker = f"<!-- paperqa:{bundle_id} -->" if bundle_id else ""
    if marker and marker in text:
        return path
    status = (payload.get("status") or {}).get("state") or (
        "degraded" if payload.get("degraded") else "ok"
    )
    lines = [
        "",
        marker,
        f"## 自有库证据问答：{payload.get('question', '').strip()}",
        f"- 来源：PaperQA2 / {(payload.get('capability') or {}).get('pdf_dir') or '未配置'}",
        f"- 状态：{status}",
    ]
    if payload.get("degraded"):
        lines.append(f"- 降级：{payload.get('message') or 'PaperQA unavailable'}")
    else:
        answer = (
            payload.get("formatted_answer")
            or payload.get("answer")
            or "（PaperQA 未返回答案文本）"
        )
        lines += ["", answer.strip()]
        refs = payload.get("evidence") or []
        if refs:
            lines.append("\n### 引注/上下文")
        for ref in refs[:12]:
            source = ref.get("source") or {}
            locator = ref.get("locator") or {}
            label = source.get("title") or "(unknown source)"
            target = source.get("url") or locator.get("value") or ""
            page = f" · p. {locator['page']}" if locator.get("page") is not None else ""
            lines.append(
                f"- {label}{(' — ' + target) if target else ''}{page} · EvidenceRef `{ref.get('id', '')}`"
            )
            if locator.get("exact"):
                lines.append(f"  - {locator['exact']}")
    _atomic_write_text(path, text + "\n" + "\n".join(lines).rstrip() + "\n")
    return path


def _finalize_answer(payload: dict, target: dict | None, output_dir: str | Path | None):
    if target:
        payload["target"] = dict(target)
    if output_dir:
        with ARTIFACT_LOCK:
            append_sources_contract(output_dir, payload)
            persist_evidence_bundle(output_dir, payload)
    return payload


def ask_self_library(
    question: str,
    *,
    target: dict | None = None,
    pdf_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    python: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    q = " ".join(str(question or "").split())
    if not q:
        raise ValueError("question is required")
    status = paperqa_status(python=python, pdf_dir=pdf_dir)
    if status.get("state") != "ready":
        return _finalize_answer(_degraded(q, status), target, output_dir)

    try:
        result = subprocess.run(
            [
                status["python"],
                str(Path(__file__).resolve()),
                "--ask",
                "--question",
                q,
                "--paper-dir",
                status["pdf_dir"],
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        message = f"PaperQA query timed out after {e.timeout}s"
        payload = _degraded(q, status, message, state="timeout")
        return _finalize_answer(payload, target, output_dir)
    except Exception as e:
        payload = _degraded(q, status, str(e), state="error")
        return _finalize_answer(payload, target, output_dir)
    raw_payload = _parse_marker(result.stdout)
    if result.returncode != 0:
        message = (raw_payload or {}).get("error") or (
            result.stderr or result.stdout or "PaperQA query failed"
        )[-1000:]
        payload = _degraded(q, status, message, state="error")
    elif not raw_payload or raw_payload.get("ok") is not True:
        message = (raw_payload or {}).get(
            "error"
        ) or "PaperQA returned malformed output"
        payload = _degraded(q, status, message, state="bad-payload")
    else:
        bundle = paperqa_payload_to_bundle(raw_payload, question=q, status=status)
        agent_status = str(raw_payload.get("agent_status") or "success").lower()
        if agent_status != "success":
            state = "error" if agent_status == "fail" else "degraded"
            message = f"PaperQA agent status: {agent_status}"
            if bundle["evidence"] or bundle["answer"]:
                payload = {
                    **bundle,
                    "ok": False,
                    "degraded": True,
                    "agent_status": agent_status,
                    "capability": status,
                    "message": message,
                    "status": {
                        **bundle["status"],
                        "state": state,
                        "message": message,
                    },
                }
            else:
                payload = _degraded(q, status, message, state=state)
        elif bundle["status"]["state"] != "ok":
            message = "PaperQA returned no cited answer"
            payload = _degraded(q, status, message, state="bad-payload")
        else:
            payload = {
                **bundle,
                "ok": True,
                "degraded": False,
                "agent_status": agent_status,
                "capability": status,
            }
    return _finalize_answer(payload, target, output_dir)


def _health_payload() -> dict:
    try:
        import importlib.metadata as metadata
        import paperqa  # noqa: F401

        return {"ok": True, "version": metadata.version("paper-qa")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--ask", action="store_true")
    parser.add_argument("--question")
    parser.add_argument("--paper-dir")
    args = parser.parse_args(argv)
    if args.health:
        payload = _health_payload()
        _emit(payload)
        return 0 if payload.get("ok") else 1
    if args.ask:
        if not args.question or not args.paper_dir:
            _emit({"ok": False, "error": "--question and --paper-dir are required"})
            return 2
        try:
            _emit(_run_paperqa_question(args.question, args.paper_dir))
            return 0
        except Exception as e:
            _emit({"ok": False, "error": str(e)})
            return 1
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
