from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.identity import StrictModel


class SettingsPatch(StrictModel):
    expected_configuration_id: UUID
    values: dict[str, object]


class RebuildInput(StrictModel):
    embedding_model: str = Field(min_length=1, max_length=200)
    embedding_dimension: int = Field(ge=1, le=65536)


class EvaluationMetrics(StrictModel):
    recall_at_5: float = Field(ge=0, le=1)
    recall_at_10: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    precision_at_5: float = Field(ge=0, le=1)


# The switch gate reads recall at depth 10. Under pooled judging an unjudged chunk counts as irrelevant, so each
# query's Recall@5 is capped at min(5, relevant) / relevant; the frozen set measured a 0.7792 ceiling for a perfect
# ranker, which makes Recall@5 >= 0.85 unreachable by construction rather than merely unsolved.
EVALUATION_TARGETS = {"recall_at_10": 0.85, "mrr": 0.75, "ndcg_at_10": 0.80, "precision_at_5": 0.80}


class EvaluationInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    dataset_kind: Literal["tuning", "frozen"] = "frozen"
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_id: UUID
    metrics: EvaluationMetrics
    notes: str | None = Field(default=None, max_length=2000)
