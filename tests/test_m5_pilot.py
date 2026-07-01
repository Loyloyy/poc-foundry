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
def test_critic_prompt_is_blackbox_but_rejects_cheap_shortcuts():
    # #1 (DEC #42) tightened the bar: still BLACK-BOX + mechanism-agnostic (you needn't prove pgvector
    # ran), but a cheap echo/keyword/lookup SHORTCUT passing is now grounds for inadequacy — that is
    # what let the echo-toy through under the old lenient "default to adequate" stance.
    p = prompts.critic_adequacy_prompt("the criterion", "def test_x(): assert True", "iface")
    low = p.lower()
    assert "black-box" in low
    assert "mechanism" in low and "out of scope" in low          # still not required to prove the mechanism
    assert "shortcut" in low and "echo" in low and "generalis" in low


def test_critic_prompt_keeps_teeth_against_constant_stub():
    # the bar that catches the ORIGINAL presence-only gaming (a constant stub passing) must remain
    p = prompts.critic_adequacy_prompt("c", "src", "iface").lower()
    assert "constant" in p and "echo stub" in p
    assert "assert true" in p and "is not none" in p


# ── tester-output robustness (DECISIONS #34 follow-on 5): a staged test that doesn't PARSE (a stray
#    markdown fence the extractor missed) silently dooms the iteration. Harden _extract_code + re-author. ──
def _compiles(src):
    compile(src, "<t>", "exec")
    return True


def test_extract_code_strips_fence_variants():
    from poc_foundry.phases.pipeline import _extract_code
    body = "def test_x():\n    assert 1 == 1"
    # clean fence, language tag, trailing space after the tag (the iter2 bug), bare fence
    for opener in ("```python", "```py", "```python ", "```"):
        out = _extract_code(f"{opener}\n{body}\n```")
        assert _compiles(out), f"failed for opener {opener!r}: {out!r}"
        assert "```" not in out


def test_extract_code_strips_stray_fence_on_fallback():
    from poc_foundry.phases.pipeline import _extract_code
    # a half-open fence (no closing) — regex won't match; the defensive line-strip must still save it
    out = _extract_code("```python\ndef test_x():\n    assert True")
    assert "```" not in out and _compiles(out)


