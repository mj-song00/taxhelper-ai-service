from typing import Any

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, description="User's natural language question.")
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of top chunks to retrieve.",
    )


class SearchConditions(BaseModel):
    intent: str = Field(description="Detected legal search intent.")
    tax_domain: str = Field(description="Inferred tax domain.")
    action: str = Field(description="Detected user action in tax workflow.")
    keywords: list[str] = Field(default_factory=list)
    law_names: list[str] = Field(default_factory=list)
    article_numbers: list[str] = Field(default_factory=list)
    rewritten_query: str = Field(description="Query rewritten for retrieval.")


class LawChunk(BaseModel):
    chunk_id: str
    law_id: str | None = None
    law_name: str | None = None
    title: str | None = None
    article: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Pagination(BaseModel):
    page: int = Field(description="Candidate page number (1-based).")
    size: int = Field(description="Candidate page size used for Spring retrieval.")
    total_elements: int | None = Field(default=None)
    total_pages: int | None = Field(default=None)
    has_next: bool | None = Field(default=None)
    has_previous: bool | None = Field(default=None)


class RetrieveResponse(BaseModel):
    question: str
    search_prompt: str = Field(
        description="Prompt text to use for law/case retrieval step.",
    )
    search_conditions: SearchConditions
    candidate_pagination: Pagination
    chunks: list[LawChunk]
    prompt_context: str = Field(
        description="Concatenated context to pass to answer generation.",
    )


class PrecedentSearchConditions(BaseModel):
    intent: str = Field(description="Detected precedent search intent.")
    tax_domain: str = Field(description="Inferred tax domain.")
    keywords: list[str] = Field(default_factory=list)
    court_names: list[str] = Field(default_factory=list)
    case_numbers: list[str] = Field(default_factory=list)
    chunk_types: list[str] = Field(
        default_factory=list,
        description="Preferred precedent chunk types (ISSUE, SUMMARY, FULL_TEXT).",
    )
    rewritten_query: str = Field(description="Query rewritten for retrieval.")


class PrecedentChunk(BaseModel):
    chunk_id: str
    precedent_id: str | None = None
    chunk_type: str | None = None
    title: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrecedentRetrieveResponse(BaseModel):
    question: str
    search_prompt: str = Field(
        description="Prompt text to use for precedent retrieval step.",
    )
    search_conditions: PrecedentSearchConditions
    candidate_pagination: Pagination
    chunks: list[PrecedentChunk]
    prompt_context: str = Field(
        description="Concatenated context to pass to answer generation.",
    )

class ChatRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=20)


class ChatResponse(BaseModel):
    question: str
    answer: str
    chunks: list[Any]