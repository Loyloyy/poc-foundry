#!/usr/bin/env python3
"""Local M0(c) contract proof WITHOUT pytest (the dev box has pydantic but no pytest, rule #3).

Exercises the same invariants as tests/test_contract.py against the committed sample fixture, using
only stdlib + pydantic + the vendored schema. In the container, prefer `pytest tests/`.

    python3 scripts/run_contract_checks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # import poc_foundry without installing (no host pip)

from poc_foundry.artifact import DeepResearchArtifact, validate_citations  # noqa: E402
from poc_foundry.ingest import clamp_confidence, load_run, validate_semantics  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sample_artifact"

passed = 0
failed = 0


def check(desc: str, cond: bool) -> None:
    global passed, failed
    if cond:
        print(f"  PASS: {desc}"); passed += 1
    else:
        print(f"  FAIL: {desc}"); failed += 1


rf = load_run(FIXTURE)
check("sample loads as DeepResearchArtifact", isinstance(rf.artifact, DeepResearchArtifact))
check("latest version detected (v1)", rf.version == 1)
check("companion report.md loaded", bool(rf.report_md))

issues = validate_semantics(rf.artifact)
errors = [i for i in issues if i.severity == "error"]
check("no semantic ERRORS on clean fixture", errors == [])
if errors:
    for e in errors:
        print(f"      {e}")

src_ids = {s.id for s in rf.artifact.sources}
check("citation invariant holds", all(eid in src_ids for f in rf.artifact.findings for eid in f.evidence_ids))
check("reproducibility enum ∈ {HIGH,MED,LOW,None}",
      all(r.reproducibility in {"HIGH", "MED", "LOW", None} for r in rf.artifact.reference_repos))

# Negative: an unresolved evidence id is flagged + dropped.
rf.artifact.findings[0].evidence_ids.append("src-bogus")
check("validate_semantics flags unresolved citation",
      any(i.code == "citation-unresolved" for i in validate_semantics(rf.artifact)))
validate_citations(rf.artifact)
check("validate_citations drops unresolved id", "src-bogus" not in rf.artifact.findings[0].evidence_ids)

# Defensive clamp.
rf.artifact.findings[0].confidence = 1.7
rf.artifact.findings[1].confidence = -0.2
n = clamp_confidence(rf.artifact)
check("clamp_confidence clamps out-of-range (2)", n == 2)
check("all confidences in [0,1] after clamp",
      all(0.0 <= f.confidence <= 1.0 for f in rf.artifact.findings))

# Lightweight import guarantee.
check("poc_foundry.artifact pulls no heavy agent stack",
      not any(m in sys.modules for m in ("langchain", "langgraph", "deepagents")))

print(f"\nSUMMARY: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
