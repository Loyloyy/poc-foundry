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

## (sections appended as slices land)
