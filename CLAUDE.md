# CLAUDE.md

@AGENTS.md

The working agreement above (`AGENTS.md`) is the source of truth — it applies to every agent. The
notes below are Claude-Code-specific.

## Claude-specific notes

- **Local verification only.** This box is Python 3.10 and has no venv/uv (rule #3). The only local
  checks are `python -m py_compile <files>` and running pure pydantic/stdlib modules directly.
  Everything that imports the agent stack (langchain/langgraph/deepagents) or runs the pipeline is a
  **server-in-Docker** step — author it, hand the user the command block, don't try to run it here.
- **The local GREEN BAR is `bash scripts/check.sh`** — py_compile + the no-pytest fakes suite
  (`run_spine_tests.py`) + contract checks + the data-hygiene guard (`check_hygiene.sh`). Run it before
  handing the user any commit; every new gate/logic change gets a fakes test so it's covered here.
- **Never run git mutations** on this repo (rule #2). If a commit/push is needed, tell the user — and
  run `bash scripts/check_hygiene.sh` first (rule #1: no host/model-id/path/key in TRACKED files).
- **Consult the planning chat via the user** for ledger deviations / structural surprises — don't
  guess on structural calls (rule in `AGENTS.md` → Decision culture).
- **Per-phase test commands.** The user wants each milestone provably finished before the next.
  When you complete a slice, give the exact command(s) that prove it (local `py_compile`/contract
  test, or a server command block). `ROADMAP.md` records the acceptance check per milestone.

## Status

**M0 ✅ · M1 ✅ · M2a ✅ · M2b ✅ COMPLETE** (all server-validated, 2026-06-23). M2b shipped the
hygiene scrubber, budget/cap enforcement + contention indicator, run-cap salvage (abandoned.patch +
descope report + gaps), and cooperative stop/resume. Next: **M2c — periphery** (research-on-gaps,
Langfuse spans, tiered evals v1, playbook injection + reflection, template CI). Local checks:
`python3 scripts/run_spine_tests.py` (65) + `run_contract_checks.py` (11). See `ROADMAP.md` for live
milestone state and `DECISIONS.md` for the rationale log (newest: #22).

**M2c in progress:** S1 observability ✅ **server-validated (2026-06-24)** — tolerated-absent Langfuse
spans (langfuse v4 API) at the broker/VERIFY/gate/critic/clean-room/LLM/proxy-denial seams, flush-on-
exit; trace `build/poc-…` with 21 observation levels in the `stage-3-poc` project. Local checks now:
`run_spine_tests.py` (74) + contract (11). Next: S2 (tiered evals v1).
