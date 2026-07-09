import json

import paperqa_bridge


def test_paperqa_status_reports_missing_runtime(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()

    status = paperqa_bridge.paperqa_status(python=tmp_path / "missing-python", pdf_dir=pdf_dir)

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
    assert "状态：missing" in text


def test_ask_self_library_parses_sidecar_result_and_appends_contract(monkeypatch, tmp_path):
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
    assert payload["status"]["state"] == "ready"
    text = (output_dir / "docs" / "agents" / "muse" / "sources.md").read_text(encoding="utf-8")
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
        context = [Context()]

    payload = paperqa_bridge.answer_to_payload(Answer(), question="fallback", pdf_dir=tmp_path)

    assert payload["ok"] is True
    assert payload["formatted_answer"] == "格式化答案"
    assert payload["references"] == [{"title": "平台治理论文", "url": "https://example.test/paper"}]
    assert payload["context"][0]["text"] == "原文片段"
