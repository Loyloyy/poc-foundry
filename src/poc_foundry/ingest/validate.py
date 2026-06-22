"""Semantic-invariant validation for an ingested artifact (contract §6 + design §5.3 P0).

Pydantic already enforces SHAPE on load; this asserts MEANING:
  • every Finding.evidence_id resolves to a Source.id          (hard — error)
  • ReferenceRepo.reproducibility ∈ {HIGH, MED, LOW, None}      (hard — error)
  • generated_at is ISO-8601                                    (error)
  • Source.fetched_at / ReferenceRepo.last_commit are ISO       (warn)
  • Source.origin ∈ {web, vault, code}                          (warn)
  • Finding.confidence ∈ [0, 1]                                 (warn — NOT hard-clamped upstream;
                                                                 clamp defensively with clamp_confidence)
Returns a list of Issue (never raises) so the probe/P0 can report all problems at once.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from poc_foundry.artifact import DeepResearchArtifact

_REPRO_OK = {"HIGH", "MED", "LOW"}
_ORIGIN_OK = {"web", "vault", "code"}


@dataclass
class Issue:
    severity: str  # "error" | "warn"
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[{self.severity.upper()}] {self.code}: {self.message}"


def _is_iso_datetime(s: str) -> bool:
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


def _is_iso_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (ValueError, AttributeError):
        return False


def validate_semantics(artifact: DeepResearchArtifact) -> list[Issue]:
    issues: list[Issue] = []
    valid_src_ids = {s.id for s in artifact.sources}

    # Citation invariant (hard).
    for i, f in enumerate(artifact.findings):
        for eid in f.evidence_ids:
            if eid not in valid_src_ids:
                issues.append(Issue("error", "citation-unresolved",
                                    f"findings[{i}].evidence_ids: '{eid}' does not resolve to a Source"))
        if not (0.0 <= f.confidence <= 1.0):
            issues.append(Issue("warn", "confidence-range",
                                f"findings[{i}].confidence={f.confidence} outside [0,1] (clamp defensively)"))

    # ReferenceRepo enrichment enums + dates.
    for i, r in enumerate(artifact.reference_repos):
        if r.reproducibility is not None and r.reproducibility not in _REPRO_OK:
            issues.append(Issue("error", "reproducibility-enum",
                                f"reference_repos[{i}].reproducibility='{r.reproducibility}' not in {sorted(_REPRO_OK)}|None"))
        if r.last_commit is not None and not _is_iso_date(r.last_commit):
            issues.append(Issue("warn", "last_commit-iso",
                                f"reference_repos[{i}].last_commit='{r.last_commit}' is not an ISO YYYY-MM-DD date"))

    # Source origin enum + fetched_at ISO.
    for i, s in enumerate(artifact.sources):
        if s.origin not in _ORIGIN_OK:
            issues.append(Issue("warn", "origin-enum",
                                f"sources[{i}].origin='{s.origin}' not in {sorted(_ORIGIN_OK)}"))
        if s.fetched_at is not None and not _is_iso_datetime(s.fetched_at):
            issues.append(Issue("warn", "fetched_at-iso",
                                f"sources[{i}].fetched_at='{s.fetched_at}' is not ISO-8601"))

    # Top-level timestamp (hard).
    if not _is_iso_datetime(artifact.generated_at):
        issues.append(Issue("error", "generated_at-iso",
                            f"generated_at='{artifact.generated_at}' is not ISO-8601"))

    return issues


def clamp_confidence(artifact: DeepResearchArtifact) -> int:
    """Defensively clamp every Finding.confidence into [0,1] IN PLACE. Returns the count clamped.

    The contract notes confidence is constrained by the extraction prompt but NOT hard-clamped, so a
    rare out-of-range model emission can slip through (contract §6). Stage 3 clamps on ingest.
    """
    clamped = 0
    for f in artifact.findings:
        c = min(1.0, max(0.0, f.confidence))
        if c != f.confidence:
            f.confidence = c
            clamped += 1
    return clamped
