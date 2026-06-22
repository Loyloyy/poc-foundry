"""Deterministic phase pipeline (design §5.3). The phases are plain ``(state, ctx) -> dict``
functions; ``graph.py`` wires them with LangGraph. Import-light (heavy deps lazy inside phases)."""
from poc_foundry.phases.context import Ctx, Template, load_template
from poc_foundry.phases.pipeline import (
    p0_ingest,
    p1_spec,
    p2_plan,
    p3_scaffold,
    p4_iterate,
    p5_docs,
    p6_cleanroom,
    p7_emit,
    p_critic,
)

__all__ = [
    "Ctx",
    "Template",
    "load_template",
    "p0_ingest",
    "p1_spec",
    "p2_plan",
    "p3_scaffold",
    "p4_iterate",
    "p_critic",
    "p5_docs",
    "p6_cleanroom",
    "p7_emit",
]
