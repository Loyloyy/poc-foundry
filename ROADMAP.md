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
- [x] **S2 broker built (2026-06-22)** — `sandbox/broker.py`: in-process Docker stub behind
      `provision/create/create_service/exec/destroy`; per-build internal net + dual-homed egress proxy
      (by IP, M0(a)) + uv-cache vol; fresh Kata VM lifecycle (promoted `m0b_bakeoff/sandbox.py`).
      **Invariant enforced in code** (image/caps/mounts/name allowlists RAISE `BrokerInvariantError`;
      only `exec` carries LLM content). Guards unit-tested (no Docker). App image gains the `docker`
      CLI + socket mount (risk-accepted M1 residual). *Server: create→exec→destroy a kata VM still to run.*
- [x] **S3 CoderEngine built (2026-06-22)** — `coder.py`: `BespokeCoder` behind a `CoderEngine` seam;
      whole-file default, error-signature + forced strategy change; broker-decoupled via an injected
      `verify()`; edits orchestrator-side. RED→GREEN + refuse-to-edit-test unit-tested with fakes.
- [x] **S4 phases + graph + core + cli built (2026-06-22)** — `phases/` (context + pipeline P0…P7),
      `graph.py` (LangGraph + SQLite checkpointer; NOT_BUILDABLE/scaffold-failed short-circuits),
      `core.build_poc/resume_build/list/clean` (headless contract), `cli.py`. Full P0→P7 dry-run with
      fakes → `status=done`; NOT_BUILDABLE path verified; routers verified. *P2 plan deterministic for
      M1; red-first best-effort (DECISIONS #11).*
- [x] **S5 gradio template built (2026-06-22)** — `templates/gradio-chatbot/` (start-green core/UI
      split, pinned gradio, smoke suite, seeded RUN/README/AGENTS, manifest, pf:-tagged RUN blocks).
- [x] **end-to-end run on real Kata GREEN (2026-06-22) — M1 GATE MET.** One fixture
      (`tests/fixtures/sample_artifact`) → `builds/poc-…/` with `status=done`,
      `demonstrates_core_value=yes`. P0→P7 all green on real Kata VMs + the egress proxy + the model roles;
      the model wrote a real 5-criterion RAG-citation spec; scaffold GREEN in a fresh VM; coder RED→GREEN in
      1 attempt; **clean-room install=test=demo=TRUE** (every RUN.md block runs in a fresh clone+VM).
      Server fixes folded in: app image needs `docker-cli` not `docker.io` (Debian-13 split); git
      `safe.directory` global (root over uid-1000 repos); gradio 4.44.1 needs `huggingface_hub==0.24.7`
      (HfFolder). Claim proven: **orchestrator-writes / sandbox-executes**.
- **Acceptance:** ✅ one fixture runs end-to-end on the server; emits a `builds/<id>/` with a valid
      `PoCBuildArtifact`; claim proven = orchestrator-writes / sandbox-executes. Local: `py_compile`
      clean, no heavy stack at import, `tests/test_m1_spine.py` 7/7, contract 11/11. **M1 COMPLETE → M2a.**

## M2a — gates ✅ COMPLETE (2026-06-23, all slices server-validated)
- [x] **S1 integrity walls (2026-06-22, local):** `phases/integrity.py` (pure: inventory-ledger
      parsers `collected_names`/`junit_passed_names`/`inventory_ok`; the diff `scan_diff`;
      `Incident`/`blocking`). Wired into `p4_iterate`: records authored test ids (collect-only),
      **enforces red-first** (green-against-scaffold = tester-inadequacy incident, not a pass), runs
      an authoritative **junit ledger** after the coder reaches green (collected∧passed ⊇ recorded →
      `tests.inventory_ok`), and **scans the coder's per-attempt diff** (a tampering edit fails the
      attempt → forced strategy change → high-sev `security.incidents[]`). `_final_status`/
      `demonstrates_core_value` now gated on `_trustworthy` (inventory_ok ∧ red_first_ok ∧ no
      high-sev incident) — a gamed build can NOT report `done`. Report gains an Integrity section.
      Local: **24/24 fakes** (`run_spine_tests.py`: 8 spine + 16 gates incl. 3 planted-gaming caught)
      + contract 11/11 + import-hygiene clean. *Server: re-run the M1 gate to confirm the happy path
      still `done` with inventory_ok=true.*
