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


# ── corpus grounding (DECISIONS #34 follow-on): the spec/tester must be grounded in the template's
#    FIXED corpus, else the architect invents facts the scaffold can't retrieve → stuck/descope. ──
_KB = "The PoC answers ONLY from a FIXED corpus exposed as `core.CORPUS`. Topics: A, B, C."


def test_spec_prompt_injects_knowledge_and_forbids_invention():
    p = prompts.spec_prompt(_art(), "iface", services=[{"name": "pg"}], knowledge=_KB)
    assert "core.CORPUS" in p
    assert "do not invent" in p.lower()
    # absent → no knowledge section (other templates unaffected)
    assert "knowledge base" not in prompts.spec_prompt(_art(), "iface").lower()


def test_tester_prompt_injects_knowledge_before_suffix():
    p = prompts.tester_prompt(["c"], "g", "iface", knowledge=_KB)
    assert "core.CORPUS" in p
    # the format/hard-rule suffix must still come AFTER the injected knowledge
    assert p.index("core.CORPUS") < p.rindex("```python block.")


def test_rag_template_declares_corpus_knowledge():
    from poc_foundry.phases.context import load_template
    t = load_template("gradio-rag-pgvector")
    assert "CORPUS" in t.knowledge and "verbatim" in t.knowledge.lower()
    # the citation-format pin that prevents the unsatisfiable-test bug (int id, not [doc-N])
    assert "INTEGER" in t.knowledge and "[doc-1]" in t.knowledge
    # a template without a knowledge note loads cleanly with an empty string
    assert load_template("gradio-chatbot").knowledge == ""


# ── critic recalibration (DECISIONS #34 follow-on 3): the non-degraded critic was applying an
#    IMPOSSIBLE bar to a black-box test ("a lookup stub could satisfy it without pgvector"), making the
#    RAG template unbuildable. Re-anchor it to OBSERVABLE behavioral adequacy — without losing teeth. ──
def test_critic_prompt_is_blackbox_behavioral_and_defaults_adequate():
    p = prompts.critic_adequacy_prompt("the criterion", "def test_x(): assert True", "iface")
    low = p.lower()
    assert "black-box" in low
    assert "default to adequate" in low
    # explicitly out-of-scope: requiring proof of internal mechanism / rejecting on a lookup table
    assert "lookup table" in low
    assert "out of scope" in low


def test_critic_prompt_keeps_teeth_against_constant_stub():
    # the bar that catches the ORIGINAL presence-only gaming (a constant stub passing) must remain
    p = prompts.critic_adequacy_prompt("c", "src", "iface").lower()
    assert "constant" in p and "echo stub" in p
    assert "assert true" in p and "is not none" in p
