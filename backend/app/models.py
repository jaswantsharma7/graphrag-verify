"""Shared API models and strict provider response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Public contracts.
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Chunk(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    document_name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    position: int = Field(ge=0)
    embedding: list[float] = Field(default_factory=list)


class Evidence(StrictModel):
    id: str
    document_id: str
    document_name: str
    page: int | None = None
    text: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    retrieval: Literal["vector", "graph", "lexical"]
    entity_ids: list[str] = Field(default_factory=list)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    status: Literal["supported", "unsupported", "contradicted"]
    citations: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=2000)


class GraphNode(StrictModel):
    id: str
    label: str
    kind: str


class GraphEdge(StrictModel):
    id: str
    source: str
    target: str
    label: str
    evidence_ids: list[str] = Field(default_factory=list)


class GraphView(StrictModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class QueryRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] = Field(default_factory=list, max_length=1000)
    top_k: int = Field(default=6, ge=2, le=20)
    hops: int = Field(default=2, ge=1, le=3)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 160 for value in values):
            raise ValueError("Document ids must be non-empty and at most 160 characters")
        return list(dict.fromkeys(values))


class QueryResult(StrictModel):
    id: str
    question: str
    answer: str
    draft: str
    status: Literal["grounded", "partial", "ungrounded", "no_evidence"]
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    claims: list[Claim]
    evidence: list[Evidence]
    graph: GraphView
    latency_ms: float = Field(ge=0)
    mode: Literal["demo", "live"]


# Internal extraction schemas use an explicit relationship allowlist.
EntityKind = Literal["organization", "person", "place", "product", "concept", "event"]
RelationKind = Literal[
    "OWNS", "PART_OF", "PARTNERS_WITH", "SUPPLIES", "LOCATED_IN", "WORKS_FOR",
    "DEVELOPED_BY", "USES", "ACQUIRED", "LEADS", "CAUSES", "PRECEDES",
]
RELATION_TYPES = (
    "OWNS", "PART_OF", "PARTNERS_WITH", "SUPPLIES", "LOCATED_IN", "WORKS_FOR",
    "DEVELOPED_BY", "USES", "ACQUIRED", "LEADS", "CAUSES", "PRECEDES",
)


class EntityMention(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: EntityKind = "concept"


class ExtractedRelation(StrictModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    kind: RelationKind
    quote: str = Field(min_length=1, max_length=4000)


class ChunkExtraction(StrictModel):
    entities: list[EntityMention] = Field(default_factory=list, max_length=60)
    relationships: list[ExtractedRelation] = Field(default_factory=list, max_length=60)


class DraftClaim(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    citations: list[str] = Field(default_factory=list, max_length=20)


class Generation(StrictModel):
    answer: str = Field(default="", max_length=20000)
    claims: list[DraftClaim] = Field(default_factory=list, max_length=20)


class Evaluation(StrictModel):
    claims: list[Claim] = Field(default_factory=list, max_length=20)
