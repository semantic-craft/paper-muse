import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import paperqa_bridge


def test_paperqa_status_reports_missing_runtime(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()

    status = paperqa_bridge.paperqa_status(
        python=tmp_path / "missing-python", pdf_dir=pdf_dir
    )

    assert status["state"] == "missing"
    assert status["optional"] is True
    assert status["ready"] is False


def test_ask_self_library_degrades_and_writes_sources_when_runtime_missing(tmp_path):
    output_dir = tmp_path / "paper"

    payload = paperqa_bridge.ask_self_library(
        "平台数据权力的反例是什么？",
        python=tmp_path / "missing-python",
        pdf_dir=tmp_path / "pdfs",
        output_dir=output_dir,
    )

    assert payload["degraded"] is True
    sources = output_dir / "docs" / "agents" / "muse" / "sources.md"
    text = sources.read_text(encoding="utf-8")
    assert "自有库证据问答：平台数据权力的反例是什么？" in text
    assert "状态：unavailable" in text
    assert payload["capability"]["state"] == "missing"


def test_ask_self_library_parses_sidecar_result_and_appends_contract(
    monkeypatch, tmp_path
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    output_dir = tmp_path / "paper"
    ready = {
        "state": "ready",
        "ready": True,
        "optional": True,
        "python": "/fake/python",
        "pdf_dir": str(pdf_dir),
        "message": "PaperQA ready",
    }
    monkeypatch.setattr(paperqa_bridge, "paperqa_status", lambda **_kw: ready)

    class Result:
        returncode = 0
        stderr = ""
        stdout = paperqa_bridge.RESULT_MARK + json.dumps(
            {
                "ok": True,
                "degraded": False,
                "question": "如何反驳 A？",
                "pdf_dir": str(pdf_dir),
                "formatted_answer": "答案带 (paper-1) 引注。",
                "answer": "答案",
                "references": [{"title": "Local Paper", "url": "doi:10.123/test"}],
                "context": [{"title": "Local Paper", "text": "关键原文片段"}],
            },
            ensure_ascii=False,
        )

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "/fake/python"
        assert "--ask" in cmd and "--paper-dir" in cmd
        assert kwargs["timeout"] == 123
        return Result()

    monkeypatch.setattr(paperqa_bridge.subprocess, "run", fake_run)

    payload = paperqa_bridge.ask_self_library(
        "如何反驳 A？",
        pdf_dir=pdf_dir,
        output_dir=output_dir,
        timeout=123,
    )

    assert payload["ok"] is True
    assert payload["status"]["state"] == "ok"
    assert payload["capability"]["state"] == "ready"
    text = (output_dir / "docs" / "agents" / "muse" / "sources.md").read_text(
        encoding="utf-8"
    )
    assert "答案带 (paper-1) 引注。" in text
    assert "Local Paper — doi:10.123/test" in text
    assert "关键原文片段" in text


def test_answer_to_payload_extracts_context_references(tmp_path):
    class Context:
        title = "平台治理论文"
        url = "https://example.test/paper"
        text = "原文片段"

    class Answer:
        formatted_answer = "格式化答案"
        answer = "纯答案"
        question = "问题"
        contexts = [Context()]

    payload = paperqa_bridge.answer_to_payload(
        Answer(), question="fallback", pdf_dir=tmp_path
    )

    assert payload["ok"] is True
    assert payload["formatted_answer"] == "格式化答案"
    assert payload["references"] == [
        {"title": "平台治理论文", "url": "https://example.test/paper"}
    ]
    assert payload["context"][0]["text"] == "原文片段"


def test_answer_to_payload_unwraps_pinned_2026_response_shape(tmp_path):
    doc = SimpleNamespace(
        docname="Data Lifecycle Study",
        dockey="ITEM0001",
        citation="Author (2026). Data Lifecycle Study.",
        content_hash="sha256:paper-v1",
        url="https://example.test/paper",
        doi="10.123/example",
        doi_url="https://doi.org/10.123/example",
        pdf_url=None,
        file_location=str(tmp_path / "paper.pdf"),
        other={},
    )
    text = SimpleNamespace(
        name="Data Lifecycle Study pages 12-13",
        text="withdrawal does not remove learned influence",
        doc=doc,
    )
    context = SimpleNamespace(
        id="pqac-1234",
        context="The study finds continuing influence after withdrawal.",
        text=text,
    )
    session = SimpleNamespace(
        question="撤回同意是否真正可逆？",
        answer="撤回后影响仍持续。",
        formatted_answer="撤回后影响仍持续 [1]。",
        contexts=[context],
    )
    response = SimpleNamespace(status="success", session=session)

    payload = paperqa_bridge.answer_to_payload(
        response, question="fallback", pdf_dir=tmp_path
    )

    assert payload["agent_status"] == "success"
    assert payload["question"] == "撤回同意是否真正可逆？"
    assert payload["answer"] == "撤回后影响仍持续。"
    assert payload["formatted_answer"] == "撤回后影响仍持续 [1]。"
    assert payload["context"] == [
        {
            "name": "Data Lifecycle Study pages 12-13",
            "title": "Data Lifecycle Study",
            "citation": "Author (2026). Data Lifecycle Study.",
            "docname": "Data Lifecycle Study",
            "dockey": "ITEM0001",
            "url": "https://example.test/paper",
            "doi": "10.123/example",
            "page": 12,
            "source_identity": "paperqa:ITEM0001",
            "source_version": "sha256:paper-v1",
            "text": "withdrawal does not remove learned influence",
            "summary": "The study finds continuing influence after withdrawal.",
        }
    ]


def test_paperqa_payload_maps_used_passage_to_evidence_ref(tmp_path):
    payload = {
        "ok": True,
        "answer": "撤回同意后影响仍会持续。",
        "formatted_answer": "撤回同意后影响仍会持续 [1]。",
        "index_version": "paperqa-index-v7",
        "context": [
            {
                "title": "Data Lifecycle Study",
                "text": "withdrawal does not remove learned influence",
                "prefix": "the experiment found that ",
                "suffix": " across all model variants",
                "page": 12,
                "source_identity": "zotero:users:0:attachment:ATT1",
                "source_version": "42",
                "url": "zotero://select/library/items/ITEM1",
                "dockey": "ATT1",
                "zotero": {
                    "provider": "zotero-local-api",
                    "library_type": "user",
                    "library_id": 0,
                    "library_name": "My Library",
                    "item_key": "ITEM1",
                    "item_version": 42,
                    "item_type": "attachment",
                    "parent_item": "ITEM1",
                    "attachment_key": "ATT1",
                    "annotation_key": None,
                    "collections": ["COL1"],
                    "tags": ["consent"],
                },
            }
        ],
    }

    bundle = paperqa_bridge.paperqa_payload_to_bundle(
        payload,
        question="撤回同意是否真正可逆？",
        status={"paperqa_version": "2026.3.18", "state": "ready"},
    )

    assert bundle["status"]["state"] == "ok"
    assert bundle["provider_version"] == "2026.3.18"
    assert bundle["index_version"] == "paperqa-index-v7"
    ref = bundle["evidence"][0]
    assert ref["source"]["native"]["attachment_key"] == "ATT1"
    assert ref["locator"]["page"] == 12
    assert ref["locator"]["exact"] == "withdrawal does not remove learned influence"
    assert ref["locator"]["prefix"] == "the experiment found that "
    assert ref["locator"]["suffix"] == " across all model variants"


def test_paperqa_bundle_does_not_crossattach_url_after_skipped_reference(tmp_path):
    """回归：_extract_references 跳过无题无址项并按 (title,url) 去重，references 与 context
    下标错位。旧代码按下标回填 url，会把后一篇文献的 url 挂到无址的孤儿 chunk 上（张冠李戴，
    契约保真破坏）。修复后孤儿 chunk 保持无 url，不冒领 C 的地址。"""
    payload = {
        "ok": True, "answer": "a", "formatted_answer": "a",
        "context": [
            {"title": "Doc A", "url": "https://a", "text": "chunk A"},
            {"text": "orphan chunk without title or url"},   # 被 _extract_references 跳过
            {"title": "Doc C", "url": "https://c", "text": "chunk C"},
        ],
        # references = _extract_references(context)：孤儿 chunk 被跳过 → 与 context 下标错位
        "references": [
            {"title": "Doc A", "url": "https://a"},
            {"title": "Doc C", "url": "https://c"},
        ],
    }
    bundle = paperqa_bridge.paperqa_payload_to_bundle(
        payload, question="q", status={"paperqa_version": "2026.3.18", "state": "ready"})
    orphan = next(e for e in bundle["evidence"]
                  if e["locator"].get("exact", "").startswith("orphan chunk"))
    assert orphan["source"].get("url", "") == ""   # 修复前按下标回填冒领 https://c


def test_repeated_question_persists_one_markdown_section_and_readable_evidence(
    monkeypatch, tmp_path
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    output_dir = tmp_path / "paper"
    ready = {
        "state": "ready",
        "ready": True,
        "optional": True,
        "python": "/fake/python",
        "pdf_dir": str(pdf_dir),
        "paperqa_version": "2026.3.18",
    }
    monkeypatch.setattr(paperqa_bridge, "paperqa_status", lambda **_kw: ready)

    class Result:
        returncode = 0
        stderr = ""
        stdout = paperqa_bridge.RESULT_MARK + json.dumps(
            {
                "ok": True,
                "answer": "撤回后影响仍持续。",
                "formatted_answer": "撤回后影响仍持续 [1]。",
                "index_version": "idx-7",
                "context": [
                    {
                        "title": "Local Paper",
                        "url": "zotero://select/library/items/ITEM1",
                        "dockey": "ATT1",
                        "page": 12,
                        "text": "withdrawal does not remove learned influence",
                        "source_identity": "zotero:users:0:attachment:ATT1",
                        "source_version": "42",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(paperqa_bridge.subprocess, "run", lambda *_a, **_kw: Result())

    first = paperqa_bridge.ask_self_library(
        "撤回同意是否真正可逆？", pdf_dir=pdf_dir, output_dir=output_dir
    )
    second = paperqa_bridge.ask_self_library(
        "  撤回同意是否真正可逆？  ", pdf_dir=pdf_dir, output_dir=output_dir
    )

    assert first["id"] == second["id"]
    sources = output_dir / "docs" / "agents" / "muse" / "sources.md"
    assert sources.read_text(encoding="utf-8").count("## 自有库证据问答") == 1
    assert "PaperQA2 / 已配置的本地文献库（路径已省略）" in sources.read_text(
        encoding="utf-8"
    )
    assert str(pdf_dir) not in sources.read_text(encoding="utf-8")
    evidence_id = first["evidence"][0]["id"]
    stored = paperqa_bridge.read_evidence(output_dir, evidence_id)
    assert stored == first["evidence"][0]
    store = json.loads(
        (output_dir / "docs" / "agents" / "muse" / "evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert store["bundles"][first["id"]]["evidence_ids"] == [evidence_id]


def test_evidence_store_corruption_is_reported_without_overwrite(tmp_path):
    output_dir = tmp_path / "paper"
    store = output_dir / "docs" / "agents" / "muse" / "evidence.json"
    store.parent.mkdir(parents=True)
    store.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid evidence store"):
        paperqa_bridge.persist_evidence_bundle(
            output_dir,
            {
                "id": "evb_1",
                "question": "q",
                "answer": "a",
                "formatted_answer": "a",
                "evidence": [],
                "status": {"state": "ok"},
            },
        )

    assert store.read_text(encoding="utf-8") == "{broken"


def test_concurrent_card_answers_do_not_lose_structured_or_markdown_results(tmp_path):
    output_dir = tmp_path / "paper"

    def write(index):
        evidence_id = f"evr_{index}"
        payload = {
            "id": f"evb_{index}",
            "question": f"question {index}",
            "answer": f"answer {index}",
            "formatted_answer": f"answer {index}",
            "evidence": [
                {
                    "id": evidence_id,
                    "source": {"title": f"paper {index}", "url": f"https://e/{index}"},
                    "locator": {"value": f"https://e/{index}"},
                }
            ],
            "status": {"state": "ok"},
        }
        paperqa_bridge.append_sources_contract(output_dir, payload)
        paperqa_bridge.persist_evidence_bundle(output_dir, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, (1, 2)))

    store = json.loads(
        (output_dir / "docs" / "agents" / "muse" / "evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(store["bundles"]) == {"evb_1", "evb_2"}
    assert set(store["evidence"]) == {"evr_1", "evr_2"}
    sources = (output_dir / "docs" / "agents" / "muse" / "sources.md").read_text(
        encoding="utf-8"
    )
    assert "question 1" in sources and "question 2" in sources


@pytest.mark.parametrize(
    ("mode", "result", "expected_state"),
    [
        ("timeout", None, "timeout"),
        ("exception", None, "error"),
        (None, {"returncode": 0, "stdout": "not-json", "stderr": ""}, "bad-payload"),
        (
            None,
            {
                "returncode": 1,
                "stdout": paperqa_bridge.RESULT_MARK
                + json.dumps({"ok": False, "error": "model provider key missing"}),
                "stderr": "",
            },
            "error",
        ),
    ],
)
def test_paperqa_failures_are_recoverable_and_never_fake_an_empty_answer(
    monkeypatch, tmp_path, mode, result, expected_state
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    ready = {
        "state": "ready",
        "ready": True,
        "python": "/fake/python",
        "pdf_dir": str(pdf_dir),
    }
    monkeypatch.setattr(paperqa_bridge, "paperqa_status", lambda **_kw: ready)

    if mode in {"timeout", "exception"}:

        def fake_run(*_args, **_kwargs):
            if mode == "timeout":
                raise paperqa_bridge.subprocess.TimeoutExpired("paperqa", 31)
            raise RuntimeError(f"private runtime path: {tmp_path}")

    else:

        class Result:
            returncode = result["returncode"]
            stdout = result["stdout"]
            stderr = result["stderr"]

        fake_run = lambda *_args, **_kwargs: Result()
    monkeypatch.setattr(paperqa_bridge.subprocess, "run", fake_run)

    payload = paperqa_bridge.ask_self_library("问题", pdf_dir=pdf_dir, timeout=31)

    assert payload["ok"] is False
    assert payload["degraded"] is True
    assert payload["answer"] == ""
    assert payload["evidence"] == []
    assert payload["status"]["state"] == expected_state
    assert str(tmp_path) not in json.dumps(payload)


def test_paperqa_child_failure_logs_only_safe_diagnostic_type(
    monkeypatch, tmp_path, caplog
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    ready = {
        "state": "ready",
        "ready": True,
        "python": "/fake/python",
        "pdf_dir": str(pdf_dir),
    }
    monkeypatch.setattr(paperqa_bridge, "paperqa_status", lambda **_kw: ready)
    private_detail = f"provider key failed at {tmp_path}"

    class Result:
        returncode = 1
        stderr = private_detail
        stdout = paperqa_bridge.RESULT_MARK + json.dumps(
            {
                "ok": False,
                "error_type": "AuthenticationError",
                "error": private_detail,
            }
        )

    monkeypatch.setattr(paperqa_bridge.subprocess, "run", lambda *_a, **_kw: Result())

    with caplog.at_level("ERROR"):
        payload = paperqa_bridge.ask_self_library("问题", pdf_dir=pdf_dir)

    assert payload["status"]["state"] == "error"
    assert "error_type=AuthenticationError" in caplog.text
    assert private_detail not in caplog.text
    assert private_detail not in json.dumps(payload)


@pytest.mark.parametrize("agent_status", ["truncated", "unsure", "fail"])
def test_non_success_agent_status_is_degraded_but_keeps_cited_partial_result(
    monkeypatch, tmp_path, agent_status
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    ready = {
        "state": "ready",
        "ready": True,
        "python": "/fake/python",
        "pdf_dir": str(pdf_dir),
    }
    monkeypatch.setattr(paperqa_bridge, "paperqa_status", lambda **_kw: ready)

    class Result:
        returncode = 0
        stderr = ""
        stdout = paperqa_bridge.RESULT_MARK + json.dumps(
            {
                "ok": True,
                "agent_status": agent_status,
                "answer": "partial answer",
                "formatted_answer": "partial answer [1]",
                "context": [
                    {
                        "title": "Local Paper",
                        "dockey": "ITEM1",
                        "url": "https://example.test/paper",
                        "text": "cited passage",
                    }
                ],
            }
        )

    monkeypatch.setattr(paperqa_bridge.subprocess, "run", lambda *_a, **_kw: Result())

    payload = paperqa_bridge.ask_self_library("question", pdf_dir=pdf_dir)

    assert payload["ok"] is False
    assert payload["degraded"] is True
    assert payload["status"]["state"] == (
        "error" if agent_status == "fail" else "degraded"
    )
    assert payload["answer"] == "partial answer"
    assert len(payload["evidence"]) == 1
