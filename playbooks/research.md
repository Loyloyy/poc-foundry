# Research playbook (research agent)

Curated guidance for the targeted research-on-gaps agent (design §5.8). Used when an iteration has an
open question or the escalation ladder hits a stuck loop.

- **Evidence, not parametric memory.** Only FETCHED material is citable. Every claim in `research.md`
  must point to a source you actually retrieved through the egress proxy — do not answer from memory.
- **Narrow the query.** Search for the specific error signature, API symbol, or open question that
  blocked the iteration — not the broad topic. Prefer official docs / the package's own README / a
  pinned release changelog over a forum guess.
- **Stay on-allowlist.** All fetches go through the egress proxy; if a host is not allowlisted, note the
  gap rather than trying to bypass it.
- **Hand the coder something actionable.** Output a short, cited `research.md`: the resolved question, a
  minimal code-shaped answer, and the source links. Keep it tight — it becomes coder context.
