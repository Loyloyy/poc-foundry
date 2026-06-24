"""Targeted research-on-gaps (design §5.3 P4.a, §5.8) — the escalation ladder's last rung.

A NARROW, in-build lookup (NOT Stage-2's broad deep research): when the coder is stuck on the same
error N times, or an iteration carries an open question, a small agent runs a few web searches /
fetches about THAT specific thing and writes a short CITED ``iterations/<i>/research.md`` for the
coder. "Fetched = citable; parametric memory ≠ evidence" (§5.3 P4.a).

Locus: the ORCHESTRATOR (reuses the existing agent stack on depot-net), querying the SHARED
service-depot SearXNG (NOT a per-build broker sibling) — so the per-build egress allowlist is
untouched. Tolerated-absent throughout: a missing ``SEARX_URL`` / httpx / deepagents degrades to
fetch-only or a no-op with a recorded caveat, NEVER crashing a build.
"""
from poc_foundry.research.agent import ResearchResult, run_research
from poc_foundry.research.tools import scan_injection

__all__ = ["ResearchResult", "run_research", "scan_injection"]
