"""VENDORED MIRROR of Stage 2's ``ai_engineer_research.artifact`` (schema + read/validate ONLY).

This is a temporary, faithful copy of the Stage-2→3 contract schema so Stage 3 can read artifacts
without resolving the SHA-pinned git dependency yet (DECISIONS #2 — stopgap for M0/M1; the design
end-state is the git dep). The ``extract`` module (LLM extraction, heavy deps) is deliberately NOT
vendored — Stage 3 only READS artifacts.

DO NOT EDIT schema.py / store.py / validate.py here — they are kept BYTE-IDENTICAL to the Stage-2
source so drift is a clean diff. Provenance + sync instructions: ``_VENDORED.md`` in this directory;
check drift with ``scripts/check_vendored_schema.sh``. Migration trigger (swap to the git dep) is in
DECISIONS #2.
"""
from .schema import (
    Architecture,
    DeepResearchArtifact,
    Finding,
    ImplementationStep,
    ReferenceRepo,
    Source,
    TechStackItem,
)
from .store import latest_version, list_artifacts, load, new_artifact_id, save
from .validate import validate_citations

__all__ = [
    "Source",
    "Finding",
    "TechStackItem",
    "Architecture",
    "ReferenceRepo",
    "ImplementationStep",
    "DeepResearchArtifact",
    "new_artifact_id",
    "validate_citations",
    "save",
    "load",
    "latest_version",
    "list_artifacts",
]
