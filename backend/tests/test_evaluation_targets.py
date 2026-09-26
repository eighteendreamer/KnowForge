import pytest
from acceptance_evaluation import score_query

from app.schemas.system import EVALUATION_TARGETS, EvaluationMetrics


def test_recall_at_10_is_the_gate_because_recall_at_5_is_capped_by_the_label_depth():
    """Six relevant chunks cannot fit in five slots, so a perfect ranker still fails a Recall@5 target."""
    labels = {f"c{index}": 2 for index in range(6)}
    perfect = list(labels)
    scored = score_query(perfect, labels)
    assert scored["recall_at_5"] == pytest.approx(5 / 6)
    assert scored["recall_at_10"] == pytest.approx(1.0)
    assert scored["precision_at_5"] == 1.0


def test_recall_is_none_only_for_queries_without_any_relevant_chunk():
    scored = score_query(["c1"], {})
    assert scored["recall_at_5"] is None
    assert scored["recall_at_10"] is None


def test_unjudged_positions_count_as_misses_at_both_depths():
    labels = {"c1": 1, "c2": 2}
    scored = score_query(["c1", "noise", "noise", "c2", "noise", "noise"], labels)
    assert scored["recall_at_5"] == pytest.approx(1.0)
    assert scored["false_positive_top5"] == 3


def test_every_gate_metric_is_a_field_the_record_endpoint_accepts():
    """A target key missing from the schema would silently drop out of `passed`, so this is pinned explicitly."""
    fields = set(EvaluationMetrics.model_fields)
    assert set(EVALUATION_TARGETS) <= fields
    assert "recall_at_5" not in EVALUATION_TARGETS