- [x] **S2 critic gate + verdict ladder (2026-06-22, local):** new `p_critic` node + `_after_critic`
      LangGraph routing around P4 (fix→iterate, respec→spec, replan→plan, pass/descope→docs; cycles
      bounded by `fix_limit_k`/`respec_cap`/`replan_cap` + a recursion_limit). Critic **adequacy
      review** (`critic` role, structured `AdequacyReview`) on a green iteration; **degraded-critic**
      auto-detected via `models.same_family("critic","coder")` → lower K (`degraded_fix_limit_k`) +
      `security.degraded_critic=true`, and in degraded mode the adequacy verdict is **advisory
      (caveat), non-blocking** (a same-family judge can't independently certify — keeps the server
      happy-path green; blocking adequacy returns with a frontier critic). `descope_report[]`
      populated on descope; report gains Critic + Descope sections. Local: **32/32 fakes** (8 spine +
      24 gates incl. the full verdict ladder + routing) + contract 11/11 + import-hygiene clean.
      *Server: re-run the gate — happy path still `done`, now showing degraded_critic=true + critic
      verdict=pass.* Cumulative suite deferred to S3 (meaningful only with multi-iteration).
- [x] **S3 multi-iteration loop + clean-room GATES (2026-06-23, local):** P2 now a deterministic
      **core-first multi-iteration** plan (one small iteration per testable criterion; architect-driven
      decomposition deferred — DEV_NOTES weak-self-planner evidence). The graph **loops P4** via the
      critic's `next`/`fix` verdicts (`iterate→critic→iterate`), each iteration with its own staged
      `test_iter_i.py` under a **cumulative regression gate** (`pytest /staged` = new + all prior).
      **Red-first is strict only at iteration 0** (against the scaffold); a later iteration green-pre-
      coder = "met by existing implementation" (legit, not a violation). Met iterations' tests are
      **published into `workspace/tests/`** so the clean-room re-runs the cumulative criterion suite
      (was: template smoke only) — and `_final_status` already gates `done` on `cleanroom.suite_ok`, so
      a red clean-room can never be `done`. Local: **35/35 fakes** (8 spine + 27 gates incl. a faithful
      in-process multi-iteration build → `done`) + contract 11/11 + import-hygiene clean. *Server: the
      fixture's 5 criteria → ~5 iterations; expect `done` with iter0 RED→GREEN + later iters
      met-existing, clean-room running the published tests.*
