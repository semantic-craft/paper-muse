from types import SimpleNamespace

from knowledge_storm.collaborative_storm.engine import DiscourseManager
from knowledge_storm.collaborative_storm.modules.roundtable_personas import (
    FIXED_ROUNDTABLE_EXPERTS,
    warmstart_experts_to_process,
    with_fixed_roundtable_experts,
)


def test_fixed_roundtable_experts_prepend_dynamic_experts():
    experts = with_fixed_roundtable_experts(["Domain Expert: follows the topic"])

    assert experts[:2] == FIXED_ROUNDTABLE_EXPERTS
    assert "别照搬" in experts[0]
    assert experts[2:] == ["Domain Expert: follows the topic"]


def test_fixed_roundtable_experts_dedupe_generated_roles():
    experts = with_fixed_roundtable_experts(
        [
            "跨学科猎人：duplicate generated role",
            "Domain Expert: follows the topic",
        ]
    )

    assert experts == [
        *FIXED_ROUNDTABLE_EXPERTS,
        "Domain Expert: follows the topic",
    ]


def test_warmstart_processes_fixed_and_requested_dynamic_experts():
    experts = with_fixed_roundtable_experts(
        [
            "Domain Expert 1: follows the topic",
            "Domain Expert 2: follows the topic",
            "Domain Expert 3: extra generated role",
        ]
    )

    selected = warmstart_experts_to_process(experts, generated_expert_limit=2)

    assert selected == [
        *FIXED_ROUNDTABLE_EXPERTS,
        "Domain Expert 1: follows the topic",
        "Domain Expert 2: follows the topic",
    ]


def test_discourse_expert_refresh_keeps_fixed_roundtable_seats():
    manager = object.__new__(DiscourseManager)
    manager.runner_argument = SimpleNamespace(
        topic="topic", max_num_round_table_experts=1
    )
    manager.generate_expert_module = lambda **_kw: SimpleNamespace(
        experts=["Domain Expert: follows the topic"]
    )
    manager._parse_expert_names_to_agent = lambda experts: experts

    manager._update_expert_list_from_utterance("focus", "background")

    assert manager.experts == [
        *FIXED_ROUNDTABLE_EXPERTS,
        "Domain Expert: follows the topic",
    ]
