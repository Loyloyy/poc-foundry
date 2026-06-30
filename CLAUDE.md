# CLAUDE.md

@AGENTS.md

The working agreement above (`AGENTS.md`) is the source of truth — it applies to every agent. The
notes below are Claude-Code-specific.

## Claude-specific notes

- **Local verification only.** This box is Python 3.10 and has no venv/uv (rule #3). The only local
  checks are `python -m py_compile <files>` and running pure pydantic/stdlib modules directly.
  Everything that imports the agent stack (langchain/langgraph/deepagents) or runs the pipeline is a
  **server-in-Docker** step — author it, hand the user the command block, don't try to run it here.
- **The local GREEN BAR is `bash scripts/check.sh`** — py_compile + the no-pytest fakes suite
  (`run_spine_tests.py`) + contract checks + the data-hygiene guard (`check_hygiene.sh`). Run it before
  handing the user any commit; every new gate/logic change gets a fakes test so it's covered here.
- **Never run git mutations** on this repo (rule #2). If a commit/push is needed, tell the user — and
  run `bash scripts/check_hygiene.sh` first (rule #1: no host/model-id/path/key in TRACKED files).
- **Consult the planning chat via the user** for ledger deviations / structural surprises — don't
  guess on structural calls (rule in `AGENTS.md` → Decision culture).
- **Per-phase test commands.** The user wants each milestone provably finished before the next.
  When you complete a slice, give the exact command(s) that prove it (local `py_compile`/contract
  test, or a server command block). `ROADMAP.md` records the acceptance check per milestone.

## Status

**M0 ✅ · M1 ✅ · M2a ✅ · M2b ✅ COMPLETE** (all server-validated, 2026-06-23). M2b shipped the
hygiene scrubber, budget/cap enforcement + contention indicator, run-cap salvage (abandoned.patch +
descope report + gaps), and cooperative stop/resume. Next: **M2c — periphery** (research-on-gaps,
Langfuse spans, tiered evals v1, playbook injection + reflection, template CI). Local checks:
`python3 scripts/run_spine_tests.py` (65) + `run_contract_checks.py` (11). See `ROADMAP.md` for live
milestone state and `DECISIONS.md` for the rationale log (newest: #22).

**M2c ✅ ALL 5 SLICES SERVER-VALIDATED (2026-06-24):** S1 observability (Langfuse spans) · S2 tiered
evals (`cli eval`/`run_evals.py`; spec/plan 1.0 vs fixtures) · S3 experience loop (`playbooks.py`
injection lands with the format suffix LAST + Tier-1 reflection → lessons.md; hint write made
tolerated-absent after an NFS root-squash crash — hint PERSISTENCE needs `chmod 777 playbooks/hints`
on the host, else build still `done` + "hint NOT persisted") · S4 research-on-gaps (`research/` pkg:
vendored tools + BESPOKE search→fetch→synthesize agent, deepagents-swappable; triggers = open-questions
+ stuck-loop; shared depot SearXNG — real cited research.md, coder consumes it; DECISIONS #25) · S5
template CI (`core.template_ci`/`preflight_templates` + `cli template-ci` — both templates GREEN in
fresh VMs). ZERO leaks throughout. DECISIONS #23–#26.

**M3 ✅ COMPLETE (2026-06-25, server-validated over the SSH tunnel):** the web UI — S1 event seam +
single-slot `RunManager` + SSE (`events.py`, `web/runmanager.py`, `web/server.py`; `Ctx.say`/`graph.wrap`
emit through an optional `ctx.events` sink threaded via `build_poc`/`resume_build` — CLI path unchanged,
rule #5 holds) · S2 React+TS+Vite SPA in `frontend/`, **prebuilt `dist/` committed** to
`src/poc_foundry/web/dist/` (npm on the dev box like Stage 2; server serves the bundle). Watched a real
build live over the tunnel; cooperative Stop→Resume; history/docs/descope; localhost-publish boundary
(uvicorn 0.0.0.0 in-container, host publishes `127.0.0.1:8181`). Source picker, Langfuse session
deep-link, Stop "Stopping…" UX, caveats card. DECISIONS #27–#28. **Ops gotchas:** `.env` changes need
`up -d --force-recreate` (not `restart`); `DC` must pass BOTH `-f` files (compose + override); web binds
`127.0.0.1:8181`.

**M4 ✅ COMPLETE (2026-06-25, all server-validated). Local: `run_spine_tests.py` (157) + contract (11) + hygiene.**
- **S1 `refine` ✅ SERVER-VALIDATED** — `core.refine_build(id, *, coder_override)` re-attacks a finished
  build's descoped backlog on a stronger coder (backlog-only refine graph; reuses persisted workspace +
  red-first staged tests; per-call `models.set_role_alias` rebind, NOT a global `.env` flip; critic bar
  unchanged — respec/replan pinned to caps). CLI `refine` + web ✦ Refine button. A descoped fixture went
  incomplete→**done** on the server; critic descoped gameable greens live. DECISIONS #29.
- **S2a daemon rejection-audit ✅ SERVER-VALIDATED** — broker records rejected `create*`/lifecycle
  append-only to `PF_BROKER_AUDIT_LOG` (daemon-owned → durable + orchestrator-independent), no secret in
  any entry; `audit` RPC → `security.incidents[]`. DECISIONS #30. (`sandbox/audit.py`.)
- **S2b key-proxy ✅ SERVER-VALIDATED (core + infra)** — `security/keyproxy.py` (swap sacrificial→real,
  deny-on-mismatch, redact) + `security/findings.py` (Finding-0). On-prem vLLM is KEYLESS, so the key-proxy
  is the real control for key-requiring providers, demonstrated with a **canary**. OPT-IN infra
  (`PF_KEYPROXY_UPSTREAM`): the broker spins a per-build dual-homed key-proxy, generates a rotatable
  sacrificial token, injects `PF_SANDBOX_MODEL_BASE_URL` (+ a `NO_PROXY` bypass) into the VM; real key
  stays daemon-side. Normal builds byte-for-byte unchanged. DECISIONS #31, #33.
- **S2c `demo-security` CLI + 4 live beats ✅ SERVER-VALIDATED (4/4)** — `core.security_demo` →
  `security/demo.py`: canary/Finding-0 · egress containment · key-proxy (real key withheld) · broker
  rejection. Pure analyzers fakes-tested; canary redacted. DECISIONS #32, #32c (sacrificial-token + curl
  egress evidence), #32d (GPG_KEY is a public base-image constant, not a secret).
- **S2d Security-Demo web tab ✅ SERVER-VALIDATED** — `RunManager.security_demo` + `/api/security-demo` +
  a Builds/Security tab (`SecurityDemo.tsx`) rendering the beats live off the SSE seam. DECISIONS #32b.
- **S3 `docs/PLATFORM.md` ✅** — the workshop teaching artifact (wiki patterns cited to real modules).
- **Optional breadth (not done; only on request):** JS template (npm-on-server → rule #3 — raise first),
  multi-service composition, eval harness v2.
- **Residuals:** hint persistence (`chmod 777 playbooks/hints`); depot SearXNG pins; Langfuse exact-trace
  deep-link (capture `trace_id` at build time); `PF_MAX_RUN_WALL_CLOCK_S` to bound degenerate runs; the
  langgraph "unregistered msgpack type" warning on refine's checkpoint recovery (harmless; register
  `allowed_msgpack_modules`); no frontier coder endpoint yet (refine's stronger-coder met-flip deferred).

**M5 🟡 IN PROGRESS (2026-06-29; pilots + real model-calling builds). Local: `run_spine_tests.py` (172) + contract (11) + hygiene.**
- **A1 RAG pilot ✅ DONE + server-validated** — first REAL Stage-2 artifact (`dra-…fa650c-m`, "Self-hosted
  RAG over a private document store") → `gradio-rag-pgvector` → `status=done` 4/4, clean run. Surfaced +
  fixed SIX real weaknesses (DECISIONS #34): discrimination prompt · spec `max_tokens=8000` · corpus
  grounding (`knowledge` field) · **critic recalibration** (the one gate change — black-box behavioral
  adequacy, teeth retained) · citation-format pin · fence-robust extraction + compile-and-re-author.
- **B model-calling template ✅ DONE + hand-verified** — new `gradio-rag-llm`: real semantic embeddings
  (`fastembed`+`bge-small-en-v1.5`, 384-d, **baked into the sandbox image → fully offline, no user
  endpoint**) + real LLM generation (calls the vLLM from the VM via `PF_SANDBOX_MODEL_BASE_URL`, B0
  broker injection) + code-appended `[N]` citation as the deterministic anchor + **verifiable grounding**
  (answer↔cited-doc lexical overlap — the critic certifies it). DECISIONS #35–#38. Chain of real fixes:
  openai/httpx `proxies` (`httpx<0.28`) · reasoning-model `max_tokens` · gradio web-stack pin + the
  clean-room demo gate now LAUNCHES the UI (#36) · construct-only model-connectivity guard. Hand-proof:
  "how does RAG work" → `[1]` + real grounded answer (was "I don't know [2]" pre-fix).
- **NOT done (M5 backlog):** A2 MCP pilot · A3 durable-agent pilot · key-proxy exercised in a REAL
  model-calling build (mechanism proven by the M4 demo beat, not yet combined with a build) · the
  replan-waste residual (a `replan` re-attacks already-met iterations from scratch). See `HANDOVER_M6.md`.
