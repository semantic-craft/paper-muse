"""#47 圆桌复用卡片证据：EvidenceRef ⇆ Co-STORM Information 双向映射 + 注入知识库。

离线可测：真实 KnowledgeBase 用 stub encoder（insert_under_root 路径不调 LLM/encoder）。
覆盖 issue #47 验收：证据身份进圆桌、双向映射、同源去重、报告/知识库指回 evidence id+locator、
降级安全空转。
"""

import roundtable_evidence as R
from evidence import ProviderRecord, evidence_ref_from_record
from knowledge_storm.dataclass import KnowledgeBase
from knowledge_storm.interface import Information


def _kb():
    # insert_under_root 不调 encoder/LLM → stub 即可（不联网、不加载 embedding 模型）
    return KnowledgeBase(topic="t", knowledge_base_lm=None,
                         node_expansion_trigger_count=10, encoder=object())


def _ref(url="https://doi.org/x", identity="", title="卡片文献", exact="关键论据句",
         provider="openalex", query="问式", relation="supports",
         source_kind="scholarly-work", page=None):
    return evidence_ref_from_record(
        ProviderRecord(source_id="S", title=title, url=url, version="",
                       source_kind=source_kind, relation=relation, identity=identity,
                       locator_kind="pdf-page" if page is not None else "url",
                       locator_value=str(page) if page is not None else url,
                       exact=exact, page=page),
        provider, query)


def test_evidence_ref_to_information_carries_id_and_locator():
    """AC2/AC4：EvidenceRef → Information，evidence_id/relation/provider/locator 落 meta。"""
    ref = _ref(page=12, source_kind="library-document", provider="paperqa", relation="refutes")
    info = R.evidence_ref_to_information(ref)
    assert info.meta["evidence_id"] == ref["id"]
    assert info.meta["relation"] == "refutes"
    assert info.meta["provider"] == "paperqa"
    assert info.meta["locator"]["page"] == 12
    assert info.title and info.url            # Co-STORM 拿 url 当身份，不能为空


def test_information_without_web_url_falls_back_to_locator_or_id():
    """自有库文档无 web url → 退回 locator 值 / identity / evidence id 作 Information.url。"""
    ref = _ref(url="", identity="zotero:users:0:attachment:ATT1", exact="库内段落",
               page=3, source_kind="library-document", provider="paperqa")
    info = R.evidence_ref_to_information(ref)
    assert info.url                           # 非空（否则 Co-STORM 身份/去重会塌）
    assert info.meta["evidence_id"] == ref["id"]


def test_information_round_trip_preserves_evidence_id():
    """AC4：Information.to_dict → from_dict 保 meta[evidence_id]（报告/对话/instance_dump 身份往返）。"""
    ref = _ref()
    info = R.evidence_ref_to_information(ref)
    back = Information.from_dict(info.to_dict())
    assert R.information_evidence_id(back) == ref["id"]
    # 非卡片证据的 Information（无 meta[evidence_id]）反向映射返回空
    assert R.information_evidence_id(Information("u", "d", ["s"], "t")) == ""


def test_seed_card_evidence_into_knowledge_base_preserves_identity():
    """AC1/AC4/AC5：卡片证据 seed 进真实知识库，evidence_id + relation + locator 存活并随 to_dict 序列化
    （→ 报告/instance_dump 可指回原证据，而不只是标题层级）。"""
    kb = _kb()
    ref = _ref(page=7, relation="refutes")
    turn = R.seed_card_evidence(kb, [ref])
    assert turn is not None
    assert len(kb.info_uuid_to_info_dict) == 1
    dumped = kb.to_dict()["info_uuid_to_info_dict"]
    meta = list(dumped.values())[0]["meta"]
    assert meta["evidence_id"] == ref["id"]
    assert meta["relation"] == "refutes"
    assert meta["locator"]["page"] == 7


def test_seed_dedups_same_evidence_by_identity():
    """AC3：相同 source/query 的卡片证据 seed 多次 → 知识库不产生重复对象。"""
    kb = _kb()
    ref = _ref()
    R.seed_card_evidence(kb, [ref, ref, ref])
    assert len(kb.info_uuid_to_info_dict) == 1


def test_seed_multiple_distinct_evidence_keep_each_identity():
    """多条不同来源证据都进知识库，各自保留 evidence_id（外部 + 自有库混合）。"""
    kb = _kb()
    refs = [
        _ref(url="https://doi.org/a", provider="openalex"),
        _ref(url="https://doi.org/b", provider="cnki", source_kind="cnki-record"),
    ]
    R.seed_card_evidence(kb, refs)
    ids = {v["meta"]["evidence_id"] for v in kb.to_dict()["info_uuid_to_info_dict"].values()}
    assert ids == {refs[0]["id"], refs[1]["id"]}


def test_seed_empty_or_no_kb_is_safe_noop():
    """AC 降级：无证据 / 无知识库 → 安全空转返回 None，不拖垮圆桌启动。"""
    assert R.seed_card_evidence(_kb(), []) is None
    assert R.seed_card_evidence(_kb(), None) is None
    assert R.seed_card_evidence(None, [_ref()]) is None
    assert R.card_evidence_turn([{"no_id": 1}]) is None   # 无 id 的项被忽略
