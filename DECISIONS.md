# DECISIONS

Running log of non-trivial choices and rationale for **poc-foundry** (Stage 3 of the 3-part
GenAI-scout system). Newest entries appended over time. Keep this file GENERIC — no server IPs,
hostnames, or served-model ids (those live only in the gitignored `.env`).

The canonical architecture is `../stage3-planning/STAGE3_DESIGN_v1.6.md` (frozen; §10 is the
resolved-decision ledger). This log records implementation choices made while *building* that design,
plus any sanctioned stopgaps.

---

## #1 — Inherited v1.6 design; ledger adopted (2026-06-21)

Stage 3 starts from the **frozen v1.6 design** (`STAGE3_DESIGN_v1.6.md`), which survived 7 adversarial
review passes. Its §10 resolved-decision ledger is **adopted as-is**: gVisor→Kata; LiteLLM-proxy ban
(OpenCode is an M0-only control arm, not a pipeline component); deterministic-outside/agentic-inside;
test-author separation + red-first + 6 integrity walls; curated start-green templates; gradio-first;
production sibling services (no embedded-only, no DinD); fresh-VM-per-iteration; schema via SHA-pinned
dep after the extras split; broker stub-M1/real-M2a + the create-param invariant; clean-room
thin-M1/gates-M2; NOT_BUILDABLE + DONE floor + final verdict + demo_quality; customer descope =
ladder-then-report (no mid-build interrupt); two-tier playbooks; depot additive profile; M0 spikes a–d.

The hard rules (`AGENTS.md` §"Hard rules") are inherited verbatim from Stage 2 and are
non-negotiable. Deviations from the §10 ledger require new evidence + a planning-chat consult via the
user. Implementation details (file layout, naming, internal APIs) are decided freely and logged here.

## #2 — Stage-2 schema: vendor-copy stopgap for M0/M1 (was: SHA-pinned dep) (2026-06-21)

**Design end-state (unchanged):** Stage 3 imports the `DeepResearchArtifact` schema from
`ai-engineer-research` as a **SHA-pinned git dependency** (`git+https://github.com/<org>/...@<sha>`),
installed only at image build, so the schema can't silently drift from Stage 2 (design §5.10; ledger).

