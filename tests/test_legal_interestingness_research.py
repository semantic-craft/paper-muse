from pathlib import Path


RESEARCH_ASSET = (
    Path(__file__).parents[1]
    / "docs/research/2026-07-13-chinese-legal-interestingness-criteria.md"
)


def test_legal_interestingness_asset_is_prompt_ready_for_both_communities():
    text = RESEARCH_ASSET.read_text(encoding="utf-8")

    for community in ("教义学", "社科法学"):
        assert f"## {community}" in text
        assert f"### {community}｜#93 质量闸判据" in text
        assert f"### {community}｜#94 rubric 措辞" in text
        assert f"### {community}｜中文法学示例" in text

    assert "证据缺口·经验判断" in text
    assert "不得预测录用、被引或论文质量" in text
    assert "Goyanes 五维不进入本判据" in text
    assert "Oppenheimer 清晰度不进入本判据" in text
