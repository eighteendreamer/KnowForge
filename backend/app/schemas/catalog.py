from typing import Literal

from pydantic import Field

from app.schemas.identity import StrictModel


class TagInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#18A058", pattern=r"^#[0-9a-fA-F]{6}$")


class TagPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    review_status: Literal["pending", "approved", "rejected"] | None = None


class TagMerge(StrictModel):
    source_ids: list[int] = Field(min_length=1, max_length=100)
    target_id: int = Field(gt=0)


class TagBatch(StrictModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    review_status: Literal["approved", "rejected"] | None = None


class CategoryInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = Field(default=0, ge=0)


class CategoryPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int | None = Field(default=None, ge=0)


class CategoryMove(StrictModel):
    target_id: int = Field(gt=0)
    position: Literal["before", "after", "inside"]


class MoveDocuments(StrictModel):
    doc_ids: list[str] = Field(min_length=1, max_length=100)
    category_id: int | None = Field(default=None, gt=0)
