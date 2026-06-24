"""Role → chat-model factory (design §5.4). NO LiteLLM, NO model names in code (rules #2/#4).

Roles: architect / coder / tester / critic / scribe. Each reads a `.env` triple
(`<ROLE>_MODEL/_API_BASE/_API_KEY`); a blank triple falls back to `PF_DEFAULT_ROLE` (single on-prem
model runs everything — the Stage-2 role-triple-fallback pattern). Any role flips to a frontier
endpoint by editing its triple.

`build_chat_model` lazily imports `langchain_openai` so importing this module stays light on the
3.10 dev box; `chat_text` is a stdlib-only raw call for the coder's tight loop (no langchain in the
inner loop). Secrets live only in this client code, never in agent context (Finding-0).
"""
from __future__ import annotations

import json as _json
import os
import statistics
import time
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── budget meter (M2b S2) ─────────────────────────────────────────────────────
class BudgetExceeded(BaseException):
    """A run/iteration LLM-call or wall-clock cap was hit (design §5.8). Subclasses ``BaseException``
    (NOT ``Exception``) ON PURPOSE: the phases wrap model calls in broad ``except Exception`` guards
    (the coder loop, the critic-adequacy fallback, the scribe) — a budget breach must ESCAPE those to
    halt the whole RUN, where ``core`` catches it and salvages (rollback + honest ``incomplete``)."""

    def __init__(self, cap: str):
        self.cap = cap
        super().__init__(f"budget cap hit: {cap}")


class _Meter:
    """Process-global LLM-call meter — the natural choke point (every role call goes through this
    module). Counts calls per-run and per-iteration, samples each call's latency (→ the contention
    indicator = median observed latency), and ENFORCES the call-count + (secondary, generous)
    wall-clock caps by raising ``BudgetExceeded`` BEFORE an over-budget call is issued. Primary budgets
    are deterministic under load (call counts); wall-clock is the backstop (one vLLM serves every
    role + the PoC runtime). Disabled until ``begin_run`` so direct-phase fakes (no ``build_poc``) and
    import-time use are no-ops."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.enabled = False
        self.run_calls = 0
        self.iter_calls = 0
        self.latencies: list[float] = []
        self.wall_start: float | None = None
        self.iter_start: float | None = None
        self.caps: dict = {}

    def begin_run(self, cfg) -> None:
        self.reset()
        self.caps = {
            "max_iter": int(getattr(cfg, "max_llm_calls_per_iter", 0) or 0),
            "max_run": int(getattr(cfg, "max_llm_calls_per_run", 0) or 0),
            "iter_wall_s": int(getattr(cfg, "max_iter_wall_clock_s", 0) or 0),
            "run_wall_s": int(getattr(cfg, "max_run_wall_clock_s", 0) or 0),
        }
        now = time.time()
        self.wall_start = now
        self.iter_start = now
        self.enabled = True

    def begin_iteration(self) -> None:
        """Reset the per-iteration counters at the start of each P4 iteration (fresh per-iter budget)."""
        self.iter_calls = 0
        self.iter_start = time.time()

    def count(self) -> None:
        """Called BEFORE each LLM call; raises ``BudgetExceeded`` if issuing it would exceed a cap."""
        if not self.enabled:
            return
        now = time.time()
        c = self.caps
        if c.get("max_run") and self.run_calls >= c["max_run"]:
            raise BudgetExceeded("max_llm_calls_per_run")
        if c.get("max_iter") and self.iter_calls >= c["max_iter"]:
            raise BudgetExceeded("max_llm_calls_per_iter")
        if c.get("run_wall_s") and self.wall_start and (now - self.wall_start) >= c["run_wall_s"]:
            raise BudgetExceeded("max_run_wall_clock_s")
        if c.get("iter_wall_s") and self.iter_start and (now - self.iter_start) >= c["iter_wall_s"]:
            raise BudgetExceeded("max_iter_wall_clock_s")
        self.run_calls += 1
        self.iter_calls += 1

    def record_latency(self, dt: float) -> None:
        if self.enabled:
            self.latencies.append(dt)

    def snapshot(self) -> dict:
        """For ``budget`` at emit (safe even if ``begin_run`` was never called → all-zero)."""
        med = statistics.median(self.latencies) if self.latencies else None
        wall = (time.time() - self.wall_start) if self.wall_start else 0.0
        return {"llm_calls": self.run_calls, "wall_s": round(wall, 1),
                "contention_indicator": (round(med, 2) if med is not None else None)}


METER = _Meter()


def _load_dotenv() -> None:
    from poc_foundry.config import _load_dotenv as _ld
    _ld(_REPO_ROOT / ".env")


# sampling defaults keyed on the ORIGINAL role (task shapes temperature; endpoint may be shared)
_ROLE_TEMP = {"architect": 0.2, "coder": 0.1, "tester": 0.1, "critic": 0.0, "scribe": 0.3}


# ── per-call role rebind (M4 S1 refine) ───────────────────────────────────────
# A PROCESS-LOCAL alias map (NOT a global `.env` change): ``refine`` points the ``coder`` role at a
# stronger/frontier endpoint for the duration of one refine run, then clears it. Resolving through the
# alias means BOTH the coder loop (``chat_text("coder", …)``) AND the degraded-critic check
# (``same_family("critic","coder")``) see the rebound endpoint — so a frontier coder correctly reads as
# a DISTINCT family (the critic stays independent; design §5.4, DECISIONS #28: never weaken the critic).
_ROLE_ALIASES: dict[str, str] = {}


def set_role_alias(role: str, alias: str | None) -> None:
    """Rebind ``role`` to another configured role's ``.env`` triple for this process (``alias=None``
    clears it). Used by ``core.refine_build`` around a single run; never persisted."""
    key = role.strip().lower()
    if alias and alias.strip():
        _ROLE_ALIASES[key] = alias.strip()
    else:
        _ROLE_ALIASES.pop(key, None)


def resolve_role(role: str) -> tuple[str, str, str]:
    """(model, api_base, api_key) for a role; blank triple → PF_DEFAULT_ROLE fallback. A process-local
    refine alias (``set_role_alias``) is applied FIRST, so a rebound role resolves the alias's triple."""
    _load_dotenv()
    role = _ROLE_ALIASES.get(role.strip().lower(), role)
    r = role.upper()
    model = os.environ.get(f"{r}_MODEL", "").strip()
    if not model or model.startswith("<"):
        r = os.environ.get("PF_DEFAULT_ROLE", "architect").strip().upper()
        model = os.environ.get(f"{r}_MODEL", "").strip()
    base = os.environ.get(f"{r}_API_BASE", "").strip()
    key = os.environ.get(f"{r}_API_KEY", "").strip() or "not-needed"
    if not model or not base or model.startswith("<") or base.startswith("<"):
        raise RuntimeError(f"role '{role}' not configured (set {r}_MODEL/_API_BASE in .env)")
    return model, base, key


