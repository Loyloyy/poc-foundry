# poc-foundry — Stage 3 of the GenAI-scout system

Turns ONE Stage-2 `DeepResearchArtifact` (+ its run folder) into a **verified, runnable, documented
PoC** — gradio-first, built inside fresh-per-iteration Kata Containers VM sandboxes with
production-grade sibling services, gated by a deterministic LangGraph harness with red-first
tester/coder separation, emitted as a self-contained continuation bundle a teammate can extend in
OpenCode, plus honest reporting. **Verification is the value.**

```
Stage 1  ai-engineer-wiki      curated entity pages
   │
Stage 2  ai-engineer-research  page → grounded DeepResearchArtifact + run folder
   │
Stage 3  poc-foundry (here)    ONE artifact → verified runnable documented PoC + honest report
```

Pipeline (deterministic outside, agentic inside):

```
P0 ingest+freshness → P1 spec → P2 plan → P3 SCAFFOLD →
   [P4 iteration i: research? → red-first tests → code → VERIFY → critic verdict → commit gate]
→ P5 docs+demo → P6 CLEAN-ROOM → P7 emit + final verdict + reflect
```

## Status

**M0 · M1 · M2a · M2b · M2c · M3 ✅ COMPLETE** (server-validated). The pipeline builds + *verifies* a
PoC inside Kata VM sandboxes, *survives* (budgets/salvage/stop-resume), is *observable/evaluable/
self-improving/can research a stuck point*, and is *watchable live over a web UI* (slice board,
Stop/Resume, history, honest descope reporting). **Next: M4 — breadth** (security red-team demo + vLLM
key-proxy + Security-Demo tab · `refine` flow · `docs/PLATFORM.md`); see
`../stage3-planning/HANDOVER_M4.md`. See `ROADMAP.md` for milestone state, `DECISIONS.md` for rationale,
`DEV_NOTES.md` for gotchas.

## Design & contracts (read these)

- **Canonical design (frozen v1.6):** `../stage3-planning/STAGE3_DESIGN_v1.6.md`
- **Input contract (Stage 2 → 3):** `../ai-engineer-research/docs/STAGE3_CONTRACT.md`
- **Working agreement:** `AGENTS.md` (source of truth; `CLAUDE.md` imports it)

## Build / run model

Authored locally (Python 3.10, no venv), run on the on-prem GPU server in Docker. The user handles
all git. Real endpoints/keys live only in a gitignored `.env` (see `.env.example`). Shared services
(SearXNG, Langfuse) come from the sibling `../service-depot` repo over `depot-net` — bring them up
with `./depot up stage-3` before runs that need search/tracing.

## Intended repo layout (materializes slice by slice)

```
poc-foundry/
├── AGENTS.md · CLAUDE.md · README.md · DECISIONS.md · DEV_NOTES.md · ROADMAP.md
├── .env.example · .gitignore · pyproject.toml
├── config/        pipeline.yaml (budgets/caps/license policy/vetted services/allowlist) · prompts/
├── playbooks/     research.md · testing.md · building.md · gotchas.md · hints/ (auto, expiring)
├── templates/     gradio-chatbot/ · gradio-generic-app/  (skeleton + pinned deps + smoke test)
├── docker/        Dockerfile (app) · compose · sandbox image · proxy image/config
├── src/poc_foundry/   core.py (build_poc) · graph.py · state.py · phases/ · coder.py · sandbox/
│                      scanner.py · verify.py · ingest/ · tools/ · artifact/ · models/config/…
├── scripts/       M0 spikes (kata smoke · coder bake-off · ingest probe · egress spike)
└── tests/         (+ tests/fixtures/ — sanitized golden Stage-2 run folders)
```
