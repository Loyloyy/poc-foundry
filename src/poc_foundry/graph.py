"""LangGraph wiring for the deterministic harness (design §5.1). A linear state machine over
``BuildState`` with two short-circuits (NOT_BUILDABLE / scaffold-failed → emit), checkpointed at
every node boundary by a SQLite saver (enables resume — design §5.9).

LangGraph is heavy and ≥3.11-only, so it is imported lazily inside ``build_graph`` (this module stays
``py_compile``-able on the 3.10 dev box; nothing here imports langgraph at module load).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from poc_foundry.phases import (
    Ctx,
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

_NODES = [
    ("ingest", p0_ingest),
    ("spec", p1_spec),
    ("plan", p2_plan),
    ("scaffold", p3_scaffold),
    ("iterate", p4_iterate),
    ("critic", p_critic),
    ("docs", p5_docs),
    ("cleanroom", p6_cleanroom),
    ("emit", p7_emit),
]


def _after_ingest(state) -> str:
    return "emit" if state.status == "failed" else "spec"


def _after_spec(state) -> str:
    return "emit" if (state.spec is not None and not state.spec.buildable) else "plan"


def _after_scaffold(state) -> str:
    return "emit" if state.status == "failed" else "iterate"


def _after_critic(state) -> str:
    """Verdict ladder + loop routing (design §5.3/§5.4): ``fix`` re-runs the SAME iteration; ``next``
    advances to the next iteration; ``respec``/``replan`` reset back to P1/P2; ``proceed`` continues
    to docs (plan exhausted). Counters in ``BuildState`` (capped by the critic) + a recursion_limit
    (core.py) guarantee the cycles terminate."""
    return {"fix": "iterate", "next": "iterate", "respec": "spec", "replan": "plan",
            "proceed": "docs"}.get(state.verdict, "docs")


def _after_critic_refine(state) -> str:
    """Refine loop routing (M4 S1): only ``fix``/``next`` re-enter iterate; everything else
    (``proceed``, and — should they ever fire — ``respec``/``replan``) falls through to docs. Refine
    forces the respec/replan budgets to their caps (it re-attacks the SAME plan with a stronger coder,
    never re-specs/re-plans), so the critic only ever returns fix/next/proceed here."""
    return {"fix": "iterate", "next": "iterate"}.get(state.verdict, "docs")


def _wrap_node(ctx: Ctx, name, fn):
    from poc_foundry import control

    def run(state):
        control.raise_if_stopped(ctx.build_dir, name)   # cooperative stop at the node boundary (S4)
        if ctx.events is not None:                       # M3: stream a slice-board snapshot to the UI
            from poc_foundry import events as _ev
            _ev.emit(ctx.events, _ev.make_event("node", ctx.build_id, node=name,
                                                 snapshot=_ev.snapshot(state)))
        return fn(state, ctx)
    return run


def _checkpointer(cfg):
    from langgraph.checkpoint.sqlite import SqliteSaver

    db = Path(cfg.builds_dir) / "checkpoints.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    return SqliteSaver(conn)


def build_graph(ctx: Ctx, cfg):
    """Compile the build graph bound to ``ctx`` (the non-serialized wiring). The SQLite checkpointer
    persists each node boundary keyed by ``thread_id == build_id``."""
    from langgraph.graph import END, START, StateGraph

    from poc_foundry.state import BuildState

    g = StateGraph(BuildState)
    for name, fn in _NODES:
        g.add_node(name, _wrap_node(ctx, name, fn))

    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", _after_ingest, {"spec": "spec", "emit": "emit"})
    g.add_conditional_edges("spec", _after_spec, {"plan": "plan", "emit": "emit"})
    g.add_edge("plan", "scaffold")
    g.add_conditional_edges("scaffold", _after_scaffold, {"iterate": "iterate", "emit": "emit"})
    g.add_edge("iterate", "critic")
    g.add_conditional_edges("critic", _after_critic,
                            {"iterate": "iterate", "spec": "spec", "plan": "plan", "docs": "docs"})
    g.add_edge("docs", "cleanroom")
    g.add_edge("cleanroom", "emit")
    g.add_edge("emit", END)

    return g.compile(checkpointer=_checkpointer(cfg))


def build_refine_graph(ctx: Ctx, cfg):
    """Compile the REFINE graph (M4 S1): the descoped-backlog re-attack of a FINISHED build on a
    stronger coder. It re-enters the verified core at P4 — ``iterate → critic`` (loop) → ``docs →
    cleanroom → emit`` — over the persisted workspace + already-authored (red-first) staged tests; P0–P3
    (ingest/spec/plan/scaffold) are NOT re-run. Seeded with a hand-built ``BuildState`` (not a
    checkpoint replay), so it uses its own ``thread_id``."""
    from langgraph.graph import END, START, StateGraph

    from poc_foundry.state import BuildState

    nodes = {"iterate": p4_iterate, "critic": p_critic, "docs": p5_docs,
             "cleanroom": p6_cleanroom, "emit": p7_emit}
    g = StateGraph(BuildState)
    for name, fn in nodes.items():
        g.add_node(name, _wrap_node(ctx, name, fn))

    g.add_edge(START, "iterate")
    g.add_edge("iterate", "critic")
    g.add_conditional_edges("critic", _after_critic_refine, {"iterate": "iterate", "docs": "docs"})
    g.add_edge("docs", "cleanroom")
    g.add_edge("cleanroom", "emit")
    g.add_edge("emit", END)

    return g.compile(checkpointer=_checkpointer(cfg))