def same_family(role_a: str, role_b: str) -> bool:
    """True if two roles resolve to the SAME served model id — the degraded-critic test (design §5.4:
    critic independence is contingent on a distinct judge family; otherwise degraded mode + a lower K
    apply, recorded in ``security.degraded_critic``). CONSERVATIVE: if either role can't be resolved,
    assume same-family (degraded) so we never silently over-trust an unconfigured critic."""
    try:
        return resolve_role(role_a)[0] == resolve_role(role_b)[0]
    except Exception:  # noqa: BLE001
        return True


def build_chat_model(role: str, *, temperature: float | None = None, max_tokens: int = 4000,
                     timeout: int | None = None):
    """A LangChain `ChatOpenAI` bound to the role's endpoint (lazy import). For structured calls
    (architect/critic/scribe). Use `.with_structured_output(Model)` for typed extraction."""
    from langchain_openai import ChatOpenAI  # lazy — heavy

    METER.count()   # one constructed model == one intended invoke in this codebase (cap choke point)
    model, base, key = resolve_role(role)
    temp = _ROLE_TEMP.get(role.lower(), 0.2) if temperature is None else temperature
    to = timeout if timeout is not None else int(os.environ.get("PF_LLM_TIMEOUT_S", "300"))
    return ChatOpenAI(model=model, base_url=base, api_key=key, temperature=temp,
                      max_tokens=max_tokens, timeout=to)


def chat_text(role: str, prompt: str, system: str | None = None, *,
              max_tokens: int = 4000, temperature: float | None = None, timeout: int = 300) -> str:
    """Stdlib raw chat completion (no langchain) — for the coder's bounded loop. Returns the
    assistant text (falls back to a `reasoning` field if `content` is empty)."""
    model, base, key = resolve_role(role)
    temp = _ROLE_TEMP.get(role.lower(), 0.1) if temperature is None else temperature
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temp}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=_json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    from poc_foundry import tracing   # import-light; lazy here so models.py import stays cheap
    with tracing.span(f"llm.{role}", role=role) as _sp:
        METER.count()                   # cap choke point (raises BudgetExceeded before an over-budget call)
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _json.loads(r.read())
        METER.record_latency(time.time() - t0)
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        out = msg.get("content") or msg.get("reasoning") or ""
        _sp.update(output=out[:2000])
        return out
