# Testing playbook (tester)

Curated guidance for writing the red-first staged test.

- **Red-first, against the scaffold.** Your test must FAIL on the start-green scaffold's stub — assert
  the actual criterion behaviour, not just that a function exists or returns a non-None value.
- **Call the interface directly.** Import `from core import generate_reply` and exercise it with inputs
  relevant to the criterion. No gradio, no network, no sleeping.
- **Specific + deterministic assertions.** A naive echo stub must not pass. Prefer asserting on concrete
  substrings / shapes the criterion implies (e.g. a citation marker `[1]`, a known corpus keyword)
  over vague `is not None` / type-only checks.
- **One file, one concern.** One pytest file per iteration's criterion; functions named `test_*`. Keep
  fixtures tiny and inline. Output ONLY the file content — no prose.
- **Don't over-constrain.** Assert the behaviour the criterion names, not an exact wording the coder
  cannot know — leave room for a correct implementation to vary its phrasing.
