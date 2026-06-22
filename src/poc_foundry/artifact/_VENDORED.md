# Vendored mirror — `poc_foundry.artifact`

**Mirror of:** `ai-engineer-research` → `src/ai_engineer_research/artifact/`
**Copied from:** working tree at commit `2710e7ec677c225398bf76b4eeee66ee7f964d8f` (2026-06-15).
**Why a mirror (not the git dep):** see `DECISIONS.md` #2 — stopgap for M0/M1 while the GitHub
org/URL + push status are unconfirmed. The design end-state is a SHA-pinned git dependency.

## Files

| file | status |
|---|---|
| `schema.py` | **byte-identical** copy of the source — do not edit |
| `store.py` | **byte-identical** copy of the source — do not edit |
| `validate.py` | **byte-identical** copy of the source — do not edit |
| `__init__.py` | **adapted** — drops the `extract` import (heavy; Stage 3 only reads) |

The three byte-identical files let `scripts/check_vendored_schema.sh` detect drift with a plain diff.
`__init__.py` is intentionally different (trimmed) and is excluded from the drift check.

## Keeping it in sync / retiring it

- **Check drift:** `bash scripts/check_vendored_schema.sh` (diffs against the local
  `../ai-engineer-research` source; non-zero exit = drift → re-copy + bump the SHA above).
- **Retire (migration trigger):** when the user confirms Stage 2 is pushed to GitHub at a known SHA,
  replace this package with the one-line SHA-pinned dep in `pyproject.toml` (the commented block is
  already there) and delete this directory. Imports change from `poc_foundry.artifact` to
  `ai_engineer_research.artifact` (or re-export). No other rework.
