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
- [x] **S3 experience loop — playbook injection + Tier-1 reflection (2026-06-24, server-validated):** `playbooks.py`
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
      playbook shapes a recorded prompt.* **Server-validated (2026-06-24):** the `## Playbook` block
      injects with the format suffix LAST; reflection wrote a grounded lessons.md. Hint write was
      crashing the build at P7 on NFS root-squash → fixed tolerated-absent (`write_hint`→None on OSError
      + guarded p7/reflect/research writes); hint PERSISTENCE needs `chmod 777 playbooks/hints` on the
      host (NFS), else "hint NOT persisted" + build still `done`. (107 fakes; +1 unwritable-dir test.)
- [x] **S4 research-on-gaps (2026-06-24, server-validated):** the §5.8 ladder's last rung, per the planning-chat
      DECISION MEMO (orchestrator locus; SHARED depot SearXNG, not a per-build sibling; minimal-real =
      prove the rung, not research quality). New `research/` pkg: vendored Stage-2 tools
      (search/fetch/GitHub/PyPI; lazy httpx+trafilatura; app-level `research_hosts` gate; injection
      tripwire) + a BESPOKE search→fetch→synthesize agent (deepagents-swappable via the seam; bespoke
      keeps the budget meter exact). Triggers: (a) `art.open_questions`→`Spec`/`IterationPlan`→research
      at top of p4 (feeds tester+coder); (b) stuck repeated-signature abandon → `p_critic` routes to
      research (+ `PF_FORCE_RESEARCH=1` hook), replacing the #19 stub. Containment: Finding-0 tool
      surface + citation-only `research.md` air-gap + untrusted-data framing + tripwire→`security.incidents[]`
      + unchanged gates. Tolerated-absent (no SEARX_URL → no-op). `vetted_services` + the build-VM
      allowlist UNTOUCHED. DECISIONS #25. Local: **101 fakes** (+11 `test_m2c_research.py`) + contract
      11/11 + hygiene clean. *Server (pending): the fixture's open question → a cited research.md the
      coder consumes via depot SearXNG; ZERO leaks. Depot-side (user): digest-pin searxng + pin engines.*
      **Server-validated (2026-06-24):** the fixture's open question → `iterations/0/research.md` with 4
      REAL SearXNG sources, synthesized + cited (+ honest "inconclusive"); coder consumed it; build `done`;
      ZERO leaks. SearXNG JSON format works on the depot instance.
- [x] **S5 template CI (2026-06-24, server-validated):** `core.template_ci` + `core.preflight_templates` +
      `cli template-ci [--preflight]` (§5.3 P3). Preflight = dockerless static check (enumerate
      `templates/*/template.json`, resolve each, assert declared services pinned in `vetted_services`).
      `template_ci` = scaffold+smoke each template in a FRESH Kata VM (ONE broker, fresh VM per template,
      all reaped); workspaces on local disk for the Kata bind. DECISIONS #26. Local: **106 fakes**
      (+5 `test_m2c_template_ci.py`) + contract 11/11 + hygiene clean. **Server-validated (2026-06-24):**
      preflight 2/2; `template-ci` scaffold+smoked BOTH templates GREEN in fresh Kata VMs; ZERO leaks.
- **Acceptance:** spec/plan evals run against fixtures (S2 ✅ server-validated) · manual spans in Langfuse
      `stage-3-poc` (S1 ✅) · playbook injection + Tier-1 reflection → lessons.md + hints/ + a seeded
      playbook shapes a prompt (S3, server re-validate pending the playbooks mount) · research-on-gaps
      spins/reaches SearXNG → cited research.md the coder uses, ladder routes on a stuck loop (S4, server
      pending) · template CI scaffold+smokes every template GREEN (S5, server pending). Plus always: zero
      leaks, green bar green, both templates build `done`, hygiene clean on emitted output.

