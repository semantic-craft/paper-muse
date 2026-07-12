"""#50：不可变反馈事件流 + 投影 + 离线 replay 指标。

三键反馈（已知 / 新但不适用 / 新且值得深挖）、反馈修正、后续判断记为不可变事件
（feedback-events.jsonl，只追加），保留 run/card/evidence/version 关联。修正 = 追加新事件
supersede 旧判断，**不篡改历史**。现有 angle-feedback「已知」抑制面由事件投影得到，兼容不变。

离线 replay 从固定 card 快照（不调付费 API）+ 事件计算可解释指标，证明反馈正在改变下一轮：
被标「已知」的角度在下一轮不该再出现（suppressed_leaked=0）。

纯 stdlib（+ blindspot 的 muse 目录/命名归一）；时间戳由调用方传入 → 离线测试确定性。
"""

import hashlib
import json
import threading

import blindspot

EVENTS_FILE = "feedback-events.jsonl"
VERDICTS = ("已知", "新但不适用", "新且值得深挖")
_LOCK = threading.Lock()


def _events_path(output_dir):
    return blindspot._muse_dir(str(output_dir)) / EVENTS_FILE


def _event_id(name_norm: str, version: int, ts: str) -> str:
    return "fev_" + hashlib.sha256(f"{name_norm}\n{version}\n{ts}".encode("utf-8")).hexdigest()[:12]


def read_events(output_dir) -> list:
    path = _events_path(output_dir)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _event_count(output_dir) -> int:
    """现有事件行数（version 派生用）。只数非空行、不做 JSON 解析：避免为求计数把全部历史
    事件反序列化一遍（逐条追加累计 O(n²)，#15）；且计入坏行 → version 不因某行损坏而回退、
    与既有 version 冲突（单调性更稳）。"""
    path = _events_path(output_dir)
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def record_event(output_dir, *, name: str, verdict: str, ts: str, run_id: str = "",
                 card_id="", evidence_ids=None, applicability: str = "", note: str = "",
                 supersedes: str = "") -> dict:
    """追加一条不可变反馈事件（version 单调递增）。修正走再记一条（可带 supersedes 指前一条）。"""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 必须是 {VERDICTS} 之一，得到 {verdict!r}")
    with _LOCK:
        version = _event_count(output_dir) + 1
        name_norm = blindspot.normalize_name(name)
        ev = {
            "event_id": _event_id(name_norm, version, ts),
            "version": version,
            "ts": ts,
            "run_id": run_id,
            "card_id": str(card_id),
            "name": name,
            "name_norm": name_norm,
            "verdict": verdict,
            "evidence_ids": list(evidence_ids or []),
            "applicability": applicability,
            "note": note,
            "supersedes": supersedes,
        }
        with _events_path(output_dir).open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def project(events) -> dict:
    """折叠事件 → 每角度最新判断（后 supersede 前，历史不改）+ 抑制集 / 适用性约束 / 优先级提升。"""
    latest = {}
    for ev in sorted(events or [], key=lambda e: e.get("version", 0)):
        latest[ev.get("name_norm")] = ev
    suppressed = {n for n, ev in latest.items() if ev.get("verdict") == "已知"}
    applicability = {n: ev.get("applicability", "") for n, ev in latest.items()
                     if ev.get("verdict") == "新但不适用"}
    priority_boost = {n: 1 for n, ev in latest.items() if ev.get("verdict") == "新且值得深挖"}
    return {"suppressed": suppressed, "applicability": applicability,
            "priority_boost": priority_boost, "latest": latest}


def rebuild_angle_feedback(output_dir) -> dict:
    """从事件投影重建 angle-feedback.json——兼容面：blindspot.load_suppressed 照常读它，
    但事实源现在是事件流。返回投影。"""
    proj = project(read_events(output_dir))
    data = {ev["name_norm"]: {"name": ev["name"], "verdict": ev["verdict"]}
            for ev in proj["latest"].values()}
    (blindspot._muse_dir(str(output_dir)) / "angle-feedback.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def replay_metrics(rounds, events=None) -> list:
    """离线 replay：从固定 card 快照（rounds: 逐轮 card 列表，无付费 API）+ 事件算可解释指标。
    证明反馈改变下一轮：被标「已知」的角度不该在后续轮再出现（suppressed_leaked 应为 0）。
    每轮指标：首个有价值卡位、重复率、gold/outlier 选择性、验证 locator 比例、降级率、
    证据复用、suppressed 泄漏。"""
    proj = project(events or [])
    suppressed = proj["suppressed"]
    out, seen_prev, evidence_seen = [], set(), set()
    for i, cards in enumerate(rounds or []):
        n = len(cards)
        names = [blindspot.normalize_name(c.get("name", "")) for c in cards]
        loc_total = sum(len(c.get("evidence") or []) for c in cards)
        loc_verified = sum(
            1 for c in cards for e in (c.get("evidence") or [])
            if isinstance(e, dict) and (e.get("verification") or {}).get("status") == "provider-retrieved")
        round_eids = [e.get("id") for c in cards for e in (c.get("evidence") or [])
                      if isinstance(e, dict) and e.get("id")]
        reused = sum(1 for eid in round_eids if eid in evidence_seen)
        out.append({
            "round": i,
            "cards": n,
            "first_valuable_pos": next(
                (j for j, c in enumerate(cards)
                 if c.get("verdict") == "新且值得深挖" or c.get("gold")), None),
            "repeat_rate": round(sum(1 for x in names if x in seen_prev) / n, 4) if n else 0.0,
            "gold_selectivity": round(sum(1 for c in cards if c.get("gold")) / n, 4) if n else 0.0,
            "outlier_selectivity": round(sum(1 for c in cards if c.get("outlier")) / n, 4) if n else 0.0,
            "verified_locator_ratio": round(loc_verified / loc_total, 4) if loc_total else 0.0,
            "degradation_rate": round(sum(1 for c in cards if c.get("degraded")) / n, 4) if n else 0.0,
            "evidence_reuse": reused,
            "suppressed_leaked": sum(1 for x in names if x in suppressed),  # 应为 0=反馈生效
        })
        seen_prev |= set(names)
        evidence_seen |= set(round_eids)
    return out
