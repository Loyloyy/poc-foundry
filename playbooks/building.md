# Building playbook (architect + coder)

Curated guidance for scoping and implementing a small, verifiable Gradio PoC.

- **Keep the core pure and importable.** All testable logic lives in `core.generate_reply(message,
  history) -> str`. Gradio is a thin shell around it; never put load-bearing logic in the UI layer.
- **Smallest correct change.** Make the failing test pass with the fewest lines; do not refactor
  unrelated code or add abstractions a criterion does not require.
- **Stdlib first.** Reach for a dependency only when a criterion genuinely needs it; if the template
  already ships a helper (e.g. a `retrieve`/`cite` scaffold), build on it rather than re-implementing.
- **Determinism.** No wall-clock, randomness, or network in the gated path — tests must pass the same
  way every run. If you need an example corpus, make it small, in-memory, and topical to the artifact.
- **Honest scope.** A good spec has 3–6 criteria, exactly one `core`, each checkable by a deterministic
  unit test calling `generate_reply` directly. If the artifact cannot yield such a PoC, say so
  (NOT_BUILDABLE) rather than inventing a vacuous one.
