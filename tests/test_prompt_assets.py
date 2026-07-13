import json

from prompt_assets import (
    K_DENSE_ATTRIBUTION,
    K_DENSE_SKILL_SOURCES,
    SCAN_METHOD_PROMPT,
    ADVERSARY_METHOD_PROMPT,
)


def test_k_dense_prompt_assets_carry_source_and_license():
    assert "K-Dense" in K_DENSE_ATTRIBUTION and "MIT License" in K_DENSE_ATTRIBUTION
    assert set(K_DENSE_SKILL_SOURCES) == {
        "hypothesis-generation",
        "scientific-brainstorming",
        "peer-review",
    }
    assert all(item["license"] == "MIT" and item["url"].startswith("https://github.com/K-Dense-AI/")
               for item in K_DENSE_SKILL_SOURCES.values())
    assert "可证伪" in SCAN_METHOD_PROMPT
    assert "无证据只能标未决" in ADVERSARY_METHOD_PROMPT


def test_scan_prompt_includes_k_dense_legal_methodology():
    from blindspot import enumerate_cards

    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return json.dumps({"cards": [
            {"type": "理论框架", "name": "X", "mechanism": "m",
             "why_nonobvious": "w", "steelman": "s", "questions": ["q"]}
        ]})

    enumerate_cards("平台责任", ["根1"], "", "gemini", llm)

    assert "中文法学化科研构思方法" in prompts[0]
    assert "K-Dense" in prompts[0]
    assert "可检验预测" in prompts[0]


def test_adversary_prompt_includes_k_dense_peer_review_methodology():
    from adversary import red_team

    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return json.dumps({"failures": [
            {"statement": "概念滑移", "type": "概念滑坡", "severity": "重大"}
        ]})

    red_team("平台自治足以替代执法", llm)

    assert "中文法学化同行评审方法" in prompts[0]
    assert "K-Dense" in prompts[0]
    assert "无证据只能标未决" in prompts[0]


# ---- #94 对抗幕 rubric 增「有趣性/贡献」一行（取 #83 法学本地化产物）----

def test_adversary_rubric_carries_interestingness_contribution_line():
    """rubric 增补「有趣性/贡献」复审：按共同体换形（教义学 vs 社科法学）、要求制度可推导、
    经最强反驳存活；取 #83 产物措辞并附一枚判例式示例；不引入被排除的维度；抗注入铁律不变。"""
    p = ADVERSARY_METHOD_PROMPT
    assert "有趣性/贡献" in p
    # 双共同体换形（D9）
    assert "教义学" in p and "社科法学" in p
    assert "规范冲突" in p and "教义漏洞" in p            # 教义学形态（#83 line 85）
    assert "经验默认前提" in p                             # 社科法学形态（#83 line 104/136）
    # 制度可推导性：改变具体法律适用 / 规范评价 / 制度设计
    assert "法律适用" in p and "制度设计" in p
    # 一枚判例式示例，引用 #83 已合并产物
    assert "2026-07-13-chinese-legal-interestingness-criteria" in p
    assert "显化" in p                                     # 判例式示例素材
    # 严谨防呆：张力不替代可辩护性——反驳成立不得判高贡献
    assert "高贡献" in p
    # #83 明确排除：不得把清晰度/被引/录用当加分理由，且不引入 Goyanes/Oppenheimer
    assert "录用" in p and "被引" in p
    assert "Oppenheimer" not in p and "Goyanes" not in p and "清晰度" not in p
    # 抗注入铁律：代码三态裁决口径仍在
    assert "无证据只能标未决" in p
