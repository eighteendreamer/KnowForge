from typing import Literal

from pydantic import Field, model_validator

from app.schemas.identity import StrictModel


class SearchFilters(StrictModel):
    tags: list[str] = Field(default_factory=list, max_length=20)
    category: str | None = Field(default=None, max_length=500)
    difficulty: Literal["初级", "中级", "高级"] | None = None
    exclude_tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_tags(self):
        for tag in [*self.tags, *self.exclude_tags]:
            if not tag.strip() or len(tag) > 100:
                raise ValueError("Invalid tag")
        return self


class SearchOptions(StrictModel):
    highlight: bool = True
    include_metadata: bool = True
    rerank: bool = True
    query_rewrite: bool = True
    language: Literal["zh", "en"] = "zh"
    merge_adjacent: bool = True


class SearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    search_type: Literal["semantic", "keyword", "hybrid", "fuzzy", "auto"] = "hybrid"
    top_k: int = Field(default=8, ge=1, le=50)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    options: SearchOptions = Field(default_factory=SearchOptions)


class LookupInput(StrictModel):
    doc_id: str = Field(min_length=1, max_length=64)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    include_chunks: bool = True

    @model_validator(mode="after")
    def valid_page_range(self):
        if self.page_end is not None and (self.page_start is None or self.page_end < self.page_start):
            raise ValueError("Invalid page range")
        return self
