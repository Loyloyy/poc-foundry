# ROADMAP — poc-foundry

Milestone checkboxes + the **acceptance check** that proves each is done (the user's rule: a phase is
provably finished before the next starts). Keep these current at the end of EVERY session — cross-chat
continuity depends on it. Full milestone detail: `STAGE3_DESIGN_v1.6.md` §6.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done & verified.

---

## Slice 0 — Repo foundation
- [x] Skeleton + tracked docs (AGENTS/CLAUDE/README/DECISIONS/DEV_NOTES/ROADMAP)
- [x] `config/pipeline.yaml`, `.env.example`, `.gitignore`, minimal `pyproject.toml`
- [x] DECISIONS #1 (inherited v1.6) + #2 (schema vendoring stopgap)
- [x] `docker/` images (app · sandbox · proxy) + compose + override example (Slice 2)
- **Acceptance:** `python -m py_compile` clean; `sh -n` on entrypoint clean; on server, all three
  images build and each smoke runs (sandbox prints tool versions; proxy `squid -k parse` OK; app
  imports `poc_foundry`).

## M0 — independent de-risk spikes (NO pipeline code). Gate: (a)(c)(d) green + (b) decided → M1.

### M0(a) — Kata spike  *(server, PuTTY; runbook `scripts/m0a_kata_register_smoke.md`, probe `scripts/m0a_kata_probe.sh`)*
- [x] Runtime already registered (from poc-builder-prework Phase 3) — `"kata":{}` in docker info.
- [x] Smoke GREEN (2026-06-22): kata guest kernel **6.18.28** ≠ host **6.8** → real VM proven.
- [x] **Probe 7/7 GREEN (2026-06-22):** virtio-fs uid/gid (uid 1000) · nested RO `tests/` (Read-only
      FS) · writable junit · named-volume uv cache · memory cap had effect · **item 6 VM→proxy→vLLM
      under kata GREEN** (proxy reached by IP `172.x:3128`; allowlist + denials + CONNECT log all hold).
      Finding: Kata guests have no Docker name-DNS → broker injects the proxy IP, not name (DECISIONS #9).
- **Acceptance:** ✅ smoke kernel ≠ host; ✅ probe 7/7 GREEN. `sandbox_cgroup_only` on cgroup v2 stays
  an INFO caveat (host-side cgroups authoritative). Result → DECISIONS #9.

### M0(b) — Coder bake-off  *(server; `scripts/m0b_bakeoff/`)*
- [x] **DECIDED (2026-06-22): bespoke loop wins the `CoderEngine` seat** — bespoke 4/4 (whole) +
      4/4 (diff) vs OpenCode 1/4; NO loop-weak signal (no bespoke-fail/OpenCode-pass). Edit format:
      whole-file default, diff available. OpenCode = fallback (its 1/4 likely an adapter artifact —
      DECISIONS #8). Trace saved under `scripts/m0b_bakeoff/traces/`.
- **Prereqs:** poc-foundry-sandbox image; `.env` CODER_*/PF_DEFAULT_ROLE; for the control arm the
      `opencode` binary + opencode.json provider config with bash/webfetch DENIED (reuse prework's
      `docker/opencode.example.json`). Without opencode the bespoke arm still runs (control = skip).

### M0(c) — Ingest + spec probe
- [x] Vendor Stage-2 schema (`src/poc_foundry/artifact/`) + drift check (`scripts/check_vendored_schema.sh`)
- [x] Ingest module (`src/poc_foundry/ingest/`: load_run + semantic invariants + defensive clamp)
- [x] Contract tests (`tests/test_contract.py` + `scripts/run_contract_checks.py` no-pytest runner) —
      **11/11 GREEN locally** (vendored schema in sync; invariants hold; import is heavy-stack-free)
- [x] `m0_ingest_probe.py`: load a run folder + validate + capability sketch + `--freshness` (detect-only)
- [x] **in-container contract check GREEN on server** (2026-06-22): `11 passed, 0 failed`
- [ ] **(needs server)** run the probe against a REAL sanitized Stage-2 run folder (+ `--freshness`)
- [ ] **(gated on P1, lands early M1)** P1 spec generation on the fixture → user grades; spec lint
      — needs the architect LLM + the P1 phase (would be pipeline code; M0 is code-free). Sequencing
      note to user: this half of M0(c) overlaps M1's P1.
- **Acceptance:** local contract = GREEN ✅; server = a real run folder loads clean + (early M1) a
      graded spec + green spec lint. Result → DECISIONS.

### M0(d) — Egress proxy spike  *(server; `scripts/m0d_egress_spike.sh`)*
- [x] **GREEN on server (2026-06-22): 8/8 passed.** ALLOW (PyPI/git/uv/vLLM through the proxy) +
      DENY (google `TCP_DENIED/403`, RFC1918, direct egress, direct RFC1918). CONNECT log shows
      `TCP_TUNNEL/200` for allowlisted hosts, `TCP_MISS/200` for the vLLM exception, `TCP_DENIED/403`
      for google — the deterministic security evidence. Proxy boot required 4 squid-in-Docker fixes
      (see DEV_NOTES "full saga").
- **Acceptance:** ✅ `M0(d): GREEN`; CONNECT log shows allowed + denied attempts.

## M1 — walking skeleton (thin everywhere)
- [x] **S1 spine (2026-06-22):** package relocate (vendored Stage-2 schema → `stage2_artifact/`, freeing
      `artifact/` for the OUTPUT); `PoCBuildArtifact` schema+store (`artifact/`); `BuildState`/`Spec`/
      `Plan` (`state.py`); `BuildConfig` + pipeline.yaml/env load (`config.py`); `build_chat_model` +
      `chat_text` role factory (`models.py`). All pydantic/stdlib — round-trips + config load verified
      on the 3.10 box; contract tests still 11/11; models.py stays langchain-free at import.
- [ ] **S2 broker** — in-process stub behind the real interface (`create/create_service/exec/destroy`;
      create* params harness-fixed, only exec carries LLM content); per-build internal net + egress
      proxy (proxy-by-IP, from M0(a)); kata sandbox lifecycle (promote `m0b_bakeoff/sandbox.py`).
- [ ] **S3 CoderEngine** — promote `m0b_bakeoff` BespokeEngine (won the seat) behind the seam.
- [ ] **S4 phases + graph** — P0 ingest (have it) → P1 spec → P2 plan → P3 scaffold (gradio template
      + green smoke) → P4 code+staged VERIFY → P5 min docs → P6 thin clean-room → P7 emit; LangGraph
      wiring + checkpointer; `build_poc(...)` contract (`core.py`); CLI.
- [ ] **S5 gradio template** (`templates/gradio-chatbot/`) + end-to-end run on server.
- **Acceptance:** one fixture artifact runs end-to-end on the server; emits a `builds/<id>/` with a
      valid `PoCBuildArtifact`; claim proven = orchestrator-writes / sandbox-executes.

## M2a — gates
- [ ] tester + adequacy review + critic verdict set + fix/descope/replan/respec + cumulative suite +
      inventory ledger + diff scanner + interface contracts + clean-room GATES + out-of-process
      broker + sibling services
- **Acceptance:** a planted test-gaming attempt is caught by the ledger/scanner; clean-room gates
      a known-bad build red; broker runs out-of-process.

## M2b — resilience
- [ ] budgets/caps/escalation · checkpoint/resume/stop · salvage + descope report · contention
      indicator · hygiene scrubber
- **Acceptance:** a killed run resumes from last green commit; a forced descope yields a descope
      report; scrubber leaves no endpoint/id in emitted text.

## M2c — periphery
- [ ] research-on-gaps · Langfuse + manual spans · tiered evals v1 · CLI · playbook
      injection + reflection · template CI
- **Acceptance:** spec/plan evals run against fixtures; manual spans appear in Langfuse `stage-3-poc`.

## M3 — web UI
- [ ] slice board · Stop/Resume · history · descope report view (Stage-2 SSE seam)
- **Acceptance:** a run is watchable live over an SSH tunnel; Stop/Resume works.

## M4 — breadth
- [ ] security red-team demo (both beats) + vLLM key-proxy (ship together) · `refine` flow · JS
      template (if npm granted) · multi-service composition · eval harness v2 · `docs/PLATFORM.md`
- **Acceptance:** the security demo runs both beats with proxy logs as evidence; `demo-security` CLI.
