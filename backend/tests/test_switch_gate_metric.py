import pytest
from acceptance_evaluation import (
    PRODUCTION,
    VARIANTS,
    gate_variant,
    measured_identity,
    resolve_direct,
    select_variants,
)


def variant(mode: str, options: dict, recall: float) -> dict:
    return {
        "mode": mode,
        "options": options,
        "recall_at_5": recall,
        "recall_at_10": recall,
        "ndcg_at_10": recall,
    }


def test_switch_gate_uses_the_production_hybrid_variant_not_the_best_scoring_one():
    ablated = {"query_rewrite": False, "rerank": False}
    variants = [
        variant("keyword", PRODUCTION, 0.95),
        variant("hybrid", ablated, 0.99),
        variant("hybrid", PRODUCTION, 0.8),
    ]
    gate = gate_variant(variants)
    assert gate["mode"] == "hybrid"
    assert gate["options"] == PRODUCTION
    assert gate["recall_at_5"] == 0.8


def test_switch_gate_refuses_to_record_without_the_production_hybrid_variant():
    with pytest.raises(SystemExit):
        gate_variant(
            [
                variant("keyword", PRODUCTION, 0.95),
                variant("hybrid", {"query_rewrite": False, "rerank": True}, 0.9),
            ]
        )


def test_metrics_are_computed_before_adjacent_chunk_merge():
    assert PRODUCTION == {"query_rewrite": True, "rerank": True, "merge_adjacent": False}


def test_variants_can_be_narrowed_to_the_paths_that_need_no_model():
    assert select_variants(None, False) == [PRODUCTION]
    assert select_variants(None, True) == VARIANTS
    assert select_variants(["literal"], False) == [
        {"query_rewrite": False, "rerank": False, "merge_adjacent": False}
    ]
    with pytest.raises(SystemExit):
        select_variants(["teleport"], False)


def active_view(rebuilds=(), collection="tech_knowledge_base"):
    return {
        "configuration_id": "online",
        "values": {"qdrant_collection": collection},
        "rebuilds": [
            {"target_configuration_id": tid, "target_collection": tcol, "status": "evaluating"}
            for tid, tcol in rebuilds
        ],
    }


def measured_view(configuration_id, collection):
    return {"configuration_id": configuration_id, "values": {"qdrant_collection": collection}}


def test_direct_auto_uses_the_open_rebuild_target_instead_of_a_hand_copied_id():
    rebuilds = [
        {"status": "switched", "target_configuration_id": "old"},
        {"status": "evaluating", "target_configuration_id": "shadow-1"},
    ]
    assert resolve_direct("auto", rebuilds) == "shadow-1"
    assert resolve_direct("explicit", rebuilds) == "explicit"
    assert resolve_direct(None, rebuilds) is None


def test_direct_auto_refuses_when_no_rebuild_is_open():
    with pytest.raises(SystemExit):
        resolve_direct("auto", [{"status": "failed", "target_configuration_id": "x"}])


def test_a_measurement_claimed_against_a_shadow_config_must_match_that_rebuild_target():
    active = active_view(rebuilds=[("shadow-1", "knowforge_rebuild_a")])
    claim = measured_view("shadow-1", "knowforge_rebuild_a")
    assert measured_identity(active, claim) is claim
    with pytest.raises(SystemExit):
        measured_identity(active, measured_view("shadow-1", "tech_knowledge_base"))


def test_the_active_index_measurement_is_always_attributable():
    active = active_view(rebuilds=[("shadow-1", "knowforge_rebuild_a")])
    assert measured_identity(active, active)["configuration_id"] == "online"
