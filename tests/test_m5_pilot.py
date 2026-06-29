"""M5 RAG-pilot slice — author-side anti-gaming alignment.

The first real-artifact RAG pilot surfaced a BAR MISMATCH (DECISIONS #34): the tester was told only
that "a naive echo stub" must fail, while the critic (correctly) rejects any test a "trivial stub
unrelated to the criterion" can satisfy — e.g. a constant string already containing the citation
marker. The non-degraded critic then bounced every iteration (respec→replan→descope churn).

These pure-string tests pin that the spec criteria + the tester prompt now demand DISCRIMINATION
(a constant return value must fail; contrast a should-fire input against a should-not input), so the
author-side bar matches the critic's bar.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry import prompts


def _art():
    return SimpleNamespace(topic="self-hosted RAG", brief="", findings=[], tech_stack=[],
                           recommended_architectures=[], implementation_steps=[], open_questions=[])


def test_tester_prompt_demands_discrimination_not_mere_presence():
    p = prompts.tester_prompt(["a query about an ingested topic returns a citation marker"],
                              "demonstrate RAG", "core.generate_reply(message, history) -> str")
    low = p.lower()
    # the constant-stub bar (stronger than the old "echo stub") is stated explicitly
    assert "discrimination" in low
    assert "constant" in low and "fail" in low
    # and it asks for at least two contrasting inputs
    assert "contrasting" in low or "two contrasting" in low
    # the old, too-weak framing is gone
    assert "naive echo stub" not in low


def test_spec_prompt_criteria_are_discrimination_shaped():
    p = prompts.spec_prompt(_art(), "core.generate_reply(message, history) -> str",
                            services=[{"name": "pg"}])
    low = p.lower()
    assert "discriminating" in low
    assert "constant canned answer" in low
    # the contrast idea (should-fire vs should-not) is present
    assert "unrelated query" in low or "should not" in low


def test_tester_prompt_keeps_format_suffix_last_after_change():
    # regression: the playbook compose still keeps the hard-rule/format suffix LAST
    p = prompts.tester_prompt(["c"], "g", "iface")
    assert p.rstrip().endswith("```python block.")