## M3 — web UI ✅ COMPLETE (2026-06-25, server-validated over the SSH tunnel)
Both slices server-validated: a real build was watched live over the tunnel (slice board flipping
green, log streaming), cooperative Stop→Resume worked from the UI, history + docs + descope render,
the headless contract is unchanged, localhost-publish boundary holds. Post-validation UX polish
(DECISIONS #28 addendum): research-source **picker** (`/api/sources` → topics, not raw paths);
Langfuse "Traces" link rewritten to a browser-reachable URL + deep-linked to the build's session
(`PF_LANGFUSE_PROJECT_ID`); Stop button **"Stopping…"** state + banner; **caveats/quality** card
(surfaces `degraded_critic` + critic advisories); clamped long Stage-2 briefs. Ops gotchas learned:
`.env` changes need `up -d --force-recreate` (NOT `restart`); `DC` must pass BOTH `-f` files; port
**8181** (8770/8008 are vLLM). Real-build learning: a non-degraded critic (distinct model family)
correctly blocks gameable tests → on a hard source (PageIndex) everything honestly descopes to the
`refine` finish-path; needs `PF_MAX_RUN_WALL_CLOCK_S` to bound degenerate runs. **Next: M4.**
- [x] **S1 — event seam + RunManager + SSE** ✅ SERVER-VALIDATED (2026-06-24): SSE over the tunnel
      streamed `start`/`node`(slice-board snapshot)/`log` through a real fixture build; history + status
      + cooperative Stop→Resume + `/api/stop`(no-id) all confirmed; localhost-publish boundary.
      `events.py` (pure-stdlib `make_event`/`snapshot`/`emit`/`sse_format`); `Ctx.say` + `graph.wrap`
      emit structured events through an optional `ctx.events` sink threaded via `build_poc`/
      `resume_build`/`_prepare` (CLI passes nothing → contract unchanged); single-slot `RunManager`
      (`web/runmanager.py`: start/resume/stop/status/subscribe, 2nd start → `RunBusy`→409, fan-out +
      replay buffer, terminal `end`/`error`); FastAPI `web/server.py` (localhost-only) + `web/__main__`
      (uvicorn :8181); `web` compose service (localhost port; override mirrors app's broker/workspace).
      Fakes: `tests/test_m3_events.py` (11). DECISIONS #27.
- [x] **S2 — React SPA** ✅ SERVER-VALIDATED (2026-06-25, over the tunnel) — React 18 + TS + Vite in
      `frontend/`, mirroring the Stage-2 frontend pattern (npm on the dev box, `node_modules` gitignored,
      **prebuilt `dist/` committed** to `src/poc_foundry/web/dist/` — the `.gitignore` re-includes it past
      the blanket `dist/`). `useEventStream` subscribes to the single-slot global `/api/events` and
      accumulates `start`/`node`(snapshot)/`log`/`end`/`error`. Views (§5.12): Sidebar (new-build form,
      single-slot Start disabled while busy + 409 surfaced; history list) · live **SliceBoard** (criteria
      flip green; iteration records) · **LogPanel** (streaming `Ctx.say`) · **DocsPanel** (inline
      markdown of any exposed build file) · **DescopePanel** (descope items + `abandoned.patch` pointer +
      caps). Stop (no-id `/api/stop`) + Resume buttons; Langfuse "Traces ↗" link. Rebuild after frontend
      changes: `cd frontend && npm install && npm run build` (recommit `dist/`). DECISIONS #28.
- **Acceptance:** a run is watchable live over an SSH tunnel; Stop/Resume works from the UI; history +
  descope report render; headless core/contract unchanged; localhost-bound (no public bind).
- **S1 ✅ server-validated** (2026-06-24, see above). **S2 server check:** rebuild the image (committed
  `dist/` is `COPY src`'d in), `$DC --profile web up -d broker web`, tunnel `ssh -N -L
  8181:127.0.0.1:8181 …`, open `http://127.0.0.1:8181` → start a fixture build, watch the slice board +
  log stream live, Stop→Resume, open a historical build's docs + descope.

## M4 — breadth
- [x] **S1 `refine` flow** (2026-06-25, ✅ SERVER-VALIDATED over the tunnel) — `core.refine_build(build_id, *,
      coder_override)` re-attacks a FINISHED build's descoped backlog on a stronger coder. Backlog-only
      refine graph (`build_refine_graph`: iterate→critic→docs→cleanroom→emit, P0–P3 skipped), reuses the
      persisted workspace + already-authored red-first staged tests (pinned `IterationPlan.test_file`,
      staged in ONE-at-a-time via `refine_pending/` so a still-red criterion never blocks the cumulative
      gate), `refine_mode` disables iteration-0 strict-red-first (post-scaffold code). Per-call coder
      rebind = `models.set_role_alias` (process-local, NOT a global `.env` change; `same_family` sees it
      too). Critic bar UNCHANGED (respec/replan pinned to caps → fix→descope; DECISIONS #28). Wired into
      CLI (`refine <id> [--coder ROLE]`) + web UI (✦ Refine button on a finished build with descopes).
      Local: `run_spine_tests.py` (**131**, +12 `test_m4_refine.py`). DECISIONS #29.
      **Server result (2026-06-25):** `refine poc-…104121-57e82f` re-attacked all 4 non-`met` criteria,
      moved 2 to `met` (incomplete→**done**, demonstrates=yes), re-emitted; the critic `descope→next`'d
      2 gameable greens live (respecs=1/replans=1 caps held). The CLI path is proven; the met-flip came
      via re-verification (`met-existing`) on the BASE coder. **Deferred residual:** a genuinely stronger
      coder *solving* a hard descoped criterion (needs a frontier endpoint — no code change). Also noted:
      langgraph emits "unregistered msgpack type" deserialization WARNINGS when refine recovers the
      checkpoint (Spec/Plan/IterationRecord) — harmless now, "blocked in a future version" → register
      `allowed_msgpack_modules` or pass typed state. Web-UI ✦ Refine button not yet exercised (same core).
- [~] **S2b key-proxy + S2c red-team beats** (2026-06-25 — testable core built, infra/beats pending).
      DESIGN RESOLVED with the user: on-prem vLLM is KEYLESS (`not-needed`), so a key-proxy "over vLLM"
      would guard nothing → reframed honestly. The key-proxy is the REAL control for key-requiring
      providers (OpenAI/Claude); demonstrated here with a **canary** (a planted stand-in secret the VM
      must never see). Built + green: `security/keyproxy.py` (pure `swap_authorization` sacrificial→real,
      deny-on-mismatch, `redact`; + a stdlib reverse-proxy `serve()` for the container) and
      `security/findings.py` (`scan_sandbox_env` Finding-0: prove no orchestrator secret reached the VM,
      reports by PLACEHOLDER). Local: `run_spine_tests.py` (**145**, +6). DECISIONS #31.
      both model endpoints (the main coder + the distinct-family critic) are reachable but only the main
      endpoint is in the sandbox allowlist (correct — the critic runs orchestrator-side).
- [x] **S2b-infra key-proxy container + opt-in broker provisioning** (2026-06-25, ✅ SERVER-VALIDATED —
      4/4 beats GREEN incl. key-proxy) — OPT-IN via `PF_KEYPROXY_UPSTREAM`; normal builds byte-for-byte unchanged
      (everything guarded). The broker (`provision`) spins a per-build key-proxy (`_provision_keyproxy`):
      dual-homed (egress + internal, by IP), running the app image's `python -m poc_foundry.security.keyproxy`,
      generates a per-build rotatable SACRIFICIAL token (replaces the static `vllm_key`), passes the REAL
      key (env, daemon-side) + upstream + sacrificial to the proxy, and injects `PF_SANDBOX_MODEL_BASE_URL`
      (the proxy) into the VM (`_build_vm_env`). Image harness-fixed/allowlisted (rule #8; `_make_broker`
      adds it when enabled, `_provision_keyproxy` rejects+audits a non-allowlisted one). Reaped in
      `_destroy`. Demo gains a 4th beat (`analyze_keyproxy`): from inside the VM, the sacrificial token →
      200, a wrong token → 401, the real key absent — proven on the keyless box with a canary as the
      "real" key. Local: `run_spine_tests.py` (**157**, +5). DECISIONS #33. Override.example documents the
      `PF_KEYPROXY_*` env + the one-time `build app`.
      **Server result (2026-06-25):** `cli demo-security` ran **4/4 GREEN** with `PF_KEYPROXY_UPSTREAM` = the
      vLLM root + `PF_KEYPROXY_REAL_KEY` = a canary — the key-proxy beat showed inference 200 (real key
      swapped in) / wrong-token 401 / the canary absent from the VM. Gotcha fixed pre-validation: the
      key-proxy IP must be in the VM's `NO_PROXY` (else the VM's `http_proxy` routes the HTTP call to squid,
      which denies the internal IP); upstream must be the ROOT (no `/v1`, else a doubled path 404s).
- [x] **S2c `demo-security` CLI + 3 live beats** (2026-06-25, ✅ SERVER-VALIDATED — 3/3 GREEN) —
      `core.security_demo()` (headless, rule #5) provisions a broker and runs `security/demo.run_demo`:
      beat-1 **canary/Finding-0** (`exec("env")` → `parse_env` → `findings.scan_sandbox_env` against
      `scrub.collect_secrets()` + the planted canary; PASS = nothing leaked), beat-2 **egress containment**
      (`exec` a curl to a non-allowlisted host → `findings.egress_denied(proxy_log)`; PASS = `TCP_DENIED`
      + blocked), beat-3 **broker rejection** (an off-allowlist `create` raises `BrokerInvariantError` +
      lands in `broker.audit()`). Pure analyzers fakes-tested; canary redacted from every shared output;
      a `beat` event per beat for the web tab. CLI `demo-security [--canary]`. Local:
      `run_spine_tests.py` (**149**, +4). DECISIONS #32. Canary via `PF_DEMO_CANARY`.
- [x] **S2d Security-Demo web tab** (2026-06-25, ✅ SERVER-VALIDATED — renders 3 beats live) — a Builds /
      Security-demo tab switch (`App.tsx`); the Security tab POSTs `/api/security-demo` (→ new
      `RunManager.security_demo`, single slot, `_run_demo` runner) and renders the 3 beats live from the
      streamed `beat` events (reuses the M3 SSE seam; `useEventStream` gains `beats[]`). `frontend/`
      rebuilt on the dev box, `dist/` recommitted. Local: `run_spine_tests.py` (**150**, +1
      RunManager.security_demo fakes test). DECISIONS #32.
- [x] **S3 `docs/PLATFORM.md`** (2026-06-25) — the workshop teaching artifact: poc-foundry as a worked
      example of the wiki's patterns (Vertical-Slices, Validation-Contract, Verifiers-Rule,
      Harness-Engineering, Doom-Loop avoidance, Skills/ACE, defense-in-depth, observability/evals), each
      pattern cited to a real module. Generic (rule #1).
- [ ] JS template (if npm granted) · multi-service composition · eval harness v2
- [x] **S2a daemon-side invariant-rejection audit log** (2026-06-25, ✅ SERVER-VALIDATED) — the broker
      (rule-#8 enforcer) records every rejected `create*`/`create_service`/`provision` (+ provision/destroy
      lifecycle) APPEND-ONLY via `sandbox/audit.py` to `PF_BROKER_AUDIT_LOG` (the daemon owns the file on
      the shared `pf-broker` dir → durable + readable INDEPENDENT of the orchestrator). No secret ever
      enters a record (Finding-0). `audit` RPC + `RemoteBroker.audit()`; p7 emit merges rejections into
      `security.incidents[]` as `[high]`. Local: `run_spine_tests.py` (**139**, +8 `test_m4_security.py`).
      DECISIONS #30. **Server:** set `PF_BROKER_AUDIT_LOG` on the `broker` service (override.example
      updated); the live rejection beat (S2c) reads this file as un-bypassable evidence.
- **Acceptance:** the security demo runs the beats with proxy logs as evidence; `demo-security` CLI. ✅
- **M4 ✅ COMPLETE (2026-06-25, all server-validated):** S1 refine · S2a audit · S2b key-proxy core +
  infra · S2c `demo-security` (4 beats) · S2d web tab · S3 `docs/PLATFORM.md`. Remaining = explicitly-
  optional breadth only (JS template, multi-service composition, eval harness v2) + the logged residuals.

## M5 — pilots, real model-calling builds, breadth (design §7)
The shift from "works on fixtures" to "produces real, useful PoCs from real Stage-2 artifacts."
- [x] **A1 RAG pilot — first real artifact end-to-end ✅ SUCCESS (2026-06-29, server-validated).** Final
      run: `status=done`, `demonstrates_core_value=yes`, **all 4 criteria met** (incl. query-dependent
      retrieval — different topics cite different doc ids), **clean run** (fixes=0/respecs=0/replans=0),
      integrity ledger OK (14 test ids) + 0 incidents, clean-room install=test=demo=True, ~10 min. The
      non-degraded critic `accept→next` on GENUINE retrieval evidence each pass; fix #6's re-author guard
      fired live (`authored test did not parse — re-authoring once`). **The platform does the RAG floor,
      cleanly.** Getting here took six thin fixes (below).
      Ran `dra-20260615-112222-fa650c-m` "Self-hosted RAG over a private document store" →
      `gradio-rag-pgvector` on the server. **The first real-artifact build surfaced a structural weakness**
      (the point of the pilot ladder): the non-degraded critic correctly rejected every iter0 as
      *gameable* (citation-marker-present tests a constant stub satisfies) → respec→replan→descope churn
      (the degenerate loop). **Root cause + fix = DECISIONS #34:** a bar mismatch — the tester was told
      only "a naive echo stub must fail" while the critic rejects any "trivial stub unrelated to the
      criterion." Fixed author-side (pure-string): `tester_prompt`, `spec_prompt`, and
      `playbooks/testing.md` now demand **DISCRIMINATION** (a constant return value must FAIL; contrast a
      should-fire vs should-not input) — which the RAG scaffold already supports, so the coder's real glue
      passes it. **Two follow-on slices the re-runs then exposed (all DECISIONS #34):** (2) the architect's
      structured spec hit the 4000 `max_tokens` default → `LengthFinishReasonError`; bumped `p1_spec` to
      8000 (the critic call already used that headroom). (3) **corpus grounding** — the architect invented
      facts the template's FIXED `core.CORPUS` can't retrieve (e.g. "$2.4M Aurora budget") → coder stuck;
      added a generic opt-in `knowledge` field on templates, injected into spec+tester prompts (the RAG
      note steers tests to import `core.CORPUS` and assert a verbatim phrase from a retrieved doc → proves
      real retrieval, no fact-duplication). **(4) critic recalibration (gate change; planning chat
      unavailable, on the user's direction).** Even with the ideal grounded criterion (coder reached green),
      the non-degraded critic respec'd it as "a lookup stub could satisfy it without pgvector" — an
      IMPOSSIBLE bar for a black-box test, making the RAG floor unbuildable. Recalibrated
      `critic_adequacy_prompt` to judge OBSERVABLE behaviour only (adequate when no CONSTANT stub passes;
      may NOT demand proof-of-mechanism / reject on a hypothetical lookup table) — **teeth retained** (the
      original presence-only test is still rejected; security gates untouched). Hardened the RAG knowledge
      note to verify the snippet against the doc the reply ACTUALLY cites (ranking-robust). **(5)
      citation-format pin** — the staged test was UNSATISFIABLE (`_find_cited_doc` parsed a `[doc-N]` string
      vs the scaffold's `[<int>]`); pinned the format + int-id in the knowledge note + anchored the coder to
      compose the helpers. **(6) fence-robust extraction** — a ` ```python ` info-string variant leaked the
      markdown fence into `test_iter_2.py` → SyntaxError → iter unbuildable; loosened `_CODE_BLOCK` + strip
      stray fences + `compile()`-and-re-author-once. **RESULT (server, 2026-06-29): iter0 (core) + iter1
      `RED→GREEN` with the non-degraded critic `accept→next` on GENUINE retrieval evidence — the platform
      does the RAG floor.** (iter2 exposed fix #6; a `replan` wastefully re-attacks met iterations — logged
      residual.) Local: **168 fakes** (+14 `test_m5_pilot.py`) + contract 11 + hygiene clean. DECISIONS #34.
      With fix #6 the re-run went **`done` 4/4 with zero churn** (see the success line above).
- [ ] **A2 MCP pilot** — new template (chatbot + small MCP server, crisp tool-call assertions; §7 pilot 2).
- [ ] **A3 Durable-Agent-Execution pilot** — kill-and-resume LangGraph demo (§7 pilot 3).
- [x] **B model-calling template ✅ SERVER-VALIDATED (2026-06-29).** `gradio-rag-llm` built from the real
      RAG artifact → `status=done`, `demonstrates_core_value=yes`, **all 4 criteria met** — the platform's
      FIRST PoC that actually calls the vLLM (<reasoning-model>) to GENERATE grounded answers (code-appended `[N]`
      anchor). One honest `respec` (critic rejected a keyword-gameable test set, then `accept→next`'d a
      stronger one), `fixes=0`, 0 incidents, clean-room install=test=demo=True (demo now LAUNCHES the UI),
      ~22 min (reasoning-model cost). Validated B0 endpoint-injection + the reasoning-model `max_tokens`
      fix + the gradio pin/demo-gate (#36) end-to-end. **CORRECTION (#37):** the first two "done" builds
      were actually riding the snippet FALLBACK — `_answer` failed at `OpenAI()` construction on an
      openai/httpx `proxies` break, silently swallowed by the coder's `except`. Pinned `httpx<0.28`
      (template + sandbox image) → hand-verified the re-built bundle genuinely calls the model (`4.2s` +
      a real LLM paraphrase); added a model-connectivity guard to the template smoke so a broken call now
      FAILS the build instead of silently degrading. **B is genuinely model-calling as of #37.** Remaining
      optional: run with `PF_KEYPROXY_UPSTREAM` set to exercise the key-proxy in a real model-calling
      build (the path is proven; just not run here).
      **#37 (model call) + #38 (real embeddings):** hand-testing exposed (a) the LLM call was silently
      failing on an openai/httpx `proxies` break → pinned `httpx<0.28` + added a model-connectivity guard;
      (b) retrieval QUALITY was poor (the deterministic HASH embedding sent natural queries to the wrong
      doc) → switched `gradio-rag-llm` to REAL semantic embeddings (`fastembed` + `BAAI/bge-small-en-v1.5`,
      384-d) baked into the sandbox image (fully offline, no user endpoint) with a calibrated cosine
      threshold (0.4). Self-sufficient per the design goal. **✅ VERIFIED DONE (2026-06-29):** after a
      chain of real env fixes (offline model-bake in the sandbox image; construct-only model-connectivity
      guard so the scaffold smoke stays fast/offline; grounding asserted via answer↔cited-doc lexical
      overlap), the build went `done`/`yes` with the non-degraded critic explicitly CERTIFYING grounding
      — a green build now means "retrieves the right doc + grounds a real LLM answer in it." DECISIONS
      #37/#38. **B0 ✅ (DECISIONS #35):** the
      broker now injects `PF_SANDBOX_MODEL_BASE_URL` (the egress-allowlisted endpoint) into EVERY VM, not
      just when the key-proxy is on. **B1 ✅ local (DECISIONS #35):** new `gradio-rag-llm` template —
      pgvector retrieval + a REAL `_answer()` model call, with the `[<int>]` citation CODE-appended as the
      deterministic test anchor (tests assert structure, never the model's prose); `openai` pinned + baked
      into the sandbox image. Local: **171 fakes** + contract 11 + hygiene; preflight resolves+pins.
      **B2 (server, pending):** rebuild the sandbox image (`build sandbox`), `template-ci --preflight`,
      then a real build → confirm the PoC calls the model + emits `done` with the deterministic anchor.
- [ ] **C deferred breadth** (only as the pilots need it): multi-service composition · eval harness v2 ·
      depot caches · JS template (rule-#3 conversation first).
- **Acceptance:** a real Stage-2 artifact yields a PoC that genuinely *demonstrates core value* (human
  grade of the report); weaknesses surfaced are fixed as thin slices; zero leaks, green bar green.

## M6 — harness generalisation (thin-template autonomy probe) 🟡 IN PROGRESS (2026-06-30)
Goal: prove the harness can assemble a RAG PoC with only FRAMEWORK-level help (rails + primitives as
libraries), not pre-written solution logic — the litmus test for handling cutting-edge artifacts.
Local green bar: `run_spine_tests.py` (189) + contract (11) + hygiene.
- [x] **Thin RAG template** `gradio-rag-thin` — same rails as `gradio-rag-llm` (Gradio, pgvector sibling,
      offline embedder + model call exposed as `search`/`llm`/`_embed`/`CORPUS`), but `generate_reply` is
      a stub and the `knowledge` note is a contract, not a recipe. Thick one kept for comparison. (DEC #39)
- [x] **Run #1 (untuned) — INCONCLUSIVE but revealing.** Every criterion descoped at the cap, never green;
      root cause was the harness DISCARDING the evidence (reflection + research fed the meta-message
      `fix-attempt cap reached`; the coder's edits never applied because the parser rejected multi-block
      reasoning output). DECISIONS #39.
- [x] **Diagnostics fixes (general harness):** multi-block edit-extraction (largest fence) · `last_response`
      + non-blank `last_output` · reflection grounded in the real failure · research skips meta-messages
      (`_looks_like_error`) · forensic trail on descope (`_persist_iter_forensics`). Fakes `test_m6_diagnostics.py` (6).
- [x] **Run #2 — verdict: coder writes passing RAG glue unaided BUT games toward a toy** (reinvents a fake
      keyword retriever, never calls `search`/`llm`, whole-file-rewrites `core.py` and deletes scaffold
      exports → clean-room break → `incomplete`). Gates held: grounding criteria correctly descoped the toy.
      Forensics fixes validated on the server. DECISIONS #40.
- [x] **General fixes (kit+glue + Tier-0 directive + interface gate).** `gradio-rag-thin` restructured to
      kit+glue (`ragkit.py` non-editable primitives + editable `core.py` glue); coder `SYSTEM` gains a
      domain-agnostic "use the provided helpers, don't reinvent/amputate" directive (rung 0 of a graduated
      guidance ladder); `coder._interface_problem` reverts any edit that drops the declared interface.
      Fakes `test_m6_diagnostics.py` (10). Green bar 182.
- [x] **Run #3 — kit+glue WORKS: the coder composes REAL RAG** (`from ragkit import …` → `search` →
      relevance gate → `llm` grounding; no toy). Clean-room `install=test=demo=True`; one criterion met. But
      `incomplete` because the bottleneck moved to TESTER quality: a buggy `str`-vs-`int` citation test that
      is unsatisfiable by construction (coder diagnosed it but can't edit the test; critic doesn't catch
      buggy/strict tests) + the coder relied on the LLM for citations instead of code-appending. DECISIONS #41.
- [x] **Buggy-test recovery rung (general).** New §5.8 ladder rung: when the coder spends its WHOLE fix
      budget without green, re-author the staged test ONCE (coder's diagnosis → tester) with a FRESH fix
      budget, before replan; re-gated by red-first + critic, bounded by `reauthor_cap` (default 1). Ladder:
      fix → **re-author** → replan → descope. `prompts.tester_prompt(diagnosis=…)`, `state.reauthor_*`,
      `cfg.reauthor_cap`. Fakes (13). Green bar 185.
- [x] **Run #4 — rung didn't fire (trigger too narrow), corrected.** It was gated on "stuck" (repeated
      error signature); the coder's errors VARIED, so it descoped via fix→replan without re-authoring. Also
      saw the critic correctly `respec`/`descope` several too-WEAK/gameable tests + grounding pass twice.
      Fixed: fire on fix-budget-exhausted (not "stuck"). DECISIONS #41 follow-on.
- [x] **Run #5 — re-author rung fired & worked, but exposed the deep problem.** `core.py` did
      `from ragkit import CORPUS` only — reinvented a keyword `_search`, ECHOED the doc, never called
      `search`/`llm`. The echo-TOY again; passed 3/4 criteria because a black-box test can't tell echo from
      real RAG. Critic caught the core one but hit the respec cap → `incomplete/no`. DECISIONS #42.
- [x] **#1 anti-toy discrimination (general, verification-led).** Generalised the bar from "a constant stub
      must fail" → "a SHORTCUT (echo/keyword/lookup/constant) must fail; passing must require GENERALISATION
      (e.g. a paraphrased input the data's own words don't cover)". GATE CHANGE: critic now rejects
      echo/lookup-passable tests (keeps mechanism-agnosticism; dropped the hard "default to adequate"). Tester
      told to write the generalisation case. Symmetric **weak-test re-author rung** (dual of #41): green-but-
      gameable → strengthen that test before respec. Fixed stale re-author log wording. Green bar 187. DEC #42.
- [x] **Run #6 — #1 works at the critic; bottleneck moved to the tester.** The critic now explicitly
      rejects shortcut/lookup tests ("a stub that looks up the top doc via `search` and returns a word would
      pass without real RAG"). But the tester couldn't reliably write a test that's shortcut-proof AND
      red-first-valid AND satisfiable (oscillated: too-weak → red-first violations ×4 → ledger gap). Exposed a
      bug: red-first violation REUSED the bad test instead of re-authoring. DECISIONS #43.
- [x] **Red-first→re-author fix.** A red-first violation now re-authors the test with feedback (bounded),
      instead of looping on it. Green bar 188.
- [x] **Tool-calling pilot `gradio-tool` (cross-domain; design §7 MCP pilot, first slice).** kit+glue
      (`toolkit.py` = `call_tool`+`llm`+`CATALOG_PRODUCTS`, editable `core.py`) + a private tool sibling
      (`toolserver/`: stdlib HTTP, pinned `poc-foundry-toolserver:0.1.0`) with OPAQUE prices the model can't
      know + a `/calls` audit. Shortcut-proof discriminator = the tool's exact opaque value. DEVIATION FLAGGED:
      HTTP tool sibling, not MCP wire protocol (first slice). Preflight resolves; allowlisted; green bar 189. DEC #43.
- [ ] **Run #7 (server, pending) — FIRST tool-pilot build (untuned, NEW domain).** `$DC --profile images build
      toolserver`, then build `--template gradio-tool` (with a tool-shaped `--brief`). Does the loop compose a
      real `call_tool` (a price the toy can't fake) — proving the general machinery holds outside RAG — or
      game/descope? First-attempt outcome is the honest cross-domain signal.
- **Acceptance:** a clear, evidence-backed verdict — either a verified green (the loop genuinely composed RAG
  using the real primitives, proven by a generalisation test a toy can't fake) or a documented capability gap.
