from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ChatRequest(BaseModel):
    message: str = Field(min_length=3)
    thread_id: str | None = None


class Citation(BaseModel):
    title: str
    url: HttpUrl
    source_name: str


class SubQuestion(BaseModel):
    id: str
    question: str
    priority: int
    search_queries: list[str] = Field(min_length=2, max_length=4)


class Plan(BaseModel):
    sub_questions: list[SubQuestion] = Field(min_length=3, max_length=6)
    assumptions: list[str] = Field(default_factory=list)


class SourceFinding(BaseModel):
    title: str
    url: HttpUrl
    snippet: str
    source_name: str


class ResearchSynthesis(BaseModel):
    evidence_bullets: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ResearchNote(BaseModel):
    sub_question_id: str
    evidence_bullets: list[str]
    findings: list[SourceFinding]
    contradictions: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class QualityCheck(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    issues: list[str] = Field(default_factory=list)
    refinement_queries: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    executive_summary: str
    report: str
    key_takeaways: list[str]
    limitations: str


class DoneMetadata(BaseModel):
    sub_question_count: int
    sources_analyzed: int
    completion_timestamp: str
    quality_score: int | None = None
    refinement_used: bool = False
    timings_ms: dict[str, int] = Field(default_factory=dict)


class DonePayload(BaseModel):
    thread_id: str
    query: str
    executive_summary: str
    report: str
    key_takeaways: list[str]
    limitations: str
    citations: list[Citation]
    metadata: DoneMetadata


class WorkflowError(BaseModel):
    stage: str
    detail: str
    sub_question_id: str | None = None


class TraceEvent(BaseModel):
    node: str
    status: str
    timestamp: str
    duration_ms: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
