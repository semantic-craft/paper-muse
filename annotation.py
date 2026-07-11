"""#48：可重附着批注交接包（W3C Web Annotation selector 语义）。

把草稿字符 span、PaperQA PDF 引文、web 证据的 locator 统一成同一套复合 selector：
- quote：exact + prefix + suffix（TextQuoteSelector，位置无关，改稿后仍可定位）
- position：start / end（TextPositionSelector，作快路径提示）
- page：PDF 页锚
- source：id + version + checksum（改稿检测）

`reattach()` 在（可能改过的）文本里重定位 selector：唯一命中=verified、多处命中=ambiguous、
未命中/信息不足=unresolved——**绝不模糊/近似猜测成功**（宁可 unresolved 也不错附）。

`AnnotationSink` 把一批 EvidenceRef 汇成稳定交接包（annotation-handoff.json）交给下游
（paper-annotator 等），不要求 paper-muse 内建 PDF viewer / 批注编辑器。

纯 stdlib，无第三方依赖，主 venv 可导入；不改 evidence.py 的 EvidenceRef 契约（checksum
在打包层按当时源文本算，属批注层关注点）。
"""

import hashlib

SCHEMA_VERSION = 1

# 复合 selector 的默认上下文窗口（前后各取多少字符做 prefix/suffix，用于消歧与重附着）。
DEFAULT_CONTEXT = 32


def text_checksum(text: str) -> str:
    """源文本内容校验和（改稿检测：交接包记录的 checksum 与当前源不符即知源已变）。"""
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _clip(text: str, start: int, end: int) -> str:
    return text[max(0, start):max(0, end)]


def selector_from_span(text: str, offset: int, length: int, *, context: int = DEFAULT_CONTEXT) -> dict:
    """草稿字符 span {offset,length} → 复合 selector（quote+context+position）。
    对抗幕失败点 span、任何逐字定位都归一到这里。offset 越界 → 返回仅 quote 空的 unresolvable selector。"""
    if not text or offset < 0 or offset > len(text) or length < 0:
        return {"quote": {"exact": "", "prefix": "", "suffix": ""}, "position": None}
    end = min(len(text), offset + length)
    return {
        "quote": {
            "exact": text[offset:end],
            "prefix": _clip(text, offset - context, offset),
            "suffix": _clip(text, end, end + context),
        },
        "position": {"start": offset, "end": end},
    }


def selector_from_evidence(ref: dict) -> dict:
    """EvidenceRef.locator → 复合 selector。缺 exact 的（纯 url/page 锚）也照收——
    重附着时按信息量降级为 unresolved，不伪造命中。"""
    locator = ref.get("locator") or {}
    source = ref.get("source") or {}
    start, end = locator.get("start"), locator.get("end")
    position = ({"start": start, "end": end}
                if isinstance(start, int) and isinstance(end, int) else None)
    return {
        "evidence_id": str(ref.get("id") or ""),
        "quote": {
            "exact": str(locator.get("exact") or ""),
            "prefix": str(locator.get("prefix") or ""),
            "suffix": str(locator.get("suffix") or ""),
        },
        "position": position,
        "page": locator.get("page"),
        "source": {
            "id": str(locator.get("source_identity") or source.get("identity") or ""),
            "version": str(locator.get("source_version") or source.get("version") or ""),
        },
    }


def _context_ok(text: str, i: int, exact: str, prefix: str, suffix: str) -> bool:
    """命中位置 i 的前后邻接是否与 prefix/suffix 一致（改稿在别处时邻接不变，仍匹配）。
    文档边缘导致 prefix/suffix 被截断时放行（不因边缘截断误判不匹配）。"""
    before = text[:i]
    after = text[i + len(exact):]
    prefix_ok = (not prefix) or before.endswith(prefix) or prefix.endswith(before[-len(prefix):] or before)
    suffix_ok = (not suffix) or after.startswith(suffix) or suffix.startswith(after[:len(suffix)])
    return prefix_ok and suffix_ok


def _all_occurrences(text: str, exact: str) -> list:
    out, i = [], text.find(exact)
    while i != -1:
        out.append(i)
        i = text.find(exact, i + 1)
    return out


