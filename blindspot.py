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
