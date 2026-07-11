"""#48 可重附着批注交接包：selector 语言 + reattach + AnnotationSink。
覆盖验收：精确命中、轻微改稿重附着、歧义、彻底失配、缺字段→unresolved、交接包稳定 schema。
"""

import annotation as A
from evidence import ProviderRecord, evidence_ref_from_record


DRAFT = "引言部分。\n数据确权是破解平台数据垄断的前提，因此应先立法。\n后文展开论证。"
QUOTE = "数据确权是破解平台数据垄断的前提"


def _span_selector():
    off = DRAFT.find(QUOTE)
    return A.selector_from_span(DRAFT, off, len(QUOTE))


def test_selector_from_span_builds_composite_quote_and_position():
    off = DRAFT.find(QUOTE)
    sel = A.selector_from_span(DRAFT, off, len(QUOTE))
    assert sel["quote"]["exact"] == QUOTE
    assert sel["quote"]["prefix"].endswith("引言部分。\n") or "引言" in sel["quote"]["prefix"]
    assert sel["quote"]["suffix"].startswith("，因此")
    assert sel["position"] == {"start": off, "end": off + len(QUOTE)}


def test_reattach_exact_hit_is_verified_at_position():
    sel = _span_selector()
    r = A.reattach(sel, DRAFT)
    assert r["status"] == "verified"
    assert DRAFT[r["start"]:r["end"]] == QUOTE


def test_reattach_after_light_edit_re_anchors_to_new_position():
    """轻微改稿：在引文前插入一段文字 → 绝对位置右移，但 quote+context 仍能重定位。"""
    sel = _span_selector()
    edited = "【新增编者按：本节讨论平台经济。】\n" + DRAFT
    r = A.reattach(sel, edited)
    assert r["status"] == "verified"
    assert edited[r["start"]:r["end"]] == QUOTE
    assert r["start"] != sel["position"]["start"]        # 确实重附着到了新位置


def test_reattach_ambiguous_when_exact_repeats_without_distinguishing_context():
    text = "自治机制无法处理拒不整改者。自治机制无法处理拒不整改者。"
    sel = {"quote": {"exact": "自治机制无法处理拒不整改者。", "prefix": "", "suffix": ""}, "position": None}
    r = A.reattach(sel, text)
    assert r["status"] == "ambiguous"
    assert r["start"] is None and len(r["candidates"]) == 2


def test_reattach_ambiguous_resolved_by_prefix_suffix():
    text = "甲说：结论成立。乙说：结论成立。"
    sel = {"quote": {"exact": "结论成立。", "prefix": "乙说：", "suffix": ""}, "position": None}
    r = A.reattach(sel, text)
    assert r["status"] == "verified"
    assert text[r["start"]:r["end"]] == "结论成立。"
    assert r["start"] == text.find("乙说：") + len("乙说：")


def test_reattach_total_mismatch_is_unresolved_not_guessed():
    sel = _span_selector()
    rewritten = "本文完全改写，原句不复存在。"
    r = A.reattach(sel, rewritten)
    assert r["status"] == "unresolved"
    assert r["start"] is None


def test_reattach_missing_exact_is_unresolved():
    r = A.reattach({"quote": {"exact": "", "prefix": "x", "suffix": "y"}}, DRAFT)
    assert r["status"] == "unresolved"


def test_selector_from_evidence_maps_locator_fields():
    ref = evidence_ref_from_record(
        ProviderRecord(source_id="S", title="某文献", url="https://doi.org/x", version="v2",
                       source_kind="scholarly-work", relation="supports",
                       identity="doi:x", locator_kind="url", locator_value="https://doi.org/x",
                       exact="关键句", prefix="前文", suffix="后文"),
        "openalex", "问式")
    sel = A.selector_from_evidence(ref)
    assert sel["evidence_id"] == ref["id"]
    assert sel["quote"] == {"exact": "关键句", "prefix": "前文", "suffix": "后文"}
    assert sel["source"]["id"] == "doi:x" and sel["source"]["version"] == "v2"


def test_annotation_sink_package_evidence_reattaches_and_marks_unresolved():
    """EvidenceRef 交接包：有 exact 的重附着，纯 url 锚（无 exact）→ unresolved，不伪造命中。"""
    with_quote = evidence_ref_from_record(
        ProviderRecord(source_id="S1", title="T1", url="u1", version="",
                       source_kind="scholarly-work", relation="supports", identity="id1",
                       locator_kind="url", locator_value="u1", exact=QUOTE),
        "web", "q")
    no_quote = evidence_ref_from_record(
        ProviderRecord(source_id="S2", title="T2", url="u2", version="",
                       source_kind="scholarly-work", relation="supports", identity="id2",
                       locator_kind="url", locator_value="u2"),
        "web", "q")
    pkg = A.AnnotationSink().package([with_quote, no_quote], source_text=DRAFT, target_kind="draft")
    assert pkg["schema_version"] == A.SCHEMA_VERSION
    assert pkg["target"]["checksum"].startswith("sha256:")
    by_id = {a["evidence_id"]: a["attachment"]["status"] for a in pkg["annotations"]}
    assert by_id[with_quote["id"]] == "verified"
    assert by_id[no_quote["id"]] == "unresolved"


def test_annotation_sink_package_annotations_attaches_prebuilt_selectors():
    items = [{"id": "claim-1", "selector": _span_selector(),
              "meta": {"claim": "确权是前提", "verdicts": ["已证伪"]}}]
    pkg = A.AnnotationSink().package_annotations(items, source_text=DRAFT, target_kind="draft", target_id="d1")
    a = pkg["annotations"][0]
    assert a["annotation_id"] == "claim-1"
    assert a["attachment"]["status"] == "verified"
    assert a["meta"]["verdicts"] == ["已证伪"]
    assert pkg["target"]["id"] == "d1"


def test_package_without_source_text_is_unattached():
    pkg = A.AnnotationSink().package_annotations(
        [{"id": "x", "selector": _span_selector()}])
    assert pkg["annotations"][0]["attachment"]["status"] == "unattached"
    assert pkg["target"]["checksum"] == ""


def test_checksum_changes_when_source_changes():
    assert A.text_checksum(DRAFT) != A.text_checksum(DRAFT + "改了一个字")
    assert A.text_checksum(DRAFT) == A.text_checksum(DRAFT)      # 稳定
