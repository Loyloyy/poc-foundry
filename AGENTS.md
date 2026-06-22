# AGENTS.md — working agreement for `poc-foundry` (Stage 3)

Source-of-truth working agreement for any agent (Claude Code, OpenCode, Cursor, …) working in this
repo. `CLAUDE.md` imports this file. **Read this before any non-trivial work**, then the canonical
design spec: `../stage3-planning/STAGE3_DESIGN_v1.6.md` (frozen; its §10 is the resolved-decision
ledger — do not re-open those without new evidence + a planning-chat consult via the user).

## What this repo is

Stage 3 of a 3-part GenAI-scout system: **Stage 1** `ai-engineer-wiki` (curates entity pages) →
**Stage 2** `ai-engineer-research` (page → grounded `DeepResearchArtifact` + run folder) →
**Stage 3** `poc-foundry` (ONE artifact → a verified, runnable, documented PoC). Gradio-first PoCs
are built inside fresh-per-iteration **Kata Containers** VM sandboxes with production-grade sibling
services, gated by a deterministic **LangGraph** harness with red-first tester/coder separation,
emitted as an OpenCode-continuable bundle, plus honest reporting (descope report, NOT_BUILDABLE
verdicts, a final demonstrates-core-value judgment). **Verification is the value.**

## Hard rules (non-negotiable; inherited from Stage 2)

1. **Data hygiene.** Public-assumed repo. NEVER put server IPs, hostnames, served-model ids,
   NFS/model paths, usernames, or keys in tracked files — those live only in gitignored `.env` /
   `docker-compose.override.yml` / `build_env.json`. Tracked files use placeholders
   (`<served-model-id>`, `<vllm-host:port>`, `/path/to/...`). The hygiene scrubber (`scrub.py`)
   extends this to emitted build outputs (tracebacks embed endpoints/ids).
2. **The user handles ALL git** (commit/push locally, pull on server). Do NOT run git mutations on
   THIS repo. The only sanctioned git writes are the harness's deterministic commits *inside*
   `builds/<id>/workspace/` — that is build output, gitignored here, never pushed.
3. **No pip/venv on ANY host.** pip/uv runs only (a) inside Docker image builds, (b) inside sandbox
   VMs. The dev box is Python 3.10; the stack needs ≥3.11 → containers only. Local verification =
   `python -m py_compile` and running pure-stdlib/pydantic modules directly.
4. **No model names in app code.** Models are `.env`-driven via `build_chat_model(role)`
   (roles: architect / coder / tester / critic / scribe; `PF_DEFAULT_ROLE` fallback). No LiteLLM.
5. **Headless core.** `build_poc(...)` is the stable contract; CLI and (later) web UI hold NO
   pipeline logic.
6. **Stage-2 artifacts are READ-ONLY input.** Write only to this repo's own `builds/` and workspaces.
7. **Log non-trivial choices in `DECISIONS.md`; keep `ROADMAP.md` checkboxes current at the end of
   EVERY session.** Cross-chat continuity depends on it.
8. **Broker invariant (security-load-bearing).** `create*()` parameters are harness-fixed and
   allowlisted — NEVER templated from artifact or model output; only `exec(cmd)` ever carries
   LLM-derived content.
9. **Never claim "cannot be escaped"** anywhere. All security language is defense-in-depth; Ops
   carries a Kata patch-cadence SLA.

## Build / run model

- **Author locally, run on the on-prem GPU server in Docker.** Tracked files are generic with
  placeholders; the real `.env` / overrides are created on the server by the user.
- **No host Python execution of the pipeline** (3.10 box; stack needs ≥3.11). Keep modules
  import-light so pure parts stay locally `py_compile`-able; lazy-import heavy deps inside functions.
- **Sandbox workspaces + uv cache on LOCAL disk** (`PF_WORKSPACE_DIR`); finished `builds/` may live
  on NFS.

## Decision culture

- A **planning chat** owns the architecture (its memory carries the full debate). Consult it (via
  the user) for: any deviation from design §10's ledger, structural surprises in M0 results, or
  contract changes. Decide freely on implementation details (file layout, naming, internal APIs) —
  and log them in `DECISIONS.md`.
- **Slice-and-validate** at every step (Stage-2 discipline): build thin vertical slices, verify each
  on the server before the next. Do NOT build pipeline topology before the M0 gates pass.

## Pointers

- Canonical design: `../stage3-planning/STAGE3_DESIGN_v1.6.md`
- Input contract: `../ai-engineer-research/docs/STAGE3_CONTRACT.md`
- Milestone status + checkboxes: `ROADMAP.md`
- Rationale log: `DECISIONS.md` · Gotchas/learnings: `DEV_NOTES.md`
