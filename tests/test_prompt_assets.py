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
