# Playbooks — the two-tier experience loop (design §5.9)

Curated, role-scoped guidance injected into the agents' prompts at build time, plus a low-authority
auto-hint channel that lets a build leave notes for the next one.

## Two tiers

- **Tier 2 — curated (tracked, here):** `building.md`, `testing.md`, `research.md`, `gotchas.md`.
  Hand-maintained, high-authority guidance. A human promotes a proven Tier-1 hint up into one of these
  (scrubbed + reviewed — that promotion is the only sanctioned write to these files). These are the
  files injected into prompts with full authority.

- **Tier 1 — auto-hints (`hints/`, gitignored):** after a build, the P4 close-step coder-interrogation
  ("what would have helped?") writes `builds/<id>/iterations/<i>/lessons.md`; the emit step distils
  those into ONE expiring, size-capped hint file in `hints/`. Hints are injected with explicit
  **"unverified hint (low authority)"** framing, they **expire** (frontmatter `expires:`), and they are
  **machine-local** (gitignored — they are LLM-generated and may echo incident text, so they never get
  pushed; rule #1). They are NOT promoted automatically.

## Injection (the seam in `prompts.py` / `coder.py`)

Each role gets `ROLE_PLAYBOOKS[role]` curated bodies + any matching non-expired hints, appended into
the prompt **body**, with the role's hard-rule / output-format suffix kept **LAST** — so playbook /
hint text can never displace the code-appended rules. The whole injected section is capped to a
per-role char budget; an over-budget hint is skipped entirely.

| role | curated playbooks |
|------|-------------------|
| architect | building |
| tester | testing |
| coder | building, gotchas |
| research | research |

A hint's `applies_to:` pins are matched against the role name **and** its playbook names, so a hint
pinned to `gotchas` reaches the coder.
