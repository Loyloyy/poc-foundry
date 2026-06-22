"""Citation validation: every Finding.evidence_id must resolve to a real Source."""
from __future__ import annotations

import logging

from .schema import DeepResearchArtifact

logger = logging.getLogger(__name__)


def validate_citations(artifact: DeepResearchArtifact) -> DeepResearchArtifact:
    valid_ids = {s.id for s in artifact.sources}
    dropped = 0
    for f in artifact.findings:
        resolved = [eid for eid in f.evidence_ids if eid in valid_ids]
        dropped += len(f.evidence_ids) - len(resolved)
        f.evidence_ids = resolved
    if dropped:
        logger.info("citation validation dropped %d hallucinated evidence id(s)", dropped)
    return artifact
