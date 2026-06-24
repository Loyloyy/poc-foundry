# Dev Notes

Setup gotchas and implementation learnings for **poc-foundry** (Stage 3). Generic by design (no
server specifics — those live in `.env`). Newest sections appended over time. See `DECISIONS.md` for
the *why* behind architecture choices; this file is the *how* and the traps. Stage 3 inherits Stage
2's dev notes (`../ai-engineer-research/DEV_NOTES.md`) — start there for the shared substrate.

## Environment & toolchain (inherited + Stage-3 specifics)

- **Author locally, run on the server in Docker.** Dev box is **Python 3.10**; the stack needs
  **≥3.11**, so the pipeline runs only inside containers. Do NOT install packages locally (rule #3).
- **Local verification without running:** `python -m py_compile <files>` for syntax; run pure
  pydantic/stdlib modules directly against real inputs. Keep modules import-light (lazy-import heavy
  deps inside functions) so the pure parts stay locally testable — e.g. the vendored `artifact`
  schema is pydantic-only and imports on 3.10.

## Server facts (verified, from the design spec §2 — re-confirm on the box, don't trust blindly)

- Docker Engine 29.1.3, containerd 2.2.1; runtimes `runc` + `nvidia` (kata NOT yet registered).
- **Kata Containers 3.31.0** installed at `/opt/kata` (static bundle; `kata-runtime check` passes;
  `/dev/kvm` present; kernel 6.8 ⇒ **cgroup v2**). Docker v26+ needs Kata ≥3.30; **QEMU is the only
  Docker-tested VMM** — use QEMU, never CLH/FC. Register via `/etc/docker/daemon.json` runtimeType +
  `systemctl reload docker`. Never change the default runtime. **No GPU passthrough ever.**
- **Kata security posture:** 3.31.0 patched CVE-2026-47243 + CVE-2026-44210 (virtiofsd guest→host
  escapes). Ops needs a **patch-cadence SLA**. All claims are defense-in-depth; "cannot be escaped"
  is banned.
- **Egress (server-wide hard allowlist):** GitHub / HF / PyPI / container registries / Google+Bing
  reachable. **npm blocked** (no JS form factor in v1). Playwright CDN blocked. Unreachable hosts =
  connection resets; every tool must degrade gracefully.
- **Ops:** one pipeline + one stage active at a time on the H200. Sandbox workspaces + uv cache on
  **LOCAL disk** (`PF_WORKSPACE_DIR`); finished `builds/` may live on NFS. Compose v2 (depot needs
  it; Engine 29 ships it — confirm `docker compose version` once).
- **vLLM:** served model id may carry a **LEADING SLASH** (`GET <API_BASE>/v1/models` → `data[0].id`;
  set `<ROLE>_MODEL` to exactly that). Must be served with tool-calling enabled
  (`--enable-auto-tool-choice` + a `--tool-call-parser`). On-prem model: reliable tool-caller, weak
  self-planner → prompts must spell out steps.

## Stage-2 consumption (the vendored schema stopgap)

- Stage 3 reads artifacts by importing **only** the `DeepResearchArtifact` schema + `load()`. For
  M0/M1 this is a **vendored copy** of Stage 2's `artifact/` (pydantic-only), NOT the git dep — see
  DECISIONS #2 for the why + the migration trigger + drift guardrails.
- Artifacts are plain JSON validating against the schema (contract §3), co-located in the run folder
  (`vNN.json` + `report.md` / `comparison.md` / `code/**` / `notes/**` / `coverage.json`). Stage 3
  locates everything from the artifact `id`.
- **Defensive clamp:** `Finding.confidence` is typed `float` constrained to `[0,1]` by Stage 2's
  extraction *prompt* but is NOT hard-clamped — Stage 3 clamps on ingest (contract §6 caveat).
- No local Stage-2 run folders exist on the dev box yet → M0(c)'s full ingest probe needs a real
  sanitized run folder from the server (user is producing RAG + MCP runs as golden fixtures). Schema
  + shape contract tests run locally against a synthetic/sample artifact.

## Docker images (Slice 2)

- **apt works on the build server** (verified via Stage 2's images that `apt-get install` succeed) —
  so the proxy image installs squid from Debian mirrors. Don't assume this for *runtime* egress
  though: the sandbox VM's only exit is the allowlisting proxy, and Debian mirrors are NOT on that
  allowlist (build-time apt ≠ run-time egress).
- **Three images, two contexts.** App context = repo root (COPYs pyproject/src/config/scripts);
  sandbox/proxy contexts = their own dirs (self-contained, no repo COPY). Build:
  `docker compose -f docker/compose.yaml build app sandbox proxy`.
- **`[runtime]` extra is mandatory in the app image** — base install is schema-light; forgetting it
  silently ships an image with no langgraph/langchain/deepagents (Stage 2's extras-split lesson).
- **Proxy private-host exception is runtime-generated.** `PF_VLLM_ALLOW_HOST` (host[:port], from
  `.env`) → `entrypoint.sh` writes `/etc/squid/vllm.conf` with the right ACL (IP→`dst`,
  hostname→`dstdomain`). The real host/IP never enters a tracked file (rule #1). `squid -k parse`
  validates config at startup; CONNECT logs to stdout.
- **Domain-fronting residual (conceded, M0(d) will document):** the CONNECT allowlist matches on the
  requested host; TLS content is opaque, so a CDN-fronted host (PyPI/Fastly) is a known residual —
  the CONNECT log is the detective control, not a guarantee.
- **NFS root-squash (inherited):** pre-create `builds/` (and any NFS bind-mount source) as the user
  with `chmod 777` before first mount, or Docker-as-root can't `mkdir` it.
- **docker-socket mount is deferred to M1** (broker stub) and is the risk-accepted M1 residual — see
  `docker/docker-compose.override.yml.example` (commented) and DECISIONS at M1 wiring.

## Ingest / vendored schema (Slice 4, M0(c))

- **Local box has pydantic but NOT pytest** (no host pip, rule #3). So contract logic is provable
  locally via `scripts/run_contract_checks.py` (stdlib + pydantic, no pytest); the pytest file
  `tests/test_contract.py` is the in-container path. Both assert the same invariants.
- **Import `poc_foundry` without installing:** scripts do `sys.path.insert(0, "src")` so they run on
  the bare box. In the container the package is `pip install -e .`.
- **Vendored schema is byte-identical** to the Stage-2 source (drift = clean diff). Only
  `__init__.py` differs (drops `extract`). If you edit Stage-2's schema, run
  `bash scripts/check_vendored_schema.sh` and re-copy.
- **`load_run` is path-based** (loads `artifacts/<id>/` directly), distinct from the vendored
  `store.load(id, root=...)` (artifacts-root + id form). Stage 3 ingests by folder path.
- **Freshness is detect-only + tolerant:** `m0_ingest_probe.py --freshness` hits the GitHub API via
  urllib (honors `http(s)_proxy` env, optional `GITHUB_TOKEN`); any network failure → "unchecked"
  caveat, never a hard fail, never mutates pins (design §5.3 P0).

## M0(b) coder bake-off (Slice 4)

- **Self-contained, stdlib host** (`scripts/m0b_bakeoff/`): urllib model client + subprocess docker;
  no host pip (rule #3). Verification runs in the poc-foundry-sandbox container.
- **Relative imports** → run as a module: `python3 -m scripts.m0b_bakeoff.run` (or `python3
  scripts/m0b_bakeoff/run.py` — run.py adds the repo root to sys.path). `scripts/` is an implicit
  namespace package; `scripts/m0b_bakeoff/` has `__init__.py`.
- **Fresh workspace per (task, engine, edit-format)** so runs don't contaminate each other; workspaces
  under `PF_WORKSPACE_DIR` (local disk — needed for kata). Use `--keep` to inspect.
- **`uv pip install --system` needs root**, but the sandbox runs as uid 1000 — fine here because the
  bake-off tasks have NO requirements.txt (the reqs line short-circuits). A future task with deps
  would need `--target`/a venv or a root sandbox; note when adding one.
- **OpenCode model binding** is via opencode.json (provider endpoint/key), passed `--model
  <provider>/<model>`; bash/webfetch DENIED there. Reconcile against the live OpenCode if its CLI
  flags drift (prework's `/doc` reconciliation lesson).
- **diff edit-format** is applied host-side with `patch -p1` then `-p0` (models vary on a/ b/
  prefixes); malformed diffs fail the attempt — that IS the signal (diff suits strong models only).

## M0 server-run gotchas (found 2026-06-22, first on-server M0)

- **squid FATAL on overlapping allowlist entries.** squid 5.7 aborts startup (FATAL "Bungled …",
  not a warning) when an allowlist has a wildcard `dstdomain` that overlaps a more-specific sibling —
  e.g. `.docker.io` together with `registry-1.docker.io`, or `.github.com` with `codeload.github.com`.
  The container then Exits, its name leaves Docker DNS, and clients fail with "Could not resolve
  proxy: pf-spike-proxy" (a misleading downstream symptom). **Fix:** keep ONLY the broadest
  `dstdomain` per domain (the leading-dot wildcard already covers every subdomain). `m0d_egress_spike.sh`
  now fail-fasts with the squid logs if the proxy isn't Running, so this surfaces directly next time.
  `squid -k parse` did NOT catch it (it returned 0; the FATAL hit during real startup/store-init) —
  the running-state check in the spike is the reliable guard.
- **squid stdout logging in a container — the full saga (4 FATALs).** Getting the proxy to boot took
  four independent squid-in-Docker fixes; the broker-managed proxy at M1 must carry all of them:
  1. **ACL overlap → FATAL** (squid 5+): a wildcard `dstdomain` (`.docker.io`) listed alongside a
     more-specific sibling (`registry-1.docker.io`) aborts startup. Keep only the broadest form.
  2. **`/dev/stdout` perms → FATAL:** squid drops to the non-root `proxy` user, which can't reopen the
     container's root-owned `/dev/stdout` for the access_log.
  3. **`cache_effective_user root` → FATAL:** squid flatly refuses to run as root.
  4. **`squid -z` → FATAL "already running":** the cache-init step writes `/run/squid.pid`, so the real
     `squid -N` aborts. We have no cache_dir, so `squid -z` was dropped; `pid_filename none` too.
  **Final working setup:** run squid as `proxy`; `access_log` → a proxy-owned file
  (`/var/log/squid/access.log`); entrypoint (as root) `chown`s the log dir and **`tail -F`s the file
  to the container stdout** for `docker logs` evidence; `cache_log /dev/null`; `-d1` sends squid's own
  diagnostics to stderr; `visible_hostname` set; `pid_filename none`; no `squid -z`.
  **Lesson:** authoring container services blind (no local Docker) — lean on known squid-in-Docker
  patterns up front (non-root + file-log + tail) instead of discovering each FATAL on the server.
- **App image needs `tests/`.** The contract check (`scripts/run_contract_checks.py`) reads
  `tests/fixtures/sample_artifact`, but the app Dockerfile didn't COPY `tests/` → `NotADirectoryError`
  in-container. Fixed: `COPY tests ./tests` in the Dockerfile + a `../tests:/app/tests` mount in
  compose (live edits, no rebuild). Ad-hoc unblock without rebuild:
  `docker compose -f docker/compose.yaml run --rm -v "$(pwd)/tests:/app/tests" app python scripts/run_contract_checks.py`.
- **Two-machine git loop.** Local WSL box = git source; the server pulls from GitHub. To test a fix
  on the server before the git round-trip, hand-edit the file there + rebuild; before the next
  `git pull`, `git checkout <file>` (or stash) so the pull applies cleanly.

## Kata networking: reach the proxy by IP, not name (M0(a) finding, 2026-06-22)

**Key finding:** a Kata sandbox VM does NOT get Docker's embedded name-DNS. Under `--runtime kata`,
`127.0.0.11` in the guest is the guest's own loopback (nothing listens there), so resolving a Docker
container *name* like `pf-spike-proxy` fails ("Could not resolve proxy" / "Temporary failure in name
resolution") — even though the VM is correctly on the `internal:true` network (direct-egress + RFC1918
denials still pass). The same spike under runc resolves names fine.

- **Fix / M1 pattern:** the VM reaches the proxy by its **internal-network IP**, not its name. The
  broker resolves the proxy container's IP on the per-build internal network (`docker inspect -f
  '{{(index .NetworkSettings.Networks "<net>").IPAddress}}'`) and injects
  `HTTPS_PROXY=http://<proxy-ip>:3128` into the sandbox. This is the concrete form of the design's
  "the VM reaches the proxy on the internal network (no host.docker.internal)".
- The vLLM exception still works the same: the VM → proxy (by IP) → vLLM (the proxy's egress leg
  resolves/reaches the vLLM host). Only the VM→proxy hop must avoid Docker name-DNS.
- `m0d_egress_spike.sh` now derives `PROXY_URL` from the proxy's internal IP, so it passes under BOTH
  runc and kata. Applies to every sandbox↔sibling-service hop too (use IPs / pass them in), since the
  same DNS limitation hits Milvus/pgvector/etc. by name from a Kata VM — note for M2a sibling services.

## M1 broker + pipeline (S2–S5, 2026-06-22)

- **Same-path workspace mount (load-bearing).** The M1 broker runs IN the app container and drives
  the host docker socket, so `docker run -v <src>:/work` takes a HOST path. The orchestrator writes
  workspace files to `PF_WORKSPACE_DIR/<id>/…`; that dir must be mounted at the **identical** path in
  the app container (override: `- /var/tmp/pf-workspaces:/var/tmp/pf-workspaces`). If you mount it at a
  *different* container path, the broker passes a host path the kata VM can't see → empty `/work`.
- **uid 1000 vs root-written workspaces.** The orchestrator is root in-container; the sandbox is uid
  1000 (builder). pytest caches + `uv pip install --target .deps` need to WRITE into `/work`, so the
  phase `chown -R 1000:1000`s the workspace/clone/staged-tests before each `create()`
  (`chown_to_builder`, best-effort, root-only — no-ops in local dev). Root keeps write access, so the
  orchestrator can still edit files afterwards (they land root-owned but world-readable → uid 1000
  reads them fine). Staged VERIFY also sets `PYTHONDONTWRITEBYTECODE=1` because `/staged` is RO.
- **Clean-room install = `--target .deps` + PYTHONPATH, NOT `--system`.** `uv pip install --system`
  needs root; the sandbox is uid 1000 (the M0(b) gotcha). RUN.md's `pf:install` installs into a
  local `.deps/` and `pf:test`/`pf:demo`/`pf:run` prefix `PYTHONPATH=.deps` — the validated egress-spike
  pattern, and human-copy-pasteable.
- **App image needs the docker CLI — install `docker-cli`, NOT `docker.io` (Debian 13 split).** The
  in-process broker shells out to `docker`. On Debian 13 (trixie, `26.1.5+dfsg1`) the monolithic
  `docker.io` was split: it ships `/usr/bin/dockerd` + `docker-init` and only **Recommends** the
  client, so `apt-get install --no-install-recommends docker.io` yields **no `/usr/bin/docker`**
  (verified on the server: `command -v docker` empty, only `docker-init` present). Install
  **`docker-cli`** (the client package) instead — lighter (no daemon/containerd) and all we need since
  the daemon is the host's via the mounted socket. From Debian mirrors (build-time apt reachable). (If
  `docker-cli` is ever absent, the Docker apt repo's `docker-ce-cli` works but needs
  `download.docker.com` egress at build, which is NOT confirmed on this server.)
- **git `safe.directory` must be GLOBAL — `-c` is ignored.** Root operating git on a uid-1000-owned
  workspace trips "detected dubious ownership" (exit 128). Git **deliberately ignores `safe.directory`
  set via `-c` on the command line or local config** (so a malicious repo can't whitelist itself) — it
  only honours system/global config. `git add`/`commit` sometimes slip through with `-c`, but local-path
  `git clone <workspace>` (P6) strictly needs it GLOBAL. Fix: `ensure_git_global_safe()` runs
  `git config --global safe.directory '*'` **only when `os.geteuid()==0`** (so a dev box's gitconfig is
  never touched), called from `git_init`; the app image sets it too (belt). Every `git()` call also
  passes `-c safe.directory=*` (harmless defence).
- **Start-green templates must pin TRANSITIVE break-prone deps.** `gradio==4.44.1` does
  `from huggingface_hub import HfFolder` (removed in `huggingface_hub>=0.26`); `uv pip install`'s latest
  resolve pulled a hub that broke gradio AT IMPORT — only the clean-room `demo` step (`import app`)
  caught it (install + the stdlib smoke suite both pass without importing gradio). Pin
  `huggingface_hub==0.24.7` alongside gradio. Also: don't pass `analytics_enabled=` to
  `gr.ChatInterface` (not accepted across all 4.x point releases) — the env var
  `GRADIO_ANALYTICS_ENABLED=False` is the stable disable. **Diagnosing this needed P6 to capture the
  failing step's stderr into `caveats[]`** — it does now (don't run the clean-room blind).
- **Broker reaches the proxy by IP** (Kata has no Docker name-DNS, DECISIONS #9): `provision()`
  inspects the proxy's internal-net IP and injects `HTTPS_PROXY=http://<ip>:3128`. Sibling services
  (M2a) are reached by IP the same way (`broker.service_ip`).
- **Import hygiene held.** `import poc_foundry.core/graph/cli/phases` pulls **no** langchain/langgraph
  (verified) — heavy deps are lazy inside the phases/graph. So the spine stays `py_compile`-able and
  the pure logic is dry-runnable on the 3.10 box with fakes (`tests/test_m1_spine.py`).
- **Local proof without Docker/LLM.** `tests/test_m1_spine.py` fakes the broker (returns canned
  `ExecResult`s; the staged-verify fake checks the workspace for the expected edit) and the LLM roles
  (architect→`Spec`, tester→a red test, coder→a whole-file fix). It drives P0→P7 to `status=done`,
  proving the wiring + the RED→GREEN loop + NOT_BUILDABLE short-circuit. The REAL Kata/vLLM path is the
  server run only.

## M2a S1 integrity walls (2026-06-22)

- **Local fakes without pytest:** the 3.10 box has no pytest, so `scripts/run_spine_tests.py` is a
  tiny shim (`raises` + `monkeypatch` + `tmp_path`) that discovers + runs `test_*` in
  `tests/test_m1_spine.py` and `tests/test_m2a_gates.py`. Run it for any gate-logic change BEFORE the
  server round-trip: `python3 scripts/run_spine_tests.py` (expect `24 passed`). In-container, real
  pytest runs the same files. **A fakes test that drives P4 must model the new sandbox queries** — the
  fake `exec` now branches on `--collect-only` (return node ids) and `--junitxml` (return a junit xml
  string), not just the plain verify. If you add a sandbox query, update BOTH fakes.
- **Ledger identity = test-function NAME, not the full node id.** collect-only prints
  `test_x.py::test_foo`; junit records `classname="test_x" name="test_foo"`. Matching on the final
  `::` segment (`test_foo`) avoids rootdir/path/classname normalization headaches and still catches a
  deleted/renamed/skipped test. The tester writes module-level `test_*` functions (TESTER_SYSTEM), so
  names are unique within the one staged file — safe key. Revisit if a future tester emits test
  classes (junit classname becomes `module.Class`).
- **Junit is cat'd back through `exec`, not read from the host fs.** The junit file lands in writable
  `/work`, but the ledger parses `exec("pytest --junitxml=/work/.pf-junit.xml >/dev/null 2>&1; cat
  /work/.pf-junit.xml").stdout`. Routing it through the sandbox stdout keeps the gate decoupled from
  host-side fs/uid coupling and **fake-testable** (the fake just returns a canned xml). Same reason
  collect-only is parsed from `exec` output.
- **The diff scanner rides `verify()`.** Rather than touch the `CoderEngine` seam, the per-attempt
  scan is injected into the `verify` callable the phase passes the coder: it runs `git diff <base>`,
  and if `blocking()` returns a high-sev incident it returns `(False, "INTEGRITY: …")` BEFORE running
  pytest. The coder treats that as a normal failure → its error-signature path forces a strategy
  change on a repeat. Net: per-attempt enforcement with the coder still test-agnostic.
- **`git_diff(workspace, base)` surfaces untracked files too** (the coder may create a new file): it
  `git add -A` → `git diff --cached <base>` → `git reset` (leaving the working tree as the coder left
  it). Without the add/diff-cached, a brand-new gaming file (e.g. a new `conftest.py`) wouldn't appear
  in `git diff <base>`. (The coder is already host-blocked from non-allowlisted paths, but the scanner
  is defense-in-depth and auditable.)
- **Red-first now BLOCKS (M2a) where M1 only caveated.** A staged test that passes against the
  scaffold is a tester-inadequacy incident, not a pass — the criterion is not met and the build can't
  be `done`. The happy path is unaffected (the fixture's tester writes a genuinely red test; M1 proved
  RED→GREEN in 1 attempt). If a *real* server run trips red-first on the normal fixture, that's a
  signal the architect/tester prompt produced a trivial criterion — a prompt issue, not a harness bug.

## M2a S2 critic gate (2026-06-22)

- **The graph has cycles now — guard termination two ways.** `critic → {iterate|spec|plan}` are back-
  edges. Termination relies on (1) the critic capping its own counters (`fix_count`/`respec_count`/
  `replan_count` vs the config caps) and (2) `recursion_limit=60` on `graph.invoke` (core.py). If you
  add a verdict that loops, it MUST increment a capped counter or the run can hit the recursion limit
  (which surfaces as a LangGraph `GraphRecursionError` → the forensic `failed` artifact). The fakes
  test `test_critic_fixes_then_replans_then_descopes_a_failing_coder` pins the ladder exhaustion.
- **Respec re-runs P1→P4 (expensive); fix re-runs P4.** A `respec` verdict routes back to spec (a
  full architect call + re-scaffold + re-iterate) — capped at `respec_cap=1`. A `fix` re-runs P4 (a
  fresh tester test + a fresh coder loop) — capped at K. Both spin fresh Kata VMs each pass. The caps
  keep cost bounded; tune via `PF_RESPEC_CAP`/`PF_FIX_LIMIT_K`/`PF_DEGRADED_FIX_LIMIT_K`.
- **Degraded adequacy is advisory (server reality).** All five roles point to one on-prem model here →
  `same_family("critic","coder")` is True → degraded. In degraded mode an "inadequate" adequacy
  verdict on a GREEN iteration becomes a CAVEAT, not a respec/descope (a same-family judge can't
  independently certify). So the server happy path stays `done`; you'll see `degraded_critic: true`
  and `critic verdict=pass` in the report. To make adequacy BLOCK, configure a distinct `CRITIC_MODEL`
  (frontier) so `same_family → False`. The trivially-true-test gaming case is still caught by red-first
  regardless of critic family.
- **Fakes must patch BOTH `build_chat_model` and `same_family`.** `_patch_models` now: (a)
  `with_structured_output(AdequacyReview)` returns an adequacy fake, everything else returns the spec;
  (b) `same_family` is monkeypatched (default False = non-degraded). A critic test that wants the
  degraded path patches it True. If you add a structured-output role, branch on the model class in the
  fake (it's keyed on `model is AdequacyReview`).

## M2a S3 multi-iteration loop (2026-06-23)

- **Red-first is strict ONLY at iteration 0.** Iteration 0 runs against the scaffold echo-stub, so a
  green staged test there = tester inadequacy (VIOLATION, blocks `done`). For iteration i>0 the code
  already exists, so a green-pre-coder test legitimately means "criterion already met by prior code" →
  status `met-existing` (a pass, no coder). If you ever change the scaffold or the decomposition,
  re-check this boundary — making ALL iterations strict would regress any one-shot PoC (the chatbot
  fixture: its 5 criteria are facets of one core.py, so iters 1–4 are typically met-existing).
- **Staged tests ACCUMULATE; the verify is cumulative.** P4 writes `test_iter_{i}.py` into the staging
  tests dir without clearing it (P2 clears it once per plan). The coder's `verify()` runs the whole
  `/staged` dir, so a later iteration that breaks an earlier criterion fails → the coder must fix it
  (regression gate). The red-first probe runs the NEW file alone (`pytest /staged/test_iter_i.py`).
- **Only MET iterations' tests are published to the clean-room** (`workspace/tests/`, in P5). Descoped
  tests are withheld so the clean-room doesn't fail on a criterion we honestly descoped. The clean-room
  `python -m pytest -q` runs from the clone root → CWD on `sys.path` → `import core` resolves even for
  tests under `tests/` (this is WHY the `-m` matters; bare `pytest` would not put CWD on the path).
- **The loop is driven by the critic's verdict, not by P4.** P4 no longer increments `iteration`; the
  critic emits `next` (advance, reset fix budget) / `fix` (same iteration) / `proceed` (done iterating).
  When writing a fakes test that exercises the loop, DRIVE IT like the graph: call `p4_iterate` then
  `p_critic`, branch on `state.verdict` (`next`/`fix`→loop, `proceed`→break), with a guard counter.
  `test_multi_iteration_build_completes_with_cumulative_publish` shows the pattern + a faithful fake
  sandbox that actually runs the staged test functions in-process (no pytest on the 3.10 box).
- **Ledger is name-based across files (caveat).** `collected_names`/`junit_passed_names` key on the
  test-function name. Across multiple `test_iter_*.py`, a reused generic name (`test_basic`) collapses
  in the ledger. Server-proven fine on a single file (S2: 6 unique names). If the multi-iteration server
  run shows name reuse masking a deletion, upgrade the ledger key to `stem::name` (collect-only gives
  `test_iter_0.py::name`; junit gives `classname="test_iter_0"` — but confirm the real junit classname
  format on the server first, since rootdir affects it).

## Build wall-clock: where the time goes + the one safe speed-up (2026-06-23)

- **The full gate build is minutes because it does REAL multi-iteration work, not because of redundant
  testing.** Dominant cost = the the model round-trips × iterations (each iteration: 1 tester + 1–3 coder +
  1 critic call) + a fresh Kata VM boot per iteration + the clean-room VM. For the 5-criterion fixture
  that's ~15–25 the model calls + ~6 VM boots. The clean-room `uv pip install gradio` happens ONCE per build
  (only P6 installs; iterations + scaffold are stdlib-only). So the build can't be made much faster
  without removing the actual verification work.
- **Rigor lives in the local fakes, NOT in re-running the full build.** `run_spine_tests.py` (35/35,
  seconds) covers ALL gate logic; the full build's unique value is confirming the real Kata/the model/network/
  clean-room ENVIRONMENT still behaves. So: fakes are the dev inner-loop; run the full build ONCE per
  slice that touches the heavy path. Re-running it more often re-confirms the environment, not logic.
- **The one thoroughness-neutral speed-up: `PF_UV_CACHE_SHARED=1`.** Reuses a single `pf-uvcache-shared`
  docker volume across builds (gradio + huggingface_hub download once, then cached). Networks / proxy /
  sandbox VMs are STILL fresh-per-build (isolation unchanged); only the dep cache persists. **Default
  OFF** — a shared cache is a cross-build channel (a build could poison a wheel a later build installs),
  so it is a TRUSTED-INPUT dev-loop convenience ONLY; never enable it for untrusted artifacts. The
  integrity-preserving version (a devpi/verdaccio depot proxy) is the roadmapped M4 way. The default
  isolated per-build cache is untouched, so the gate's security posture is unchanged.
- **Speed-ups deliberately NOT taken (they trade thoroughness):** a 1-iteration "smoke" as the routine
  gate (loses multi-iteration coverage) and a minimal 1-criterion fixture (loses criteria breadth). Kept
  the full 5-criterion multi-iteration build as the per-slice gate.

## M2a S4a out-of-process broker (2026-06-23)

- **Enable it with the override + `PF_BROKER_SOCKET`.** `cp docker/docker-compose.override.yml.example
  docker/docker-compose.override.yml`; it defines a `broker` service (holds docker.sock, runs
  `python -m poc_foundry.sandbox.daemon`) and removes docker.sock from `app` (which gains
  `PF_BROKER_SOCKET=/var/tmp/pf-broker/broker.sock` + the `/var/tmp/pf-broker` mount). Pre-create the
  socket dir on the host: `mkdir -p /var/tmp/pf-broker && chmod 777 /var/tmp/pf-broker`. Unset
  `PF_BROKER_SOCKET` → the in-process `Broker` (default) — so this is a safe opt-in cutover.
- **The daemon must be LISTENING before the app connects.** `depends_on` only waits for 'started', so
  `rpc.call` retries the connect for ~15s to ride out the race. If you bring it up manually:
  `docker compose ... up -d broker` then check `logs broker` for `[pf-broker] listening on …`.
- **Same-path workspace mount now spans app→daemon→host.** Only the APP mounts PF_WORKSPACE_DIR
  (same-path) and writes workspace files; the daemon does NOT mount it — it passes the HOST path to
  `docker run -v <hostpath>:/work`, resolved by the host daemon. So the app keeps doing all
  workspace writes + git (orchestrator-writes); the daemon only runs docker (sandbox-executes). If you
  ever see an empty `/work` in the kata VM under the out-of-process broker, the app's PF_WORKSPACE_DIR
  mount isn't same-path.
- **The invariant is enforced DAEMON-side only.** `RemoteBroker` forwards raw create-params; the
  daemon's real `Broker` runs the allowlist guards and raises `BrokerInvariantError`, which the rpc
  layer re-raises as the same type client-side (via `error_type`). Don't add a client-side guard — it
  would be bypassable and is not the security boundary.
- **Proving the boundary on the server:** the build reaching `done` via the override is the functional
  proof; the SECURITY proof is that `app` has no docker.sock — check with
  `... run --rm app sh -c 'test -S /var/run/docker.sock && echo BAD || echo GOOD'`.
- **Single-threaded daemon, connection-per-call.** No locking, no request multiplexing — suits one
  build at a time. If multi-build concurrency is ever needed, the daemon already keys brokers by
  `build_id`, but the accept loop would need threading + per-broker locks.

## M2a S4b sibling services (2026-06-23)

- **Two one-time server steps** before a pgvector build: (1) **rebuild the sandbox image** —
  `docker compose -f docker/compose.yaml build sandbox` (it now bakes `psycopg[binary]`); (2)
  **restart the broker daemon** — it has stale Python loaded from S4a (`docker compose … up -d
  --force-recreate broker`), so it picks up the new `create_service` readiness + `ready_cmd` code.
- **Force the service template:** `… cli build <fixture> --template gradio-rag-pgvector`. The architect
  doesn't auto-select service templates yet (out of scope); the CLI `--template` flag forces it.
- **Why iterations need psycopg baked in, not installed:** the iteration VMs run the criterion tests
  but never `uv pip install` (only P6 clean-room does). A service PoC's `core.py` imports psycopg at
  call time, so the driver must already be in the image. `import psycopg` is LAZY in core.py, so the
  stdlib smoke (P3) still runs without it.
- **By-IP, not by-name:** `PF_SERVICE_PG_HOST` is the pgvector container's internal-net IP (Kata VMs
  have no Docker name-DNS — same finding as the proxy). The PoC reads it; for a human `docker compose
  up`, compose DNS resolves the `pg` service name instead (the template's compose.yaml sets
  `PF_SERVICE_PG_HOST=pg`).
- **Leak-check now includes the service container.** After a pgvector build, `docker ps -a | grep pf-`
  must be empty (the service `pf-…-svc-pg-…` is reaped by `broker.destroy()` over RPC) — except the
  long-lived `pf-broker` daemon. `docker network/volume ls | grep pf-` empty too.
- **Image/tag come from `pipeline.yaml vetted_services`, never the template's free text** (rule #8):
  `template.json` only names `{name, vetted}`; the harness resolves the pinned `image:tag`. Pin to a
  digest for production (the design's "pin exact tags"); `pg16` is the reachable convenience tag.
- **Coder's task is glue, not infra:** the scaffold ships `search()` working; the coder wires
  `generate_reply → search → "[id]" citation`. If a non-core criterion (relevance threshold) is too
  hard and descopes, the build is still `done` when the core retrieval criterion + the clean-room
  (published green tests) pass.

## Salvage: the workspace always reflects the last GREEN commit (2026-06-23)

- **An abandoned/descoped iteration's coder edits are DISCARDED** (`git reset --hard HEAD` in P4 when
  `crit_status != "met"`). Without this, the failed attempt's `core.py` lingers uncommitted and a later
  `git add -A` (P5 publish, or a met-existing iteration) commits it → the clean-room clones broken code
  → suite RED → an otherwise-sound build is (correctly) gated to `incomplete`. The pgvector server run
  exposed exactly this (iter2 descoped → broken `core.py` committed → clean-room failed). `reset --hard`
  reverts TRACKED files only — untracked `.deps`/caches survive. Green commits + met-existing (no edits)
  are unaffected.
- **Corollary:** every published criterion test must pass against the FINAL committed code, because the
  cumulative gate kept earlier criteria green AND the workspace never advances past a green commit. If a
  clean-room test fails despite green iterations, suspect (a) the workspace reflecting a failed edit
  (this salvage fix), or (b) an iteration vs clean-room ENV difference (e.g. a service not reachable).

## Local tooling: the green bar + the hygiene guard (2026-06-23)

- **`bash scripts/check.sh` is the one-command GREEN BAR** — py_compile (3.10) + the no-pytest fakes
  suite (`run_spine_tests.py`, 44) + the contract checks (`run_contract_checks.py`, 11) + the
  data-hygiene guard. Run it before handing the user any commit. Every new gate/logic change gets a
  fakes test so it's covered here (the full Kata build stays the server gate).
- **`bash scripts/check_hygiene.sh` enforces rule #1 on TRACKED files.** Static layer (real
  `_(MODEL|API_BASE|API_KEY)=` values, `/nfs/` paths, key patterns) runs anywhere; the dynamic layer
  (only when a local `.env` exists — i.e. on the server) greps tracked files for the REAL `.env`
  host/model values, catching PROSE leaks (e.g. a doc naming the served model), not just `KEY=VALUE`.
  It caught a real prose leak (the served-model id had drifted into DECISIONS/DEV_NOTES/ROADMAP);
  scrubbed to "the on-prem model". Run it on the SERVER for the authoritative check.

## pgvector template hardening — reliable builds for the degraded coder (2026-06-23)

- **Why a build went `incomplete`:** the architect's core criterion varies run-to-run; a hard one
  ("citation marker AND ≥3 consecutive words from the matched chunk") sat above the degraded coder's
  ceiling → core descoped → honest `incomplete` (the DONE floor working). Correct behaviour, but it
  made the sibling-service template a coin-flip as a regression gate.
- **Fix = make the coder's core task pure glue.** The scaffold `core.py` now ships WORKING helpers —
  `retrieve(query)` (pgvector ranking + a relevance gate), `snippet(doc)` (a verbatim ≥3-word quote),
  `cite(doc)` (`[id]` marker) — and `generate_reply` is a stub whose docstring shows the 3-line
  composition. The coder composes helpers (no SQL/psycopg), which M0(b) showed the model does reliably.
  Red-first still holds (the stub does no retrieval).
- **The relevance gate is LEXICAL, not a vector threshold.** The deterministic hashing embedding does
  NOT cleanly separate matched from unmatched (measured: matched 0.9–1.26 vs unmatched 1.13–1.41 — they
  OVERLAP; `quantum banana` landed closer than `postgres`). So `retrieve` gates on "does the query share
  a token with the corpus vocabulary" (reliable: unrelated → `[]`), and uses pgvector only to RANK the
  matches. pgvector is still genuinely exercised (the `<->` query runs); the gate just isn't trusted to
  a noisy distance. The lexical gate short-circuits before any DB call, so the unrelated-query case is
  even smoke-testable without pgvector.
- **Corpus is topical to the artifact** (RAG / retrieval / pgvector / gradio) so the tester's
  domain-derived queries hit. If you change the artifact fixture, re-align the corpus vocabulary or the
  tester may query words the corpus lacks → a matching-query test that can't pass.

## (sections appended as slices land)

## LangGraph checkpoint msgpack: register state types (forward-compat, M2b S4)

On `resume`/`get_state` (the salvage + stop paths deserialize the checkpoint), LangGraph logs a
**non-fatal** warning per run:

```
Deserializing unregistered type poc_foundry.state.Spec from checkpoint. This will be blocked in a
future version. Set LANGGRAPH_STRICT_MSGPACK=true to block now, or add to allowed_msgpack_modules
to allow explicitly: [('poc_foundry.state', 'Spec')]   # also Plan, artifact.schema.IterationRecord
```

- **Status:** resume/get_state WORK today (proven on the server: `_salvage_run` recovered state and
  emitted correctly 3×). The "blocked in a future version" is not yet active.
- **Fix when convenient (server-testable):** register `poc_foundry.state.{Spec,Plan}` +
  `poc_foundry.artifact.schema.IterationRecord` (and any other pydantic types stored in `BuildState`)
  in LangGraph's `allowed_msgpack_modules` allowlist where the `SqliteSaver` is built (`graph.py`).
  NOT shipped blind: it's a langgraph-version-specific API that can't be `py_compile`-verified on the
  3.10 dev box (rule #3) — author it against the installed langgraph version and confirm the warning
  is gone on a server resume.

## Budget meter resets per process (cross-resume undercount, M2b S4)

`models.METER` is process-global and in-memory. On a `resume`, it starts fresh, so the resumed run's
`budget.llm_calls` / `wall_s` count only the resume leg — NOT the pre-stop calls (those ran in the
prior process). The per-iter / per-run CAPS still bound each leg correctly; only the cross-resume
accounting total is missing. Fix when needed: persist the meter counters in `BuildState` (checkpointed)
and re-seed `METER.begin_run` from them on resume. Deferred — low impact (resume is the exception path).

## Tracing (`tracing.py`) — langfuse 4.x on the server, NOT v3 (M2c S1)

`langfuse` isn't installed on the 3.10 dev box (it's the `obs` extra, server/Docker only), so the SDK
API can't be run locally. It was first authored against **v3**; the server actually resolved
**langfuse 4.9.1** (because `pyproject` only said `langfuse>=3`), and v4 renamed the API — so every
span hit a guarded `AttributeError` and no-op'd → **empty trace despite auth/keys/host all OK**. Fixed
+ pinned `langfuse>=4,<5`. The v4/v3 differences that bit us (introspected on the server):

- **Span creator:** v4 `client.start_as_current_observation(name=…, as_type="span"|…, input=…,
  metadata=…)` (a ctx-mgr yielding a `LangfuseSpan`); v3 was `start_as_current_span`. `_LangfuseTracer`
  **feature-detects** both (`getattr(... "start_as_current_observation") or ... "start_as_current_span"`)
  and `test_m2c_tracing` exercises each with a fake client.
- **No `update_current_trace` / `obs.update_trace` in v4.** The TRACE name therefore comes from the
  ROOT observation's name → I name the root `build/<id>`. tags/session_id have no v4 setter, so they
  ride in the root obs `metadata`.
- **Events:** `client.create_event(name=…, input=…)` — same in both.
- **Obs methods (v4):** `update(output=…, metadata=…, level=…)`, `end()`, `create_event(…)`,
  `score`/`score_trace`, `set_trace_io`, `start_(as_current_)observation` (nesting), `id`, `trace_id`.

How to debug an empty trace fast (no langfuse locally, so the green bar can't catch an API drift):
run the SDK UNGUARDED inside the app container —
`$DC run --rm -e CK='from langfuse import get_client;import importlib.metadata as M;c=get_client();print(M.version("langfuse"),c.auth_check());s=c.start_observation(name="diag");s.update(output="x");s.end();c.flush();print("OK")' app python -c "import os;exec(os.environ['CK'])"`.
`auth True` + an `AttributeError` ⇒ API mismatch; a `Read timed out … Failed to export span batch`
⇒ langfuse-web OTEL ingest is slow/unhealthy (see below), not a code bug.

- **OTEL export timeout (watch this).** Right after langfuse-web recovered from its clickhouse-network
  crash loop, span export to `langfuse-web:3000` **timed out at 5s** (`Failed to export span batch …
  Read timed out`). Likely warm-up; if it persists, the OTEL ingest endpoint is too slow and we bump
  the exporter timeout (langfuse env / `OTEL_*`) or check langfuse-worker→clickhouse health.
- **`span(name, **attrs)` takes the name positionally** — an attr keyed `name=` raises `TypeError`.
  Broker spans use `box=`/`svc=` for the sandbox/service name (guarded by a test).
- **Server is shared, SDK is per-app.** poc-foundry + stage-2 hit the SAME langfuse instance; the SDK
  version is each app's own pip pin. poc-foundry is pinned to 4.x. A dedicated **`stage-3-poc`** project
  (own keys in `.env`) was created so stage-3 traces don't pollute stage-2 — no code change, since
  `tracing.py` is project-agnostic. **Validated 2026-06-24:** `build/poc-…` trace, 21 observation
  levels, full span tree, survives a depot restart.
- **The validation tail was all service-depot infra, not poc-foundry.** Three langfuse outages while
  proving the live trace: clickhouse + minio containers orphaned off `service-depot_default` by a stray
  `docker network/system prune` (deletes the net under running `restart:always` containers → zero
  attachments → clickhouse/minio DNS breaks, postgres survives), then a post-recreate `:3000`
  connection-refused. Fixed in service-depot (`./depot down/up` + a prune guard). If tracing breaks
  again, suspect the depot network FIRST (audit every container's networks), not the poc-foundry code.

Flush-on-exit is in `core` (`build_poc`/`resume_build` `finally`). The msgpack-registration follow-up
(above) was NOT folded into this slice — same unverifiable-heavy-dep-API reason; shipping it blind
risks the working resume path.

## M2c S3 — the `playbooks/` mount gotcha (2026-06-24)

The app container mounts `config/src/scripts/templates/tests/builds` but historically NOT
`playbooks/`, and the image was built before S3 → on the first server run the curated playbooks did
NOT inject (the tester-prompt one-liner showed no `## Playbook` block) and, worse, the Tier-1 hint
was written to the container's EPHEMERAL `/app/playbooks/hints` (lost on `docker compose run --rm`),
so the experience loop never actually persisted. `write_hint` masks this because it `mkdir -p`s the
dir regardless. Fix: (a) `COPY playbooks ./playbooks` in the Dockerfile (fresh-clone fallback), and
(b) **mount `../playbooks:/app/playbooks` in the app override** — that mount is REQUIRED for both
curated-playbook injection AND for auto-hints to persist to the host across builds. The override is
gitignored, so the server's `docker-compose.override.yml` must add the line by hand (the tracked
`.example` now shows it). No image rebuild is needed for the fix (the mount shadows `/app/playbooks`).

## M2c S4/S5 server-validated + the NFS hint-write fix (2026-06-24)

Server run validated S2 (eval 1.0/1.0), S3 injection (the `## Playbook` block lands with the format
suffix last) + reflection (grounded lessons.md), **S4 research-on-gaps** (real SearXNG JSON works on
the depot instance — `research.md` with 4 cited sources + honest "inconclusive"; coder consumed it;
build `done`), and **S5 template-ci** (both templates GREEN in fresh VMs, zero leaks).

**BUG found + fixed — the Tier-1 hint write crashed a build at P7 (NFS root-squash).** The repo is on
NFS; the app runs as root; root-squash maps root→nobody, so writing `/app/playbooks/hints/<id>.md`
(the mounted NFS dir, not 777) raised `PermissionError` — and the write was NOT guarded, so an
otherwise-successful build died at emit. Fix: `playbooks.write_hint` now catches `OSError` → returns
None (tolerated-absent at the source); `p7_emit`'s hint block + `_reflect`/`_maybe_research` disk
writes are wrapped too. The experience loop is a nice-to-have and must NEVER fail a build. For the loop
to actually PERSIST hints on NFS, pre-create the dir writable (same as builds/):
`mkdir -p playbooks/hints && chmod 777 playbooks/hints` (documented in the override example). Without
it the build succeeds and just logs "hint NOT persisted". +1 fakes test (write_hint → None on an
unwritable dir).
