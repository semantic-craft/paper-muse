"""
盲区扫描引擎（spec v2 §4）：第一性原理拆解 → 多模型三类卡枚举 →
去重/离群/抑制 → 新颖性三角定位（英文 web + zsearch 中文/自有语料）→ 流式出卡 → 落盘。

纯引擎，LM 与检索全部依赖注入，便于离线测试；真实接线见文件末尾 real_* 系列与 CLI。
"""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

# ---- persona（用户提供原文后整体替换本常量）----
FIRST_PRINCIPLES_PERSONA = (
    "你是第一性原理思考者：回到问题的根本，把问题拆解到最小可验证单元，"
    "永远追问『为什么成立』而不是『怎么做』；拒绝沿袭现成框架的惯性。"
)

CARD_TYPES = ["学科视角", "理论框架", "研究方法"]


# ---- 纯函数层 ----

def normalize_name(name: str) -> str:
    s = re.sub(r"[（(][^）)]*[）)]", "", name)  # 括号注记（缩写/译名）整体剔除
    s = re.sub(r"[\s【】\[\]\-—·]", "", s.strip().lower())
    return s


def extract_json(text: str):
    """从可能带说明文字/代码围栏的模型输出里抠出第一个 JSON 对象。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"输出中未找到 JSON：{text[:200]}")
    return json.loads(m.group(0))


def dedupe_cards(cards: list) -> list:
    merged = {}
    for c in cards:
        key = normalize_name(c["name"])
        if key in merged:
            models = merged[key]["source_models"]
            merged[key]["source_models"] = sorted(set(models) | set(c["source_models"]))
        else:
            merged[key] = dict(c)
    return list(merged.values())


def mark_outliers(cards: list) -> list:
    for c in cards:
        c["outlier"] = len(c["source_models"]) == 1
    return cards


def apply_suppression(cards: list, suppressed: set) -> list:
    return [c for c in cards if normalize_name(c["name"]) not in suppressed]


def classify_novelty(en_hits, zh_hits):
    """→ (分类, 是否金标)。zh_hits=None 表示 zsearch 不可用（明示未检，不装懂）。"""
    if zh_hits is None:
        return ("中文面未检", False)
    if zh_hits >= 3:
        return ("主流", False)
    if zh_hits >= 1:
        return ("边缘有人做", False)
    # zh_hits == 0
    gold = en_hits >= 3  # 英热中冷 = 引入型创新机会
    return ("交叉空白", gold)


# ---- 提示词 + 拆解与枚举（注入式 LM）----

ENUM_SCHEMA_HINT = (
    '只输出 JSON：{"cards": [{"type": "学科视角|理论框架|研究方法", "name": "...", '
    '"mechanism": "一句话机制", "why_nonobvious": "为什么对该研究者非显而易见", '
    '"steelman": "最强反驳：哪类审稿人会怎么打", "feasibility": "方法卡必填：数据从哪来", '
    '"questions": ["1-2个拷问句"]}]}'
)

REQUIRED_CARD_FIELDS = {"type", "name", "mechanism", "why_nonobvious", "steelman", "questions"}


def decompose_topic(topic: str, profile: str, llm_call) -> list:
    prompt = (
        f"{FIRST_PRINCIPLES_PERSONA}\n\n"
        f"研究者画像（可能为空）：{profile}\n"
        f"论文主题/困惑：{topic}\n\n"
        "用第一性原理把它拆成 3-5 个根本问题（最小可验证、互相独立、直指本质）。"
        '只输出 JSON：{"fundamentals": ["...", "..."]}'
    )
    return extract_json(llm_call(prompt))["fundamentals"]


def enumerate_cards(topic: str, fundamentals: list, profile: str, model_tag: str, llm_call) -> list:
    prompt = (
        "你要为一篇中文法学论文勘探非显而易见的切入点。跨学科越远越好，但必须论证适配性。\n"
        f"主题：{topic}\n根本问题：{json.dumps(fundamentals, ensure_ascii=False)}\n"
        f"研究者画像（『非显而易见』以此为参照系）：{profile or '未提供'}\n\n"
        "硬性配额：三类卡各至少 2 张——学科视角（其他学科怎么看这个问题）、"
        "理论框架（具体理论及其机制）、研究方法（实证/比较法/计算法学等，必附 feasibility 数据来源）。"
        "卡片之间必须彼此截然不同（学科、方法论、规范/实证、时间尺度错开），拒绝同一角度的变体。\n"
        + ENUM_SCHEMA_HINT
    )
    raw = extract_json(llm_call(prompt)).get("cards", [])
    cards = []
    for c in raw:
        if not REQUIRED_CARD_FIELDS <= set(c):
            continue
        c["source_models"] = [model_tag]
        cards.append(c)
    return cards
