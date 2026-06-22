"""M0(c) contract tests — the vendored Stage-2 schema loads + the semantic invariants hold.

Pure pydantic/stdlib → runs on the 3.10 dev box (`python -m pytest tests/test_contract.py`, or see
the no-pytest fallback at the bottom). Validates a committed sanitized sample artifact (shape via
pydantic, meaning via validate_semantics) + the citation validator + the defensive clamp.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from poc_foundry.stage2_artifact import DeepResearchArtifact, validate_citations
from poc_foundry.ingest import clamp_confidence, load_run, validate_semantics

FIXTURE = Path(__file__).parent / "fixtures" / "sample_artifact"


def test_sample_loads_and_is_shape_valid():
    rf = load_run(FIXTURE)
    assert isinstance(rf.artifact, DeepResearchArtifact)
    assert rf.version == 1
    assert rf.artifact.id == "dra-20260101-000000-abc123-m"
    assert rf.report_md and rf.report_md.startswith("#")


def test_sample_has_no_semantic_errors():
    rf = load_run(FIXTURE)
    issues = validate_semantics(rf.artifact)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"unexpected semantic errors: {[str(e) for e in errors]}"


def test_citation_invariant_resolves():
    rf = load_run(FIXTURE)
    src_ids = {s.id for s in rf.artifact.sources}
    for f in rf.artifact.findings:
        for eid in f.evidence_ids:
            assert eid in src_ids


def test_reproducibility_enum():
    rf = load_run(FIXTURE)
    for r in rf.artifact.reference_repos:
        assert r.reproducibility in {"HIGH", "MED", "LOW", None}


def test_validate_citations_drops_unresolved():
    rf = load_run(FIXTURE)
    rf.artifact.findings[0].evidence_ids.append("src-does-not-exist")
    validate_citations(rf.artifact)
    assert "src-does-not-exist" not in rf.artifact.findings[0].evidence_ids


def test_validate_semantics_flags_unresolved_citation():
    rf = load_run(FIXTURE)
    rf.artifact.findings[0].evidence_ids.append("src-bogus")
    issues = validate_semantics(rf.artifact)
    assert any(i.code == "citation-unresolved" for i in issues)


def test_confidence_clamp_defensive():
    rf = load_run(FIXTURE)
    rf.artifact.findings[0].confidence = 1.7
    rf.artifact.findings[1].confidence = -0.2
    n = clamp_confidence(rf.artifact)
    assert n == 2
    assert all(0.0 <= f.confidence <= 1.0 for f in rf.artifact.findings)


def test_artifact_import_is_lightweight():
    """Reading artifacts must NOT drag in the heavy agent stack (the extras-split guarantee)."""
    import poc_foundry.stage2_artifact  # noqa: F401
    for heavy in ("langchain", "langgraph", "deepagents"):
        assert heavy not in sys.modules, f"{heavy} imported by poc_foundry.stage2_artifact (should be schema-only)"


if __name__ == "__main__":  # no-pytest fallback for the bare dev box
    raise SystemExit(pytest.main([__file__, "-q"]))
