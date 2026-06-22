"""DeepResearchArtifact — the versioned, machine-readable Stage 2→3 contract.

Consumed (later) by the downstream PoC-builder. Kept flat (<=3-4 levels) for cross-model
reliability. The M1 extraction pass fills this via a schema-constrained pass and validates that
every evidence_id resolves to a real Source.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Source(BaseModel):
    id: str
    url: str
    title: str | None = None
    origin: str = "web"  # "web" | "vault" | "code"
    fetched_at: str | None = None  # ISO timestamp of the fetch that produced this source (None = unknown)


class Finding(BaseModel):
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)  # -> Source.id, must resolve
    confidence: float = 0.5  # 0..1


class TechStackItem(BaseModel):
    layer: str  # "orchestration" | "inference" | "vector_store" | ...
    choice: str
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


class Architecture(BaseModel):
    name: str
    summary: str
    components: list[str] = Field(default_factory=list)
    diagram_hint: str | None = None


class ReferenceRepo(BaseModel):
    name: str
    url: str
    license: str | None = None
    why_relevant: str
    # Deterministic enrichment (core._finalize, from the evidence side-store + code/ dir) — NOT LLM-filled.
    stars: int | None = None
    last_commit: str | None = None  # ISO date (YYYY-MM-DD) of last push
    archived: bool | None = None
    code_gathered: bool = False  # did this run gather real files under code/<owner-repo>/
    reproducibility: str | None = None  # "HIGH"|"MED"|"LOW"; None = unknown (no structured evidence)


class ImplementationStep(BaseModel):
    order: int
    action: str
    tools: list[str] = Field(default_factory=list)
    est_effort: str | None = None  # "S" | "M" | "L" or hours


class DeepResearchArtifact(BaseModel):
    # ---- versioning / lineage ----
    id: str
    version: int = 1
    parent_id: str | None = None
    generated_at: str
    model_versions: dict = Field(default_factory=dict)

    # ---- request ----
    topic: str
    brief: str = ""
    seed_pages: list[str] = Field(default_factory=list)  # Stage-1 wiki page ids used as seed

    # ---- content ----
    findings: list[Finding] = Field(default_factory=list)
    recommended_architectures: list[Architecture] = Field(default_factory=list)
    tech_stack: list[TechStackItem] = Field(default_factory=list)
    reference_repos: list[ReferenceRepo] = Field(default_factory=list)
    implementation_steps: list[ImplementationStep] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    report_markdown: str = ""
