"""M2b S2 fakes — the budget meter + cap enforcement + budget/caps_hit population (no Docker/LLM).

Tests the process-global ``_Meter`` (count caps, per-iteration reset, wall-clock backstop, the
contention median, the disabled no-op), that ``BudgetExceeded`` is a ``BaseException`` (so it ESCAPES
the phases' broad ``except Exception`` guards to halt the run), that ``config`` loads the caps, and
that ``p7_emit`` populates ``budget`` + ``caps_hit`` on the emitted artifact.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import poc_foundry.models as M
from poc_foundry.config import load_config
from poc_foundry.models import BudgetExceeded, _Meter


def _caps(max_iter=0, max_run=0, iter_wall_s=0, run_wall_s=0):
    return SimpleNamespace(max_llm_calls_per_iter=max_iter, max_llm_calls_per_run=max_run,
                           max_iter_wall_clock_s=iter_wall_s, max_run_wall_clock_s=run_wall_s)


def test_budget_exceeded_escapes_except_exception():
    assert issubclass(BudgetExceeded, BaseException)
    assert not issubclass(BudgetExceeded, Exception)   # the load-bearing property
    swallowed = False
    try:
        try:
            raise BudgetExceeded("x")
        except Exception:                # the broad guard in coder/critic/scribe must NOT catch it
            swallowed = True
    except BudgetExceeded as be:
        assert be.cap == "x"
    assert swallowed is False


def test_meter_run_call_cap_fires():
    m = _Meter()
    m.begin_run(_caps(max_run=2))
    m.count(); m.count()
    try:
        m.count()
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded as be:
        assert be.cap == "max_llm_calls_per_run"


def test_meter_per_iter_cap_resets_each_iteration():
    m = _Meter()
    m.begin_run(_caps(max_iter=2, max_run=100))
    m.count(); m.count()
    try:
        m.count()
        raise AssertionError("expected per-iter cap")
    except BudgetExceeded as be:
        assert be.cap == "max_llm_calls_per_iter"
    m.begin_iteration()         # fresh per-iteration budget
    m.count()                   # OK again
    assert m.run_calls == 3     # the run counter accumulates across iterations


def test_meter_wall_clock_backstop():
    m = _Meter()
    m.begin_run(_caps(run_wall_s=1, max_run=100))
    m.wall_start -= 10          # pretend 10s elapsed
    try:
        m.count()
        raise AssertionError("expected wall-clock cap")
    except BudgetExceeded as be:
        assert be.cap == "max_run_wall_clock_s"


def test_meter_disabled_is_a_noop():
    m = _Meter()                # never begun
    for _ in range(500):
        m.count()
    assert m.snapshot() == {"llm_calls": 0, "wall_s": 0.0, "contention_indicator": None}


def test_meter_contention_indicator_is_latency_median():
    m = _Meter()
    m.begin_run(_caps())
    for dt in (1.0, 5.0, 3.0):
        m.record_latency(dt)
    snap = m.snapshot()
    assert snap["contention_indicator"] == 3.0
    assert snap["llm_calls"] == 0          # latency samples are independent of the call count


def test_config_loads_call_and_wall_caps():
    cfg = load_config()
    assert cfg.max_llm_calls_per_iter == 60
    assert cfg.max_llm_calls_per_run == 400
    assert cfg.max_iter_wall_clock_s == 1800
    assert cfg.max_run_wall_clock_s == 14400


def test_p7_emits_budget_and_caps_hit(tmp_path):
    from poc_foundry.artifact import load as load_artifact
    from poc_foundry.phases import Ctx, load_template, p7_emit
    from poc_foundry.state import BuildState

    cfg = load_config(tmp_path / "builds")
    M.METER.begin_run(cfg)
    M.METER.count(); M.METER.count()                  # 2 calls this run
    M.METER.record_latency(2.0); M.METER.record_latency(4.0)   # median = 3.0

    bid = "poc-budget-0001"
    ws, stg = tmp_path / "ws", tmp_path / "stg"
    ws.mkdir(); stg.mkdir()

    class _StubBroker:
        def proxy_log(self):
            return ""

    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=tmp_path, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=stg,
              broker=_StubBroker(), coder=None)
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws),
                       caps_hit=["max_llm_calls_per_run"])

    p7_emit(state, ctx)
    pa = load_artifact(cfg.builds_dir / bid)
    assert pa.status == "incomplete"                  # no clean-room / no core met → never 'done'
    assert pa.caps_hit == ["max_llm_calls_per_run"]
    assert pa.budget.llm_calls == 2
    assert pa.budget.contention_indicator == 3.0
    assert pa.budget.wall_s >= 0.0
    M.METER.reset()
