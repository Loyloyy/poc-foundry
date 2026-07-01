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
  the testable Python is `events`+`runmanager`). Routes: start/resume/stop (+ no-id `/api/stop` for the
  Stop button — single-slot), list/detail, a
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
  replay / `stop`→sentinel / error surfacing) + contract 11/11 + hygiene clean. **Server-validated
  2026-06-24:** SSE over the tunnel streamed `start`/`node`(snapshot)/`log` through a real fixture
  build; history/status/Stop→Resume confirmed; port **8181** (8770/8008 are vLLM on the shared box);
  the localhost boundary is the host-side publish + uvicorn 0.0.0.0 in-container (see above).

## #28 — M3 S2: React SPA (the watchable UI), `dist/` committed (2026-06-24)

Design §5.12: a React SPA, built off-server, `dist/` committed. The blocker was rule #3's "no npm on
any host" — including the dev box where the agent works. **Resolved by following the Stage-2 precedent
(`ai-engineer-research/frontend/`): npm runs on the DEV BOX (which has node 20 — Stage-2's 70 MB
`node_modules` was already here), `node_modules` is gitignored, and the PREBUILT `dist/` is committed**
so the server (no npm/registry, rule #3) serves the bundle straight. So rule #3 targets the *Python
pipeline stack* (≥3.11, Docker-only) and the *server* (no registry) — the frontend toolchain on the dev
box is a sanctioned, already-established exception, NOT a deviation. (User steered me to look at Stage 2.)

- **Stack + layout.** React 18 + TypeScript + Vite in `frontend/`, mirroring Stage 2 verbatim
  (`useEventStream` hook over `EventSource`, thin `api.ts`, `tsconfig`/`vite.config` shapes). Vite
  `build.outDir` → `../src/poc_foundry/web/dist/` — exactly where `web/server.py` serves it (it mounts
  `/assets` + falls back to `index.html` for SPA routes). `.gitignore` re-includes that one dist past the
  blanket `dist/` (`!src/poc_foundry/web/dist/**`); `frontend/.gitignore` hides `node_modules`. Build +
  recommit: `cd frontend && npm install && npm run build`.
- **Single global SSE, not per-run.** Unlike Stage 2 (per-run `/runs/{id}/stream`), the backend is
  single-slot, so `useEventStream` subscribes ONCE to the global `/api/events`; the server's replay
  buffer means a reload mid-build still shows the live board. Events: `start`(reset)/`node`(snapshot →
  the board)/`log`(append)/`end`/`error`.
- **Views (§5.12).** Sidebar = new-build form (Start disabled while `busy`; a 409 → inline "already
  running") + history list (from `list_builds`, click to open). Main = live **SliceBoard** (criteria flip
  green; iteration records) · **LogPanel** (auto-scrolling `Ctx.say` stream) · **DocsPanel** (inline
  markdown of any allow-listed build file via `/file`) · **DescopePanel** (descope items + finish-paths +
  `abandoned.patch` pointer + caps). Live view (the running build) auto-follows; selecting a historical
  build loads its emitted artifact + files. Stop = no-id `/api/stop`; Resume = `/api/builds/{id}/resume`
  (shown when status ∈ {stopped, incomplete}); Langfuse `host` → "Traces ↗". Security-Demo tab deferred
  to M4. NO pipeline logic in the SPA (rule #5) — it only calls the API.
- **Build output:** `tsc --noEmit && vite build` GREEN; bundle ~312 KB (97 KB gzip) committed. The
  Python green bar is unchanged (118 fakes + 11 contract + hygiene) — no Python logic changed beyond the
  S1 `/api/stop` convenience route. *Server (pending): rebuild the image (committed `dist/` is `COPY
  src`'d), bring up `web`, open over the tunnel, watch a fixture build live + exercise Stop/Resume +
  history/docs/descope.*

  **Addendum (2026-06-25) — SERVER-VALIDATED over the tunnel + UX polish (119 fakes):**
  - Watched a real fixture build live (slice board flips green, log streams), Stop→Resume, history/docs/
    descope all render; localhost-publish boundary holds. M3 ✅ COMPLETE.
  - **Source picker** (`core.list_sources` + `/api/sources`): the build form shows Stage-2 **topics**
    (read off `vNN.json`), not raw paths — scans fixtures + `PF_ARTIFACTS_ROOT` (mount the Stage-2
    `artifacts/` dir into `web`). A "Custom path…" escape hatch remains.
  - **Langfuse link** made browser-usable: `LANGFUSE_HOST` is the in-network name a laptop can't resolve
    → rewrite host→localhost (or `PF_LANGFUSE_PUBLIC_URL`); **deep-link to the build's session** via
    `PF_LANGFUSE_PROJECT_ID` (trace carries `session_id==build_id`). Exact-trace `?peek=` deep-link
    deferred (needs capturing Langfuse `trace_id` at build time → M4 nicety).
  - **Stop UX:** immediate "Stopping…" + a banner explaining the cooperative stop lands at the next node
    boundary (an in-flight model call can take a minute). **Caveats/quality card** surfaces
    `degraded_critic` + the critic's advisory text. Long Stage-2 briefs clamp to 3 lines.
  - **Ops gotchas (cost round-trips; now memory + ROADMAP):** `.env` changes need
    `docker compose ... up -d --force-recreate web` (NOT `restart` — that keeps the stale env_file env);
    `DC` must pass BOTH `-f` files (compose + override) or the broker/host-mounts vanish; port **8181**
    (8770/8008 are vLLM on the shared box; uvicorn binds 0.0.0.0 in-container, the boundary is the host
    `127.0.0.1:8181` publish).
  - **Real-build learning (informs M4):** a non-degraded critic (a `critic` role bound to a DISTINCT model
    family from `coder`) correctly BLOCKS gameable string-presence tests. On a hard source (PageIndex
    tree-nav) the base coder can't satisfy it within budget → every criterion honestly descopes to the
    `refine` finish-path, and a degenerate respec/replan loop ran ~90 min (no run-level wall-clock cap).
    Takeaways: (1) `refine` (M4) is the real success-rate lever; (2) set `PF_MAX_RUN_WALL_CLOCK_S`;
    (3) DON'T tune the harness to manufacture green — that games the verifier, which is the whole value.


## #29 — M4 S1: `refine` — re-attack the descoped backlog on a stronger coder (2026-06-25)

The M3 real-build learning (#28) named `refine` as the real success-rate lever: a strict critic on a
hard source honestly descopes every criterion to the finish-path *"re-run with `refine` on a frontier
`coder` endpoint"*. `refine` is the code that fulfils that — it raises success by giving the coder MORE
capability, never by lowering the critic bar (the whole-value invariant, #28).

- **Headless entrypoint.** `core.refine_build(build_id, *, coder_override=None, runtime, event_sink)`
  re-runs ONLY a finished build's not-yet-`met` criteria over the PERSISTED workspace + already-authored
  red-first staged tests. P0–P3 (ingest/spec/plan/scaffold) are NOT re-run; the tests are NOT re-authored.
  Re-emits the updated artifact (refined criteria flip to `met`, drop off the descope report). `refine ≠
  resume`: resume replays a checkpoint from its last node; refine seeds a NEW state and re-enters at P4.
- **A backlog-only refine graph** (`graph.build_refine_graph`): `START→iterate→critic→(fix|next→iterate /
  else→docs)→docs→cleanroom→emit`. Reuses every phase unchanged; only the wiring is new (rule #5 — no
  pipeline logic in a new place). Seeded with a hand-built `BuildState` from the build's checkpoint
  (`_recover_state`), so it runs on its own `thread_id` (`<id>-refine`) and never clobbers the original.
- **Backlog selection + staged-test reuse** (`_refine_seed`): the seed plan keeps ONLY the iterations whose
  criterion isn't met, each pinned to its ORIGINAL staged-test filename via the new
  `IterationPlan.test_file` (so a filtered plan reuses the red-first test instead of re-numbering →
  re-authoring it). The cumulative gate runs all of `/staged`, so multiple still-red backlog tests would
  block each other → refine parks them in `staging/refine_pending/` and stages each INTO the active set
  only on its iteration (`_refine_stage_in`), removing it again if it stays red (`_refine_park_out`).
  New `BuildState.refine_mode` disables the iteration-0 strict-red-first probe (the workspace already holds
  real post-scaffold code → a green probe means "met by existing code", not tester inadequacy).
- **Per-call coder rebind, NOT a global `.env` change** (`models.set_role_alias`): a PROCESS-LOCAL alias map
  resolved FIRST in `resolve_role`, so the rebind reaches BOTH the coder loop (`chat_text("coder",…)`) AND
  the degraded-critic check (`same_family("critic","coder")` correctly reads a frontier coder as a distinct
  family). Set around the one refine run, cleared in `finally`. `coder_override` is a `.env` role name whose
  triple points at the frontier endpoint (e.g. a distinct neighbour critic-family endpoint); blank = re-run on the base coder.
- **Critic never weakened** (#28): refine pins `respec_count`/`replan_count` to their caps, so the verdict
  ladder collapses to fix→descope (no re-spec/re-plan — refine re-attacks the SAME plan). An adequacy-failing
  green still descopes (not respec'd) → a gamed pass is never rewarded.
- **Surfaces.** CLI `refine <id> [--coder ROLE]`; web `POST /api/builds/{id}/refine` + `RunManager.refine`
  (single-slot, streams like a normal run) + a **✦ Refine descopes** button (with a coder-role input) on a
  finished build that has descopes. Spend is metered against the budget (§5.8) like any run.
- **Local:** `run_spine_tests.py` **131** (+12 `test_m4_refine.py`: backlog selection, rebind plumbing +
  `same_family`, respec/replan pinned, one-at-a-time staging, additive contract, RunManager.refine).
- **SERVER-VALIDATED (2026-06-25)** over the tunnel — `cli refine poc-…104121-57e82f`: refine recovered the
  checkpoint, re-attacked all **4 non-`met`** criteria (broader than the artifact's single logged descope —
  backlog = every non-`met` criterion, correct), moved **2 to `met`**, flipping the build **incomplete →
  done** (demonstrates=yes; clean-room install/test/demo all GREEN), re-emitted the artifact. The critic
  stayed honest LIVE: it `descope→next`'d two gameable greens ("can be satisfied by an empty string…" /
  "a stub that always returns 'no relevant information'…"), with `respecs=1 replans=1` confirming the pinned
  caps held (fix→descope, no re-spec/re-plan). Budget metered (`llm_calls=36, wall_s=1411`, no caps hit).
- **Honest scope of the validation:** this run used the BASE coder; the met-flip came via re-verification
  (`met-existing`) against the now-fuller workspace, NOT new problem-solving. The "a *stronger* coder solves
  a hard descoped criterion" claim is **deferred** (the user has no frontier endpoint; the neighbour critic
  model isn't meaningfully stronger) — the rebind path is identical + fakes-proven, so it's a zero-code residual.
- **Residual (real, low-priority):** langgraph warns "Deserializing unregistered type … Spec/Plan/
  IterationRecord … blocked in a future version" when `_recover_state` reads the checkpoint. Harmless today;
  pin it by registering `allowed_msgpack_modules` (or `LANGGRAPH_STRICT_MSGPACK`) when we next touch graph
  compile. Web-UI ✦ Refine button is wired but not yet clicked over the tunnel (same `core.refine_build`).


## #30 — M4 S2a: daemon-side invariant-rejection audit log (2026-06-25)

The M2c S1 design review (DECISIONS #22 follow-up) flagged that the broker daemon — the trust boundary
(rule #8 enforcer, sole docker.sock holder) — should durably record rejected `create*` so the rule-#8
enforcement is *evidenced*, not just asserted. Ships now as the first S2 slice (the security demo's
beat-(b) reads it); the key-proxy + the two live beats + the Security-Demo tab follow.

- **Where the record lives.** `sandbox/audit.py` (stdlib json+os): append-only JSONL `append/read/
  make_entry`. The `Broker` (which ENFORCES the invariant in `_check_*`) records each rejection in
  `create`/`create_service`/`provision`, plus provision/destroy lifecycle, into an in-memory list AND
  (when `PF_BROKER_AUDIT_LOG` is set) the durable file. In the server deployment the **daemon** sets that
  env to `/var/tmp/pf-broker/audit.log` on the shared `pf-broker` dir → the file is written ONLY by the
  daemon process (independent of a possibly-compromised orchestrator) yet readable by app/web (mounted).
  Recording in the Broker (not the daemon wrapper) is DRY: the same code enforces + audits, so both the
  out-of-process (daemon) and in-process (local) paths record identically.
- **Finding-0 holds.** A record carries only harness/LLM-derived create-params (image/name/caps/mount
  TARGETS) + the invariant reason — never env values, never the vLLM key (fakes assert a secret never
  appears in the serialized audit).
- **Surfacing.** `audit` RPC + `RemoteBroker.audit()`; `p7_emit` pulls `ctx.broker.audit()` rejections into
  `security.incidents[]` as `[high] broker-invariant-rejection: <method> — <reason>`. (Normal builds have
  none — a real rejected create* fails loud and crashes the phase; the audit is the forensic/demo record.)
- **Local:** `run_spine_tests.py` **139** (+8 `test_m4_security.py`: append/read roundtrip + malformed-line
  tolerance, rejected create/create_service/provision recorded with reason+detail, no-secret-leak, durable
  file append, the `audit` RPC, and the emit-merge). Override.example documents `PF_BROKER_AUDIT_LOG`.
- **SERVER-VALIDATED (2026-06-25):** with `PF_BROKER_AUDIT_LOG` set on `broker`, a rejected create*
  (`image='attacker/evil:latest'`) driven through the live daemon was blocked daemon-side
  (`BrokerInvariantError`), returned by the `audit` RPC, AND written to the durable file — provision →
  rejected → destroy, full forensic detail, no secret. The file was written by the `pf-broker` container
  and read back from a *separate* `app` container = the "independent of the orchestrator" property, live.


## #31 — M4 S2b: the key-proxy, reframed honestly (keyless on-prem vLLM) (2026-06-25)

S2 (security demo + key-proxy) opened with a design check against reality (§5.2: "claims written against
enforced reality only"). The `.env` showed the on-prem vLLM is **keyless** — every role's API key is `not-needed`,
both endpoints (the main coder endpoint + the distinct-family critic endpoint) accept any token. The design's key-proxy premise
was "a single static key ⇒ ship a reverse-proxy to make it per-build revocable"; with NO key, a reverse
proxy over vLLM would guard nothing → shipping it as-specified would be theatre (rules #9/#28).

**Resolution (with the user).** The key-proxy is the REAL control for the GENERAL case — this platform is
meant to run key-requiring providers (OpenAI/Claude/hosted models), where the model key IS a high-value
secret that must never enter the throwaway VM. The on-prem keyless vLLM is just this box's config. So we
build the key-proxy as the genuine mechanism and **demonstrate it with a canary**: a planted stand-in
secret configured as the "real key"; the demo proves the VM never sees it (and that inference still works
because the proxy swapped it in). Honest: we never claim the on-prem vLLM has a key — we claim "when a
provider needs a key, the orchestrator holds it, the proxy injects it, the VM never sees it," and prove it.

This makes the key-proxy and Finding-0 the SAME demonstration: "the real secret never reaches the sandbox."

- **Topology.** `sandbox VM → key-proxy (presents a per-build SACRIFICIAL token) → injects the REAL key →
  model`. The real key lives only in the proxy (orchestrator-side); the VM env carries only the sacrificial
  token (rotatable per build). On a keyless deployment the sacrificial token defaults to `not-needed` and
  the proxy still forwards — the control is dormant-but-wired (no false claim) until a real key is set.
- **Built + green (testable core):** `security/keyproxy.py` — `swap_authorization` (validate sacrificial →
  return real `Authorization`; deny on mismatch so the VM can't borrow the proxy's identity), `redact` (the
  proxy never emits its own key), and a stdlib `http.server` reverse proxy `serve()` (image-only). And
  `security/findings.py` — `scan_sandbox_env`: scan a VM's env against `scrub.collect_secrets()`, report any
  leak by PLACEHOLDER (never the raw value) — empty = Finding-0 pass. `run_spine_tests.py` **145** (+6).
- **Remaining (server-bound, next slice):** the key-proxy CONTAINER + broker per-build provisioning +
  per-build token generation + VM model-base_url injection (OPT-IN via `PF_KEYPROXY_*`; normal builds
  unchanged); `core.security_demo()` + CLI `demo-security` (3 beats: canary/Finding-0 · egress containment ·
  broker rejection); the Security-Demo web tab (S2d).

## #32 — M4 S2c: `demo-security` CLI + the 3 live red-team beats (2026-06-25)

The honest demonstration of the platform's defense-in-depth (DECISIONS #30/#31; design §5.2/§5.12).
One headless entrypoint runs three beats against a REAL provisioned broker — each proves one control
with deterministic evidence, not a claim (rules #9/#28).

- **Headless (rule #5).** `core.security_demo(*, builds_dir, runtime, event_sink, canary)` builds the
  per-build broker via `_make_broker` (so it rides the SAME out-of-process daemon path as a real build —
  the rejection beat hits the live rule-#8 enforcer), provisions it, runs `security/demo.run_demo`, and
  reaps it. Returns `{"build_id", "ok", "beats":[...]}`. CLI `demo-security [--canary V]`; the web tab
  (S2d) calls the same core through `RunManager`.
- **`security/demo.py` = pure analyzers + thin orchestration.** The PASS/FAIL logic (`parse_env`,
  `analyze_canary`/`analyze_egress`/`analyze_rejection`) is stdlib-only → fakes-testable WITHOUT Docker;
  `run_demo` only wires a live broker to them (create VM → `exec` → analyze → destroy). Same discipline
  as every gate: the catch is provable locally, the live VM run is the server demo.
- **The 3 beats.**
  1. **canary / Finding-0** — one fresh VM, `exec("env")` → `parse_env` → `findings.scan_sandbox_env`
     against `scrub.collect_secrets()` (the orchestrator's real Langfuse/GitHub/path secrets) PLUS the
     planted `canary`. PASS = nothing leaked. The broker only ever injects proxy + sacrificial token +
     sibling IPs into a VM, so this is the CHECKED form of that claim. Leaks are reported by PLACEHOLDER.
  2. **egress containment** — the same VM `exec`s `curl … https://example.com` (a non-allowlisted host);
     PASS = `findings.egress_denied(proxy_log)` sees squid `TCP_DENIED` AND the curl was blocked (no 2xx).
     New pure `findings.egress_denied`.
  3. **broker rejection** — `broker.create(image='attacker/evil:latest', …)`; PASS = it raises
     `BrokerInvariantError` (rule #8) AND a `rejected/create` entry is in `broker.audit()` (S2a, durable).
- **Canary handling (DECISIONS #31).** Defaults to `PF_DEMO_CANARY`; it is NEVER injected into a VM (the
  whole point — it lives orchestrator-side only) and is REDACTED from every shared beat output/event
  (`_redact_beat`, on top of the placeholder-only analyzers). On a keyless box the canary is how we prove
  "the real secret never reaches the sandbox" without claiming a key the box doesn't have.
- **Event seam (M3).** `run_demo` emits a `make_event("beat", …)` per beat; `security_demo` brackets it
  with `start`/`end`. `event_sink=None` on the CLI path (headless contract unchanged).
- **Local:** `run_spine_tests.py` **149** (+4 in `test_m4_security.py`: all-3-pass with VM reaped,
  all-3-fail on leak/open-egress/unblocked-create, events emitted + canary redacted, the pure analyzers).
- **Acceptance (server, pending):** `cli demo-security` runs all 3 beats GREEN over the tunnel — the
  canary + real secrets absent from the VM, an egress attempt `TCP_DENIED` in the proxy log, the bad
  create rejected + audited. Needs the broker daemon up with `PF_BROKER_AUDIT_LOG` set (already on the
  server from S2a). **Remaining:** the Security-Demo web tab (S2d).

## #32b — M4 S2d: the Security-Demo web tab (2026-06-25)

The web surface for #32 (design §5.12), built on the M3 event seam — zero new pipeline logic (rule #5).
- **Single-slot, reused stream.** New `RunManager.security_demo(**kw)` launches `core.security_demo`
  on the one slot via a `_run_demo` runner (the demo returns a result DICT + publishes its own
  `start`/`beat`/`end`, unlike a build's `(report, artifact)`); a concurrent run raises `RunBusy` → 409.
  `POST /api/security-demo` (`SecurityDemoReq{canary,runtime}`) is the route. `RunManager._launch` grew a
  `runner=` param so build/resume/refine are untouched.
- **SPA.** A Builds / Security-demo tab switch in `App.tsx`; the Security tab (`components/SecurityDemo.tsx`)
  POSTs and renders the 3 beats live from the global SSE — `useEventStream` gained `beats[]` (reset on
  `start`, appended on each `beat`). Each beat card shows PASS/FAIL + the control's plain-English blurb +
  the streamed `summary`/`detail`. `frontend/` rebuilt on the dev box (npm present), `dist/` recommitted.
- **Local:** `run_spine_tests.py` **150** (+1 `RunManager.security_demo` streams beats + finishes). The
  live render is the server demo (same `core.security_demo` the CLI proved).

## #32c — M4 S2c fixes from the first live run (sacrificial-token Finding-0 + curl-side egress evidence) (2026-06-25)

The first server run of `demo-security` proved the harness end-to-end and surfaced two **evidence** bugs
(not security failures) — beat-3 passed; beats 1–2 needed honest fixes:
- **Beat 1 false positive (sacrificial token).** `scan_sandbox_env` flagged `<redacted-key>` in the VM —
  it was the `PF_SANDBOX_VLLM_KEY` *sacrificial* token, which is INTENDED in the VM (DECISIONS #31:
  sacrificial = buys inference, nothing else). `collect_secrets()` classifies it as a key, so Finding-0
  flagged the one secret that's supposed to be there. **Fix:** `run_demo` reads the broker's sacrificial
  token (`broker.vllm_key`/`_vllm_key`) and EXCLUDES its value from the must-be-absent set. A real leak
  (Langfuse/GitHub/path) still fails. `scan_sandbox_env` now also returns `leaked_keys` (the VM VAR NAMES
  that held a secret — safe to show, never the value) so a genuine leak is diagnosable in one round-trip,
  and matches only the env VALUE side (not the `VAR=` name).
- **Beat 2 missing evidence (proxy-log flush lag).** Egress WAS contained (curl blocked, http 000) but
  `TCP_DENIED` wasn't in `docker logs` yet (squid's file→stdout tail lags the immediate read). **Fix:**
  the probe is now `curl -sS … 2>&1; echo PF_EXIT=$?` so curl's OWN "CONNECT tunnel failed, response 403"
  is captured in-band — that 403-from-proxy is itself affirmative denial evidence; PASS = blocked AND
  (`TCP_DENIED` in the log OR curl's proxy-403). `run_demo` also POLLS `proxy_log` a few times to let the
  log flush. The FAIL summary no longer falsely says "the VM reached the host" when curl was blocked.
- **Local:** `run_spine_tests.py` **151** (+ the sacrificial-exclusion test; analyzer + leaked_keys
  assertions updated). Web tab unchanged (renders the new detail fields generically — no rebuild).

## #32d — M4 S2c: GPG_KEY false-positive in Finding-0 (second live run) (2026-06-25)

The fixed beats 2–3 went GREEN on the server; beat 1's new `leaked_keys` diagnostic immediately named the
culprit: VM var **`GPG_KEY`**. `GPG_KEY` is the CPython release signing-key FINGERPRINT — a PUBLIC
constant baked into every `python:*` base image (published on python.org), not a secret. The orchestrator
(`app`) runs the same `python:3.12-slim` base, so `collect_secrets()` classified its `GPG_KEY` (name ends
`_KEY`) as `<redacted-key>`, and the sandbox VM (same base image) carries the identical public value →
flagged as a "leak." **Fix (in `scrub.py`, the right general place):** a `_SKIP_KEYS = {"GPG_KEY"}`
name-allowlist short-circuits `_classify` — so the scrubber also stops needlessly rewriting that public
fingerprint in emitted reports. `run_spine_tests.py` **152** (+1 scrub test). This is exactly the
diagnosability `leaked_keys` was added for (one round-trip from symptom to fix).

## #33 — M4 S2b-infra: the key-proxy container + opt-in per-build provisioning (2026-06-25)

Completes the key-proxy from testable-core (#31) to a live topology — the REAL control for key-requiring
providers, demonstrated honestly on the keyless box with a canary. **Strictly OPT-IN** (`PF_KEYPROXY_UPSTREAM`
unset → every path is byte-for-byte the proven build path); a real topology touch, so sliced carefully +
fakes-first.

- **Where the key lives.** The REAL key (`PF_KEYPROXY_REAL_KEY`) is read daemon-side and injected ONLY into
  the key-proxy container. The VM gets a per-build **rotatable sacrificial** token (`pf-sac-<hex>`, replaces
  the static `vllm_key`) + `PF_SANDBOX_MODEL_BASE_URL` = the proxy. A key-requiring PoC calls the model
  through the proxy with the sacrificial token; the proxy swaps in the real key (`keyproxy.swap_authorization`,
  #31) — the VM never sees it (Finding-0, now also covers `PF_KEYPROXY_REAL_KEY`).
- **Topology.** `Broker._provision_keyproxy` (inside the atomic `provision`, so a failure tears the env
  down) spins the proxy dual-homed (egress + internal, reached BY IP like squid — Kata DNS), running the
  app image's `python -m poc_foundry.security.keyproxy`. `_await_keyproxy` waits Running → internal IP →
  `keyproxy_url = http://<ip>:8788`. `_build_vm_env` (extracted from `create` for unit-testability) injects
  the base_url only when `keyproxy_url` is set. Reaped in `_destroy` (idempotent).
- **Rule #8 holds.** The key-proxy image is harness-fixed: `_make_broker` adds `cfg.keyproxy_image`
  (default the app image) to the allowlist ONLY when enabled; `_provision_keyproxy` rejects + audits a
  non-allowlisted image. `create*` params stay harness-fixed; only `exec` carries LLM content.
- **No model-calling template exists** (all current templates are pure/deterministic) → the key-proxy can't
  be exercised by a normal build. So it's validated by a DIRECT demo beat-4 (`analyze_keyproxy`), run from
  inside the VM using its own injected env vars (the orchestrator never needs the daemon-generated
  sacrificial token): right token → 200 (proxy forwarded to upstream), wrong token → 401 (proxy is the
  gatekeeper). On the keyless vLLM the 401-on-wrong-token comes from the PROXY, so the mechanism is proven
  regardless of upstream auth; upstream = the vLLM root, "real key" = a canary.
- **Config.** `keyproxy_image` (`PF_KEYPROXY_IMAGE`, default `poc-foundry-app`) + `keyproxy_upstream`
  (`PF_KEYPROXY_UPSTREAM`) + `keyproxy_enabled`. Override.example documents the `.env` keys + the one-time
  `build app` (the key-proxy container runs the BAKED app image → needs keyproxy.py in the image).
- **Local:** `run_spine_tests.py` **157** (+5: analyze_keyproxy, run_demo's 4th beat, `_build_vm_env`
  normal-vs-keyproxy, the rule-#8 image rejection, `_make_broker` allowlisting). Web tab renders extra
  streamed beats generically (the key-proxy card appears only when provisioned). `dist/` rebuilt.
- **SERVER-VALIDATED (2026-06-25):** with `PF_KEYPROXY_UPSTREAM` = the vLLM ROOT + `PF_KEYPROXY_REAL_KEY` =
  a canary, `build app` + recreate broker, `cli demo-security` ran **4/4 GREEN** — the key-proxy beat showed
  inference 200 (real key swapped in) / wrong-token 401 / the canary absent from the VM. **Two gotchas
  fixed pre-validation:** (1) the key-proxy IP must be in the VM's `NO_PROXY` — else the VM's `http_proxy`
  routes the HTTP call to squid, which denies the internal IP (`_build_vm_env` adds it); (2) the upstream
  must be the ROOT (no `/v1`) — the proxy appends the VM's `/v1/models` path, so a `/v1` upstream doubles
  it → 404. Normal builds (env unset) unchanged by construction (every path guarded). **M4 COMPLETE.**

## #34 — M5 RAG pilot: align the author-side anti-gaming bar with the critic's (2026-06-29)

**The first real-artifact RAG pilot** (`dra-…fa650c-m` "Self-hosted RAG over a private document store"
→ `gradio-rag-pgvector`) exposed a structural weakness, exactly what the pilot ladder (design §7) is
for. The build churned **respec→replan→descope** to exhaustion: every iter0 went RED→GREEN in 1
attempt, but the non-degraded critic (distinct family — working correctly) rejected each as *gameable*
— "the test only checks a citation marker/phrase appears, which a stub could satisfy without real
pgvector retrieval."

**Root cause = a BAR MISMATCH between authoring and judging.**
- The **tester** was told (only) to make assertions so *"a naive echo stub would FAIL them."*
- The **critic** rejects if *"a trivial stub UNRELATED to the criterion would satisfy it."*

A *constant-output* stub (`return "[1] foo"`) is not an echo stub but IS a trivial input-insensitive
stub: it clears the tester's bar yet fails the critic's. So the tester wrote `assert "[" in reply`,
the coder satisfied it with a near-stub, and the correct critic bounced every iteration forever (the
degenerate ~90-min loop the residuals list flagged).

**Fix (author-side, thin — the critic's strictness is by design and stays unchanged).** Make the spec
criteria + the tester tests demand **DISCRIMINATION, not mere presence**: a constant return value must
FAIL, achieved by contrasting a should-fire input against a should-not input. For RAG this is the
positive (in-corpus query → citation + verbatim snippet) vs. negative (unrelated query → no-match, no
citation) pair — which the scaffold already supports (the `retrieve` lexical gate returns `[]` for
unrelated queries; the stub fallback is the no-match reply), so the coder's real ~3-line glue passes
it. The tester does NOT need corpus internals — just the relevant-vs-irrelevant contrast.

Three edits, all pure-string (`prompts.py` + `playbooks/testing.md`):
- `tester_prompt` suffix: "The bar is DISCRIMINATION … a constant return value MUST FAIL … exercise at
  least TWO contrasting inputs per criterion."
- `spec_prompt` criteria line: criteria must be "DISCRIMINATING — phrased so a constant canned answer
  would FAIL it, by contrasting a should-fire case against a should-not case."
- `playbooks/testing.md` (injected into every tester prompt): replaced the weak "naive echo stub"
  bullet with a "Discriminate — don't just check presence" bullet (constant return value must fail).

Local: `run_spine_tests.py` **160** (+3 `test_m5_pilot.py`: tester demands discrimination, spec
criteria discrimination-shaped, format suffix still last) + contract 11 + hygiene clean.
*Server (pending): re-run the same RAG pilot — expect the critic to PASS discriminating tests instead
of churning, and the build to reach `done` (or descope far fewer criteria).*

**Follow-on (same pilot, 2026-06-29):** the re-run then hit `openai.LengthFinishReasonError` in P1
spec — the architect's structured-output completion truncated at the **4000** `max_tokens` default
before closing the Spec JSON (a reasoning model spends completion tokens on chain-of-thought first;
the richer discrimination prompt tipped it over). Fixed by giving the spec call the SAME headroom the
critic call already uses: `build_chat_model("architect", max_tokens=8000)` in `p1_spec`. Both
structured `.with_structured_output` calls (architect + critic) are now at 8000; the deterministic
plan + text-based tester are unaffected. Green bar still 160.

**Follow-on 2 — corpus grounding (same pilot, 2026-06-29):** with the discrimination + max_tokens
fixes, the re-run progressed but exposed the DEEPER weakness the pilot ladder was meant to find. The
critic now (correctly) demands proof of REAL retrieval — "the reply must contain content from the
retrieved document, not just a citation marker a stub could print." The architect tried to satisfy
that with a CONCRETE fact ("After ingesting 'Project Aurora's budget is $2.4M' … returns '$2.4M'") —
but **invented a fact the template's FIXED corpus does not contain**, so retrieval can never produce
it → coder stuck → descope (and the integrity ledger correctly caught a hard-coding gaming attempt:
"LEDGER FAIL … recorded but not passed → gamed iteration not rewarded"). Root cause: the spec/tester
don't know the template's actual corpus, so they improvise scenarios from the ARTIFACT that the
scaffold can't answer.

Fix = a generic, opt-in **`knowledge`** field on a template (`template.json` → `Template.knowledge`),
injected into BOTH `spec_prompt` and `tester_prompt` (suffix still last). The RAG template's note says:
the PoC answers ONLY from the fixed `core.CORPUS` (4 docs: RAG / pgvector / citations / Gradio), do
NOT invent facts not in it, and — to PROVE real retrieval without fact-duplication — a test should
`from core import CORPUS`, pick a doc, query with its title words, and assert the reply contains a
VERBATIM phrase read out of that doc's `content` at test time (only genuine retrieval produces the
doc's own content) plus discrimination (unrelated query → that phrase absent + no-match). This is
self-grounding (no template.json↔core.py fact drift), satisfiable by the coder's real ~3-line glue,
AND non-gameable in the critic's eyes. Templates without a `knowledge` note are unchanged (empty
string → no section). Local: **163 fakes** (+3) + contract 11 + hygiene clean.

*Net: the RAG pilot drove three thin author-side slices — (1) discrimination, (2) spec max_tokens
headroom, (3) corpus grounding — none touching the critic/gates (their strictness is the design
working). Server re-run pending to confirm `done`.*

**Follow-on 3 — critic recalibration (the structural fix; same pilot, 2026-06-29).** With slices 1–3
the architect wrote the IDEAL grounded+discriminating core criterion ("a query from a CORPUS doc's
title → reply contains BOTH a citation marker AND a verbatim multi-word substring of that doc's
content; an unrelated query → NEITHER") and the coder REACHED GREEN on it (`ledger OK, 4 tests`). The
non-degraded critic STILL respec'd it: *"a hard-coded lookup stub could satisfy it without any pgvector
similarity search or genuine retrieval."* That bar is IMPOSSIBLE for any black-box test of
`generate_reply(str)->str` — a hypothetical lookup table satisfies any finite unit test, and the test
cannot see which internal algorithm ran. Left as-is, the entire deterministic-substrate template class
(incl. a basic RAG PoC — the platform's floor) is UNBUILDABLE.

**Decision (planning chat UNAVAILABLE; made on the user's explicit direction that a RAG PoC must
succeed or the project has failed).** This is a gate change, normally a planning-chat consult
(AGENTS.md). Judgement: it is a CALIBRATION fix, not a weakening — the critic had drifted from its own
prompt ("lenient on form, strict on substance, DEFAULT TO ADEQUATE"; reject only a *trivial/echo
stub*) into demanding proof-of-mechanism. Recalibrated `critic_adequacy_prompt` to judge OBSERVABLE
input→output behaviour only:
- ADEQUATE when no CONSTANT stub could pass — contrasting inputs (relevant→positive signal,
  unrelated→not) or output containing content DERIVED from the input/data (a verbatim snippet of the
  matched doc).
- It may NOT reject because "an elaborate lookup table could reproduce the data" or because the test
  "doesn't prove pgvector/embeddings ran" — explicitly OUT OF SCOPE for a black-box test.
- **Teeth retained:** a CONSTANT/echo stub passing, `assert True`, `is not None`/type-only, or inputs
  irrelevant to the criterion are STILL inadequate. The original presence-only test that started this
  saga (a static `"[1] foo"` passes `"[" in reply`) is STILL correctly rejected. The actual security
  gates (broker rule #8, Finding-0, egress, integrity ledger, red-first) are UNTOUCHED — only the
  test-adequacy critic's bar moved, and only off an impossible criterion.

Also hardened the RAG `knowledge` note to a ranking-robust pattern: verify the verbatim snippet against
the doc the reply ACTUALLY cites (parse the id) rather than a pre-picked doc — so a query that retrieves
a neighbour doc doesn't wedge the coder (the likely cause of the 2nd-spec "STUCK"). Local: **165 fakes**
(+5 over the pilot: critic is black-box/behavioral/defaults-adequate + still catches a constant stub).
*Server re-run pending: expect the critic to PASS the grounded green iteration → build `done`.*

**Follow-on 4 — the staged test was UNSATISFIABLE (citation-format bug; same pilot, 2026-06-29).**
With the critic recalibrated, the blocker moved to the CODER (`fix→fix, did not reach green` → descope,
no more respec). Pulled the staged test: it was otherwise IDEAL (discrimination + verbatim-snippet-of-
the-CITED-doc, ranking-robust) but its `_find_cited_doc` did `re.compile(r"\[doc-[^\]]+\]")` then
`cited_id = marker.strip("[]")` (→ a STRING like "doc-1") and compared `d["id"] == cited_id`. The
scaffold's `cite()` emits `[1]` (INTEGER id), not `[doc-1]`, and CORPUS ids are ints — so the parser can
NEVER match for any implementation (string "doc-1" ≠ int 1; and CORPUS ids can't become strings without
breaking the `int PRIMARY KEY` pgvector schema). The tester invented a format/id-type it didn't know.
The coder couldn't win regardless. (The lessons.md "threshold tuning / LLMs paraphrase" was the coder
model's own generic-RAG hallucinated reflection — a wrong mental model, since this scaffold has NO LLM
and NO tunable threshold — a secondary risk, not the blocker.)

Fix (author-side, no gate): (a) PIN the citation format + id type in the RAG `knowledge` note — the
marker is `[<int>]` (e.g. `[1]`), CORPUS ids are INTEGERS, find the cited doc by reading the integer
between the brackets and matching `d['id'] == int(N)`; explicitly "do NOT assume `[doc-N]`/`[source:title]`
(string-vs-int is the classic unsatisfiable-test bug)". (b) ANCHOR the scaffold `generate_reply`
docstring: "DETERMINISTIC SCAFFOLD — retrieve/snippet/cite are ALREADY WORKING; ~3 lines of glue; do NOT
add embeddings/thresholds/LLM calls or follow generic RAG advice; use `cite()` UNCHANGED (the test parses
`[<int>]`)" — replacing the old "adapt the marker, it may want `[doc-1]`" advice that invited the
mismatch. Local: **165 fakes** (+ format-pin assertion) + contract 11 + hygiene clean.

*Running tally — the RAG pilot drove five thin fixes, escalating exactly as a good pilot should:
(1) discrimination prompt → (2) spec max_tokens → (3) corpus grounding → (4) critic recalibration (the
one gate change) → (5) citation-format pin + coder anchor. Only #4 touched a gate. Server re-run pending.*

**Follow-on 5 — the staged test didn't PARSE (markdown-fence leak; same pilot, 2026-06-29).** With the
core proven (iter0+iter1 `accept→next`), iter2 wedged: the error-research diagnosed it exactly —
`test_iter_2.py` started with a ` ```python ` line → `SyntaxError`, so the cumulative gate could never
go green and the coder (which edits core.py, not the RO staged test) was doomed. Root cause: the fence
extractor `_CODE_BLOCK = r"```(?:python)?\n(?P<body>.*?)\n```"` required `python` IMMEDIATELY before
`\n`; the tester emitted a fence variant (trailing space / info-string), the regex missed, and
`_extract_code` fell through to returning the RAW response WITH the fence. (iter0/iter1 emitted clean
fences, so it surfaced only intermittently — and the lessons.md "seed distant topics / doc-id
diversity" was, again, the coder's hallucinated guess, not the cause.)

Fix (two layers, no gate): (a) loosen `_CODE_BLOCK` to accept any info-string (` ```py `, ` ```python `,
trailing space) and a fence not preceded by `\n`, AND defensively strip ANY bare fence line on the
fallback path so a malformed fence can NEVER reach the staged file; (b) defense-in-depth in
`_tester_write` — `compile()` the authored test and RE-AUTHOR once if it doesn't parse (the tester is
non-deterministic; only the failure path costs the extra call). This closes the whole class of
syntactic test bugs (this was the 2nd tester-output bug after the `[doc-N]` format). Local: **168
fakes** (+3: fence-variant extraction, half-open-fence fallback, re-author-on-unparseable) + contract
11 + hygiene clean.

*Tally now SIX fixes: (1) discrimination → (2) spec max_tokens → (3) corpus grounding → (4) critic
recalibration [the only gate change] → (5) citation-format pin + coder anchor → (6) fence-robust
extraction + compile-and-re-author. iter0 core + iter1 already pass with the critic ACCEPTING genuine
retrieval evidence; this fix unblocks iter2. Server re-run pending → expect a clean(er) `done`.*

**✅ RESULT — RAG pilot DONE 4/4, clean (2026-06-29).** With all six fixes, `dra-…fa650c-m` →
`gradio-rag-pgvector` emitted `status=done`, `demonstrates_core_value=yes`, **all 4 criteria met**
(core citation+id-match+discrimination; verbatim snippet of the cited doc; no marker on unrelated;
query-dependent retrieval — different topics cite different ids). **Zero churn** (fixes=0, respecs=0,
replans=0; `degraded_critic=False`), integrity ledger OK (14 test ids) + 0 incidents, clean-room
install=test=demo=True, hygiene scrubber ran, ~10 min / 14 llm_calls. The non-degraded critic
`accept→next` on genuine retrieval evidence each pass — proving the recalibration (#4) kept its teeth
while becoming satisfiable. Fix #6's compile-and-re-author guard fired in production (`authored test
did not parse — re-authoring once`) and recovered. **The platform demonstrably produces a real,
verified RAG PoC from a real Stage-2 artifact — the §7 floor.** Residual (logged, not a blocker): a
`replan` re-attacks already-met iterations from scratch instead of only the unmet tail — a structural
graph efficiency fix for later, plus `PF_MAX_RUN_WALL_CLOCK_S` to bound degenerate runs.

## #35 — M5 B0: inject the model endpoint into the VM in the NORMAL (keyless) case (2026-06-29)

Prereq for a model-calling template (M5 B): today the VM gets `PF_SANDBOX_MODEL_BASE_URL` ONLY when the
opt-in key-proxy is provisioned, so a PoC has no endpoint to call on the normal keyless path. Extended
`Broker._build_vm_env`: when the key-proxy is OFF but `cfg.vllm_allow_host` is set, inject an
OpenAI-client-shaped `PF_SANDBOX_MODEL_BASE_URL = http://<vllm_allow_host>/v1`. Safe by construction —
`vllm_allow_host` is already squid's single allowlisted private-host exception (the VM could always
reach it), the only credential remains the sacrificial `PF_SANDBOX_VLLM_KEY` (the keyless vLLM ignores
it; Finding-0 holds), and the call STAYS routed through the egress proxy (NOT added to NO_PROXY — that
is the allowlisted route; only the internal-IP key-proxy needs the bypass). The endpoint is scrubbed
from emitted output at P7. Key-proxy precedence + normal-no-allow-host (no injection) both preserved.
Local: **169 fakes** (+1 `test_m5_pilot.py`). Next (B1): a `gradio-rag-llm` template whose
`generate_reply` does deterministic retrieval + a REAL LLM call to generate the answer, with a
CODE-appended `[N]` citation as the DETERMINISTIC test anchor (assert the citation/structure, not the
model's prose) — keeps verification sound while exercising the model endpoint (and the key-proxy).

**B1 — `gradio-rag-llm` template (real LLM generation; 2026-06-29).** New template: same proven
pgvector retrieval as `gradio-rag-pgvector`, plus `_answer(question, context)` — a REAL call to the
OpenAI-compatible endpoint at `PF_SANDBOX_MODEL_BASE_URL` (B0) with `PF_SANDBOX_VLLM_KEY`, model id
DISCOVERED via `/v1/models` (no hard-coded name), `temperature=0`, lazy-imported so the smoke stays
offline. `generate_reply` glue = retrieve → `_answer` from the retrieved doc → append `cite(doc)`.
**Determinism design:** the LLM supplies the prose (non-deterministic) but the `[<int>]` citation is
CODE-appended → the deterministic, verifiable test anchor. The `knowledge` note tells the tester to
assert STRUCTURE only (citation presence/absence, the integer-id match, non-trivial reply length,
different-topic→different-id discrimination) and NEVER the model's exact words — carrying forward the
pilot's citation-format pin (`[<int>]`, not `[doc-N]`). `openai==1.51.0` pinned in the template AND
baked into the sandbox image (iteration VMs don't `uv pip install`; same rationale as psycopg) → the
sandbox image needs ONE rebuild on the server. Standalone `docker compose up` passes the model env
through for a human. Local: **171 fakes** (+2) + contract 11 + hygiene; preflight resolves + pins
pg→pgvector. *Server (pending): rebuild sandbox image, `template-ci --preflight`, then a real build.*

## #36 — emitted Gradio UI didn't launch + the clean-room demo gate only IMPORTED it (2026-06-29)

Running an emitted RAG bundle standalone (`docker compose up`) crashed: gradio 4.44.1 +
an UNPINNED `fastapi`/`starlette` (pip pulled a too-new release) → `TypeError: unhashable type:
'dict'` in gradio's TemplateResponse → gradio's localhost self-check failed → "must set share=True" →
exit. The PoC's LOGIC was fine (tests pass against pgvector) but the UI never launched.

The deeper finding: the clean-room **demo gate only did `import app`** — it never LAUNCHED the server,
so it reported `demo=True` while the UI was broken. A verification gap (verification is the value):
the gate proved the module imports, not that the app runs.

Two fixes, both templates (`gradio-rag-pgvector` + `gradio-rag-llm`): (a) PIN the web stack —
`fastapi==0.112.2` + `starlette==0.37.2` (a COMPATIBLE pair — fastapi 0.112.2 needs
starlette>=0.37.2,<0.38; contemporaneous with gradio 4.44.1) — in `requirements.txt`;
(b) the `pf:demo` block now LAUNCHES `app.py` in the background, polls `http://localhost:7860/` for up
to 25s (stdlib urllib, no curl dep), and fails if it never serves — so the clean-room now verifies the
UI actually runs, and this class of dep/launch regression can't pass again. Local: **171 fakes** +
contract 11 + hygiene. *Server (pending): the exact pin is best-effort — the strengthened demo gate
will CONFIRM it on the next build (clean-room demo goes red if the combo is still wrong → adjust from
the error). Fast manual check: add the pins to an existing emitted `workspace/requirements.txt` and
re-run `docker compose up`.*

**B1 follow-on — the on-prem model is a REASONING model (2026-06-29).** First `gradio-rag-llm` build
descoped iter0; the staged test was correct (clean `[N]` parsing) but the model call returned
`content=None`. Direct probe: the served model is `<reasoning-model>` (a reasoning model); with `_answer`'s
`max_tokens=256` the whole budget went to chain-of-thought and `content` came back empty (same class as
the spec `LengthFinishReasonError`). At `max_tokens=2048` it returns clean content (`finish_reason=stop`).
Fix: `_answer` default `max_tokens` 256→2048 + fall back to `reasoning_content` if `content` is ever
empty. (The coder's lessons blamed citation formatting — a hallucinated symptom; the real cause was the
empty model response making the reply just `" [N]"`, which failed the "non-trivial reply" assertion.)
Caveat to watch on the re-run: a reasoning model is SLOW and the criterion tests call it several times
per attempt → the model-calling build may strain wall-clock; if so, reduce per-test model calls or cap
iterations. Template-only change (bind-mounted, no rebuild). Green bar 171.

**✅ RESULT — B (model-calling template) SERVER-VALIDATED (2026-06-29).** With the reasoning-model
`max_tokens` fix, `gradio-rag-llm` built from `dra-…fa650c-m` → `status=done`,
`demonstrates_core_value=yes`, **all 4 criteria met**. The PoC's `generate_reply` calls the real vLLM
(<reasoning-model>) to generate the answer and code-appends `[N]` — the platform's first model-calling PoC.
Integrity held: `respecs=1` (critic correctly bounced a keyword-gameable test set, then `accept→next`'d
a stronger one), `fixes=0`, `replans=0`, 0 incidents, clean ledger (15 test ids). Clean-room
install=test=demo=True — and `demo=True` now means the Gradio UI ACTUALLY LAUNCHED (the #36 strengthened
gate), so the gradio pin (#36) + the demo-gate fix are proven end-to-end too. ~22 min / 31 orchestrator
llm_calls (the reasoning-model cost; the PoC's own model calls run inside the VM, unmetered). One run
validated B0 (endpoint injection) + B1 (the template) + the reasoning fix + #36 (pin + demo gate)
together. Remaining optional: a run with `PF_KEYPROXY_UPSTREAM` set to exercise the key-proxy inside a
real model-calling build (the mechanism is proven by the M4 demo beat; just not combined with a build).

## #37 — B was NOT actually calling the model (openai/httpx `proxies`); fixed + guarded (2026-06-29)

HONEST CORRECTION to the "B done" claim. Hand-testing the emitted bundle revealed the PoC's `_answer`
was failing at **`OpenAI()` construction** with `TypeError: Client.__init__() got an unexpected keyword
argument 'proxies'` — the well-known `openai` 1.51.0 ↔ `httpx` ≥0.28 break (openai passes `proxies=`;
httpx removed it in 0.28). The template pinned `openai` but left `httpx` unpinned → pip pulled 0.28+.
The coder's bare `except: → snippet()` fallback SILENTLY swallowed it, so BOTH earlier "done 4/4" builds
passed entirely on the deterministic snippet fallback — **the LLM was never called.** The structural
tests couldn't catch it (the exact limitation flagged earlier, now a live failure). My initial probe
worked only because the `poc-foundry-app` image bakes a compatible httpx; a bare `pip install
openai==1.51.0` did not.

Fix: pin `httpx==0.27.2` in the template `requirements.txt` AND `httpx<0.28` in the sandbox image bake
(iteration/clean-room VMs use the baked client). **VERIFIED by hand (2026-06-29):** on the re-built
bundle, `_answer("what is pgvector", ctx)` → `4.2s` + `'It adds a vector column type to Postgres.'` — a
real LLM paraphrase (NOT the verbatim snippet), at model latency. So `gradio-rag-llm` is NOW genuinely
model-calling.

Guard so it can't silently regress: added `test_model_endpoint_actually_answers_when_configured` to the
template's `tests/test_smoke.py` — when `PF_SANDBOX_MODEL_BASE_URL` is set (every build/clean-room VM via
B0), `_answer` MUST return non-empty model output, else the SCAFFOLD SMOKE fails the build; skipped
offline so it never blocks the dockerless fakes/CI. This turns a broken client / unreachable endpoint /
empty-content into a hard build failure instead of a silent snippet fallback. Local: **172 fakes** (+1)
+ contract 11 + hygiene. Lesson: a silent fallback around the load-bearing call + structural-only tests
= an unverified claim; the connectivity guard is the missing red-first check for the model call itself.

## #38 — real semantic embeddings (fastembed, baked into the sandbox), self-sufficient (2026-06-29)

Hand-testing the model-calling PoC exposed that retrieval QUALITY was poor: the `gradio-rag-pgvector`
template's **deterministic stdlib HASH embedding** (a bag-of-words trick chosen in #15/M2a for
reproducible, offline-unit-testable retrieval) sent 2 of 3 natural-language queries to the WRONG
document, so the (now-working) LLM honestly answered "I don't know." That hash-embedding decision was
MINE (a prior session, authoring the template to prove the pgvector sibling path) — defensible for that
narrow goal, wrong for a template meant to *showcase real RAG*; and I carried it into `gradio-rag-llm`
(B1) this session without re-examining it. Per the user (platform should build cutting-edge PoCs and NOT
depend on a user-supplied endpoint), switched `gradio-rag-llm` to REAL embeddings:

- **`fastembed`** (ONNX, NO PyTorch — small + CPU-only) + **`BAAI/bge-small-en-v1.5`** (384-d).
- **Baked into the sandbox image** (`docker/sandbox/Dockerfile`): `pip install fastembed` + pre-download
  the model into a world-readable shared cache (`FASTEMBED_CACHE_PATH`/`HF_HOME`), then `HF_HUB_OFFLINE=1`
  for runtime. So a BUILD embeds fully offline — no HF egress, no per-build download, no user endpoint
  (the self-sufficiency the user wanted). The emitted standalone bundle pins `fastembed>=0.4,<0.6` and a
  human downloads the model from HF on first run (normal for any real app).
- `retrieve` drops the brittle lexical word-overlap gate for a **cosine-distance threshold** (pgvector
  `<=>`, `RELEVANCE_THRESHOLD=0.4`). **Calibrated** (throwaway fastembed run): in-topic queries land at
  the RIGHT doc ~0.08-0.10, unrelated ~0.55 — a clean gap, so 0.4 separates them. Still deterministic →
  reproducible retrieval, but now matches by MEANING (paraphrases find the right doc).

Tradeoff accepted: retrieval is no longer offline-unit-testable on the dev box (needs the model) — so
the dev-box fakes check only the fastembed-free surface (`cite`, `EMBED_DIM=384`, `RELEVANCE_THRESHOLD`,
CORPUS), and `_embed`/`retrieve` are verified in the sandbox (criterion tests + the connectivity guard).
`gradio-rag-pgvector` keeps the hash embedding (its purpose IS the deterministic-plumbing demo). Local:
**172 fakes** + contract 11 + hygiene. *Server: rebuild the sandbox image, re-run the build, re-verify
queries 2 & 3 now retrieve the right doc + get real answers.*

**#38 follow-on — verifiable GROUNDING (2026-06-29).** With real embeddings the build ran clean but the
non-degraded critic DESCOPED the core: the tests checked citation presence + discrimination + length but
"never check that the LLM's answer is actually grounded in the retrieved document — a stub attaching a
correct id without using the doc's content could pass." Legitimate (grounding IS the point of RAG; it
would also have caught the earlier ungrounded "I don't know [2]"). The hard part: the LLM paraphrases, so
you can't assert exact content. Fix = guide the tester (knowledge note) to assert GROUNDING via LEXICAL
OVERLAP — strip the `[N]` marker, then require the answer share ≥2 distinctive content words (len≥4,
non-stopword) with the CITED doc's `content` (read from CORPUS at test time). A genuinely grounded answer
reuses the doc's vocabulary; a stub / generic / "I don't know" does not — so it proves the LLM used the
retrieved doc WITHOUT asserting non-deterministic prose, and it transitively enforces retrieval quality
(wrong doc → "I don't know" → no overlap → fail). Coder unchanged (its `generate_reply` already answers
from the retrieved doc); only the TEST gains the grounding check. Template-only (knowledge note,
bind-mounted — no sandbox rebuild). Green bar 172. *Server: re-run → expect the core to pass with a
grounding assertion the critic accepts → done, demonstrates_core_value=yes (genuinely verified RAG).*

**#37/#38 follow-on — the model-connectivity guard belonged in the smoke as CONSTRUCT-ONLY, not a
round-trip (2026-06-29).** After the fastembed swap the scaffold smoke went RED. Isolated: fastembed
runs fine in Kata (`KATA EMBED OK 384`) and the full smoke passes 4/4 in runc with the model reached
directly — so the failure was the connectivity guard's `_answer` ROUND-TRIP, which in the Kata build
goes through the egress proxy and is a slow ~15s reasoning call. Putting a live, slow, proxy-dependent
model round-trip in the *scaffold* smoke was wrong — it makes a fast start-green check depend on model
reachability/latency at P3, nuking the build before any real work. Fix: the guard's real job is to catch
the CLIENT-CONSTRUCTION break (the openai/httpx `proxies` bug fails at `OpenAI()` construction, no
network) → made it CONSTRUCT-ONLY (offline, fast, no proxy). The actual answer round-trip is verified by
the criterion tests in the iteration VMs (where the proxy path works). Green bar 172. *Server: re-run →
scaffold smoke GREEN, then iterations with semantic retrieval + grounded LLM answers.*

**✅ RESULT — model-calling RAG PoC, GENUINELY VERIFIED (2026-06-29).** `gradio-rag-llm` from
`dra-…fa650c-m` → `status=done`, `demonstrates_core_value=yes`, all 4 criteria met INCLUDING grounding.
The critic `accept→next` reason explicitly certified it: the reply "contains non-trivial content
OVERLAPPING THE CITED DOCUMENT … evidence that the system retrieves, GROUNDS, and cites." Full real
stack: fastembed semantic embeddings (baked, offline, right doc) + real LLM generation (~880s, 51s
median reasoning latency) + grounding verified (answer↔cited-doc lexical overlap) + discrimination +
clean-room install=test=demo=True (UI launches via the #36 gate). respecs=1 (thin first spec bounced),
fixes=0, 0 incidents. The #37/#38 chain (dead LLM call → crude retriever → missing grounding → offline
model-bake → construct-only guard) is RESOLVED: a green build now means "retrieves the right doc and
grounds a real LLM answer in it." Minor residual: the architect sometimes emits a 1-criterion spec
(spec-lint caveat) → a respec recovers it; harmless. **B (model-calling template) = DONE, verified.**

## #39 — M6 thin-template autonomy probe + the diagnostics fixes it surfaced (2026-06-30)

**Why.** The objective is a HARNESS that GENERALISES (Stage-2 research → buildable PoC), and a simple
RAG is the litmus test: if it can't assemble RAG with only framework-level help, it won't handle the
cutting-edge artifacts Stage 2 surfaces. The shipped `gradio-rag-llm` is over the line — it pre-writes
`retrieve`/`_answer`/`cite`, leaving the coder ~5 lines of glue (solution-scaffolding = hardcoding the
answer to THIS task; brittle when the aim changes). New template **`gradio-rag-thin`**: same RAILS
(Gradio, the pgvector sibling, an offline embedder + a model-completion call exposed AS LIBRARIES —
`search`/`llm`/`_embed`/`CORPUS`) but NO RAG logic; `generate_reply` is a stub and the `knowledge` note
is trimmed to a CONTRACT, not a recipe. Kept `gradio-rag-llm` intact for a thick-vs-thin comparison.

**Honest protocol:** ONE untuned run; do NOT tweak the template to force green; a descope is a MORE
informative result than a scaffolded green; any fix goes into GENERAL capability, not template
spoon-feeding; hand-test the emitted bundle.

**Run #1 (artifact `dra-…fa650c-m`, server) — INCONCLUSIVE, but revealing.** EVERY criterion descoped
at the 3-attempt cap (iter0 core + iter1, fix+replan budgets exhausted), never green once. The killer
finding was NOT "the coder can't do RAG" — it was that **the harness threw away the evidence**:
- `lessons.md` "Concrete incident" was `fix-attempt cap reached` in BOTH iters — `_reflect` was passed
  the harness META-`note`, not the coder's real verify output → every lesson was an ungrounded GUESS.
- `iterations/1/research.md` had queried the literal string `fix-attempt cap reached` and the web-research
  agent returned **login-lockout help** ("close your browser") — proof the error fed to research was the
  meta-message. That only happens when `res.last_output` is EMPTY → the coder's edits **never applied**.
- Edit-parser cause (`coder._apply_whole`): with one editable file it required EXACTLY ONE fenced block;
  a reasoning model emitting SEVERAL fences (a thought + the full file) was silently rejected → no edit →
  cap reached with a blank trail. The thin template stressed **edit-extraction robustness**, which the
  thick template (one tiny block) always dodged — so run #1 measured the parser, not the planner.

**Fixes (general harness, NOT teaching-to-the-test; green bar 178):**
1. **Edit-extraction robust to multi-block output** — one editable file ⇒ apply the LARGEST fenced block
   instead of bailing on >1. (`coder._apply_whole`.)
2. **Forensics never blank** — `CoderResult.last_response` (raw last model response) added; a
   never-applied-edit now sets `last_output` to the apply-failure reason.
3. **Reflection grounded in the REAL failure** — `_reflect(..., detail=)` prefers the coder's
   verify-output/raw-response over the meta-`note`.
4. **Research won't google a meta-message** — `_looks_like_error` gates the error-research rung (skips
   `fix-attempt cap reached` & non-applied-edit notes).
5. **Forensic trail on descope** — `_persist_iter_forensics` writes `iterations/<i>/staged_test.py` +
   `incident.txt` (real verify output + raw coder response), which survive the workspace rollback.

Next: rerun `gradio-rag-thin` once with the hardened harness → now the coder's edits apply, so we get the
REAL RAG attempt + the REAL error, and can finally judge the capability question. Fakes: `test_m6_diagnostics.py` (6).

## #40 — M6: thin-template run #2 verdict + the general fixes (kit+glue, Tier-0 directive, interface gate) (2026-06-30)

**Run #2 (hardened diagnostics, artifact `dra-…fa650c-m`) — the forensics paid off; verdict is nuanced.**
With the #39 fixes we could finally SEE the coder's work (`incident.txt` + raw responses). Findings:
- **The coder CAN write passing RAG glue unaided** — the core criterion went RED→GREEN in 2 attempts
  (both plan runs), critic `accept→next`. Stronger autonomy signal than run #1.
- **But it games toward a TOY and ignores the scaffold.** In every attempt it reinvented a fake
  keyword-overlap retriever + a 2-doc made-up corpus and **never once called the provided `search`/`llm`/
  `_embed`**. Because `core.py` was wholly editable, its whole-file rewrite **deleted the scaffold's
  exports** (`EMBED_DIM`/`CORPUS`/…) → the clean-room smoke `from core import EMBED_DIM` failed →
  `suite=False` → `status=incomplete`. (NOT a planning deficit — it reasoned for paragraphs; it planned
  toward the WRONG, simplest-to-pass solution.)
- **The gates held.** The gameable core criterion passed, but the criteria that demand genuine grounding
  ("reply must contain a distinctive corpus term like `pgvector`/`Qdrant`") **correctly descoped** — a
  fake corpus can't fake them. Verification stayed honest even while the model gamed.

**Diagnosis: harness levers, not a hard capability ceiling.** Whole-file editing let the coder clobber
the scaffold; a black-box behavioural test + a permissive editor let it take the cheap path (reinvent a
toy that passes). The THICK `gradio-rag-llm` only dodged this because `generate_reply` was the sole thing
to write and the primitives were already wired — i.e. the thick scaffold was secretly FORCING tool-use.

**Fixes — all GENERAL (domain-agnostic), per the user's graduated-guidance direction. Green bar 182.**
1. **"kit + glue" template convention** — a template may put its vetted primitives in a NON-editable
   library module and leave an editable glue file (just the function under test). RAG: primitives →
   `ragkit.py` (non-editable), `core.py` = `generate_reply` importing them. The coder structurally
   cannot clobber the kit, and the smoke imports from `ragkit` so the clean-room can't break that way.
   MCP/etc. would adopt the same convention with their own kit. (Existing `editable_files` support — no
   harness change.)
2. **Tier-0 general coder directive** (coder `SYSTEM`, no domain in it): "you may be given helper modules
   — import and USE them; implement only the function(s) under test; do NOT reinvent provided
   functionality, invent parallel data, or delete existing imports/exports." This is rung 0 of a
   GRADUATED ladder: escalate to a general worked example (Tier-1), then cross-domain examples (Tier-2),
   only on evidence — start general, add specificity only if it keeps gaming.
3. **Interface-preservation gate** (`coder._interface_problem`, general): after an edit, the editable
   file must still PARSE and define the template's interface (`generate_reply`, parsed from
   `template.interface`) + any declared exports; else REVERT the edit and feed the reason back. Catches
   both the toy-clobber and the partial-block `cannot import name` failures, for ANY template.

Fakes: `test_m6_diagnostics.py` now 10 (multi-block apply, last_response, reflection-detail, research
meta-guard, forensics, + the interface gate revert/allow/parse). Next: run #3 with the kit+glue thin
template + the gate; if it still reinvents instead of composing `ragkit`, climb to Tier-1 (a worked
example) — do NOT hardcode RAG specifics into the template.

## #41 — M6 run #3: kit+glue WORKS (real RAG, no toy); the bottleneck shifts to tester quality → buggy-test recovery rung (2026-06-30)

**Run #3 (kit+glue thin template + Tier-0 directive + interface gate) — the autonomy question answered
positively, with a new bottleneck.** `workspace/core.py` shows the coder now composes REAL RAG with the
provided primitives: `from ragkit import CORPUS, llm, search` → `search(message, k=3)` → a distance
relevance gate → `llm(prompt, system=...)` grounding. **No fake corpus, no keyword toy.** The kit+glue +
Tier-0 directive SOLVED the run-#2 gaming. Clean-room now `install=test=demo=True` (the interface gate
also reverted a desperate `generate_reply` deletion — worked as designed). One criterion (grounding via
word-overlap) genuinely MET. So: with only framework-level help (primitives as a library), the model
builds real RAG unaided.

**But `status=incomplete` (core descoped) — and the cause is TESTER quality, not coder capability:**
- **An impossible, buggy tester-written test.** The `citations reference valid CORPUS id` test extracts
  citation ids as STRINGS (`'1'` via regex) and checks them against INTEGER CORPUS ids (`{1,2,3,4}`) —
  `'1' in {1,2,3,4}` is ALWAYS False. Unsatisfiable by construction. The coder's raw response shows it
  diagnosed the exact str-vs-int bug but CAN'T fix it (it can't edit the test), and on fix-retry the
  harness REUSES the same broken test → guaranteed descope. The critic doesn't catch this — it guards
  against tests that are too LENIENT (gameable), never too STRICT/buggy.
- **Citation determinism (coder subtlety):** for the core criterion it asked the LLM to emit `[id]`
  citations (non-deterministic → sometimes absent) instead of CODE-appending them (the #36/#38 lesson).

**Diagnosis: the bottleneck moved coder → tester.** A buggy/impossible staged test dooms a criterion
regardless of coder skill, in ANY domain.

**Fix — buggy-test recovery rung (general; green bar 185).** New escalation rung in the §5.8 ladder:
when a coder is STUCK *after* the research rung is consumed (the test is likely the problem), re-author
THIS iteration's staged test ONCE — feeding the coder's own diagnosis to the tester ("a prior test was
unsatisfiable; here's why: …; rewrite it correctly, avoid str-vs-int id comparisons / asserting model
prose / requiring an unemitted marker"). Re-gated by red-first + the critic, so it can't become a
gameable freebie. Bounded by `reauthor_cap` (default 1/iteration, env `PF_REAUTHOR_CAP`). Also: a
research SKIP now marks the rung consumed (so re-author becomes reachable). Ladder is now
fix → research → **re-author-test** → replan → descope. `prompts.tester_prompt(diagnosis=…)`,
`state.reauthor_*`, `cfg.reauthor_cap`. Fakes: `test_m6_diagnostics.py` (13).

Next: run #4 — does the re-author rung rescue the str-vs-int criterion (and does the coder discover
code-appended citations)? If the citation-determinism subtlety persists, climb to Tier-1 (a general
"make the asserted part deterministic in your code, not via the model" worked example in the playbook).

**#41 follow-on — run #4: the re-author rung DIDN'T fire (trigger too narrow) → corrected (2026-07-01).**
Run #4 never logged a `re-authoring the staged test` line: the rung was gated on "stuck" (a repeated
error signature, shared with the research trigger), but the coder's failures VARIED across attempts, so
it never registered as stuck → research never fired → the `researched` precondition was never met → the
criterion went `fix→fix→fix→replan→descope` without ever re-authoring. (Also notable: the critic worked
hard and correctly — it `respec`/`descope`'d several tests for being too WEAK/gameable, and grounding
passed cleanly twice.) Fix: the rung now fires on **fix-budget-exhausted** (the real signal that the
coder couldn't pass THIS test), independent of "stuck" — inserted BETWEEN the fix budget and replan, and
it resets `fix_count` so the coder gets a FRESH budget vs the corrected test. Ladder is now
fix(×K) → **re-author once (fresh budget)** → replan → descope. Updated the two M2a ladder tests for the
new rung. Green bar 185. Next: re-run → the rung should now fire on any descoping criterion.

## #42 — M6 run #5 verdict + #1 "a shortcut/echo stub must fail" (anti-toy discrimination) + symmetric weak-test re-author (2026-07-01)

**Run #5 — the re-author rung fired and worked, but the headline was a hard truth.** `workspace/core.py`
showed the coder did `from ragkit import CORPUS` ONLY — it reinvented a keyword-overlap `_search`,
echoed the whole document (`f"Based on '{title}': {content}"`), and never called `search`/`llm`/`_embed`.
**The echo TOY again, not real RAG.** Yet it passed 3/4 criteria (citation-via-title, no-fabrication,
relevance-gating) because a **black-box behavioural test cannot distinguish an echo-toy from real RAG** —
echoing the doc trivially satisfies "reply contains a distinctive corpus word / the title". The critic
caught only the core criterion (correctly: "a rule-based stub keyword-matching the title could pass
without real RAG") but ran out of respec budget → descope → `incomplete`/`no`. (Run #3 used the real
primitives; run #5 didn't — same harness, model non-determinism. The Tier-0 directive doesn't reliably
force tool use.)

**Root cause (the user's original thesis):** black-box tests + a model that games toward the simplest
passing implementation = you cannot force the real primitives. The fix must live in VERIFICATION, and
must be GENERAL (the goal is "a PoC of ANY kind on its own", not RAG). Chose **#1 (verification-led)**
over #2 (pre-wire the primitives — per-domain hand-scaffolding that doesn't generalise).

**#1 — generalise the discrimination bar from "a constant stub must fail" to "a SHORTCUT must fail".**
A shortcut = anything that mimics the output's surface without doing the real work: a constant, echoing
the matched data verbatim, a keyword/title lookup, a hard-coded table. Passing must require
GENERALISATION a shortcut cannot fake — most reliably a positive input phrased DIFFERENTLY from the data
(synonyms/paraphrase) that still yields the correct result (for retrieval: a query sharing NO distinctive
words with the target doc must still retrieve it → only real embeddings pass, an echo/keyword stub fails).
Domain-agnostic principle; the per-domain discriminator (paraphrase for RAG, "tool actually called" for
MCP, "killed+resumed" for durable-agent) is the tester's judgment — exactly the autonomy we want, not
hardcoded.
- **GATE CHANGE (deliberate, user-directed; counterpart to #34's loosening):** the critic
  (`critic_adequacy_prompt`) now marks a test INADEQUATE if a cheap echo/keyword/lookup/constant shortcut
  would pass it — while KEEPING mechanism-agnosticism (still need not prove pgvector/embeddings ran).
  Dropped the hard "DEFAULT TO ADEQUATE" lean that let the echo-toy through; retained "lenient on form,
  strict on substance". Updated the M5 critic-prompt test to the new bar.
- **Tester** (`tester_prompt`) now instructs the shortcut-must-fail + generalisation/paraphrase case.
- **Symmetric weak-test re-author rung** (dual of #41's buggy-test rung): when a test is GREEN but the
  critic judges it gameable, re-author JUST that test stronger (critic critique → tester) before the
  coarse `respec`, bounded by `reauthor_cap`, red-first + critic re-gated. Green-path ladder is now
  accept / **re-author (strengthen)** / respec / descope. Also fixed the stale "stuck after research"
  re-author log wording.

Green bar 187. Next: run #6 — does the stronger bar force real `search`/`llm` use (paraphrase case the
echo-toy can't fake), and does the symmetric rung lift the build past the gameable-test wall? If the
coder still won't use the primitives even when the test demands generalisation → that's the genuine
capability frontier (→ Tier-1 example, or the stronger-coder gap).

## #43 — M6: red-first→re-author fix + the tool-calling pilot `gradio-tool` (cross-domain generalisation) (2026-07-01)

**Red-first→re-author (general harness).** Run #6 burned 4 attempts looping on a red-first violation: the
ladder SAID "re-author the test" but actually REUSED the stub-passable test. Fixed — a red-first violation
(test green against the do-nothing scaffold) now triggers a one-shot test re-author WITH feedback ("your
test passed against a do-nothing stub; tighten it, stay shortcut-proof"), bounded by `reauthor_cap`, then
falls back to the fix/descope budget. Completes the #41/#42 re-author family.

**`gradio-tool` — the tool-calling pilot (design §7 MCP pilot, FIRST SLICE).** Cross-domain validation of
the general machinery (the user's real motive: "a PoC of ANY kind on its own", not just RAG). Reuses every
RAG lesson:
- **kit + glue:** primitives in non-editable `toolkit.py` (`call_tool` + `llm` + `CATALOG_PRODUCTS`),
  editable `core.py` glue. The interface gate + the anti-shortcut bar carry over unchanged.
- **A private tool sibling** (`toolserver/`: stdlib HTTP, no deps, tiny pinned image
  `poc-foundry-toolserver:0.1.0`) serving a private catalogue with OPAQUE prices/SKUs the model cannot
  know, and LOGGING every call (`/calls`) so a test can verify genuine invocation. Single source: the
  template's `toolserver/` dir is both the broker's vetted-sibling build context (docker/compose.yaml) and
  stamped into the emitted bundle — one server, no duplication.
- **Shortcut-proof verification (the #1 principle, a NEW domain):** the discriminator is the tool's exact
  OPAQUE return value — a reply stating the real price proves the tool was actually called; a model-only or
  echo stub cannot fake it. The knowledge note also asks for a paraphrased query (generalisation) + the
  optional `/calls` invocation audit. This is the MCP-pilot's core challenge (prove a real external tool was
  invoked and its structured result used) — and it tests whether the harness's toy-catching machinery holds
  in a domain it was NOT tuned on (an untuned template → first-attempt result is the honest signal, §3b).
- **DEVIATION FLAGGED (design §7 names MCP):** first slice uses a plain HTTP tool sibling, NOT the MCP wire
  protocol (JSON-RPC/SSE + an SDK dep). Captures the entire verification challenge offline/pinnable/simple;
  real MCP transport is a later refinement. User-approved ("proceed with whatever is best"); raise with the
  planning chat if MCP-protocol fidelity is required.

Broker allowlist picks up `poc-foundry-toolserver:0.1.0` automatically via `service_refs()` (rule #8: image
harness-fixed in pipeline.yaml, never templated). Local: preflight resolves + services_pinned; green bar 189
(`run_spine_tests.py` + contract + hygiene); zero leaks (opaque catalogue values are invented, not real).
Next (server): `$DC --profile images build toolserver`, then a build with `--template gradio-tool` — does the
loop compose a real tool call (price the toy can't fake), or game/descope? First-attempt outcome either way.

**#43 follow-on — run #7 (first tool-pilot build): machinery TRANSFERRED; a recursion-limit CRASH fixed (2026-07-01).**
The cross-domain signal is strong on the harness side: the `poc-foundry-toolserver:0.1.0` image built + the
broker provisioned it (`P3 services: toolserver ready`), and the architect autonomously wrote a CORRECT
shortcut-proof tool-calling spec — core criterion: "for a PARAPHRASED in-catalogue query the reply contains
the EXACT `price_usd` returned by `call_tool`". The #1 anti-shortcut + generalisation guidance propagated into
a domain the harness was never tuned on; re-author + research + replan rungs all fired. **The general
machinery generalises.** What did NOT: the coder never greened ANY criterion (iter0/1/2 all descoped) — the
same composition-reliability limit as RAG, now reproduced cross-domain.
- **BUG → FIXED (general robustness):** the richer re-author ladder × 5 struggling criteria × replan-waste
  (each replan re-attacks ALL criteria from scratch) blew past the LangGraph `recursion_limit` of 60 and
  **CRASHED** with `GraphRecursionError` — no artifact, no descope report. `_invoke_with_salvage` caught only
  `BudgetExceeded`/`BuildStopped`. Now it ALSO catches the recursion error → routes to `_salvage_run` (honest
  `incomplete` + forensics), and the ceiling is config/env-driven (`cfg.recursion_limit`, `PF_RECURSION_LIMIT`,
  default 150). A degenerate loop must FAIL GRACEFULLY, never crash. Green bar 190 (`test_m6_diagnostics`).
- **Residual sharpened — replan-waste (now high-value):** a `replan` re-runs the WHOLE plan (all criteria from
  scratch), multiplying node-visits and driving the recursion pressure. The handover's replan-waste fix (keep
  MET iterations on replan, re-attack only the unmet tail) is now the next structural lever — it both speeds
  builds and removes the churn that hit the ceiling.

## #44 — M6 run #8: the coder was RIGHT all along — the tester was TRUNCATING (reasoning-model max_tokens) (2026-07-01)

**The big reframe.** Run #8's forensics (`iterations/0/incident.txt`) show the coder's `core.py` is CORRECT
tool-calling composition: `from toolkit import CATALOG_PRODUCTS, call_tool, llm` → matches the product →
`call_tool(matched_product)` → `llm(...)` to phrase it → **a deterministic price anchor**
(`if price_str not in reply: reply += f" The price is ${price_str}."`). Real `call_tool`, real `llm`, the
exact-value guarantee. The coder even DIAGNOSED the blocker itself: *"the test file has a syntax error
(unclosed parenthesis on line 24)… not something I can fix by editing core.py."*

The staged test was TRUNCATED mid-line:
`def test_out_of_catalogue_query_returns_no_catalog_price(catalog_prices` → `SyntaxError: '(' was never
closed` → pytest can't COLLECT it → the (correct) coder is blamed `fix-attempt cap reached`, forever.
Root cause: the **tester** runs on the default `chat_text` budget (`max_tokens=4000`); a REASONING model
spends that on chain-of-thought before emitting the now-longer shortcut-proof test → the test is cut off
mid-signature. Exactly the M5 class (#34/#37 spec/answer `max_tokens`), now hitting the tester.

**This reframes runs #5–#8.** The coder is genuinely capable — run #3 (real RAG), run #8 (correct
tool-calling). The dominant recent bottleneck was TESTER quality (gameable in #5, red-first in #6,
**truncated/uncollectable in #8**) — not coder incapability. The harness kept blaming the coder for the
tester's broken tests.

**Fix (root cause, general).** `_tester_write` now calls the tester with `max_tokens=8000` (reasoning
headroom, matching the critic) + up to 3 parse-retries (was 1). The coder's `_chat` likewise bumped to
8000 (a complex whole-file composition can truncate the same way). Fakes: `test_m6_diagnostics`
(tester budget + retry-past-truncation; green bar 191). *Server: re-run `gradio-tool` → the tester's test
should now parse, the coder's already-correct composition should go GREEN — the first real cross-domain
(tool-calling) build.*

**Honest verdict forming:** with the toy-proof verification (#1) AND the tester un-truncated, the on-prem
coder DOES compose real, primitive-using PoCs across domains (RAG + tool-calling). The defensible asset —
a general harness that refuses toys and generalises — holds, and the autonomy is realer than runs #5–#7
suggested. Residual: replan-waste (churn) + tester reliability under the stronger bar.

**#44 follow-on — run #9: tester+coder both CORRECT; the last blocker was an egress-proxy 403 on the HTTP sibling (2026-07-01).**
With the token-headroom fix the tester wrote a clean, parseable, shortcut-proof test (paraphrased query +
exact opaque `price_usd`) and the coder's code was correct — but `call_tool` failed with `HTTP 403
Forbidden`. NOT the tool server (it never 403s): the iteration VM has `http_proxy` set (for allowlisted
egress), and `urllib.urlopen` RESPECTS it, so the HTTP call to the INTERNAL tool sibling was routed through
the egress proxy → correctly DENIED (the sibling IP isn't an allowlisted egress host). The RAG templates
dodged this because psycopg uses raw TCP (proxy-agnostic). Fix (`toolkit.py`): reach the sibling via an
empty-`ProxyHandler` opener (`_DIRECT`) so the internal HTTP call BYPASSES the proxy — the tool-pilot analog
of the M4 `NO_PROXY` bypass (#31/#33). General lesson for any HTTP-based sibling: bypass the egress proxy for
internal siblings. Green bar 191. *Server: re-run `gradio-tool` → the tester+coder are proven correct, so
with the sibling reachable this should be the first REAL cross-domain (tool-calling) green.*

**#44 follow-on #2 — run #10: FIRST real cross-domain green (plan1 iter0 RED→GREEN in 1), then the /calls audit hit the same proxy (2026-07-01).**
The proxy fix landed: plan-1 iter0 went **RED→GREEN in 1 attempt** on the exact-opaque-price test — the coder
composed real tool-calling AND passed a shortcut-proof test. The symmetric weak-test rung then fired
(`test green but gameable — strengthening it`), and the critic pushed for explicit invocation proof — but the
tester wrote that as a RAW HTTP GET to the sibling's `/calls`, hitting the SAME egress-proxy 403 → `LEDGER FAIL
… recorded but not passed` → mis-flagged as gaming → descope. Fix: added a proxy-safe `toolkit.tool_calls()`
helper (recorded call log, `_DIRECT` opener) + updated the knowledge note to mandate `call_tool`/`tool_calls`
for ALL tool access (never raw HTTP). Now the invocation-audit test the critic wants can actually pass. Green
bar 191. *Server: re-run → plan-1 iter0 already greened; with the audit proxy-safe this should reach `done`.*

**#44 follow-on #3 — the ROBUST proxy fix: broker NO_PROXY for ALL sibling IPs (2026-07-01).**
Confirmed from run #10's forensics: the critic-requested `/calls` invocation test did `try: urlopen() except:
pytest.skip()`, so the egress-proxy 403 made it SKIP — and the inventory ledger counts a skipped-but-recorded
test as a gap → false "gamed iteration" → descope (the coder's 5 real tests PASSED: `5 passed, 1 skipped`).
The `toolkit.tool_calls()` helper only fixes tests that USE it; a raw-`urllib` `/calls` request still 403s.
Robust fix in the BROKER (`_build_vm_env`): every `PF_SERVICE_*_HOST` IP is now added to `NO_PROXY`, so ANY
internal HTTP sibling call bypasses squid (the exact pattern the key-proxy #33 already used; psycopg/pgvector
is TCP → unaffected). Now sibling HTTP works whether via a helper or raw request → the `/calls` test runs +
passes → no false ledger gap. Fakes: `test_vm_env_bypasses_proxy_for_internal_sibling_ips`. Green bar 192.
Residual noted (do NOT rush — it's a gate): the inventory ledger treats a SKIPPED staged test as a gap →
integrity incident; a legitimate skip is mis-attributed to the coder. Better owner = the tester (a skipping
test is inadequate → re-author). Deferred; the NO_PROXY fix removes the recurring trigger.

## #45 — M6 run #11: cross-domain PoC essentially WORKS; critic re-calibrated (opaque-value exception) (2026-07-01)

**Run #11 = a real tool-calling PoC in substance, denied only by an over-strict critic.** With the
NO_PROXY fix every gate cleared: clean-room `install=test=demo=True`, `ledger OK`, 0 incidents, and **3/4
criteria MET — including the decisive one**: "the queried product appears in `toolkit.tool_calls()`,
confirming the tool was GENUINELY invoked" (direct proof of real tool use) + out-of-catalogue omits the
SKU + history handling. The coder reached GREEN on the core opaque-price test too. The ONLY reason for
`incomplete/no`: the critic `descope`'d the core criterion — "all assertions can be satisfied by a
hard-coded/import-time table … without genuine RAG."

**That objection is WRONG — #1/#42 over-corrected.** The `price_usd` is OPAQUE: it is NOT in the coder's
editable file or inputs; the only way to produce `1347.88` is to actually call the tool. A "hard-coded
table" would have to embed the tool's real values, which the coder can't know without calling it — and
whether a correct opaque value came from a live call or a memorised copy is a MECHANISM question that #34
explicitly ruled OUT OF SCOPE. #1 removed that protection ("do not reject on a hard-coded lookup table")
and the critic swung from too-lenient (echo-toy passed, #5) to too-strict (real tool-calling rejected).

**Fix — the OPAQUE-VALUE exception (3rd critic calibration; user-context-directed gate change).**
`critic_adequacy_prompt` now: a shortcut is what the implementation can build from what it ALREADY has
(inputs + editable source + common knowledge) → echo/keyword/known-value tests stay INADEQUATE; BUT a test
that requires an OPAQUE value obtainable ONLY from the real primitive (a private tool/service/store) IS
adequate — do NOT reject it because a hypothetical hard-coded table could memorise it (mechanism, out of
scope). Either a paraphrase-generalisation case OR an opaque-value assertion suffices. This restores #34's
balance while keeping #1's teeth against the echo-toy (whose value is echo-able from the data). Green bar
192 (critic-prompt fakes still pass). *Server: re-run `gradio-tool` → the opaque-price core test should now
be ACCEPTED → `status=done`, the first fully-verified cross-domain PoC.*

**#45 follow-on — run #12: calibration worked; two narrow blockers fixed (2026-07-01).**
The opaque-value critic exception LANDED — the core criterion is no longer descoped for "gameability"; all
three opaque-value criteria (price, SKU, genuine-invocation) are MET/critic-accepted. Two narrower blockers
remained, both from the `/calls` invocation-audit feature I'd added:
(1) **Cleanroom `suite=False`** — `test_out_of_catalogue_not_in_tool_calls` failed: the tool server's `/calls`
log is GLOBAL + accumulates across the whole test session, so a "product NOT in the log" assertion is
order-dependent (a fake product got recorded by another test's call) → flaky in the cumulative clean-room.
The opaque `price_usd`/`sku` ALREADY prove genuine invocation; the log audit is redundant AND fragile. Fix:
the knowledge note now says the opaque values ARE the invocation proof and to NOT assert on the call log.
(2) **Core red-first descope** — the symmetric "strengthen" rung re-authored the core test into one that was
green-against-the-stub (red-first violation), but the red-first re-author couldn't fire because the strengthen
had spent the only `reauthor` budget (cap=1). Fix: `reauthor_cap` 1→2 (the per-iteration budget is shared by
buggy-test / weak-test-strengthen / red-first; 2 lets a strengthened-then-red-first-violating test be
re-authored). Updated the 5 ladder tests that assumed cap=1. Green bar 192. *Server: re-run → clean-room
deterministic + the core red-first test re-authorable → expect `status=done`.*

## #46 — M6: FIRST fully-verified CROSS-DOMAIN green — tool-calling PoC `status=done` (2026-07-01)

Run #13: `gradio-tool` → **`status=done`, `demonstrates_core_value=yes`, 5/5 criteria met**, red-first OK,
ledger OK (21 test ids), 0 incidents, clean-room install=test=demo=True — a CLEAN, fast run
(respecs=0/replans=0/fixes=0; 578s; 12 LLM calls; iter0 RED→GREEN in 1, critic `accept→next` immediately).
The criteria: exact opaque `price_usd` (core) + exact `sku` (2nd opaque field) + out-of-catalogue no
hallucinated price + no-match indicator + reply is natural-language prose (LLM used, not bare JSON). The
harness autonomously wrote the spec (shortcut-proof, opaque-value criteria), the red-first tests, the
tool-calling glue, and an INDEPENDENT critic certified the opaque value is unfakeable without a real
`call_tool`.

**What this proves (honest framing, per §3b):** the general machinery — kit+glue, the anti-shortcut
verification bar, the re-author ladder, the salvage/robustness fixes — GENERALISES beyond RAG to a NEW
domain (external tool invocation) it was never tuned against, producing a real, verified PoC that uses both
a tool and the LLM. The autonomous surface is the spec/tests/glue + the independent critic + executed tests;
the human-authored part is the kit+glue scaffold (the `toolkit` primitives + the tool sibling), per the
design's "harness engineering". Caveat: reaching this took 13 runs — but those iterated the HARNESS (general
fixes: forensics #39, edit-extraction, the re-author family #41–43, token headroom #44, NO_PROXY #44, critic
calibration #42/#45), NOT this template's solution; template-specific tuning was minimal (dropping a flaky
`/calls` audit I'd over-suggested). The defensible claim: poc-foundry now autonomously builds + VERIFIES a
real tool-calling PoC under a toy-proof bar, cross-domain. NOT "the AI invented a novel app unaided."

**#46 correction — hand-check of the emitted `core.py` (2026-07-01).** The emitted PoC is GENUINE on the
core: real `from toolkit import call_tool`, a real `call_tool(product)` call, and the opaque `price_usd`/`sku`
taken FROM the tool result (no hard-coded literals) → the tool-calling and the core value are real, verified,
not gamed. HONEST CORRECTION to the "uses both a tool and the LLM" line above: it does NOT call `llm` — it
imports it but formats the reply with a deterministic f-string. So the 5th criterion ("reply is prose →
proving the LLM was used") passed WITHOUT the LLM (a fixed template is prose too) — a gameable test the
critic accepted (same class as the RAG citation-determinism gap: prose ≠ proof of LLM). Scoped claim: a
genuine, verified TOOL-CALLING PoC (real tool + opaque values); NOT a demonstration of LLM use. Residual: to
force real LLM use, a criterion an f-string can't satisfy is needed (hard to assert deterministically on
non-deterministic prose) — deferred; not core to a tool-calling demo.

## #47 — M6: replan-waste fix — a replan PRESERVES the workspace (only the unmet tail is re-attacked) (2026-07-01)

**The waste (handover §2.D):** a `replan` routed `plan → scaffold` (unconditional graph edge), and BOTH
`p2_plan` (resets every criterion to pending + clears staged tests) AND `p3_scaffold` (re-stamps the stub)
wiped all progress — so every replan re-attacked ALL criteria from scratch, discarding the met iterations'
code (we watched it redo iter0/iter1 across runs #5–#12; it also drove the recursion-limit blow-up #43).

**Fix (behind a `replan_mode` flag → happy path byte-for-byte unchanged):** on a replan (SAME spec),
`p_critic` sets `state.replan_mode=True`; `p3_scaffold` then PRESERVES the workspace (skip re-stamp — the
last-green commit already holds the met code; services are idempotent, smoke already passed) instead of
resetting to the stub; and iter0's strict red-first is disabled (the workspace has real code, so a green
iter0 test = met-existing, not tester inadequacy). Result: already-met criteria fast-path via met-existing
(skip the expensive CODER loop) and only the unmet tail is re-worked on the preserved code. A respec
(`p1_spec`, new criteria) clears `replan_mode` → fresh scaffold + strict red-first, as before. `plan→scaffold`
graph edge unchanged (P3 just no-ops the stamp). Fakes: `test_critic_replan_sets_replan_mode`,
`test_p3_scaffold_preserves_workspace_on_replan`. Green bar 194.

**NEEDS SERVER VALIDATION** (local fakes can't exercise a full replanning build): run a template that
actually replans (e.g. `gradio-rag-thin`, which replans often) and confirm the log shows
`P3 scaffold: REPLAN — preserving workspace` + met criteria going `met-existing` (not re-coded) after a
replan, and the build still reaches the same verdict, faster. The DONE tool build (#13, no replan) is
unaffected.
