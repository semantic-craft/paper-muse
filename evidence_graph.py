"""
证据图投影器（#53）：把七件产物 + evidence.json + run-manifest + feedback-events
折叠成可随时重建的 `{nodes, edges}` 投影。纯读、幂等、不改源、不引入第二事实源——
删掉投影后能从权威文件重建，重建结果逐字节稳定。

节点 kind：topic / card / claim / failure / evidence / annotation / feedback
关系 relation：supports / refutes / context / derived-from / annotates / deepens

设计要点（呼应 #53 验收）：
- 卡片/拓本节点只存于七件 markdown（无结构 cards.json）→ 容错解析 perspectives.md /
  sources.md 取 topic/card 节点与 card→evidence 边；join key = normalize_name。
- 未解析定位符 / 降级 provider / 被修正反馈在图中显式可见（unresolved/degraded/superseded），
  绝不当 verified。
- 每个源独立 try/except：单文件缺失或部分损坏只丢该源的贡献，不拖垮整图（部分损坏容忍）。
"""

import json
import re
from pathlib import Path

import blindspot          # normalize_name：与 feedback 事件的 name_norm 同一 join key

MUSE_SUBDIR = Path("docs") / "agents" / "muse"

# --- 七件 markdown 稳定契约（生产端见 blindspot._write_outputs / adversary._write_*）---
_TOPIC_RE = re.compile(r"^#\s*切入点卡片：(?P<topic>.+?)\s*$")
_CARD_RE = re.compile(
    r"^##\s+(?P<name>.+)（(?P<type>[^（）｜]+)｜(?P<novelty>[^（）｜]+)｜(?P<badge>[^（）]*)）\s*$"
)
_SRC_CARD_RE = re.compile(r"^-\s+\[(?P<name>[^\]]+)\]\s+.*—")
_SRC_JSON_RE = re.compile(r"^\s*-\s+EvidenceRef-JSON:\s+(?P<json>\{.*\})\s*$")
_CLAIM_RE = re.compile(r"^##\s+主张\s+(?P<cid>\S+)：(?P<text>.+)（(?P<src>[^（）]+)）\s*$")
_FAILURE_RE = re.compile(r"^###\s+\[(?P<fid>[^\]]+)\]\s+(?P<statement>.+?)\s*$")
_FAILURE_META_RE = re.compile(
    r"^-\s+类型：(?P<ftype>.+?)｜严重度：(?P<severity>.+?)｜裁决：\*\*(?P<verdict>.+?)\*\*\s*$"
)
_FAILURE_EV_RE = re.compile(
    r"^-\s+\[(?P<stance>证伪|佐证|上下文)\]\s+(?P<title>.*?)\s+—\s+(?P<url>.*?)"
    r"\s+·\s+EvidenceRef\s+`(?P<eid>[^`]+)`\s*$"
)
_UNRESOLVED_MARK = "未检得证据 · 不放行"

_STANCE_RELATION = {"证伪": "refutes", "佐证": "supports", "上下文": "context"}
_CLAIM_ORIGIN = {"抽自草稿": "draft", "构思幕卡片送入": "card", "手输主线": "input"}


# ---------------------------------------------------------------- 读文件（纯读、容错）

def _muse_dir(output_dir) -> Path:
    """只读版：绝不 mkdir（blindspot._muse_dir 会建目录，投影器不改文件系统）。"""
    return Path(output_dir).expanduser() / MUSE_SUBDIR


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list:
    out = []
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # 部分损坏：跳过坏行，保住其余
    return out


# ---------------------------------------------------------------- markdown 解析

def _parse_perspectives(text: str):
    """→ (topic 文本 or "", [{name, type, novelty}])。容错：不匹配的卡头跳过。"""
    topic = ""
    cards = []
    for line in text.splitlines():
        if not topic:
            m = _TOPIC_RE.match(line)
            if m:
                topic = m.group("topic").strip()
                continue
        m = _CARD_RE.match(line)
        if m:
            cards.append({
                "name": m.group("name").strip(),
                "type": m.group("type").strip(),
                "novelty": m.group("novelty").strip(),
            })
    return topic, cards


