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

**M2c ✅ ALL 5 SLICES SERVER-VALIDATED (2026-06-24):** S1 observability (Langfuse spans) · S2 tiered
evals (`cli eval`/`run_evals.py`; spec/plan 1.0 vs fixtures) · S3 experience loop (`playbooks.py`
injection lands with the format suffix LAST + Tier-1 reflection → lessons.md; hint write made
tolerated-absent after an NFS root-squash crash — hint PERSISTENCE needs `chmod 777 playbooks/hints`
on the host, else build still `done` + "hint NOT persisted") · S4 research-on-gaps (`research/` pkg:
vendored tools + BESPOKE search→fetch→synthesize agent, deepagents-swappable; triggers = open-questions
+ stuck-loop; shared depot SearXNG — real cited research.md, coder consumes it; DECISIONS #25) · S5
template CI (`core.template_ci`/`preflight_templates` + `cli template-ci` — both templates GREEN in
fresh VMs). ZERO leaks throughout. Local checks: `run_spine_tests.py` (**107**) + contract (11).
DECISIONS #23–#26. **Next: M3 (web UI). Residual: confirm hint persists after `chmod 777
playbooks/hints` + the depot-side SearXNG digest/engine pins (user's service-depot repo).**