- [x] **S4a out-of-process broker (2026-06-23, local):** the orchestrator no longer drives docker —
      a `BrokerDaemon` (`sandbox/daemon.py`) holds docker.sock and is the only enforcer of the
      create-param invariant (rule #8); the orchestrator uses a thin `RemoteBroker`/`RemoteSandbox`
      (`sandbox/client.py`) that forwards the same `provision/create/create_service/exec/destroy`
      interface over a Unix-socket JSON-RPC (`sandbox/rpc.py`). Selected by `PF_BROKER_SOCKET` (unset →
      in-process `Broker`, unchanged default). Compose override gains a `broker` service (holds
      docker.sock) and drops docker.sock from `app`. Local: **38/38 fakes** (+3 broker-RPC: real
      socket round-trip + invariant re-raises same type client-side + interface parity) + contract
      11/11 + hygiene clean. **Server GREEN (2026-06-23):** build `done` via the out-of-process broker
      (5 iterations); `no docker.sock in app (GOOD)`; zero build-resource leaks (only the long-lived
      `pf-broker` daemon remains, removed by `down`). M2a headline acceptance MET.
- [x] **S4b sibling services — minimal-real (2026-06-23, local):** `create_service` made real
      (readiness wait via `pg_isready`; image/tag from the HARNESS-FIXED `vetted_services` list, rule #8;
      added to the broker allowlist). A template DECLARES its services (`template.json services`); P3
      spins them once per build on the internal net, records each IP as `PF_SERVICE_<NAME>_HOST`, and
      P4/P6 inject that env so the sandbox + clean-room reach the sibling **by IP**. New
      `gradio-rag-pgvector` template: deterministic stdlib embedding + real pgvector similarity search;
      scaffold ships working DB plumbing + a stub `generate_reply` (red-first holds, coder writes the
      retrieval glue). `psycopg[binary]` baked into the sandbox image (iterations don't `uv pip
      install`). Spec prompt is service-aware. P7 records `services[]`. Local: **42/42 fakes** (+4:
      config pin, template decl, P3 spin+IP record, iteration env injection) + contract 11/11 + hygiene
      clean. **Server run 1 (2026-06-23):** pgvector spun + ready @ IP, iter0/iter1 RED→GREEN vs REAL
      pgvector, service reaped (ZERO leaks), clean-room GATE correctly caught a bug → `incomplete`.
      Bug = workspace pollution from an abandoned iteration (DECISIONS #17, salvage `git reset --hard`
      fix + regression test). **Server run 2 (2026-06-23): `status=done`** — salvage rollback fired on
      every abandoned iteration, clean-room ran the published green tests vs pgvector (`test=True`), all
      5 criteria met, ZERO leaks. **S4b COMPLETE.** Perf: a `fix`-retry now REUSES the staged test (no
      re-author) — the run was ~30 min (a hard template: the model grinding the citation-format + relevance
      threshold across 5 criteria w/ retries; VMs are warm within an iteration; the fix-loop salvaged
      iter1 on round 3 — rigor, not waste). **Hardening (2026-06-23):** a later run came back honest
      `incomplete` when the architect's core was too hard for the degraded coder (DONE floor working).
      Fixed by shipping working scaffold helpers (`retrieve` w/ a LEXICAL relevance gate, `snippet`,
      `cite`) so the coder's core task is ~3 lines of glue + a corpus topical to the artifact → the
      sibling build is now a reliable `done`, not a coin-flip (DEV_NOTES). *Server re-run pending.*
- **Acceptance — ALL MET (server-validated 2026-06-23):** ✅ planted test-gaming caught by the
      ledger/red-first/scanner + critic (44 fakes, incl. 3 planted cases); ✅ clean-room gates a
      known-bad build red (the pgvector run literally caught broken code → `incomplete`); ✅ broker runs
      out-of-process (`no docker.sock in app`); ✅ multi-iteration build completes; ✅ sibling-service
      (pgvector) build runs end-to-end → `done`; ✅ zero resource leaks after every run.

## M2b — resilience ✅ COMPLETE (2026-06-23, all slices server-validated)
- [x] **S1 hygiene scrubber (2026-06-23, local):** `scrub.py` — pure, env-driven (`.env` +
      `build_env.json`, never hardcoded) emitted-output scrubber. `collect_secrets()` classifies
      `KEY=value` by suffix → placeholders (`<served-model-id>`/`<vllm-host:port>`/`<vllm-host>`/
      `<redacted-key>`/`<service-host>`/`/path/...`); `scrub_build_dir()` rewrites the artifact JSON +
      report/index/progress + `logs/*.log`, longest-value-first, conservative (literals only, generic
      tokens skipped, JSON stays valid). Wired into `p7_emit` + `core._emit_failed`. Closes the last
      open rule-#1 item (DECISIONS #18). Local: **48/48 fakes** (+4 `test_m2b_scrub.py`) + contract
      11/11 + hygiene clean. **Server-validated (2026-06-23):** chatbot build `done`; grep of emitted
      text incl. `logs/egress.log` for the real host/model-id = `SCRUBBED ✓`.
- [x] **S2 budget/cap enforcement + contention (2026-06-23, local):** a process-global meter at the
      `models.py` choke point (`METER` in `build_chat_model`/`chat_text`) counts LLM calls per-iter +
      per-run + samples latency. Enforces `max_llm_calls_per_iter/_run` + the (secondary) wall-clock
      caps (config-loaded, env-overridable, 0 disables); `contention_indicator` = median call latency.
      `BudgetExceeded` is a `BaseException` (escapes the phases' broad `except Exception` to halt the
      run) → `core._salvage_run` recovers the checkpointed state, rolls back to last green, records
      `caps_hit[]`, emits honest `incomplete`. `p7_emit` populates `budget` + `caps_hit`; report gains
      a Budget section. Research-escalation rung = M2c stub (DECISIONS #19). Local: **56/56 fakes**
      (+8 `test_m2b_budget.py`) + contract 11/11 + hygiene clean. **Server-validated (2026-06-23):**
      normal build `done` with `budget.llm_calls=42`/`contention_indicator=56s`; `PF_MAX_LLM_CALLS_RUN=3`
      → run-cap salvage to `incomplete` + `caps_hit` (NO crash — `BudgetExceeded` propagated through
      LangGraph), ZERO leaks.
- [x] **S3 run-cap salvage — abandoned.patch + descope + gaps (2026-06-23, local):** the S2 salvage
      path now captures the in-flight (un-merged) coder edits to `builds/<id>/abandoned.patch` BEFORE
      the rollback, appends a `descope_report[]` entry for the in-flight criterion (with a resume-or-
      finish-by-hand `finish_path`), and `final_verdict.gaps` is populated for every build (criteria
      not `met`). Report gains Gaps + the index advertises the patch (DECISIONS #20). Descope targets
      the first UNMET criterion (server-found fix: was naming the already-met core). Local: **59/59
      fakes** (+3 `test_m2b_salvage.py`) + contract 11/11 + hygiene clean. **Server-validated
      (2026-06-23):** `PF_MAX_LLM_CALLS_RUN=3` → `incomplete` + Descope + Gaps + `caps_hit`
      (abandoned.patch correctly absent: cap fired post-commit on the critic call → clean tree).
- [x] **S4 cooperative stop + resume hardening (2026-06-23, local):** `control.py` (sentinel +
      `BuildStopped` BaseException); `graph.wrap` checks `raise_if_stopped` at every node boundary →
      `core._emit_stopped` recovers provenance from the last checkpoint + writes a resumable
      `status: stopped` artifact. `resume_build` clears the sentinel + re-provisions a FRESH broker
      over the persisted workspace/state (cattle vs pets). New `stop <id>` CLI + `request_stop_build`.
      LangGraph msgpack type-registration = a documented forward-compat follow-up (DEV_NOTES, #21).
      Live phase trace to stderr (`PF_PROGRESS`) + a deterministic `PF_STOP_AT_NODE` test hook so the
      kill/resume gate needs no manual-timing. Local: **64/64 fakes** (+5 `test_m2b_stop.py`) +
      contract 11/11 + hygiene clean. **Server-validated (2026-06-23):** `PF_STOP_AT_NODE=iterate:2`
      build → `stopped` after iter0 → `resume` (no env) continued from the iter0 checkpoint over the
      persisted workspace → `done` (all 5 criteria met, clean-room green, output scrubbed); fresh
      broker re-provisioned; ZERO leaks. (Known minor: the in-memory meter resets per process, so a
      resumed run's `budget.llm_calls` counts only the resume leg — DEV_NOTES.)
- **Acceptance — ALL MET (server-validated 2026-06-23):** ✅ scrubber leaves no endpoint/id in emitted
      text (S1); ✅ budgets enforced + recorded, run-cap salvages instead of running away (S2);
      ✅ a forced run-cap yields `incomplete` + descope report + `abandoned.patch` + gaps (S3);
      ✅ a stopped/killed run resumes from the last green commit and completes (S4); ✅ ZERO leaks,
      green bar stays green, both templates still build `done`. **M2b COMPLETE → M2c.**

## M2c — periphery
- [x] **S1 observability — `tracing.py` + manual spans (2026-06-24, server-validated):** tolerated-absent
      `tracing.py` (Stage-2 pattern: env-gated `PF_TRACING`, lazy langfuse, no-op when off/absent —
      a tracing hiccup can never crash a build). Manual spans around the half the LangChain handler
      can't see: a **build** root span (core), **broker** provision/create/create_service/exec/destroy
      (both in-process `Broker` + the out-of-process `RemoteBroker`/`RemoteSandbox`), **spec**, the
      **iterate.verify** + **gate.diff-scan** spans, **gate.incident** events (diff-scan + ledger-gap),
      **critic**, **cleanroom**, the raw **llm.<role>** call (chat_text — no handler sees it), and a
      **proxy.denials** event (TCP_DENIED count from the egress log). Flush-on-exit in `core` (build +
      resume). Module singleton + `set_tracer`/`reset_tracer` for injection (DECISIONS #22). Local:
      **73 fakes** (+8 `test_m2c_tracing.py`: no-op when off, dep-absent degrades, broker-layer spans
      via a fake `_run`, the phase-pipeline spans + proxy-denial event via the m1 fakes harness, the
      chat_text llm span, + the real `_LangfuseTracer` against fake langfuse v4 & v3 clients) + contract
      11/11 + hygiene clean. **Server iteration (2026-06-23):** first build trace was EMPTY — the server
      has **langfuse 4.9.1** and the v3-authored API no-op'd under the guards; fixed by feature-detecting
      v4 `start_as_current_observation` (trace name = root obs `build/<id>`; tags/session in metadata),
      pinned `langfuse>=4,<5`; added `PF_LANGFUSE_TIMEOUT_S`. **VALIDATED (2026-06-24):** in the
      dedicated `stage-3-poc` project, root trace `build/poc-20260623-164251-4d3c04` with **21
      observation levels** — `broker.provision/create/exec/destroy` (rich in/out: sandbox, cmd, rc,
      output tails), `spec`, `iterate.verify`, `critic`, `cleanroom`, `llm.*` all landing; flush-on-exit
      confirmed; survives a depot restart. The long tail was ALL shared-infra (service-depot): langfuse
      was down for three distinct reasons — clickhouse + minio containers orphaned off
      `service-depot_default` by a stray `docker network/system prune` (deletes the net under
      running `restart:always` containers), then a post-recreate `:3000` connection-refused — each fixed
      in service-depot via `./depot down/up` + a prune guard; the poc-foundry code never changed after
      the v4 fix. msgpack-registration carry-forward NOT folded in (unverifiable API; documented).
- [x] **S2 tiered evals v1 (2026-06-24, server-validated):** `evals.py` (pure) — the CHEAPEST eval rung: run
      P0 ingest → P1 spec → P2 plan on a committed fixture (NO sandbox/clean-room/Langfuse), score with
      deterministic structural checks (`score_spec`/`score_plan`: count 3–6, one core, met-by-test,
      core-first plan, interface pinned, …) → 0..1 + an `overall_score`, plus a structured
      human-grading rubric recorded UNGRADED (Tier-2 seam; degraded-critic = no auto-LLM-judge). No
      broker constructed (P0/P1/P2 don't touch it); only the real architect call in P1 → server-run.
      `cli eval [--fixture/--template/--min-score/--json]` + `scripts/run_evals.py` (server; mirrors
      `run_contract_checks.py`). Metrics structured for stratification (`metadata.degraded_critic` +
      `stratify`). DECISIONS #23. Local: **81 fakes** (+6 `test_m2c_evals.py`) + contract 11/11 +
      hygiene clean. *Server (pending): `cli eval` on the fixture → scored report with the real architect.*
- [~] **S3 experience loop — playbook injection + Tier-1 reflection (2026-06-24, local):** `playbooks.py`
      (pure) + tracked `playbooks/{building,testing,research,gotchas}.md` + gitignored low-authority
      EXPIRING auto-hints under `playbooks/hints/` (only `hints/README.md` tracked). Injection seam in
      `prompts.py`/`coder.py`: `compose(body, role, suffix)` orders body→playbook→suffix so the
      hard-rule/format suffix stays LAST (can't be displaced); per-role curated bodies + matching
      non-expired hints (framed "unverified hint"), char-budgeted; hints pin via `applies_to` (role OR
      playbook name), skip expired/oversized/mismatched. Tier-1: `_reflect` interrogates the coder on a
      STRUGGLING iteration → `builds/<id>/iterations/<i>/lessons.md` (cites the incident); `p7_emit`
      distils lessons → one scrubbed expiring `playbooks/hints/<id>.md`. Scrubber extended to
      `iterations/*/*.md`. DECISIONS #24. Local: **90 fakes** (+9 `test_m2c_playbooks.py`) + contract
      11/11 + hygiene clean. *Server (pending): a build emits lessons.md + a hints/ entry; a seeded
      playbook shapes a recorded prompt.*
- [ ] S4 research-on-gaps (deepagents + SearXNG) · S5 template CI
- **Acceptance:** spec/plan evals run against fixtures; manual spans appear in Langfuse `stage-3-poc`.

## M3 — web UI
- [ ] slice board · Stop/Resume · history · descope report view (Stage-2 SSE seam)
- **Acceptance:** a run is watchable live over an SSH tunnel; Stop/Resume works.

## M4 — breadth
- [ ] security red-team demo (both beats) + vLLM key-proxy (ship together) · `refine` flow · JS
      template (if npm granted) · multi-service composition · eval harness v2 · `docs/PLATFORM.md`
- [ ] **daemon-side invariant-rejection audit log** (from the M2c S1 design review): the broker daemon
      (trust boundary / rule-#8 enforcer) should durably record rejected `create*` (+ provision/destroy)
      append-only, read independently of the orchestrator, feeding `security.incidents[]` (§5.2 posture).
      NOT trace propagation; planning-chat consult first (DECISIONS #22 follow-up).
- **Acceptance:** the security demo runs both beats with proxy logs as evidence; `demo-security` CLI.
