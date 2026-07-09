from typing import List, Optional

FIXED_ROUNDTABLE_EXPERTS = [
    "第一性原理专家: 回到问题的基本约束、因果链和可证伪假设，要求每个判断说明前提、证据和边界。",
    "跨学科猎人: 从相邻学科、历史案例、技术实践和制度设计中寻找类比、反例和可迁移框架。",
]


def _roundtable_expert_key(expert: str) -> str:
    role_name, _, _ = str(expert).replace("：", ":").partition(":")
    return (role_name.strip() or str(expert).strip()).casefold()


def with_fixed_roundtable_experts(experts: Optional[List[str]]) -> List[str]:
    merged = []
    seen = set()
    for expert in [*FIXED_ROUNDTABLE_EXPERTS, *(experts or [])]:
        expert = str(expert).strip()
        if not expert:
            continue
        key = _roundtable_expert_key(expert)
        if key in seen:
            continue
        seen.add(key)
        merged.append(expert)
    return merged


def warmstart_experts_to_process(
    experts: List[str], generated_expert_limit: int
) -> List[str]:
    total_limit = max(0, generated_expert_limit) + len(FIXED_ROUNDTABLE_EXPERTS)
    return experts[: min(len(experts), total_limit)]