**Stopgap decided with the user (this session):** for M0 (and M1 if the dep isn't confirmed pushed),
**vendor a copy** of Stage 2's `artifact/` schema into `poc-foundry` instead of resolving the git dep.
Rationale:
- The schema module is **pydantic-only** (verified: Stage 2's base install is schema-only since its
  2026-06-15 extras split) — copying drags in nothing heavy and stays `py_compile`-able on the 3.10
  dev box.
- Resolving the git dep right now is **blocked** on facts only the user can supply: the GitHub
  org/URL and whether the extras-split commit is pushed (the dep resolves via `git+https`). Vendoring
  unblocks M0 entirely.
- The handover already sanctions a dep stopgap ("use `--no-deps` until the extras split lands"); this
  is the same spirit, simpler.

**Guardrails so this stays a stopgap, not drift:**
- The vendored copy carries a header recording **source repo + the exact commit SHA** it was copied
  from, and is marked "mirror — do not edit."
- A **drift check** (diff vendored vs the local `../ai-engineer-research` source) + a **contract
  test** (a committed sanitized sample artifact must validate against the vendored schema + the
  semantic invariants from the contract §6) catch divergence.
- **Migration trigger:** when the user confirms Stage 2 is pushed to GitHub at a known SHA, swap the
  vendored copy for the one-line SHA-pinned dep and delete the mirror. No other rework — only the
  import source changes. This is sequencing, NOT a reversal of the ledger decision; permanently
  abandoning the dep would require a planning-chat consult.

## #3 — Docker images: app / sandbox / proxy (Slice 2) (2026-06-21)

Three locally-authored images (built on the server, rule #3); conventions mirror Stage 2's validated
compose (no top-level `version:`, external `depot-net`, `env_file`, source mounts, NFS pre-create).

- **App (orchestrator)** `python:3.12-slim` + `build-essential git curl`; installs `.[runtime,obs]`
  (the `[runtime]` extra is REQUIRED — base is schema-light, the Stage-2 extras lesson). Holds
  secrets. The docker-socket mount + docker SDK for the broker stub are deferred to M1 (risk-accepted
  there), so this image is not on the M0 path.
- **Sandbox** `python:3.12-slim` + `git uv pytest ruff`; non-root `builder` uid 1000 (predictable
  virtio-fs ownership — an M0(a) probe item); `UV_CACHE_DIR=/uv-cache` (per-build named volume at
  runtime); `GRADIO_ANALYTICS_ENABLED=False`. Runs under `--runtime kata`, fresh VM per iteration.
- **Proxy** `debian:bookworm-slim` + `squid` via apt (Debian mirrors reachable on the build server,
  per Stage 2). Default-deny allowlisting CONNECT proxy; public allowlist tracked in `squid.conf`
  (mirrors `pipeline.yaml`); the **single vLLM private-host exception is generated at startup by
  `entrypoint.sh` from `PF_VLLM_ALLOW_HOST`** so the real host/IP never lands in a tracked file (IP
  vs hostname auto-detected). CONNECT lines log to stdout (broker tees to `builds/<id>/logs/`).

**Proxy = squid** (over tinyproxy): battle-tested allowlist ACLs + native CONNECT logging = the
deterministic security evidence the design wants. Chose apt-installed squid over a third-party Hub
image to avoid trusting an unvetted image and to control the config surface. Python pinned to 3.12
for both runnable images (≥3.11 needed; uniform); drop to 3.11 if a runtime wheel won't build on
3.12. `sandbox`/`proxy` are compose **build/smoke targets** only — at runtime the broker spins them
per build (never long-running compose services).

## #4 — M0 spike scripts: egress (d) + Kata probe (a) (Slice 3) (2026-06-21)

Both M0(d) and the M0(a) probe are authored as **self-asserting scripts** (PASS/FAIL + exit code) so
the user can run the whole M0 surface in one pass on the server.

- **`m0d_egress_spike.sh`** builds the real topology — a Docker `internal: true` network (no route
  out) + a dual-homed `poc-foundry-proxy` (egress leg + internal leg) — and asserts ALLOW (PyPI,
  GitHub clone, `uv` install, the vLLM exception) and DENY (non-allowlisted host, non-vLLM RFC1918,
  direct egress with no proxy, direct RFC1918) by exit code, then prints the CONNECT log as evidence.
  Runs the **client under runc** (this spike is about networking, not VM isolation). The real vLLM
  host is masked in output (safe to paste back). RFC1918 deny-probe uses `10.255.255.1` (a
  likely-unrouted address) to avoid hitting a real service.
- **`m0a_kata_probe.sh`** covers the Kata-specific items under `--runtime kata` (virtio-fs uid/gid,
  nested RO `tests/`, writable junit, named-volume uv cache, caps + `sandbox_cgroup_only` note) and
  **reuses the egress spike under `PF_SPIKE_RUNTIME=kata`** for the VM→proxy→vLLM reachability item
  — one source of truth for the topology, no duplication. In-guest memory-cap enforcement is a
  best-effort WARN (the known #12203 caveat: host-side cgroup is authoritative).
- **squid rule-order fix:** the env-injected vLLM exception (`include vllm.conf`) is evaluated BEFORE
  `http_access deny !Safe_ports`, so a vLLM call on a non-standard port (e.g. 8000) isn't rejected by
  the Safe_ports (80/443) deny. Caught while authoring the spike.

## #5 — M0(c) ingest: vendored schema + ingest/validate + contract tests (Slice 4) (2026-06-21)

Executes DECISIONS #2's stopgap. `src/poc_foundry/artifact/` mirrors Stage-2's `artifact/`
(`schema.py`/`store.py`/`validate.py` **byte-identical**; `__init__.py` trimmed to drop the heavy
`extract` import — Stage 3 only reads). Provenance + sync in `artifact/_VENDORED.md`; drift via
`scripts/check_vendored_schema.sh` (plain diff — confirmed IN SYNC with the local source).

`src/poc_foundry/ingest/` adds `load_run(run_dir)` (path-based loader: latest `vNN.json` +
tolerated-absent companions) and `validate_semantics()` (contract §6: citation resolution [error],
reproducibility enum [error], generated_at ISO [error]; fetched_at/last_commit ISO + origin enum +
confidence range [warn]) + `clamp_confidence()` (the not-hard-clamped `[0,1]` caveat).

**Verified locally (3.10 + pydantic, no pytest):** `scripts/run_contract_checks.py` = 11/11 GREEN;
the ingest probe loads the sample fixture clean. The pytest file `tests/test_contract.py` runs the
same in-container.

**Sequencing call (surfaced to user):** the handover lists "run P1 spec generation on the fixture"
under M0(c), but generating a spec needs the architect LLM + the P1 phase — i.e. pipeline code,
which M0 is supposed to be free of. So the ingest/validate half of M0(c) ships now (provable); the
spec-quality half lands with P1 at the start of M1. Not a deviation — an honest overlap in the
handover's milestone boundaries.

## #6 — M0(b) coder bake-off harness (Slice 4) (2026-06-21)

Self-contained diagnostic under `scripts/m0b_bakeoff/`, reusing patterns validated in
`../poc-builder-prework` (stdlib `model_client.chat`, container `Sandbox` exec, JSON traces).

- **`CoderEngine` seam** with two impls (the winner takes the seat at M1): `BespokeEngine` — a
  code-owned bounded loop (prompt → apply edit → verify in sandbox → feed failure back), edit-format
  A/B (whole-file `*** FILE:` markers vs unified diff applied with `patch -p1/-p0`), error-signature
  tracking with a forced strategy change on repeats (escalation ladder §5.8); `OpenCodeEngine` —
  scripted headless `opencode run` (control arm). Coder edits **orchestrator-side**; the sandbox only
  executes (the design claim).
- **OpenCode arm uses `opencode run` (CLI), not the serve/HTTP API** — simpler to script and matches
  the design's "headless opencode run". bash/webfetch must be DENIED via opencode.json (prework
  lesson: OpenCode bash runs on the serve host). If the binary is absent the arm is skipped and the
  bespoke arm still runs (attribution then notes "no control arm").
- **4 tasks:** 3 planted breaks (syntax / wrong-api `json.parse`→`loads` / failing-test median-bug) +
  1 red-first feature (`slugify`) — a repair-only probe gives a false 'go', so the feature task is
  the real signal. All small, stdlib, deterministic; reference solutions confirmed to pass.
- **Attribution (fixed rule):** both-fail ⇒ model-weak (frontier reroute); bespoke-fails/
  OpenCode-passes ⇒ loop-weak (fix our loop); plus an edit-format recommendation. `run.py` prints the
  matrix + saves a trace; the human logs the engine decision here after the server run.
- **Verified locally (pure parts):** edit parsers, refuse-to-edit-test guard, diff apply, signature
  stability, attribution branches, task solvability. Model + Docker arms are server-only.

## #7 — M0(c) + M0(d) GREEN on the server (first on-hardware run) (2026-06-22)

First on-server M0 run validated two of the three gate spikes.

- **M0(c) ingest GREEN:** the in-container contract check (`run_contract_checks.py`) passed `11/11`
  on the server — vendored schema loads, semantic invariants hold, the import stays heavy-stack-free.
  (Real-fixture probe + the P1 spec half still pending; P1 overlaps M1.) One packaging fix: the app
  image now COPYs `tests/` (+ a compose mount) — it was missing → `NotADirectoryError` in-container.
- **M0(d) egress GREEN: 8/8.** `internal:true` network + dual-homed allowlisting CONNECT proxy.
  ALLOW: PyPI / GitHub clone / uv / the vLLM endpoint — all THROUGH the proxy. DENY: a non-allowlisted
  host (`TCP_DENIED/403`), non-vLLM RFC1918, direct egress (no route), direct RFC1918. The CONNECT log
  is the deterministic security evidence (`TCP_TUNNEL/200` allowed, `TCP_MISS/200` vLLM exception,
  `TCP_DENIED/403` denied). The whole egress trust model (default-deny, single named private-host
  exception, sole-exit proxy, logged) is validated on real hardware.
- **Proxy boot took 4 squid-in-Docker fixes** (DEV_NOTES "full saga"): ACL subdomain-overlap is FATAL
  in squid 5+; squid refuses root AND a non-root user can't reopen the container's `/dev/stdout`
  (→ run as `proxy`, log to a file, `tail -F` it to stdout); `squid -z` wrote a PID file that aborted
  the real `squid -N` (→ drop `squid -z`, `pid_filename none`). All in `docker/proxy/`.
- **Gate status:** (c)✅ (d)✅ ; (a) Kata + (b) bake-off still to run. (a)(b) → then M1.

## #8 — M0(b) DECISION: bespoke loop takes the CoderEngine seat (2026-06-22)

First on-server bake-off (the on-prem model via the `coder` role, runc). Results — tasks passed:
**bespoke 4/4 (whole-file) · bespoke 4/4 (unified diff) · OpenCode 1/4.**

| task | kind | bespoke whole | bespoke diff | OpenCode |
|---|---|---|---|---|
| fix_syntax | syntax | P(1) | P(1) | F(4) |
| fix_wrong_api | wrong-api | P(1) | P(1) | F(4) |
| fix_logic | failing-test | P(1) | P(2) | P(1) |
| feature_slug | red-first feature | P(1) | P(1) | F(4) |

**Decision (the fixed rule applied): the bespoke bounded loop takes the M1 `CoderEngine` seat; OpenCode
is the fallback.** Rationale via the attribution matrix: NO task showed "bespoke-fails / OpenCode-passes"
(the only "loop-weak" signal). Bespoke solved every break type + the red-first feature, mostly
single-attempt → the loop is strong AND the model is a strong coder (no model-weak signal either). This
is the result M0(b) was designed to surface, and it clears the confound the OpenCode control arm exists
to resolve.

**Edit format: whole-file is the default; diff stays available.** whole=4/4 all single-attempt; diff=4/4
but fix_logic needed 2 attempts → whole-file is marginally more reliable on this model, while diff is
clearly usable (the model handles unified diffs). M1 keeps the edit-format-adaptive seam (whole for
weaker models, diff for stronger), defaulting to whole.

**Honest caveat on OpenCode's 1/4 (do NOT over-read it):** OpenCode passed fix_logic but capped out on
the three *easier* tasks — inconsistent with `poc-builder-prework`'s finding that OpenCode + the model one-shots
single-component tasks. That points to an artifact in THIS M0(b) OpenCode adapter (the scripted
`opencode run` invocation / model binding), NOT a real OpenCode weakness. It doesn't change the decision
(bespoke won outright; OpenCode was only the diagnostic control). Flag: revisit the OpenCode adapter when
the continuation bundle / `refine` flow needs OpenCode for real (M4) — don't debug it now.

## #9 — M0(a) Kata: probe results + the Kata-DNS finding (2026-06-22)

Kata already registered (poc-builder-prework Phase 3). Smoke: kata guest kernel **6.18.28** ≠ host
**6.8** → real VM proven. Probe items 1–5 GREEN: virtio-fs uid/gid (uid 1000 maps correctly), nested
**RO `tests/`** enforced ("Read-only file system"), writable junit, named-volume uv cache persists,
memory cap has effect. `sandbox_cgroup_only` on cgroup v2 stays an INFO caveat (host-side cgroup
authoritative; never claim hard in-guest isolation — #12203).

**Load-bearing finding (item 6):** a **Kata guest VM does not get Docker's embedded name-DNS**.
`HTTPS_PROXY=http://pf-spike-proxy:3128` (by container *name*) fails inside the Kata sandbox
("Could not resolve proxy"), though the VM is correctly on the `internal:true` net (direct-egress +
RFC1918 denials still hold). Under runc the same name resolves. **Resolution (also the M1 pattern):
the broker injects the proxy's internal-network IP, not its name** — `HTTPS_PROXY=http://<proxy-ip>:3128`.
`m0d_egress_spike.sh` now derives the proxy IP via `docker inspect` and passes under both runtimes.
Extends to sandbox↔sibling-service hops (Milvus/pgvector by IP from a Kata VM) — note for M2a. Full
detail in DEV_NOTES "Kata networking". Rerun pending to confirm 7/7 (the fix is verified by-design;
the runc 8/8 already proved the proxy logic).

## #10 — M1 S1: package relocate + the typed spine (2026-06-22)

Started M1 (walking skeleton) with the foundation, sliced so each piece stays testable.

- **Package relocate (clean layout before building on it):** the vendored Stage-2 INPUT schema moved
  `src/poc_foundry/artifact/` → `src/poc_foundry/stage2_artifact/`, freeing `artifact/` for the
  Stage-3 OUTPUT `PoCBuildArtifact` (design §4.3 reserves `artifact/` for the output). Updated the few
  importers (`ingest/`, contract test, `run_contract_checks.py`, `check_vendored_schema.sh`). Two
  distinct schemas no longer share a package name. Contract tests still 11/11; drift check still works.
- **Output contract `PoCBuildArtifact`** (`artifact/schema.py` + `store.py`): the FULL flat schema per
  design §4.3 defined now (additive-only), even though M1 only populates a subset — it's the contract.
  `new_build_id()` mints `poc-<ts>-<rand6>`; versioned `vNN.json` under `builds/<id>/`.
- **`BuildState`** (`state.py`): the typed, checkpointable LangGraph state + `Spec`/`Plan`/
  `IterationPlan` sub-models. Flat + serializable (paths as str) so the SQLite saver round-trips it.
- **`BuildConfig`** (`config.py`): pipeline.yaml budgets/templates + env (PF_*, gitignored .env); env
  wins; pyyaml tolerated-absent. **`models.py`**: `build_chat_model(role)` (lazy ChatOpenAI, for
  architect/critic/scribe structured calls) + `chat_text(role,…)` (stdlib raw, for the coder's tight
  loop); role-triple + PF_DEFAULT_ROLE fallback; no model names in code (rule #4).
- **Verified on 3.10:** artifact + state pydantic round-trip; config loads; `import poc_foundry.models`
  pulls no langchain. **Remaining M1 (S2–S5):** broker, CoderEngine, phases+graph+core, gradio
  template — deferred to a fresh chat (handover) to keep a clean token budget for the broker (~300–500
  lines) + the orchestration wiring.

## #11 — M1 S2–S5: broker, CoderEngine, phases+graph+core, gradio template (2026-06-22)

The walking skeleton end-to-end. All authored locally; verified by `py_compile` + pure-Python
dry-runs with fakes (no Docker/LLM locally); the real Kata/vLLM path is the server run. Built thin
per the slice discipline; structural calls stayed inside M1 scope (no §10 ledger items reopened).

- **S2 broker (`sandbox/broker.py`).** In-process Docker stub behind the stable interface
  `provision / create / create_service / exec / destroy`, promoting `m0b_bakeoff/sandbox.py` (subprocess
  `docker` CLI — the validated path). Per build it provisions the `internal:true` net + a normal egress
  bridge + the dual-homed squid proxy (reached **by IP**, M0(a)/DECISIONS #9) + a uv-cache volume, then
  spins **fresh Kata VMs** on demand. **Invariant (rule #8) is enforced in code:** `create*` validates
  image∈allowlist, caps⊆`ALLOWED_CAPS` (empty at M1), mounts = tame abs paths, names = tame tokens —
  each RAISES `BrokerInvariantError`. Only `Sandbox.exec(cmd)` carries LLM content. **M1 residual
  (risk-accepted):** in-process, driving the host docker socket → the app image now installs the
  `docker` CLI (Debian `docker.io`, build-time apt) + the socket is mounted in the override; M2a moves
  it out-of-process.
- **S3 CoderEngine (`coder.py`).** Promoted `BespokeEngine` → `BespokeCoder` behind a `CoderEngine`
  Protocol seam. Bounded loop, whole-file default (diff available, needs `patch`), error-signature
  tracking + forced strategy change on repeats. **Decoupled from the broker** via an injected
  `verify()` callable (the phase wires it to a sandbox) → unit-testable without Docker, and the edits
  are written ORCHESTRATOR-side (the design's orchestrator-writes / sandbox-executes claim). LLM via
  `models.chat_text("coder", …)`; an injectable `llm=` makes it testable.
- **S4 phases+graph+core+cli.** `phases/` = `context.py` (Ctx, Template loader, git/mount/run-block
  helpers, `chown_to_builder`) + `pipeline.py` (P0…P7). `graph.py` wires them with **LangGraph**
  (linear + two short-circuits: ingest-failed and NOT_BUILDABLE → emit, scaffold-failed → emit) and a
  **SQLite checkpointer** keyed by `thread_id == build_id`. `core.build_poc(source, …) ->
  (report_md, PoCBuildArtifact)` is the stable headless contract (+ `resume_build`, `list_builds`,
  `clean_build`); a `build_meta.json` records the source/template so resume can reconstruct ctx and
  invoke with `None` (LangGraph resume). Thin argparse `cli.py` (build/list/resume/clean).
- **S5 template (`templates/gradio-chatbot/`).** Start-GREEN: pure `core.generate_reply` + a thin
  `gr.ChatInterface` `app.py` (analytics off, import doesn't launch), pinned `gradio==4.44.1`, a smoke
  suite, seeded RUN/README/AGENTS/.gitignore, `template.json` manifest (editable_files, interface,
  stack). RUN.md uses `<!-- pf:install|test|demo|run -->`-marked bash blocks the clean-room extracts.

**Decisions taken inside M1 (logged, not ledger deviations):**
1. **P2 plan is DETERMINISTIC for M1** (one iteration synthesized from the spec's core criterion;
   interface pinned from the template) rather than an architect LLM call. Fewer failure points for the
   skeleton; architect-driven multi-iteration planning lands when iterations matter (M2a). Honest note
   surfaced; revisit at M2a.
2. **Red-first is best-effort at M1.** P4 runs the staged test ONCE before the coder; if it's already
   green against the scaffold stub, the iteration is recorded green with a caveat
   ("met by scaffold (not red-first)"). The integrity walls that *enforce* tester/coder separation +
   the inventory ledger are M2a.
3. **uid mapping:** the orchestrator runs as root in-container but the Kata sandbox is uid 1000 →
   `chown_to_builder` (best-effort, root-only) makes workspaces/clones/staged-tests writable by 1000
   so virtio-fs maps cleanly (M0(a)); root keeps write access regardless. Staged VERIFY runs with
   `PYTHONDONTWRITEBYTECODE=1` (the `/staged` mount is RO).
4. **Workspace path = same-path mount.** Sibling-container bind sources are HOST paths, so
   `PF_WORKSPACE_DIR` must be mounted at the identical path in the app container (override updated).
   Build workspaces live under `PF_WORKSPACE_DIR/<id>/` (local disk, kata-mountable); P7 copies the
   finished `workspace/` (with `.git`) into `builds/<id>/`.
5. **Clean-room install uses `uv pip install --target .deps` + `PYTHONPATH`** (not `--system`): the
   sandbox is non-root (the M0(b) `--system`-needs-root gotcha) — the validated egress-spike pattern.

**Verified locally (3.10, no Docker/LLM):** `py_compile` clean across the package; importing
core/graph/cli/phases pulls **no** langchain/langgraph; the template stamps + its core is start-green;
a fakes dry-run runs P0→P7 to `status=done` (RED→GREEN coder fired, clean-room green, artifact +
workspace emitted); NOT_BUILDABLE short-circuits to `not-buildable`; broker invariant guards raise;
`tests/test_m1_spine.py` 7/7 (via a pytest shim). Contract tests still 11/11.

**M1 GATE GREEN on the server (2026-06-22).** One fixture → `builds/poc-…/` with `status=done`,
`demonstrates_core_value=yes`, **clean-room install=test=demo all TRUE**, on real Kata + the egress
proxy + the model. P1 spec quality is good (the model produced a coherent 5-criterion RAG-citation spec, one core;
the M0(c) spec-grading tail is satisfied). Four server-only fixes were needed (all logged in
DEV_NOTES "M1 broker + pipeline" + the new gotchas):
1. **app image: install `docker-cli`, not `docker.io`.** Debian 13 split the monolith — `docker.io
   --no-install-recommends` ships `dockerd`/`docker-init` but **no `/usr/bin/docker`**; the broker
   shells out to `docker`. `docker-cli` is the client (lighter; we use the host socket).
2. **git `safe.directory` must be GLOBAL, not `-c`.** The orchestrator (root) git-operates on
   workspaces chown'd to uid 1000; git ignores `safe.directory` from `-c`/local config (security), and
   local-path `git clone` in particular needs it global → `ensure_git_global_safe()` sets it (root-only
   guard so a dev box is untouched) + the image sets it too.
3. **gradio dep pin.** `gradio==4.44.1` imports `HfFolder` (removed in `huggingface_hub>=0.26`); uv's
   latest resolve broke gradio at import in the clean-room `demo` step → pinned `huggingface_hub==0.24.7`.
   Lesson: start-green templates must pin the *transitive* break-prone deps, not just the headline one.
4. **drop `analytics_enabled=` from `gr.ChatInterface`** (env var is the version-stable disable).
Also hardened P6 to capture the failing clean-room step's output into `caveats[]`/report (that's how
fix #3 was diagnosed in one round-trip).

**Foundation-hardening pass (post-gate, before M2a)** — close cracks that would compound over M2a's
many iterations:
- **No resource leaks on partial provision.** `Broker.provision()` is now atomic — if any step fails
  (e.g. the proxy never reaches Running after the networks/volume exist), it calls `destroy()` and
  re-raises; `destroy()` unconditionally rm's the named resources (idempotent) so nothing leaks. Both
  `build_poc`/`resume_build` now call `provision()` INSIDE the try so a provision failure also gets a
  forensic artifact + teardown. Regression-tested (`test_broker_partial_provision_cleans_up`).
- **Empty-criteria spec ≠ crash.** A buildable spec with zero success criteria would crash P4 on
  `criteria[0]`; P1 now flips it to NOT_BUILDABLE ("architect produced no testable criteria"). Tested.
- Spine suite now 8/8.

**M1 COMPLETE → M2a (gates: tester/critic, ledger, scanner, clean-room GATES, out-of-process broker,
sibling services).**

## #12 — M2a S1: integrity walls + the local no-pytest test runner (2026-06-22)

First M2a slice — the three integrity walls that make a build *trustworthy*, plus the tooling to
prove gate logic locally. Authored on the 3.10 box; verified by `py_compile` + the fakes suite; the
real Kata/vLLM path is the server happy-path re-run.

- **Local test runner (the "shim", `scripts/run_spine_tests.py`).** The handover lists
  `tests/test_m1_spine.py` as a *local* check, but the 3.10 box has no pytest (rule #3) and no shim
  existed. Built a tiny pytest shim (a `raises` ctx-mgr + a `monkeypatch` fixture with undo + a
  `tmp_path` fixture) + a `test_*` discovery runner — mirrors `run_contract_checks.py` for the spine.
  NOT a pytest replacement (only the surface the fakes suites use); in-container real pytest runs the
  same files. This makes the handover's "every new gate gets a fakes test, validate locally first"
  workflow real.
- **`phases/integrity.py` (pure).** Inventory-ledger parsers (`collected_names` from
  `pytest --collect-only -q`; `junit_passed_names` from a junit xml; `inventory_ok`/`inventory_gap`),
  the `scan_diff` diff-scanner, and `Incident`/`blocking`. Stdlib-only → `py_compile`-able + unit-
  testable. **Ledger comparison is by test-function NAME** (the final `::` segment), which sidesteps
  brittle rootdir/path/classname normalization between collect-only output and junit, and still
  catches deleted/renamed/skipped tests.
- **`p4_iterate` wiring.** (1) **Ledger record:** `--collect-only` in the pristine pre-coder VM →
  authored test names. (2) **Red-first enforcement (flips M1's best-effort):** if the staged test is
  GREEN against the scaffold, that's tester-inadequacy → `red_first_ok=False` + a high-sev incident +
  the criterion is NOT met (M1 used to accept it with a caveat). (3) **Diff scanner runs INSIDE the
  coder's `verify()`** (per-attempt): it scans `git diff <base>` before running pytest, so a tampering
  edit fails the attempt → the coder's error-signature path forces a strategy change, and the incident
  is recorded. (4) **Inventory ledger verify:** after the coder reaches green, an authoritative
  `--junitxml` run (cat'd back through `exec`, so the gate needs no host-fs coupling — keeps it fake-
  testable) must show collected∧passed ⊇ recorded, else `inventory_ok=False` + the criterion is
  descoped.
- **Status gating.** New `_trustworthy(state)` = `inventory_ok ∧ red_first_ok ∧ no high-sev incident`.
  `_final_status` returns `done` only if (core met ∧ clean-room suite_ok ∧ trustworthy);
  `demonstrates_core_value` likewise. So a gamed build reports `incomplete`, never `done` — the M2a
  acceptance. `tests.inventory_ok` + `security.incidents[]` populated; report.md gains an Integrity
  section.
- **State additions (additive):** `authored_test_ids`, `inventory_ok`, `red_first_ok`, `incidents`.
- **Why scan inside `verify()` (not just post-loop):** it gives per-attempt enforcement + the forced-
  strategy-change for free (a repeated tamper = a repeated failure signature), matching the design's
  "a positive hit fails the attempt + forces strategy change", with no change to the `CoderEngine`
  seam (the coder stays test-agnostic; the scanner rides the injected verify callable).
- **Verified locally:** `run_spine_tests.py` = **24/24** (8 spine unchanged + 16 gates: ledger/junit/
  scanner parsers, the `_final_status` gate, and **3 planted-gaming pipeline tests** — a trivially-
  green test, an inventory-ledger gap, and a hard-exit-gaming coder — each CAUGHT and blocked from
  `done`). Contract still 11/11; import hygiene clean (no langchain/langgraph at load). Updated the
  M1 fake `_FakeSandbox` to model the new collect-only/junit queries (a fake must track the real
  sandbox's new interface).
- **Deferred to later M2a slices (logged, not deviations):** the **adequacy review** (does the suite
  actually exercise the criterion / is it gameable-trivial?) is the critic's job → S2; per-iteration
  cumulative regression suite → S2/S3; the on-SERVER adversarial demo (a planted gaming run end-to-
  end) lands with S2's verdict routing or a planted-mode flag — S1 proves the catch via fakes + keeps
  the server happy-path green with the walls in place.

## #13 — M2a S2: critic gate + verdict ladder + degraded-critic mode (2026-06-22)

Second M2a slice — the critic gate that sits around P4 and decides the iteration's fate, with a
verdict ladder and honest degraded-mode handling. Authored locally; 32/32 fakes; server = the
happy-path re-run.

- **New `p_critic` node + `_after_critic` LangGraph routing.** The M1 graph was linear
  (`iterate → docs`); now `iterate → critic → {fix:iterate, respec:spec, replan:plan,
  pass/descope:docs}`. The graph has **real cycles** for the first time; termination is guaranteed by
  state counters the critic caps (`fix_count` vs `fix_limit_k`/`degraded_fix_limit_k`, `respec_count`
  vs `respec_cap`, `replan_count` vs `replan_cap`) plus a `recursion_limit=60` on `graph.invoke`
  (core.py). This is an implementation/topology call (not a §10 ledger change — the verdict SET is in
  the ledger); logged, no planning consult needed.
- **Division of labor (kept P4 server-proven, added the critic on top).** P4 still does
  tester→red-first→coder→ledger and sets the criterion met/descoped from the *mechanical* outcome. The
  critic adds the JUDGMENT layer: on a green iteration it runs an **adequacy review** (is passing the
  test trustworthy evidence, or is it gameable?); on a non-green outcome it runs the **failure ladder**
  (fix → replan → descope). So a mechanically-green-but-weak test can be downgraded, and a coder
  failure gets bounded retries before an honest descope. P4 exposes `pending_test_src`/
  `pending_criterion` for the critic to review.
- **Degraded-critic mode (design §5.4) — and a principled honesty call.** `models.same_family(a,b)`
  compares the resolved served-model ids; on this server ALL roles are one on-prem model → **degraded=True**.
  Degraded mode lowers the fix budget K (`degraded_fix_limit_k=2` vs `fix_limit_k=3`) and sets
  `security.degraded_critic=true`. **Decision:** in degraded mode the critic's **adequacy verdict is
  ADVISORY (recorded as a caveat), never blocking** — a critic sharing the coder's family cannot
  *independently* certify the test, so it must not respec/descope on its own judgment. The hard walls
  (inventory ledger / red-first / diff-scanner, S1) still gate; the trivially-true-test case is caught
  by red-first regardless. Blocking adequacy returns automatically when a distinct frontier `critic`
  endpoint is configured (`same_family → False`). Rationale: (a) preserves the M1/S1-proven server
  happy path — a same-family critic won't accidentally descope its own good 5-test suite; (b) is
  honest about what a degraded critic can certify; (c) matches §5.4's "critic-independence is
  contingent on frontier egress; until then degraded mode applies and is recorded."
- **Critic adequacy never crashes the build.** `_critic_adequacy` defaults to `adequate=True` if the
  critic endpoint is unreachable — the critic is an ADDED layer, never the sole gate.
- **Config wiring.** `BuildConfig` gains `fix_limit_k`/`respec_cap`/`degraded_fix_limit_k`/`replan_cap`
  from `pipeline.yaml critic:` (env-overridable `PF_FIX_LIMIT_K` etc.). `descope_report[]` +
  `security.degraded_critic` populated in P7; report.md gains Critic + Descope sections.
- **Verified locally (32/32 fakes):** adequate-green→pass; inadequate-green→respec then descope at
  cap (+ `descope_report` + criterion descoped); coder-failure→fix×K→replan→descope; integrity
  incident→descope (never rewards gaming, even with an "adequate" test); degraded→advisory caveat +
  lower K; `_after_critic` maps every verdict. Spine 8/8 unchanged (happy path flows through the
  critic to `done`). Contract 11/11; import hygiene clean.
- **Deferred (logged):** the **cumulative regression suite** (every iteration runs all prior tests +
  the new one) is meaningful only once P2 is multi-iteration → lands with **S3**; the architect-driven
  multi-iteration plan + clean-room status GATES are S3; the respec prompt does not yet feed the
  critic's `suggestion` back to the architect (refinement; the cap guarantees termination regardless).

## #14 — M2a S3: multi-iteration loop, cumulative suite, clean-room publish/GATE (2026-06-23)

Third M2a slice — the graph loops P4 over a multi-iteration plan under a cumulative regression gate,
and the clean-room finally runs the criterion tests (not just the template smoke). The biggest call
here was a **structural finding surfaced by slice-and-validate**, logged for the planning chat.

- **P2 = deterministic core-first multi-iteration (architect decomposition DEFERRED).** P2 emits one
  small iteration per testable criterion, the CORE criterion first (it gates `done`), capped at
  `max_iterations`. **Decision: NOT architect-LLM-decomposed**, despite the handover's "architect-
  driven" wording — DEV_NOTES/Stage-2 evidence is explicit that the on-prem model is a *weak self-
  planner*; classifying given criteria into an ordered iteration list is reliable and deterministic,
  open decomposition is not. This is a handover-instruction deviation (not a §10 ledger item) made on
  recorded evidence; flagged to the user for a planning-chat call on whether architect-grouping is
  worth adding later. The IterationPlan/loop machinery is fully multi-iteration-capable, so swapping in
  an architect grouping later is additive.
- **The loop.** `iterate → critic → {next:iterate(advance), fix:iterate(same i), respec:spec,
  replan:plan, proceed:docs}`. The critic now owns iteration advancement: `accept`/`descope` resolve
  the criterion and emit `next` (i+1, fresh fix budget) or `proceed` (plan exhausted). P4 no longer
  bumps `iteration`. fix budget K is **per-iteration** (reset on `next` and on P2/replan). Termination:
  capped counters + recursion_limit=60.
- **STRUCTURAL FINDING (the load-bearing one): naive multi-iteration trips red-first on a one-shot
  PoC.** The chatbot fixture's criteria are facets of one `core.py`; splitting them into iterations
  means later iterations' tests pass against earlier iterations' code. Strict red-first would flag those
  as tester-inadequacy and (via the trust gate) block `done` → a REGRESSION of the proven happy path.
  **Resolution: red-first is strict ONLY at iteration 0 (against the scaffold echo-stub).** For i>0, a
  green-pre-coder test means "criterion already met by prior iterations' implementation" → status
  `met-existing` (a real pass, no coder run), not a violation. This yields a genuine incremental build
  (iter0 builds the core RED→GREEN; later iters either extend RED→coder, or are already satisfied) and
  preserves the happy path. The gaming-catch for later iterations is still covered by the diff-scanner,
  the cumulative ledger, and the critic. Logged for the planning chat as the multi-iteration semantics.
- **Cumulative regression gate.** Per iteration the tester's test is staged as `test_iter_i.py`
  (ACCUMULATED, not overwritten); the coder's `verify()` runs the WHOLE `/staged` suite, so a later
  iteration that breaks an earlier criterion fails and the coder must fix it. The inventory ledger
  collects/junit over all of `/staged` (cumulative authored ⊆ passed).
- **Clean-room publish + GATE.** After the loop, P5 copies MET iterations' staged tests into
  `workspace/tests/` (descoped/abandoned tests are NOT published — the clean-room must not fail on a
  criterion we honestly descoped). The clean-room's `python -m pytest -q` (CWD on path → `import core`
  works) then re-runs the template smoke + the published criterion suite. `_final_status` already
  required `cleanroom.suite_ok` for `done`, so a red clean-room can never be `done` — the
  "clean-room gates a known-bad build red" acceptance, now with the REAL criterion tests in the clone
  (previously only the trivial smoke ran there).
- **Verified locally (35/35 fakes):** a faithful in-process fake sandbox (actually RUNS the staged
  test functions, no pytest needed) drives a 2-iteration build to `done` — iter0 core RED→GREEN via the
  coder, iter1 met-existing, both tests published; plus unit tests for `next` advancement (+ fresh fix
  budget), met-existing acceptance, the full verdict ladder under the new `proceed`/`next` vocabulary.
  Spine 8/8; contract 11/11; import hygiene clean.
- **Known caveats (logged):** the inventory ledger compares by test-function NAME (S1, server-proven on
  a single 6-test file); across multiple `test_iter_*.py` files a NAME collision (the tester reusing a
  generic `test_basic`) could mask a deletion — node-id (`stem::name`) hardening is a candidate if the
  multi-iteration server run shows name reuse (kept name-based for now to not perturb the proven path).
  Replan re-plans ALL criteria fresh (not just unmet ones) — a valid simplification bounded by
  `replan_cap`. `tests_total` is an approximate file-count + 1 (the real count is the clean-room pytest
  output).

## #15 — M2a S4a: out-of-process broker (orchestrator drops docker.sock) (2026-06-23)

Closes the M1 residual (the orchestrator held the host docker socket). Authored locally; the RPC path
is fully fakes-tested over a real Unix socket; the real Kata path is the server run via the override.

- **Topology.** A `BrokerDaemon` (`sandbox/daemon.py`) is the ONLY process mounting `/var/run/docker.sock`;
  it wraps the real `Broker` (one per `build_id`) and is the sole enforcer of the create-param
  invariant (rule #8). The orchestrator holds a thin `RemoteBroker`/`RemoteSandbox` (`sandbox/client.py`)
  that implements the SAME `provision/create/create_service/exec/service_ip/proxy_log/destroy` surface
  but forwards every call over a newline-delimited-JSON RPC on a Unix socket (`sandbox/rpc.py`). The
  phases use `ctx.broker` unchanged — they don't know which broker they hold.
- **Why the guard stays daemon-side (load-bearing).** The invariant is authoritative in the daemon's
  `Broker`, NOT re-checked in the client — a client-side check would be bypassable by a compromised
  orchestrator. A violation raises `BrokerInvariantError` daemon-side; the rpc layer carries an
  `error_type` so the client re-raises the SAME type. Fakes-proven
  (`test_broker_rpc_invariant_raises_same_type_client_side`).
- **Transport choices.** Unix socket (not TCP) so access is filesystem-gated (a shared dir bind-mounted
  into app + daemon), `0o660`. **Connection-per-call** + a **single-threaded** daemon accept loop:
  the pipeline is sequential and Ops runs one build at a time, so serializing every docker op + guard
  needs no locking and has no races. `rpc.call` retries the connect for ~15s (rides out the
  depends_on/startup race) and uses a generous read timeout (> the longest `exec`, ~1800s uv install).
- **What the daemon does NOT need.** Only the daemon mounts docker.sock. It does NOT mount the
  workspace — bind sources passed to `docker run -v <hostpath>:/work` are resolved by the HOST daemon,
  and the orchestrator already writes those files at the same host path (the same-path-mount invariant,
  now spanning app→daemon→host). So the orchestrator keeps doing all workspace writes + git
  (orchestrator-writes); the daemon only does docker ops (sandbox-executes). Clean split.
- **Opt-in, default-unchanged.** `PF_BROKER_SOCKET` selects the remote path; unset → the in-process
  `Broker` (the M1-proven default). So the existing path is untouched and the new boundary is enabled
  by the compose override (a `broker` service holding docker.sock; `app` drops docker.sock + gains the
  broker-socket dir + `depends_on: broker`). De-risks the cutover.
- **Verified locally (38/38):** the RPC fakes start the real daemon (with a fake docker engine via the
  `broker_factory` seam) on a real Unix socket and drive provision→create→exec→service_ip→proxy_log→
  create_service→destroy, assert the invariant re-raise, and assert `RemoteBroker` exposes the same
  surface as `Broker`. Contract 11/11; import hygiene clean (no docker SDK — CLI only).
- **Deferred to S4b:** `create_service` made REAL (a vetted sibling, pinned tag, reached BY IP) + a
  service-using template that proves the path end-to-end. The seam (`create_service` RPC method) is
  already wired through the daemon/client; S4b fills in the template + the by-IP wiring in a phase.
- **Server GREEN (2026-06-23):** build `done` via the override (out-of-process broker, 5 iterations,
  clean-room green); the orchestrator container has **no docker.sock** (`test -S /var/run/docker.sock`
  → GOOD); zero build-resource leaks (only the long-lived `pf-broker` daemon, removed by `compose
  down`). Socket dir lives under `/var/tmp/pf-broker` (not `/var/run`, which is a root-locked tmpfs).
  **M2a headline acceptance MET** (gaming caught S1/S2 · clean-room gates S3 · broker out-of-process
  S4a). Remaining for full M2a: S4b sibling-service build.

## #16 — M2a S4b: real sibling services + the gradio-rag-pgvector template (minimal-real) (2026-06-23)

The last M2a piece — a build that uses a REAL vetted sibling service end-to-end. Scoped as
"minimal-real" with the user: prove the infra path thoroughly (spin → by-IP → clean-room → no leaks)
without gold-plating the demo app. Authored locally; fakes lock the wiring; the real pgvector
round-trip is the server run.

- **`create_service` made real (broker).** Added a readiness wait (`_await_service`: poll Running +
  an optional `ready_cmd` like `pg_isready`, fail loud with logs). Runs under the DEFAULT runtime (a
  stock vendor image is infra, not the Kata build env). Reached BY IP (`service_ip`, same Kata-DNS
  rule as the proxy). Threaded `ready_cmd` through the daemon + client RPC.
- **Image/tag are HARNESS-FIXED (rule #8).** A template only NAMES which vetted service it wants
  (`template.json services: [{name, vetted}]`); the image + pinned tag come from `pipeline.yaml
  vetted_services` (pgvector pinned to `pg16`), resolved by the pipeline — never from artifact/model
  output. `cfg.service_refs()` adds `image:tag` to the broker allowlist; `create_service` checks it.
- **Service lifecycle in the pipeline.** P3 (`_spin_services`) spins declared services ONCE per build
  (idempotent on replan), records `PF_SERVICE_<NAME>_HOST=<ip>` (+ service env like the PG password)
  on `ctx.service_env`; P4 + P6 pass it as `env_extra` so iterations AND the clean-room reach the
  sibling by IP. Services persist across iterations (design §5.6); reaped by `broker.destroy()` at
  build end (leak-safe). P7 records `services[]`.
- **The `gradio-rag-pgvector` template.** Retrieval over REAL pgvector (Postgres + `vector`), with a
  **deterministic stdlib hashing embedding** (no model, no network) so retrieval is reproducible +
  unit-testable; the similarity search runs in pgvector (`embedding <-> %s::vector`). Decision: the
  scaffold ships the DB plumbing (`_connect`/`_ensure_corpus`/`search`) WORKING + a STUB
  `generate_reply` → the smoke test is stdlib-only/DB-free (start-green), iteration 0's retrieval
  criterion is RED against the stub (red-first holds), and the coder's job is the tractable glue
  (wire `generate_reply` to `search` + format a `[id]` citation), NOT writing psycopg/SQL from
  scratch. Self-seeding idempotent corpus → a fresh clean-room DB self-seeds on first query.
- **`psycopg[binary]` baked into the sandbox image.** Iterations run the criterion tests but do NOT
  `uv pip install` (only the clean-room does); so the common sibling DRIVER is baked in (same
  rationale as pytest/ruff already are). Pinned in `requirements.txt` too so the emitted bundle
  installs standalone. Needs a one-time `docker compose build sandbox` on the server.
- **Spec prompt is service-aware** (`spec_system(has_services)` / `spec_prompt(..., services)`): when
  the template declares a service, the architect is told the PoC uses a real provided sibling (tests
  still verify via `generate_reply`), instead of the old "no services, stdlib only" constraint.
- **Clean-room uses the per-build service** (IP injected) rather than recreating a fresh one — the
  idempotent self-seed makes that equivalent for the proof; a truly-fresh clean-room DB
  (design §5.6 "recreate from scratch") is a noted refinement.
- **Robust to coder imperfection:** if a non-core criterion (e.g. a relevance threshold) descopes, the
  build is still `done` when the CORE retrieval criterion is met + the clean-room (published green
  tests) passes — the descope mechanism (S2) handles it honestly.
- **Verified locally (42/42 fakes):** config exposes the pinned ref; the template declares its service;
  P3 spins pgvector with the fixed image/tag/ready_cmd + records the IP + password; the iteration
  sandbox receives `PF_SERVICE_PG_HOST`. The pgvector template's `_embed` is deterministic/normalized
  (ran directly). Contract 11/11; hygiene clean (psycopg lazy-imported, not at module load).

## #17 — Salvage fix: abandoned iterations must roll back to the last green commit (2026-06-23)

**Found by the first S4b server run (a successful test — it caught a real bug + the clean-room gate
worked).** The pgvector build proved the sibling path end-to-end (service spun + ready @ IP; iter0/iter1
RED→GREEN against REAL pgvector; service reaped, ZERO leaks) — but came out `incomplete` because the
**clean-room suite failed**, correctly refusing `done` for a build whose final code was broken.

- **The bug (multi-iteration, not pgvector-specific).** P4 committed ONLY on a green iteration. When a
  later iteration was abandoned (coder hit the fix cap — here the "no-citation-for-absent-topic"
  criterion, which needs a relevance threshold), the coder's **last failing uncommitted edit to
  `core.py` stayed in the working tree**. A subsequent `git add -A` commit (P5's publish, or a
  met-existing iteration) then swept that broken code into HEAD → P6 cloned it → the clean-room ran
  broken `generate_reply` (returned the scaffold's "couldn't find" stub for every query) → suite RED.
  So every criterion showed `[met]` (each passed in its OWN iteration) yet the final committed code was
  broken. The clean-room GATE caught it (status `incomplete`) — exactly its job.
- **The fix (design §5.8 salvage).** P4 now, on a non-green iteration (`crit_status != "met"`), runs
  `git reset --hard HEAD` to discard the failed coder's uncommitted edits — the workspace ALWAYS
  reflects the last green commit. `reset --hard` reverts tracked files only (untracked `.deps`/caches
  survive). Green commits + met-existing (no edits) are unaffected. So an abandoned/descoped iteration
  leaves the tree at the last sound state; P5 commits only the published tests on top; P6 clones sound
  code. With this, the same pgvector build should be `done` (core retrieval + iter1 met; iter2 descoped
  + NOT published; clean-room runs the published green tests against pgvector).
- **Regression guard:** `test_abandoned_iteration_rolls_back_to_last_green` (faithful fake: iter0 green
  emits `M0 only`, iter1's coder writes `BAD` that breaks the cumulative suite → abandoned → assert the
  workspace `core.py` is `M0 only`, not `BAD`). 43/43 fakes; contract 11/11.
- **What the run also validated:** out-of-process broker + the sibling-service infra are sound — the
  failure was purely the workspace-pollution bug, downstream of the gates working.

## #18 — M2b S1: emitted-output hygiene scrubber (`scrub.py`) (2026-06-23)

Closes the last open rule-#1 item: a build BUNDLE (shared with a human) must contain no vLLM
host/IP, served-model id, API key, or NFS/model/workspace path. `builds/` is gitignored (not a
public-repo risk) but the bundle is the share unit, so the scrubber runs at EMIT.

- **Pure, env-driven, never hardcoded.** `collect_secrets()` reads the sensitive values from the
  process env (`.env` already loaded by `config`) + the gitignored `build_env.json` sidecar,
  classifying each `KEY=value` by suffix (`_MODEL` → `<served-model-id>`; `_API_BASE`/`_ENDPOINT` →
  `<vllm-host:port>` for the full URL + authority and `<vllm-host>` for the bare host; `_API_KEY`/
  `_TOKEN`/`_PASSWORD` → `<redacted-key>`; `_ALLOW_HOST` → endpoint; `_HOST` → `<service-host>`;
  `*PATH*`/`_DIR`/`_ROOT`/`*NFS*`/`_SOCKET` → `/path/...`). No value is hardcoded — an unconfigured
  run is a clean no-op.
- **Conservative + deterministic.** Only literal values present in the run's config are rewritten
  (never mangles unrelated prose like `localhost:7860`); the substitution list is deduped and applied
  **longest-value-first** so `host:port` wins over its bare-`host` substring. Sentinels/generic tokens
  (`not-needed`, `localhost`, `postgres`, …) and <4-char tokens are skipped to avoid false positives.
- **Wired at the two emit seams.** `p7_emit` calls `scrub.scrub_build_dir(build_dir)` AFTER writing
  all outputs (artifact `v*.json`, `report.md`, `00_INDEX.md`, `PROGRESS.md`, `logs/*.log`);
  `core._emit_failed` scrubs the forensic crash artifact (a phase-crash traceback embeds the
  endpoint/id/paths). Placeholders are quote-free so the artifact JSON stays valid after scrubbing.
- **Verified locally (48/48 fakes; +4 in `tests/test_m2b_scrub.py`):** fake host/model/key/paths in →
  gone, placeholders present, generic URLs untouched, JSON still parseable, no-secrets = no-op.
  *Server check: run a build, then `scripts/check_hygiene.sh` against a `builds/<id>/` sample (its
  dynamic layer greps for the real `.env` values) — expect HYGIENE clean on the emitted text.*

## #19 — M2b S2: budget/cap enforcement + contention indicator (2026-06-23)

The M2a residual: caps were DEFINED but never enforced; `caps_hit[]` / `budget` never populated.

- **One process-global meter at the `models.py` choke point.** Every role call goes through
  `build_chat_model` (structured) or `chat_text` (coder/tester/scribe raw) — so `models.METER`
  (`_Meter`) counts there: `count()` BEFORE each call (raises if over cap), `record_latency()` after
  the `chat_text` HTTP round-trip. `build_chat_model` counts at construction (1:1 with its single
  `.invoke` in this codebase). `core.build_poc` calls `METER.begin_run(cfg)`; `p4_iterate` calls
  `METER.begin_iteration()` (fresh per-iter budget; the run counter accumulates).
- **Primary budgets = call counts (deterministic under load); wall-clock = backstop.** Enforces
  `max_llm_calls_per_iter`/`_per_run` + `max_iter`/`max_run_wall_clock_s` (all `config`-loaded,
  env-overridable; 0 disables). `contention_indicator` = the median observed call latency.
- **`BudgetExceeded` is a `BaseException`, ON PURPOSE.** The phases wrap model calls in broad
  `except Exception` (the coder loop, the critic-adequacy fallback, the scribe) — a budget breach must
  ESCAPE those to halt the whole RUN. `core._invoke_with_salvage` catches it and `_salvage_run`
  recovers the last checkpointed `BuildState` (via `graph.get_state`), rolls the workspace back to the
  last green commit (`git reset --hard HEAD`, the #17 pattern), records the cap in `caps_hit[]`, and
  emits an honest `incomplete` (the clean-room never ran → can never be `done`). The forensic
  `abandoned.patch` + the descope-report entry are S3.
- **`p7_emit` populates** `budget{wall_s, llm_calls, contention_indicator}` (from `METER.snapshot()`,
  safe even when the meter was never begun → all-zero) + `caps_hit[]`; the report gains a Budget
  section. The "targeted research escalation" rung (§5.8) needs the research phase → stays an M2c stub.
- **Verified locally (56/56 fakes; +8 in `test_m2b_budget.py`):** the run/per-iter/wall-clock caps
  fire with the right cap name; the per-iter counter resets while the run counter accumulates;
  `BudgetExceeded` escapes `except Exception`; the disabled meter is a no-op; contention = latency
  median; `config` loads the 4 caps; `p7_emit` writes `budget` + `caps_hit`. Contract 11/11; hygiene
  clean. *Server: (a) a normal build shows `budget.llm_calls`/`contention_indicator` populated; (b)
  `PF_MAX_LLM_CALLS_RUN=3` forces a run-cap salvage → `status=incomplete`,
  `caps_hit=["max_llm_calls_per_run"]`, rolled back, ZERO leaks.*

## #20 — M2b S3: run-cap salvage — abandoned.patch + descope entry + gaps (2026-06-23)

Builds on the S2 (#19) salvage path: a run-cap breach now produces a forensic, human-finishable
incomplete (design §5.8), not just a bare `incomplete`.

- **`abandoned.patch`.** Before the rollback, `_salvage_run` captures the in-flight (un-merged) coder
  edits via `git_diff(ws)` (working-tree diff vs HEAD, incl. untracked) and writes them to
  `builds/<id>/abandoned.patch` (only when non-empty), so a human can apply + finish the abandoned
  iteration. THEN it `git reset --hard HEAD`s the workspace to the last green commit (#17 pattern), so
  the emitted `workspace/` is sound.
- **Descope entry.** A `descope_report[]` item is appended for the **first not-yet-met** criterion
  (the natural resume point), NOT `plan.iterations[state.iteration]` — the cap can fire on the critic
  call AFTER an iteration committed green, so that index may point at an already-met criterion (caught
  on the first server run: the entry named the `[met]` core — fixed + regression-tested). Spent
  attempts are charged only when that criterion is the one the in-flight iteration was working (else
  0). `why_failed = "run halted by budget cap: <cap>"`; `finish_path` names BOTH options: resume with
  a higher cap (state + workspace persist), or apply `abandoned.patch` + finish by hand in OpenCode.
- **`final_verdict.gaps`** (now populated for EVERY build, not just salvage): every criterion whose
  status != `met` (descoped / pending / partial). An honest gap list vs the spec — a `done` build has
  none; a `partial`/`incomplete` lists what's missing. Report gains a "Gaps vs spec" section; the
  index advertises `abandoned.patch` when present.
- **Verified locally (58/58 fakes; +2 in `test_m2b_salvage.py`):** a fake graph + a real tmp git
  workspace with an in-flight edit → `abandoned.patch` captures the in-flight code, the workspace is
  rolled back to green, the artifact is `incomplete` + `caps_hit` + a descope entry for the in-flight
  criterion + gaps (met criteria excluded); a clean-tree salvage writes no patch. Contract 11/11;
  hygiene clean. *Server: the `PF_MAX_LLM_CALLS_RUN=3` run (S2 case b) should now ALSO drop an
  `abandoned.patch` + a Descope-report / Gaps section in `report.md`.*

## #21 — M2b S4: cooperative stop + resume hardening (2026-06-23)

The M2a residual: checkpoint/resume existed but was untested; Stop was not wired.

- **Cooperative stop via a sentinel.** `poc-foundry stop <id>` (CLI) → `core.request_stop_build` →
  `control.request_stop` writes `builds/<id>/.stop`. `graph.build_graph`'s node `wrap` calls
  `control.raise_if_stopped(ctx.build_dir)` at EVERY node boundary → raises `BuildStopped` (a
  `BaseException`, same rationale as `BudgetExceeded` #19: must escape the phases' broad
  `except Exception`). The graph checkpoints AFTER each completed node, so a stop at the next node's
  start loses no state. `core._emit_stopped` recovers provenance from the last checkpoint and writes a
  lightweight `status: stopped` artifact (+ scrubbed) — the build is `resume`-able.
- **Resume hardening.** `resume_build` now `clear_stop`s the sentinel first (so a prior stop doesn't
  immediately re-trip), and the docstring states the model: a FRESH broker/VM is provisioned over the
  PERSISTED workspace + checkpoint (VMs are cattle; workspace + state are pets — design §5.9). Both
  fresh and resumed runs go through `_invoke_with_salvage`, so a resumed run is equally stop-/cap-able.
- **New CLI verb** `stop <id>`; `request_stop_build` added to the headless core.
- **Operability (found while server-testing):** (1) `ctx.say` now streams the phase trace to stderr
  (`PF_PROGRESS=0` silences it; the fakes runner sets it) so a long run is observable instead of dumping
  everything at the end. (2) A deterministic stop hook `PF_STOP_AT_NODE` (+ `PF_STOP_AT_NODE_HITS`)
  in `raise_if_stopped` stops at the Nth entry of a named node — so the kill/resume acceptance test is
  reproducible WITHOUT racing a manual Ctrl-C (the host can't write the root-owned `.stop` sentinel
  anyway). `resume` run without the env continues to completion.
- **Forward-compat note (deferred, non-blocking):** LangGraph warns "Deserializing unregistered type
  poc_foundry.state.Spec/Plan / artifact.IterationRecord … blocked in a future version." Resume WORKS
  today (server `get_state` succeeded); registering these in `allowed_msgpack_modules` is a small
  server-testable follow-up (can't `py_compile`-verify a langgraph API change on the 3.10 box → not
  shipping it blind). Tracked in DEV_NOTES.
- **Verified locally (63/63 fakes; +4 in `test_m2b_stop.py`):** the sentinel round-trip + node guard;
  `BuildStopped` escapes `except Exception`; `request_stop_build` writes the sentinel + resume clears
  it; `_emit_stopped` writes a resumable `stopped` artifact with recovered provenance. Contract 11/11;
  hygiene clean. **Server-validated (2026-06-23):** `PF_STOP_AT_NODE=iterate:2` build → `stopped`
  after iter0; `resume` (no env) continued from the iter0 checkpoint over the persisted workspace →
  `done` (5/5 criteria, clean-room green, output scrubbed); fresh broker re-provisioned; ZERO leaks.
- **Known minor (meter resets per process).** `budget.llm_calls` on a RESUMED run counts only the
  resume leg (the in-memory `METER` starts fresh per process; the pre-stop calls were in the prior
  process). Accurate cross-resume budgeting would persist the meter in `BuildState` — deferred (the
  caps still bound EACH leg; the cross-resume total is the only gap). Tracked in DEV_NOTES.

**M2b — resilience COMPLETE (2026-06-23):** S1 scrubber + S2 budgets/caps/contention + S3 run-cap
salvage (abandoned.patch + descope + gaps) + S4 stop/resume, all server-validated. → M2c.

## #22 — M2c S1: observability — `tracing.py` + manual spans (2026-06-23)

Design §5.11: Langfuse project `stage-3-poc` (separate keys from Stage-2) via the **tolerated-absent**
`tracing.py` pattern + **manual spans around the half a LangChain callback handler can't see** (broker/
exec/VERIFY/gates/critic/clean-room/proxy denials; and our LLM calls go through raw urllib in
`chat_text`, which no handler sees either).

- **Tolerated-absent (Stage-2 discipline).** `PF_TRACING` gates it (OFF by default); `langfuse` is
  lazy-imported (module stays `py_compile`-able + import-light on the 3.10 box); if disabled / dep
  absent / creds misconfigured, `get_tracer()` returns a no-op tracer and **every** `span`/`event`/
  `build`/`flush` is a safe no-op. Every real-langfuse call is additionally wrapped in `try/except` →
  a tracing fault degrades to no-op rather than crashing a build (rule: tracing must never take down a
  run). Flush-on-exit is mandatory (ephemeral `docker compose run`): `core.build_poc`/`resume_build`
  call `tracing.flush()` in `finally`.
- **Module singleton + injection.** A process-global `_current` tracer (lazy `_init_tracer`), with
  `set_tracer`/`reset_tracer` so the fakes inject a recording `FakeTracer` and assert spans fire at the
  right seams. Callers use module-level delegators `tracing.span(name, **attrs)` / `event` / `build` /
  `flush`. **Gotcha baked into the API:** `span(name, ...)` takes the span name positionally, so attrs
  must not be keyed `name=` (use `box=`/`svc=` for sandbox/service names) — caught by the broker test.
- **Seams instrumented.** root **build** (core, tags=[driver, template] / ["resume", template]);
  **broker.provision/create/create_service/exec/destroy** in BOTH the in-process `Broker`/`Sandbox`
  and the out-of-process `RemoteBroker`/`RemoteSandbox` (the server path — exec spans the RPC
  round-trip incl. VERIFY); **spec** (P1 architect); **iterate.verify** (cumulative pytest) +
  **gate.diff-scan** spans and **gate.incident** events (diff-scan + ledger-gap); **critic** (adequacy
  review); **cleanroom**; **llm.<role>** (chat_text); and a **proxy.denials** event (TCP_DENIED count
  parsed from the egress log at P7 — the detective egress control surfaced in the trace).
- **Authored blind → corrected on the server (langfuse 4.x, not v3).** No `langfuse` on the 3.10 box,
  so the API was first authored against v3. The server resolved **langfuse 4.9.1** (`pyproject` said
  only `langfuse>=3`), and v4 renamed the API — so every guarded span hit an `AttributeError` and
  no-op'd → an EMPTY trace even though `auth_check`/keys/host were fine. The guards did their job (no
  crash) but also HID the mismatch from the green bar — confirming "the server is the validation" for
  heavy-dep APIs. Fixed: `_LangfuseTracer` feature-detects `start_as_current_observation` (v4) vs
  `start_as_current_span` (v3); v4 has no `update_current_trace`/`update_trace`, so the trace name comes
  from the root obs name (`build/<id>`) and tags/session ride in `metadata`; `create_event`/`flush`
  unchanged. Pinned `langfuse>=4,<5` so it can't silently resolve onto another API-breaking major.
  (DEV_NOTES has the full v4 surface + an unguarded-SDK debug one-liner.)
- **Shared server, per-app SDK; dedicated project (done).** poc-foundry traces to the SAME on-prem
  langfuse instance as stage-2 (easier to access; a 4.x client against the v3 server image works). A
  dedicated **`stage-3-poc`** project was created with its own keys (in `.env`) so stage-3 traces don't
  pollute stage-2's — NO code change (`tracing.py` is project-agnostic; the `PROJECT` constant is only
  a label).
- **The long tail was ALL shared-infra (service-depot), not poc-foundry.** Validating the live trace
  surfaced three distinct langfuse-server outages: (1) clickhouse + (2) minio containers orphaned off
  `service-depot_default` by a stray `docker network/system prune` (it deletes the network under
  running `restart:always` containers → they keep running with zero net attachments → DNS for
  clickhouse/minio breaks while the postgres path survives), then (3) a post-`down/up` `:3000`
  connection-refused. Each fixed in service-depot (`./depot down/up` reattaches everything; a prune
  guard prevents recurrence). The `PF_LANGFUSE_TIMEOUT_S` knob was added during this (a slow-ingest
  safety margin), but the real fixes were server-side. Lesson logged: tolerated-absent tracing did its
  job — every one of these failures left the BUILD itself green (`done`), exactly as designed.
- **msgpack-registration carry-forward NOT folded in here.** It's an unverifiable langgraph-version
  API and the working resume path shouldn't be risked on a guess; left as the documented follow-up
  (#21 / DEV_NOTES).
- **Verified locally (73/73 fakes; +8 `test_m2c_tracing.py`):** no-op when off; PF_TRACING-on-but-dep-
  absent degrades to no-op; `set_tracer`/`reset_tracer`; broker-layer spans via a fake `_run`; the
  phase-pipeline spans + the proxy-denial event via the m1 fakes harness (build still `done` — spans
  don't perturb it); the chat_text `llm.<role>` span; and the real `_LangfuseTracer` wired against
  fake langfuse **v4 + v3** clients (the feature-detection both ways). Contract 11/11; hygiene clean.
- **SERVER-VALIDATED (2026-06-24):** project `stage-3-poc`, root trace
  `build/poc-20260623-164251-4d3c04` with **21 observation levels** — `broker.*` (with sandbox/cmd/rc/
  output), `spec`, `iterate.verify`, `critic`, `cleanroom`, `llm.*` all landing; flush-on-exit
  confirmed; survives a depot restart. **S1 DONE.**
- **ONE TRACE PER BUILD (consolidation, 2026-06-24).** The first validated run also emitted a SCATTER
  of standalone top-level traces (`broker.exec`/`create`/`provision`/`destroy`) alongside the rich
  `build/<id>` tree: (a) `broker.destroy`+flush ran in `core`'s outer `finally`, OUTSIDE the build span;
  (b) **LangGraph runs nodes WITHOUT propagating the OTEL context**, so spans created in a node started
  fresh root traces. First attempt (pin children to the build `trace_id` via `trace_context`) BACKFIRED
  on the server — the 22-obs tree got mis-named `broker.destroy` and the rest still scattered. The
  working fix is **explicit parenting**: `build` keeps the live root observation and every later span/
  event is created FROM that root object (`root.start_as_current_observation(...)` /
  `root.create_event(...)`), setting the parent by object reference — independent of thread/context —
  so the whole build lands in the single `build/<id>` trace (falls back to a client-level span when no
  build is active or the obj lacks the method). Teardown moved INSIDE the build span. Volume itself is
  a non-issue — one trace with ~20–50 nested observations is the intended Langfuse model and self-hosted
  handles it easily; the goal was a single drill-down tree, not fewer observations. Local: 74 fakes (a
  v4 test asserts the child span+event get `parent == build/<id>`; one asserts a span OUTSIDE a build is
  client-level; the v3 fallback uses `start_as_current_span`).
- **ACTUAL root cause of the scatter — the broker DAEMON was double-emitting (2026-06-24).** The
  scattered `broker.*` traces were NOT a nesting/OTEL problem at all: the out-of-process **broker daemon
  (`pf-broker`)** runs the instrumented in-process `Broker` (`broker.py`) AND inherits `PF_TRACING=1`
  (via `env_file ../.env`), but has **no build-root context** → every `provision/create/exec/destroy`
  span it created became a standalone top-level trace, duplicating the orchestrator's (which trace the
  same ops WITH the build context via `client.py`/`RemoteBroker`). Fix: `tracing.disable()` (forces the
  no-op tracer for a process) called at the top of `daemon.main()` — the daemon never traces; the
  orchestrator side remains the single source of broker spans, nested under `build/<id>`. (The
  in-process broker.py spans still help the local no-daemon path, where they DO share the build
  process/context.) +1 fakes test (`disable()` silences even an injected tracer). **SERVER-VALIDATED
  (2026-06-24): one clean trace per build — no scattered `broker.*` rows.** S1 fully closed.
- **DESIGN-REVIEW of the daemon-silence decision (2026-06-24) → KEEP (option a).** A spec-grounded
  review (against §5.11/§5.2/§5.5/§10) confirmed orchestrator-side-only broker tracing is correct. Key
  reframe: **Langfuse is NOT the security system-of-record** — the design assigns security evidence to
  named sinks (the egress proxy's own CONNECT log → `logs/`; `security.incidents[]`; the deterministic
  diff-scanner), and §5.11 groups Langfuse with EVALS/observability. So silencing the daemon's Langfuse
  removes zero authoritative security evidence (it only held context-less duplicate build-flow spans).
  The orchestrator is the right altitude because the observability/eval telemetry needs build context
  (iteration, role, the `build/<id>` tree, stratification) that only it has. RPC trace-context
  propagation (option b) was rejected: real complexity (OTEL context across the socket, per-build root
  in the daemon, v3/v4 resilience, cross-process flush) for build-flow telemetry the orchestrator
  already covers — not a correctness/security need. No §10 conflict (keeping the daemon as the
  authoritative enforcer IS the §10 broker decision; observability isn't a §10 item).
- **FOLLOW-UP (separate, real — for the planning chat).** The daemon is the trust boundary + rule-#8
  enforcer, but a rejected `create*` today only raises `BrokerInvariantError` over RPC — **nothing
  durable is recorded daemon-side**, which §5.2's "full logging → logs/ + `security.incidents[]`"
  posture implies the enforcer should leave. Fix is NOT trace propagation: a small **daemon-owned,
  append-only audit record of invariant rejections (and provision/destroy), read independently of the
  orchestrator, feeding `security.incidents[]`** — the trusted-side security evidence belongs in a
  trusted-side security log, not the build's evals trace. Independent of S1; flag to the planning chat
  (scope, not a §10 re-open). Likely M4 security-hardening territory; tracked, not built now.

## #23 — M2c S2: tiered evals v1 — spec + plan evals against fixtures (2026-06-24)

Design §5.11's CHEAPEST eval rung (the headline M2c acceptance): run **only** P0 ingest → P1 spec →
P2 plan on a committed Stage-2 fixture (NO sandbox, NO clean-room, NO Langfuse — minutes, not a
half-hour build), then score the products. New `evals.py` (pure: stdlib + pydantic, phases
lazy-imported inside the runner so it stays `py_compile`-able + import-light on the 3.10 box).

- **Two scoring layers.** (1) **Deterministic structural checks** — `score_spec` (criteria count
  3–6, exactly-one-core, goal/demo non-empty, non_goals present, all `met-by-test`, substantive +
  non-duplicate criterion text) and `score_plan` (≥1 iteration, within the iteration cap, **core-first**,
  acceptance + interface pinned per iteration); each → a 0..1 fraction-passed score, `overall_score`
  a `computed_field` (mean; plan omitted for NOT_BUILDABLE). (2) A **structured human-grading rubric**
  (`default_rubric`: faithfulness / verifiability / core-centrality / scope-realism / decomposition)
  recorded **ungraded** (`grade=null`) — the Tier-2 seam (the server reality is degraded-critic / one
  model for all roles, so we deliberately do NOT auto-judge with an LLM here; §5.4 independence).
- **No broker constructed.** P0/P1/P2 never touch the broker, so the runner builds a `Ctx` with
  `broker=None, coder=None` and a temp staging dir (cleaned up). The only external call is the real
  **architect** LLM in P1 — so the runner runs **on the server**; the deterministic SCORING is proven
  locally by `tests/test_m2c_evals.py` with a FAKE architect.
- **Scoring vs normalization (subtle).** `p1_spec._normalize_spec` already forces one core +
  `met-by-test` typing, so the harness path can never fail `exactly_one_core`/`all_met_by_test` — those
  scorer branches are asserted **directly** on hand-built specs (`test_score_spec_catches_structural_defects`).
  What the harness path CAN catch (and the weak-spec test asserts): count, demo, non_goals, duplicates.
- **Entry points.** `cli eval [--fixture … --template … --min-score F --json PATH]` (server) +
  `scripts/run_evals.py` (plain no-pytest runner, mirrors `run_contract_checks.py` but calls the
  architect → server-only). A crash inside a single eval is caught and recorded as `ok=False` (an eval
  RESULT, not a green-bar break); `--min-score`/`--json` make it usable as a regression gate + a
  persisted artifact (under gitignored `builds/evals/`). Metrics recorded structured
  (`metadata.degraded_critic`, a `stratify` dict) for later stratification.
- **Local: 81 fakes** (+6 `test_m2c_evals.py`: good-spec→full marks, weak-spec→specific fails,
  NOT_BUILDABLE, the two direct scorers, format/save round-trip) + contract 11/11 + hygiene clean.
  **Server-validated (2026-06-24):** `cli eval --json …` on `sample_artifact` → spec_score=1.0,
  plan_score=1.0, overall=1.0 (all 13 checks PASS), JSON written; the real architect produced a
  4-criterion RAG-citation spec + a core-first plan (fast `.env` caps `max_iterations=1`). **S2 DONE.**

## #24 — M2c S3: experience loop — playbook injection + Tier-1 reflection (2026-06-24)

Design §5.9 two-tier playbooks + the §5.3 P4.f close-step interrogation. New `playbooks.py` (pure
stdlib + the tracked `playbooks/` tree) + an injection seam in `prompts.py`/`coder.py` + a per-iteration
reflection step in `p4_iterate` + a post-build hint-distil in `p7_emit`.

- **Two tiers.** Tier 2 = curated tracked `playbooks/{building,testing,research,gotchas}.md`, full
  authority, hand-maintained. Tier 1 = low-authority EXPIRING auto-hints under `playbooks/hints/`
  (**gitignored** — LLM-generated, may echo incident text, scrubbed-but-untrusted → never pushed; only
  `hints/README.md` is tracked). Promotion Tier-1→Tier-2 is a human merge (out of scope; the structure
  + expiry are left in place).
- **Injection (the seam).** `ROLE_PLAYBOOKS` maps architect→[building], tester→[testing],
  coder→[building,gotchas], research→[research]. `playbook_section(role)` concatenates the curated
  bodies + matching non-expired hints (framed **"Unverified hint (low authority, expires …)"**), capped
  to a per-role char budget. `compose(body, role, suffix)` orders it **body → playbook → suffix**, so the
  code-appended **hard-rule / output-format suffix stays LAST and can't be displaced** (the load-bearing
  invariant — asserted by a test that checks the format-suffix index > the playbook index).
  `prompts.spec_prompt`/`tester_prompt` were split into body+suffix to use it; the coder threads
  `playbook=` through `BespokeCoder.run` → `_prompt` (lands before its `# Task` format block).
- **Hint matching + caps.** A hint's `applies_to:` pins match the role name OR its playbook names (so a
  hint pinned to `gotchas` reaches the coder). The injector skips expired (`expires:` < today),
  oversized (> `HINT_MAX_CHARS`=600), or pin-mismatched hints. `write_hint` caps on write **reserving
  the truncation-marker length** so a written hint is always readable back (the one bug found locally:
  cap + marker first pushed it over the read threshold → the injector skipped its own fresh hint).
- **Tier-1 reflection.** `_reflect` runs ONLY on a STRUGGLING iteration (`attempts≥2 OR incidents OR
  status∈{abandoned,incident,red-first-failed}`) — a lesson must cite a concrete incident, so a clean
  first-try-green iteration writes nothing (no wasted LLM call). It interrogates the **coder** role
  ("what would have helped?", `prompts.reflection_prompt`/`REFLECTION_SYSTEM`) and writes
  `builds/<id>/iterations/<i>/lessons.md` with the incident citation + the answer. Best-effort
  (`except Exception` → skip), but `BudgetExceeded` (a `BaseException`) still escapes to salvage, as
  everywhere. `p7_emit` then distils all `iterations/*/lessons.md` into ONE scrubbed, size-capped,
  expiring hint (`playbooks/hints/<build-id>.md`, `applies_to=[coder,gotchas]`).
- **Hygiene.** `scrub.scrub_build_dir` now also scrubs `iterations/*/*.md` (lessons + S4 research); the
  hint body is scrubbed again via `scrub_text` before it lands in the tree. `PF_PLAYBOOKS_DIR` /
  `PF_HINTS_DIR` env overrides added; the local fakes runner points `PF_HINTS_DIR` at a tempdir so a
  fakes-driven `p7` never writes into the tracked tree.
- **Validation hook.** `PF_FORCE_REFLECT=1` (mirrors `PF_STOP_AT_NODE`) forces `_reflect` on a clean
  fast build so the lessons→hint seam is provable server-side without a genuinely struggling 30-min run.
- **Local: 90 fakes** (+9 `test_m2c_playbooks.py`) + contract 11/11 + hygiene clean.
- **Server (2026-06-24, partial):** reflection + hint distil WORK (`reflection → iterations/0/lessons.md`,
  `distilled 1 lesson(s) → hint poc-…md`), build `done`, zero leaks. BUT the curated playbooks did NOT
  inject — the app container never mounted `playbooks/` and the image predates S3, so `/app/playbooks`
  was absent and the hint went to the EPHEMERAL container fs (DEV_NOTES). Fix: `COPY playbooks` in the
  Dockerfile + **mount `../playbooks:/app/playbooks`** in the app override (REQUIRED for injection AND
  hint persistence; the override is gitignored → add by hand on the server; no rebuild needed — the
  mount shadows). *Re-validate: tester-prompt shows the `## Playbook` block; hint lands in HOST
  `playbooks/hints/`.*

## #25 — M2c S4: research-on-gaps (the escalation ladder's last rung) (2026-06-24)

Implements the §5.8 "still stuck → targeted research escalation" rung + the §5.3 P4.a research
sub-step, per the planning-chat DECISION MEMO (orchestrator locus; shared depot SearXNG; minimal-real
scope = prove the rung, not research quality). New `research/` package + trigger wiring in
`p4_iterate`/`p_critic`. **NOT Stage-2's deep research** — a narrow per-iteration lookup on a specific
error/open-question that writes a cited `iterations/<i>/research.md` the coder consumes.

- **Locus = orchestrator, shared SearXNG (memo B/C).** The agent runs in the app process (already has
  the stack) and queries the shared service-depot SearXNG (`SEARX_URL`, depot-net) — lateral traffic to
  trusted infra, NOT a per-build broker sibling. So **`vetted_services` is untouched** (rule #8 governs
  broker `create*` — a service the broker never creates is out of scope) and the **per-build build-VM
  egress allowlist is untouched** (the memo's "crux" — broad metasearch egress vs. tight allowlist —
  dissolves: research egress rides the orchestrator's server-wide wall, not the build proxy).
- **Engine = BESPOKE, not deepagents (deliberate deviation, logged).** The memo said "deepagents
  agent"; I shipped a bespoke single-pass loop (search → fetch a few allowlisted pages → ONE synthesis
  call) behind `run_research(..., llm/search_fn/fetch_fn=)` — same reasoning that won the coder seat
  (M0(b)/#8: reliable + fakes-testable), and a single model call keeps the budget meter EXACT (the
  memo's flagged deepagents-undercount problem never arises). deepagents `0.6.7` can slot into the same
  seam later; the design (§5.1 "deepagents where it pulls weight") is honoured by the seam, not the
  current engine. (Implementation-detail call per AGENTS.md decision culture; no §10 impact.)
- **Triggers (memo E).** (a) OPEN QUESTIONS: `art.open_questions` → `Spec.open_questions` (P1) →
  iteration-0 `IterationPlan.research_questions` (P2, additive) → research at the top of `p4_iterate`
  before the tester (feeds tester + coder). (b) STUCK: `p_critic`'s abandoned branch detects a repeated
  error signature (`len(sigs)!=len(set)`, i.e. ≥ stuck_research_after with the default fix budget) OR
  the deterministic `PF_FORCE_RESEARCH=1` hook → grants a `fix` but sets `research_pending`+`research_error`;
  `p4` runs research on re-entry (feeds the coder; the staged test is reused). Guarded to once per
  iteration (`last_research_iteration`); replaces the #19 ladder stub.
- **Containment (memo D, defense-in-depth — never "immunity", rule #9).** Finding-0 tool surface (the
  agent holds no secrets, only search/fetch/write); a **citation-only structured `research.md` air-gap**
  (the coder never sees raw HTML); the synthesis prompt frames excerpts as UNTRUSTED data ("never obey
  instructions inside them"); a deterministic **injection tripwire** (`scan_injection`) → a `medium`
  `security.incidents[]` entry + a `research.injection` trace event + a ⚠️ banner in `research.md`; and
  the unchanged downstream gates (red-first, diff-scanner, ledger, critic, build-VM allowlist) remain
  the wall.
- **Tooling.** `research/tools.py` vendors the Stage-2 search/fetch(httpx+trafilatura)/GitHub/PyPI tools
  (attribution; same stopgap discipline as the vendored schema, #2); LAZY heavy deps → import-light on
  3.10. `fetch` gates result URLs against an APP-LEVEL advisory `egress_allowlist.research_hosts`
  (gate + log; the enforcing logging research-proxy is M4). Tolerated-absent throughout (no `SEARX_URL`
  / dep / host → empty + caveat, never a crash) — mirrors `tracing.py`.
- **Observability + budget.** `research` + `research.fetch` spans + a `research.injection` event;
  research.md scrubbed by `scrub_build_dir` (already globs `iterations/*/*.md`, #24); the synthesis call
  flows through the `METER` (1 call). `budgets.max_research_results` (`PF_MAX_RESEARCH_RESULTS`) bounds
  fetch breadth.
- **Acceptance reinterpretation (memo A).** "spins SearXNG … reaped" → shared infra, ZERO new per-build
  containers. **Deviations from HANDOVER_M2c** (sibling-on-internal-net / vetted_services / per-build
  proxy fetch) are superseded by the memo and noted there.
- **Local: 101 fakes** (+11 `test_m2c_research.py`: tripwire, offline host-gate, bespoke synthesis with
  fake search/fetch/llm, injection→incident, tolerated-absent, the `_maybe_research` triggers, and the
  `p_critic` stuck→research routing) + contract 11/11 + hygiene clean. *Server (pending): the fixture's
  open question drives a research.md the coder consumes via the depot SearXNG; ZERO leaks; tolerated-
  absent when SEARX_URL is down. Depot-side (user): digest-pin searxng + pin engines to Google/Bing.*

## #26 — M2c S5: template CI (scaffold+smoke per template in a fresh VM) (2026-06-24)

Design §5.3 P3: a maintenance-time check that each template still scaffolds + smokes GREEN in a fresh
Kata VM, so template rot (a yanked pin, a smoke regression) is caught off the build path. New
`core.template_ci` + `core.preflight_templates` + a `cli template-ci [--preflight]` subcommand.

- **Two layers.** `preflight_templates` is the DOCKERLESS static check (fakes-testable): enumerate every
  `templates/*/template.json`, resolve each, and assert each declared service is PINNED in
  `vetted_services` (rule #8 — an unpinned service can't be spun → the template would fail mid-build).
  `template_ci` adds the real VM smoke: ONE broker for the run (net+proxy+uv-vol), a FRESH VM per
  template (reuses P3's `stamp_template` + the broker smoke path: stamp → git-init → `pytest <suite>`),
  every VM + the broker reaped. `--preflight` runs only the static layer (no Docker).
- **Workspaces on local disk.** CI workspaces live under `cfg.workspace_dir/<ci_id>/` (host==container
  path) so the broker can bind them into Kata VMs (sibling-container semantics) — NOT `/tmp` inside the
  orchestrator. Cleaned up after the run.
- **Smoke needs no services.** P3 runs the scaffold smoke BEFORE `_spin_services`, so template CI is a
  pure stamp+`pytest` in a fresh VM — pgvector's `pg` sibling isn't spun (its pin is checked statically
  in preflight). Tracing: a `template-ci` root span + a `template-ci.smoke` span per template.
- **Local: 106 fakes** (+5 `test_m2c_template_ci.py`: discovers + resolves both real templates;
  pgvector's `pg`→pgvector pinned; an unpinned-service template flagged; an unresolvable template
  recorded; the `--preflight` CLI exits 0) + contract 11/11 + hygiene clean. *Server (pending):
  `cli template-ci` scaffold+smokes both templates GREEN in fresh VMs; ZERO leaks.*

## #27 — M3 S1: web-UI event seam + single-slot RunManager + SSE (2026-06-24)

Design §5.12: make a run watchable WITHOUT moving pipeline logic into the UI (rule #5). The web layer
is a SECOND thin presentation over the unchanged headless contract — it only calls `core`.

- **The seam is one optional callable on `Ctx`** (`ctx.events`), threaded as an OPTIONAL `event_sink`
  kwarg through `build_poc`/`resume_build`/`_prepare`. The CLI never passes it → `ctx.events is None` →
  emitting is a pure no-op and the contract is byte-for-byte unchanged (a fakes test asserts both
  signatures default `event_sink=None`). Chosen over a module-global registry: explicit, thread-safe
  for a future multi-build world, and trivially testable.
- **Two emit points, additive.** `Ctx.say` mirrors each progress line to the sink as a `log` event
  (stderr stream kept verbatim for the CLI); `graph.wrap` emits a `node` event carrying a slice-board
  `snapshot(state)` at every node boundary — so the board flips green as `success_criteria[].status`
  and `iteration_records` advance. `build_poc` emits an early `start` (the freshly minted id reaches the
  UI before the first slow node). `snapshot` reads `BuildState` purely via `getattr` → no coupling, no
  heavy import; lives in pure-stdlib `events.py` (`py_compile`s on 3.10).
- **`events.emit` is tracing-grade tolerant** — a flaky subscriber NEVER crashes a build (same
  discipline as `tracing`/hint-write). `sse_format` serializes `event:`+`data:` frames.
- **Single-slot `RunManager`** (`web/runmanager.py`, pure stdlib + threading/queue): ONE build at a time
  (matches the runtime reality — one vLLM, build-id-scoped broker nets); a 2nd concurrent start raises
  `RunBusy` → the server answers **409**. Launches `build_poc`/`resume_build` on a daemon thread, wires
  the sink to fan every event out to all SSE subscribers, keeps a replay buffer (a late/reconnecting SPA
  sees the run so far), emits a terminal `end`/`error`. `stop` delegates to `request_stop_build` (M2b S4
  — the cooperative-stop sentinel is already checkpoint-backed; NOT reimplemented). Core fns are
  DI-injected (defaults lazy-import `core`) so the module imports + runs under the no-pytest fakes
  without the agent stack.
- **`web/server.py` (FastAPI) is image-only** (imports the `ui` extra; never imported by the fakes —
  the testable Python is `events`+`runmanager`). Routes: start/resume/stop, list/detail, a
  suffix-allowlisted + traversal-guarded `file` reader (serves already-scrubbed build files), `status`,
  and the `events` SSE stream (async generator, `asyncio.to_thread` on the queue + client-disconnect
  check). Serves the committed `dist/` (placeholder until S2). **The localhost boundary is the
  HOST-SIDE PUBLISH** — compose maps `127.0.0.1:8181:8181`, so the service is reachable only from the
  server loopback, then over an SSH tunnel. Uvicorn listens **0.0.0.0 in-container** by necessity
  (Docker forwards the published port to eth0, NOT the container loopback — a 127.0.0.1 listen returns
  empty/connection-reset through the port map; this matches the depot's own langfuse/searxng). The
  process holds the secrets → **no in-app auth, and we don't claim one** (rule #1 / §5.12); chose host-
  publish-on-loopback over in-container 127.0.0.1 + the SSH tunnel as the boundary. Port **8181** (8770/
  8008 are vLLM on the shared box). New `web` compose service; override mirrors `app`'s broker socket +
  `PF_WORKSPACE_DIR` so the UI can run builds.
- **Local: 118 fakes** (+11 `test_m3_events.py`: `say→sink→sse_format`; snapshot projection + empty-state
  tolerance; failing-sink tolerance; contract-additive signatures; RunManager 409 / fan-out+`end` /
  replay / `stop`→sentinel / error surfacing) + contract 11/11 + hygiene clean. *Server (pending): `$DC
  --profile web up web`, `curl -N /api/events` while POSTing a build start → `start`/`node`/`log`/`end`
  stream. S2 (React SPA over the tunnel) next.*
