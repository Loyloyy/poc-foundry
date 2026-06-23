# hints/ — Tier-1 auto-hints (gitignored)

Machine-local, low-authority, EXPIRING hints written by the emit step from a build's
`iterations/<i>/lessons.md` (the P4 close-step coder-interrogation). One `<build-id>.md` per build.

These files are **gitignored** (only this README is tracked): they are LLM-generated, may echo
incident text, and are scrubbed-but-untrusted (rule #1) — they never get pushed. Each carries
frontmatter:

```
---
date: 2026-06-24
source_build: poc-20260624-...
applies_to: coder, gotchas
expires: 2026-07-24
---
<the hint body>
```

`applies_to` is matched against role names AND playbook names; `expires` past today → the injector
skips it; an over-budget hint is skipped too. To make a hint durable, a human **promotes** it (Tier 2):
review + scrub + merge the lesson into the matching curated playbook, then delete the hint.