def _parse_sources(text: str):
    """→ [(card_name, ref_dict)]，来自 sources.md 每卡内联的 EvidenceRef-JSON。"""
    out = []
    current = None
    for line in text.splitlines():
        m = _SRC_CARD_RE.match(line)
        if m:
            current = m.group("name").strip()
            continue
        m = _SRC_JSON_RE.match(line)
        if m and current:
            try:
                ref = json.loads(m.group("json"))
            except json.JSONDecodeError:
                continue
            if isinstance(ref, dict) and ref.get("id"):
                out.append((current, ref))
    return out


def _parse_failure_points(text: str):
    """→ [{id, text, origin, failures:[{id, statement, ftype, severity, verdict,
    unresolved, evidence:[{eid, relation}]}]}]。逐行状态机，容错跳过坏行。"""
    claims = []
    claim = None
    failure = None
    for line in text.splitlines():
        m = _CLAIM_RE.match(line)
        if m:
            claim = {
                "id": m.group("cid").strip(),
                "text": m.group("text").strip(),
                "origin": _CLAIM_ORIGIN.get(m.group("src").strip(), m.group("src").strip()),
                "failures": [],
            }
            claims.append(claim)
            failure = None
            continue
        m = _FAILURE_RE.match(line)
        if m and claim is not None:
            failure = {
                "id": m.group("fid").strip(),
                "statement": m.group("statement").strip(),
                "ftype": "", "severity": "", "verdict": "未决",
                "unresolved": False, "evidence": [],
            }
            claim["failures"].append(failure)
            continue
        if failure is None:
            continue
        m = _FAILURE_META_RE.match(line)
        if m:
            failure["ftype"] = m.group("ftype").strip()
            failure["severity"] = m.group("severity").strip()
            failure["verdict"] = m.group("verdict").strip()
            continue
        if _UNRESOLVED_MARK in line:
            failure["unresolved"] = True
            continue
        m = _FAILURE_EV_RE.match(line)
        if m:
            failure["evidence"].append({
                "eid": m.group("eid").strip(),
                "relation": _STANCE_RELATION.get(m.group("stance"), "context"),
            })
    return claims


# ---------------------------------------------------------------- 节点/边装配

def _nid(kind: str, raw) -> str:
    return f"{kind}:{raw}"


def _ref_is_unresolved(ref: dict) -> bool:
    """定位符未决 / verification 未决 → 不当 verified。"""
    verification = ref.get("verification") or {}
    if verification.get("status") == "unresolved" or verification.get("degraded"):
        return True
    locator = ref.get("locator") or {}
    return locator.get("kind") == "unresolved" or not (locator.get("value") or (ref.get("source") or {}).get("url"))


def _evidence_node_from_ref(ref: dict) -> dict:
    source = ref.get("source") or {}
    retrieval = ref.get("retrieval") or {}
    verification = ref.get("verification") or {}
    return {
        "id": _nid("evidence", ref["id"]),
        "kind": "evidence",
        "ref_id": ref["id"],
        "title": source.get("title", ""),
        "url": source.get("url", ""),
        "provider": retrieval.get("provider", ""),
        "relation": ref.get("relation", "context"),
        "status": verification.get("status", ""),
        "unresolved": _ref_is_unresolved(ref),
        "degraded": bool(verification.get("degraded")),
    }


class _Graph:
    """节点按 id 去重（后见补空缺属性，不覆盖已有非空值）；边按 (source,target,relation) 去重。"""

    def __init__(self):
        self._nodes = {}
        self._edges = {}

    def node(self, node: dict):
        nid = node["id"]
        cur = self._nodes.get(nid)
        if cur is None:
            self._nodes[nid] = dict(node)
        else:
            for k, v in node.items():
                if not cur.get(k) and v:
                    cur[k] = v
        return nid

    def edge(self, source: str, target: str, relation: str, **attrs):
        key = (source, target, relation)
        if key not in self._edges:
            self._edges[key] = {"source": source, "target": target,
                                "relation": relation, **attrs}

    def mark_degraded(self, evidence_ref_id: str, reason: str):
        nid = _nid("evidence", evidence_ref_id)
        node = self._nodes.get(nid)
        if node is not None:
            node["degraded"] = True
            node.setdefault("degraded_reasons", [])
            if reason and reason not in node["degraded_reasons"]:
                node["degraded_reasons"].append(reason)

    def finalize(self) -> dict:
        nodes = sorted(self._nodes.values(), key=lambda n: (n["kind"], n["id"]))
        edges = sorted(self._edges.values(),
                       key=lambda e: (e["relation"], e["source"], e["target"]))
        return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- 投影主入口

