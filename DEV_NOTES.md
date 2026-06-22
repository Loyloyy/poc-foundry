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

## (sections appended as slices land)
