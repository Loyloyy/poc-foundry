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
- [ ] Register runtime (daemon.json runtimeType + `systemctl reload docker`, scheduled window)
- [ ] Smoke: `docker run --runtime kata --rm ubuntu:24.04 uname -r` (guest kernel ≠ host)
- [~] Probe script authored: virtio-fs uid/gid; nested RO `tests/` in RW workspace; writable junit;
      named-volume uv cache; caps + `sandbox_cgroup_only` note; VM→proxy→vLLM via the egress spike
      under `--runtime kata`. **Run pending** (`bash scripts/m0a_kata_probe.sh`).
- **Acceptance:** smoke prints a guest kernel ≠ host; `m0a_kata_probe.sh` ends `GREEN` (0 FAIL) —
  WARN/INFO items (in-guest mem cap, sandbox_cgroup_only) documented as caveats; fallbacks if needed
  (host-side cgroups authoritative / tar-based exec bridge). Result → DECISIONS.

### M0(b) — Coder bake-off  *(server; `scripts/m0b_bakeoff/`)*
- [~] Harness authored: `BespokeEngine` (bounded loop, whole-file/diff edit formats, error-signature
      tracking + forced strategy change) + `OpenCodeEngine` (scripted `opencode run`) behind a common
      `CoderEngine` seam; 4 tasks (syntax / wrong-api / failing-test breaks + red-first feature);
      `run.py` matrix + attribution. **Pure parts verified locally** (parsers, diff-apply, attribution,
      task solvability). **Run pending** (`python3 -m scripts.m0b_bakeoff.run`).
- **Acceptance:** a recorded **decision** in DECISIONS (winner takes the `CoderEngine` seat, loser =
      fallback) + the attribution matrix (model-weak vs loop-weak) + edit-format recommendation. The
      runner prints all three + saves a JSON trace. (Decision required, not "green".)
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
- [~] Script authored: `internal:true` net + dual-homed proxy; ALLOW (PyPI/git/uv/vLLM) + DENY
      (non-allowlisted host, non-vLLM RFC1918, direct egress, direct RFC1918); CONNECT-log evidence;
      domain-fronting residual noted. **Run pending** (`bash scripts/m0d_egress_spike.sh`).
- **Acceptance:** `m0d_egress_spike.sh` prints `M0(d): GREEN` (exit 0 = all allow/deny asserts hold);
      CONNECT log shows the allowed + denied attempts. Result → DECISIONS.

## M1 — walking skeleton (thin everywhere)
- [ ] artifact → spec → 1-iter plan → scaffold → code → staged VERIFY → minimal docs → thin
      clean-room → artifact emitted; in-process broker stub; egress-isolated from day one
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
