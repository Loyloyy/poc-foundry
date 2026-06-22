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

First on-server bake-off (GLM-5.1-NVFP4 via the `coder` role, runc). Results — tasks passed:
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
the three *easier* tasks — inconsistent with `poc-builder-prework`'s finding that OpenCode+GLM one-shots
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
