"""P0 ingest — load a Stage-2 run folder + validate the contract (schema + semantic invariants).

Pure pydantic/stdlib so it stays locally testable on the 3.10 box. The freshness pass and capability
manifest (which need egress) land with M1; M0(c) covers loading + validation.
"""
from .load import RunFolder, load_run
from .validate import Issue, clamp_confidence, validate_semantics

__all__ = ["RunFolder", "load_run", "Issue", "validate_semantics", "clamp_confidence"]