def build_graph(output_dir) -> dict:
    """从权威产物折叠证据图投影。纯读、幂等：同样输入 → 逐字节相同输出。
    返回 {"nodes":[...], "edges":[...], "meta":{...}}。"""
    d = _muse_dir(output_dir)
    g = _Graph()
    degraded_providers = []

    # --- 构思幕：topic + card 节点（perspectives.md），card→evidence（sources.md）---
    topic, cards = _parse_perspectives(_read_text(d / "perspectives.md"))
    topic_id = ""
    if topic:
        topic_id = g.node({"id": _nid("topic", blindspot.normalize_name(topic)),
                           "kind": "topic", "title": topic})
    card_id_by_norm = {}
    for card in cards:
        norm = blindspot.normalize_name(card["name"])
        cid = g.node({"id": _nid("card", norm), "kind": "card", "name": card["name"],
                      "type": card["type"], "novelty": card["novelty"]})
        card_id_by_norm[norm] = cid
        if topic_id:
            g.edge(cid, topic_id, "derived-from")

    for card_name, ref in _parse_sources(_read_text(d / "sources.md")):
        norm = blindspot.normalize_name(card_name)
        card_node_id = card_id_by_norm.get(norm) or g.node(
            {"id": _nid("card", norm), "kind": "card", "name": card_name})
        card_id_by_norm.setdefault(norm, card_node_id)
        ev_id = g.node(_evidence_node_from_ref(ref))
        # provider 级 relation（构思幕检索证据默认 "discovery"）规范到图的 stance 词表：
        # 只有 supports/refutes 是明确表态，其余（discovery 及未知）都归「上下文来源」，
        # 否则该边落成非规范关系 → 被 evidence_for_card 静默丢弃、卡片抽屉显示无来源。
        rel = ref.get("relation", "context")
        g.edge(ev_id, card_node_id, rel if rel in ("supports", "refutes") else "context")

    # --- 证据仓：evidence.json 的 evidence 注册表（补全身份桩的 ref 详情）---
    store = _read_json(d / "evidence.json") or {}
    evidence_store = store.get("evidence") if isinstance(store.get("evidence"), dict) else {}
    for ref in evidence_store.values():
        if isinstance(ref, dict) and ref.get("id"):
            g.node(_evidence_node_from_ref(ref))

    # --- 对抗幕：claim / failure 节点 + evidence→failure（failure-points.md）---
    for claim in _parse_failure_points(_read_text(d / "failure-points.md")):
        claim_id = g.node({"id": _nid("claim", claim["id"]), "kind": "claim",
                           "text": claim["text"], "origin": claim["origin"]})
        for f in claim["failures"]:
            failure_id = g.node({
                "id": _nid("failure", f["id"]), "kind": "failure",
                "statement": f["statement"], "ftype": f["ftype"],
                "severity": f["severity"], "verdict": f["verdict"],
                "unresolved": f["unresolved"],
            })
            g.edge(failure_id, claim_id, "refutes")   # 失败点 = 对主张的一处攻击
            for ev in f["evidence"]:
                g.node({"id": _nid("evidence", ev["eid"]), "kind": "evidence",
                        "ref_id": ev["eid"]})           # 身份桩，详情待 evidence.json 补
                g.edge(_nid("evidence", ev["eid"]), failure_id, ev["relation"])

    # --- 批注交接：annotation 节点（annotation-handoff.json）---
    handoff = _read_json(d / "annotation-handoff.json") or {}
    for ann in (handoff.get("annotations") or []):
        if not isinstance(ann, dict):
            continue
        ann_raw = ann.get("annotation_id") or ""
        meta = ann.get("meta") or {}
        attachment = ann.get("attachment") or {}
        ann_id = g.node({
            "id": _nid("annotation", ann_raw), "kind": "annotation",
            "claim_text": meta.get("claim", ""),
            "attachment_status": attachment.get("status", ""),
        })
        # annotation_id 形如 "claim-{id}" → annotates 该主张
        if ann_raw.startswith("claim-"):
            claim_ref = _nid("claim", ann_raw[len("claim-"):])
            if claim_ref in g._nodes:
                g.edge(ann_id, claim_ref, "annotates")
        for eid in meta.get("evidence_ids") or []:
            if not eid:
                continue
            g.node({"id": _nid("evidence", eid), "kind": "evidence", "ref_id": eid})
            g.edge(ann_id, _nid("evidence", eid), "context")

    # --- 反馈事件：feedback 节点 + deepens（feedback-events.jsonl，含 superseded）---
    events = _read_jsonl(d / "feedback-events.jsonl")
    superseded_ids = {ev.get("supersedes") for ev in events if ev.get("supersedes")}
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("event_id"):
            continue
        fb_id = g.node({
            "id": _nid("feedback", ev["event_id"]), "kind": "feedback",
            "name": ev.get("name", ""), "verdict": ev.get("verdict", ""),
            "ts": ev.get("ts", ""),
            "superseded": ev.get("event_id") in superseded_ids,  # 被修正 → 不当 current
        })
        norm = ev.get("name_norm") or blindspot.normalize_name(ev.get("name", ""))
        card_node_id = card_id_by_norm.get(norm)
        if card_node_id:
            g.edge(fb_id, card_node_id, "deepens")
        for eid in ev.get("evidence_ids") or []:
            if not eid:
                continue
            g.node({"id": _nid("evidence", eid), "kind": "evidence", "ref_id": eid})
            g.edge(fb_id, _nid("evidence", eid), "context")

    # --- run-manifest：降级 provider 在图中可见（标记对应证据节点）---
    for manifest in _read_jsonl(d / "run-manifest.jsonl"):
        if not isinstance(manifest, dict):
            continue
        degradation = manifest.get("degradation") or []
        if not degradation:
            continue
        degraded_providers.extend(str(x) for x in degradation)
        for eid in manifest.get("evidence_ids") or []:
            for reason in degradation:
                g.mark_degraded(str(eid), str(reason))

    graph = g.finalize()
    graph["meta"] = {
        "topic_id": topic_id,
        "degraded_providers": sorted(set(degraded_providers)),
    }
    return graph