def reattach(selector: dict, text: str) -> dict:
    """在（可能改过的）text 里重定位 selector。
    → {status: verified|ambiguous|unresolved, start, end, reason}。
    verified 唯一命中；ambiguous 多处不猜；unresolved 未命中/无 exact。绝不近似匹配。"""
    quote = selector.get("quote") or {}
    exact = str(quote.get("exact") or "")
    if not exact or not text:
        return {"status": "unresolved", "start": None, "end": None,
                "reason": "无 exact 引文或源文本为空"}
    prefix = str(quote.get("prefix") or "")
    suffix = str(quote.get("suffix") or "")

    occ = _all_occurrences(text, exact)
    if not occ:
        return {"status": "unresolved", "start": None, "end": None, "reason": "exact 引文未在源中逐字命中"}

    # position 快路径：原 start 仍逐字命中且上下文不矛盾 → 该段未被改动
    pos = selector.get("position") or {}
    s0 = pos.get("start")
    if isinstance(s0, int) and s0 in occ and _context_ok(text, s0, exact, prefix, suffix):
        return _verified(s0, exact)

    # prefix/suffix 上下文收敛（改稿在别处：邻接不变仍能锁定）
    if prefix or suffix:
        ctx = [i for i in occ if _context_ok(text, i, exact, prefix, suffix)]
        if len(ctx) == 1:
            return _verified(ctx[0], exact)
        if len(ctx) > 1:
            return {"status": "ambiguous", "start": None, "end": None,
                    "candidates": ctx, "reason": "上下文仍无法唯一定位"}

    if len(occ) == 1:
        return _verified(occ[0], exact)
    return {"status": "ambiguous", "start": None, "end": None,
            "candidates": occ, "reason": "exact 多处命中且无上下文消歧"}


def _verified(start: int, exact: str) -> dict:
    return {"status": "verified", "start": start, "end": start + len(exact), "reason": ""}


class AnnotationSink:
    """把证据/批注汇成稳定的可重附着交接包，交给下游批注消费者（paper-annotator 等）。
    只产 selector + 重附着状态；不内建 PDF viewer / 批注编辑器。"""

    def _attach(self, sel: dict, source_text) -> dict:
        """对一条 selector 重附着：给了源就落 verified/ambiguous/unresolved，没给就 unattached。"""
        if source_text is None:
            return {"status": "unattached", "start": None, "end": None, "reason": ""}
        if not (sel.get("quote") or {}).get("exact"):
            return {"status": "unresolved", "start": None, "end": None,
                    "reason": "无 exact 引文（纯 url/page 锚），需下游按 page/source 处理"}
        a = reattach(sel, source_text)
        return {k: a[k] for k in ("status", "start", "end", "reason")}

    def _envelope(self, annotations, source_text, kind, tid, tver) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": {"kind": kind, "id": tid, "version": tver,
                       "checksum": text_checksum(source_text) if source_text is not None else ""},
            "annotations": annotations,
        }

    def package(self, refs, *, source_text: str | None = None,
                target_kind: str = "draft", target_id: str = "", target_version: str = "") -> dict:
        """EvidenceRef 交接包：每条证据的 locator → selector + 重附着状态。"""
        anns = [{**selector_from_evidence(ref), "attachment": self._attach(selector_from_evidence(ref), source_text)}
                for ref in (refs or []) if isinstance(ref, dict)]
        return self._envelope(anns, source_text, target_kind, target_id, target_version)

    def package_annotations(self, items, *, source_text: str | None = None,
                            target_kind: str = "draft", target_id: str = "",
                            target_version: str = "") -> dict:
        """预构 selector 交接包：items = [{"id", "selector"(selector_from_span 等产), "meta"}]。
        对抗幕草稿锚失败点走这条。"""
        anns = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            sel = it.get("selector") or {}
            anns.append({"annotation_id": str(it.get("id") or ""),
                         "quote": sel.get("quote") or {"exact": "", "prefix": "", "suffix": ""},
                         "position": sel.get("position"),
                         "meta": it.get("meta") or {},
                         "attachment": self._attach(sel, source_text)})
        return self._envelope(anns, source_text, target_kind, target_id, target_version)