def test_tester_write_reauthors_on_unparseable_test(monkeypatch):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    calls = {"n": 0}

    def fake_chat_text(role, prompt, system=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "```python\nthis is (not valid python\n```"   # broken first
        return "```python\ndef test_ok():\n    assert True\n```"  # clean on re-author

    monkeypatch.setattr(M, "chat_text", fake_chat_text)
    ctx = SimpleNamespace(template=SimpleNamespace(knowledge=""), say=lambda *a, **k: None)
    code = pipeline._tester_write(ctx, ["c"], "goal", "iface")
    assert calls["n"] == 2          # it re-authored exactly once
    assert _compiles(code) and "def test_ok" in code


# ── B0 (model-calling substrate): the VM must learn the model endpoint even WITHOUT the key-proxy,
#    so a model-calling PoC can reach the egress-allowlisted vLLM directly. ──
def test_vm_env_injects_allowlisted_model_endpoint_without_keyproxy():
    from poc_foundry.sandbox.broker import Broker
    cfg = SimpleNamespace(sandbox_image="poc-foundry-sandbox", proxy_image="poc-foundry-proxy",
                          kata_runtime="kata", uv_cache_shared=False, vllm_allow_host="10.0.0.8:8008")
    b = Broker("poc-m5", cfg, allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"})
    b.proxy_url = "http://10.0.0.2:3128"
    env = b._build_vm_env(None)
    # normal (keyless) path now injects an OpenAI-client-shaped base_url to the allowlisted endpoint
    assert env["PF_SANDBOX_MODEL_BASE_URL"] == "http://10.0.0.8:8008/v1"
    # it stays ROUTED THROUGH the egress proxy (the allowlisted path) — allow-host NOT bypassed
    assert "10.0.0.8" not in env.get("NO_PROXY", "")
    # the opt-in key-proxy still WINS when provisioned (its URL + NO_PROXY bypass)
    b.keyproxy_url = "http://10.0.0.9:8788"
    env2 = b._build_vm_env(None)
    assert env2["PF_SANDBOX_MODEL_BASE_URL"] == "http://10.0.0.9:8788"
    assert "10.0.0.9" in env2["NO_PROXY"]


def test_vm_env_bypasses_proxy_for_internal_sibling_ips():
    # M6: an HTTP sibling (e.g. the tool server) reached via http_proxy would route through squid, which
    # DENIES the internal IP → 403. Every PF_SERVICE_*_HOST IP must be in NO_PROXY so sibling HTTP works,
    # whether the PoC/test uses a helper or a raw urllib request. (psycopg/pgvector is TCP → unaffected.)
    from poc_foundry.sandbox.broker import Broker
    cfg = SimpleNamespace(sandbox_image="poc-foundry-sandbox", proxy_image="poc-foundry-proxy",
                          kata_runtime="kata", uv_cache_shared=False, vllm_allow_host="")
    b = Broker("poc-tool", cfg, allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"})
    b.proxy_url = "http://10.0.0.2:3128"
    env = b._build_vm_env({"PF_SERVICE_TOOLSERVER_HOST": "10.0.0.7", "PF_SERVICE_PG_HOST": "10.0.0.5"})
    assert "10.0.0.7" in env["NO_PROXY"] and "10.0.0.7" in env["no_proxy"]   # HTTP sibling bypasses squid
    assert "10.0.0.5" in env["NO_PROXY"]                                     # every sibling IP
    assert env["HTTP_PROXY"] == "http://10.0.0.2:3128"                       # egress still goes via proxy


# ── B1: the model-calling template ───────────────────────────────────────────
def test_rag_llm_template_resolves_pins_and_anchors_on_citation():
    from poc_foundry.phases.context import load_template
    from poc_foundry.core import preflight_templates
    t = load_template("gradio-rag-llm")
    assert t.services == [{"name": "pg", "vetted": "pgvector"}]
    assert "generate_reply" in t.interface
    kl = t.knowledge.lower()
    # the deterministic-anchor strategy: cite the [<int>] marker, NEVER the model's prose
    assert "deterministic" in kl and "never assert exact" in kl
    assert "_answer" in t.knowledge          # points the coder at the real model-call helper
    assert "[doc-n]" in kl                    # warns against the unsatisfiable format (pilot lesson)
    r = preflight_templates(["gradio-rag-llm"])[0]
    assert r["resolves"] and r["services_pinned"]


def test_rag_llm_scaffold_imports_light_and_exposes_helpers():
    # importing core must stay LIGHT — fastembed/psycopg/openai are lazy, so it loads on the dev box;
    # the model-backed _embed/retrieve are exercised in the sandbox (criterion tests), not here.
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "templates" / "gradio-rag-llm" / "files" / "core.py"
    spec = importlib.util.spec_from_file_location("rag_llm_core", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.cite({"id": 3}) == "[3]"                  # integer-id [N] format (fastembed-free)
    assert mod.EMBED_DIM == 384                          # bge-small-en-v1.5 output dim
    assert 0 < mod.RELEVANCE_THRESHOLD < 2               # cosine-distance cutoff
    assert len(mod.CORPUS) >= 2 and all("id" in d and "content" in d for d in mod.CORPUS)


def test_rag_llm_smoke_guards_the_model_connectivity_contract():
    # the model-connectivity guard must ship in the suite so a broken model call FAILS the build
    # (instead of silently degrading to the snippet fallback) — the openai/httpx `proxies` lesson.
    from pathlib import Path
    smoke = (Path(__file__).resolve().parents[1] / "templates" / "gradio-rag-llm" / "files"
             / "tests" / "test_smoke.py").read_text()
    assert "PF_SANDBOX_MODEL_BASE_URL" in smoke and "OpenAI(" in smoke   # construct-only (offline)
    assert "pytest.skip" in smoke   # skipped offline so it never blocks dockerless CI
