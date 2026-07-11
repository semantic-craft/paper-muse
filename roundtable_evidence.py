"""#47：卡片 EvidenceRef ⇆ Co-STORM Information 双向映射 + 注入圆桌知识库。

从构思卡进入 Co-STORM 圆桌时，把卡片已有的统一 EvidenceRef 作为先验证据 seed 进
知识库，让证据身份贯穿专家发言 / 动态知识结构 / 报告 / instance_dump——身份经
`Information.meta["evidence_id"]` 随 `to_dict`/`from_dict` 全程往返，圆桌产物据此可
回连 `evidence.json`（`paperqa_bridge.read_evidence`），减少无关联的重复检索。

注入走 `KnowledgeBase.update_from_conv_turn(insert_under_root=True)`——确定性、无需
LLM placement；缺证据 / 无知识库时安全空转（不拖垮圆桌启动）。

knowledge_storm 依赖较重，本模块由服务端 warm_start 路径按需 import（那时引擎已加载），
不进服务启动关键路径。
"""

from knowledge_storm.dataclass import ConversationTurn
from knowledge_storm.interface import Information

# 合成注入回合的角色名（圆桌转录/知识库里可辨识「卡片携带的先验证据」）。
CARD_EVIDENCE_ROLE = "卡片证据"


def evidence_ref_to_information(ref: dict) -> Information:
    """统一 EvidenceRef → Co-STORM Information。
    id 塞进 meta["evidence_id"] 作贯穿身份；locator/relation/provider 一并留在 meta，
    使圆桌产物可回连 evidence.json。自有库文档常无 web url → 退回 locator 值 / identity /
    evidence id 作 Information 的 url（Co-STORM 拿 url 当身份，不能为空）。"""
    source = ref.get("source") or {}
    locator = ref.get("locator") or {}
    retrieval = ref.get("retrieval") or {}
    exact = str(locator.get("exact") or "")
    title = str(source.get("title") or "")
    url = str(source.get("url") or locator.get("value")
              or source.get("identity") or ref.get("id") or "")
    snippets = [s for s in (exact, title) if s][:1] or [url]
    return Information(
        url=url,
        description=title or exact,
        snippets=snippets,
        title=title or url,
        meta={
            "evidence_id": str(ref.get("id") or ""),
            "relation": str(ref.get("relation") or ""),
            "provider": str(retrieval.get("provider") or ""),
            "query": str(retrieval.get("query") or ""),
            "source_kind": str(source.get("kind") or ""),
            "locator": locator if isinstance(locator, dict) else {},
        },
    )


def information_evidence_id(info) -> str:
    """反向映射：从 Information（可能刚 from_dict 反序列化）取回 evidence id，
    用于圆桌产物回连 evidence.json。非卡片证据的 Information 返回空串。"""
    meta = getattr(info, "meta", None)
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("evidence_id") or "")


def card_evidence_turn(refs, *, note: str = "") -> ConversationTurn | None:
    """把卡片 EvidenceRef 列表包成一条合成 ConversationTurn（cited_info 携带各 Information）。
    无有效证据（空列表 / 无 id）→ None。"""
    infos = [evidence_ref_to_information(r)
             for r in (refs or []) if isinstance(r, dict) and r.get("id")]
    if not infos:
        return None
    turn = ConversationTurn(
        role=CARD_EVIDENCE_ROLE,
        raw_utterance=note or "从构思卡携带的已有证据进入圆桌。",
        utterance_type="Grounding",
    )
    turn.raw_retrieved_info = list(infos)
    # cited_info 需为 {citation_idx: Information}——update_from_conv_turn 取其 values() 插入；
    # 具体键不重要，insert_information 会按 hash 重派 citation_uuid（同 url/snippets/query 天然去重）。
    turn.cited_info = {i + 1: info for i, info in enumerate(infos)}
    return turn


def seed_card_evidence(knowledge_base, refs, *, note: str = "") -> ConversationTurn | None:
    """把卡片证据注入 Co-STORM 知识库（insert_under_root，确定性、无需 LLM placement）。
    返回合成的 ConversationTurn（调用方可选择追加进 conversation_history）。
    无证据 / 无知识库 → None，安全空转（缺证据不拖垮圆桌启动）。"""
    turn = card_evidence_turn(refs, note=note)
    if turn is None or knowledge_base is None:
        return None
    knowledge_base.update_from_conv_turn(turn, insert_under_root=True)
    return turn
