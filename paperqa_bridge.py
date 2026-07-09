"""Optional PaperQA2 bridge for self-library evidence questions.

PaperQA has a fast-moving dependency tree, so Paper Muse calls it through a
separate Python interpreter instead of importing it into the main runtime.
Missing PaperQA runtime is a feature degradation, not an app startup failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT_MARK = "__PAPERQA_RESULT__"
DEFAULT_TIMEOUT = 900


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
                return json.loads(line[len(RESULT_MARK):])
            except json.JSONDecodeError:
                return None
    return None


def _emit(payload: dict):
    print(RESULT_MARK + json.dumps(payload, ensure_ascii=False))


def paperqa_status(python: str | Path | None = None, pdf_dir: str | Path | None = None) -> dict:
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
        return {**base, "state": "failed", "installed": True, "message": "PaperQA python 不可执行"}

    try:
        version = subprocess.run([str(py), "--version"], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {**base, "state": "failed", "installed": True, "message": str(e)}
    if version.returncode != 0:
        return {
            **base,
            "state": "failed",
            "installed": True,
            "message": (version.stderr or version.stdout or "python --version failed")[-500:],
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
        message = (payload or {}).get("error") or (health.stderr or health.stdout or "PaperQA health check failed")
        return {**base, "state": "failed", "installed": True, "message": str(message)[-500:]}

    base = {**base, "installed": True, "paperqa_version": payload.get("version")}
    if papers is None:
        return {
            **base,
            "state": "pdf_dir_missing",
            "message": "未配置 PAPER_MUSE_PDF_DIR / PAPER_MUSE_ZOTERO_PDF_DIR",
        }
    if not papers.is_dir():
        return {**base, "state": "pdf_dir_missing", "message": f"PDF 目录不存在：{papers}"}
    return {**base, "state": "ready", "ready": True, "message": "PaperQA ready"}


def _degraded(question: str, status: dict, message: str | None = None) -> dict:
    return {
        "ok": False,
        "degraded": True,
        "question": question,
        "answer": "",
        "formatted_answer": "",
        "context": [],
        "references": [],
        "status": status,
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
    keys = ("name", "text_name", "title", "citation", "docname", "dockey", "url", "doi")
    item = {k: _clip(_get(value, k), 500) for k in keys if _get(value, k)}
    text = _get(value, "text") or _get(value, "summary") or _get(value, "context")
    if text:
        item["text"] = _clip(text, 2000)
    if not item:
        item["text"] = _clip(value, 2000)
    return item


def _extract_references(context: list) -> list:
    refs, seen = [], set()
    for item in context:
        title = item.get("title") or item.get("citation") or item.get("name") or item.get("text_name")
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
    context = [_context_item(c) for c in (_get(answer_response, "context", []) or [])]
    return {
        "ok": True,
        "degraded": False,
        "question": str(_get(answer_response, "question", question) or question),
        "pdf_dir": str(Path(pdf_dir).expanduser().resolve()),
        "formatted_answer": _clip(_get(answer_response, "formatted_answer"), 12000),
        "answer": _clip(_get(answer_response, "answer"), 12000),
        "context": context,
        "references": _extract_references(context),
    }


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
    kwargs = {"temperature": _float_env("PAPER_MUSE_PAPERQA_TEMPERATURE", 0.5), "agent": agent}
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
    return Path(output_dir).expanduser().resolve() / "docs" / "agents" / "muse" / "sources.md"


def append_sources_contract(output_dir: str | Path, payload: dict) -> Path:
    """Append PaperQA answer into the existing docs/agents/muse/sources.md contract."""
    path = _sources_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8").rstrip()
    else:
        text = "# 文献锚点\n"
    status = (payload.get("status") or {}).get("state") or ("degraded" if payload.get("degraded") else "ready")
    lines = [
        "",
        f"## 自有库证据问答：{payload.get('question', '').strip()}",
        f"- 来源：PaperQA2 / {payload.get('pdf_dir') or (payload.get('status') or {}).get('pdf_dir') or '未配置'}",
        f"- 状态：{status}",
    ]
    if payload.get("degraded"):
        lines.append(f"- 降级：{payload.get('message') or 'PaperQA unavailable'}")
    else:
        answer = payload.get("formatted_answer") or payload.get("answer") or "（PaperQA 未返回答案文本）"
        lines += ["", answer.strip()]
        refs = payload.get("references") or []
        context = payload.get("context") or []
        if refs or context:
            lines.append("\n### 引注/上下文")
        for ref in refs[:12]:
            if ref.get("url"):
                lines.append(f"- {ref.get('title') or '(unknown source)'} — {ref['url']}")
            else:
                lines.append(f"- {ref.get('title') or '(unknown source)'}")
        for item in context[:8]:
            label = item.get("title") or item.get("citation") or item.get("name") or item.get("text_name") or "context"
            snippet = item.get("text")
            if snippet:
                lines.append(f"- {label}: {snippet}")
    path.write_text(text + "\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def ask_self_library(
    question: str,
    *,
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
        payload = _degraded(q, status)
        if output_dir:
            append_sources_contract(output_dir, payload)
        return payload

    try:
        result = subprocess.run(
            [status["python"], str(Path(__file__).resolve()), "--ask", "--question", q, "--paper-dir", status["pdf_dir"]],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        payload = _degraded(q, {**status, "state": "failed", "message": str(e)}, str(e))
        if output_dir:
            append_sources_contract(output_dir, payload)
        return payload
    payload = _parse_marker(result.stdout)
    if result.returncode != 0 or not payload or payload.get("ok") is not True:
        message = (payload or {}).get("error") or (result.stderr or result.stdout or "PaperQA query failed")[-1000:]
        payload = _degraded(q, {**status, "state": "failed", "message": message}, message)
    else:
        payload["status"] = status
    if output_dir:
        append_sources_contract(output_dir, payload)
    return payload


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