# ---------------------------------------------------------------- 关系查询

def _incoming(graph: dict, target_id: str, relations=None) -> list:
    return [e for e in graph["edges"] if e["target"] == target_id
            and (relations is None or e["relation"] in relations)]


def _outgoing(graph: dict, source_id: str, relations=None) -> list:
    return [e for e in graph["edges"] if e["source"] == source_id
            and (relations is None or e["relation"] in relations)]


def _node(graph: dict, node_id: str):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def evidence_for_card(graph: dict, card_id: str) -> dict:
    """按卡片查：支持/证伪/上下文来源 + 后续判断（反馈，含 superseded）。
    card_id 可给完整节点 id（"card:xxx"）或原始 name（自动 normalize）。"""
    if not card_id.startswith("card:"):
        card_id = _nid("card", blindspot.normalize_name(card_id))
    out = {"card": _node(graph, card_id), "supports": [], "refutes": [],
           "context": [], "feedback": []}
    if out["card"] is None:
        return out
    for e in _incoming(graph, card_id):
        src = _node(graph, e["source"])
        if src is None:
            continue
        if e["relation"] in ("supports", "refutes", "context") and src["kind"] == "evidence":
            out[e["relation"]].append(src)
        elif e["relation"] == "deepens" and src["kind"] == "feedback":
            out["feedback"].append(src)
    return out


def evidence_for_claim(graph: dict, claim_id: str) -> dict:
    """按主张查：每个失败点（对主张的攻击）+ 其支持/证伪/上下文证据 + 批注。"""
    if not claim_id.startswith("claim:"):
        claim_id = _nid("claim", claim_id)
    out = {"claim": _node(graph, claim_id), "failures": [], "annotations": []}
    if out["claim"] is None:
        return out
    for e in _incoming(graph, claim_id):
        src = _node(graph, e["source"])
        if src is None:
            continue
        if e["relation"] == "refutes" and src["kind"] == "failure":
            bucket = {"failure": src, "supports": [], "refutes": [], "context": []}
            for fe in _incoming(graph, src["id"], ("supports", "refutes", "context")):
                ev = _node(graph, fe["source"])
                if ev is not None and ev["kind"] == "evidence":
                    bucket[fe["relation"]].append(ev)
            out["failures"].append(bucket)
        elif e["relation"] == "annotates" and src["kind"] == "annotation":
            out["annotations"].append(src)
    return out
