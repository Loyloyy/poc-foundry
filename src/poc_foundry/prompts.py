"""Phase prompts for the structured-call roles (architect / tester / scribe; design §5.3, §5.4).

Kept as plain string builders (no model binding here). The on-prem model is a reliable tool-caller
but a weak self-planner (DEV_NOTES) — so the prompts SPELL OUT the constraints. All instructions
steer toward a small, **verifiable** walking-skeleton PoC: stdlib-only, unit-testable core, no
external services or network in the gated path (M1).
"""
from __future__ import annotations


def summarize_artifact(art) -> str:
    """Compact, hygiene-safe digest of a DeepResearchArtifact for the architect."""
    lines = [f"Topic: {art.topic}"]
    if art.brief:
        lines.append(f"Brief: {art.brief}")
    if art.findings:
        lines.append("Key findings:")
        for f in art.findings[:6]:
            lines.append(f"  - ({f.confidence:.2f}) {f.claim}")
    if art.tech_stack:
        lines.append("Tech stack:")
        for t in art.tech_stack[:8]:
            lines.append(f"  - {t.layer}: {t.choice} — {t.rationale}")
    if art.recommended_architectures:
        a = art.recommended_architectures[0]
        lines.append(f"Architecture: {a.name} — {a.summary} (components: {', '.join(a.components)})")
    if art.implementation_steps:
        lines.append("Implementation steps:")
        for s in sorted(art.implementation_steps, key=lambda x: x.order)[:6]:
            lines.append(f"  {s.order}. {s.action}")
    if art.open_questions:
        lines.append("Open questions: " + "; ".join(art.open_questions[:4]))
    return "\n".join(lines)


def spec_system(has_services: bool = False) -> str:
    base = (
        "You are a pragmatic solutions architect scoping a TINY, runnable proof-of-concept from a "
        "research artifact. The PoC is a single Gradio chatbot whose logic lives in a pure, importable "
        "`core.generate_reply(message, history) -> str`. Your spec must be buildable in a few small "
        "iterations and VERIFIABLE by unit tests that call `core.generate_reply` directly. Be honest: "
        "if the artifact cannot yield such a PoC, say so.")
    if has_services:
        return base + (" The PoC runs WITH a real provided sibling service (e.g. a vector database); "
                       "the tests still verify behaviour only through `core.generate_reply`, which "
                       "uses the service under the hood. No OTHER services or networks.")
    return base + " No external services, no network, stdlib only."


# Back-compat alias (the default, service-less system prompt).
SPEC_SYSTEM = spec_system(False)


def spec_prompt(art, interface: str, services: list | None = None) -> str:
    services = services or []
    if services:
        names = ", ".join(s.get("name", "") for s in services)
        svc_line = (f"- The PoC uses a real provided sibling service ({names}); each criterion is still "
                    "checked by a pytest test calling `core.generate_reply` directly (which uses the "
                    "service). Do NOT require GPUs or any service beyond the provided one.\n")
    else:
        svc_line = "- Do NOT require networks, GPUs, databases, or services.\n"
    return (
        f"{summarize_artifact(art)}\n\n"
        f"Target interface (fixed): {interface}\n\n"
        "Produce a PoC spec with:\n"
        "- goal: one sentence describing what the chatbot demonstrates.\n"
        "- success_criteria: 3 to 6 criteria. EXACTLY ONE has core=true (the single thing that, if it "
        "works, the PoC demonstrates its core value). Each criterion text must be checkable by a "
        "pytest unit test calling `core.generate_reply` directly — concrete and deterministic "
        "(e.g. 'replies that mention a known corpus keyword include a citation marker'). Set "
        "type='met-by-test' for all of them.\n"
        f"{svc_line}"
        "- non_goals: 2 to 4 things explicitly out of scope for this PoC.\n"
        "- demo_scenario: one or two sentences a human could follow to see it work.\n"
        "- buildable: true normally; false ONLY if no testable chatbot PoC can represent this "
        "artifact — then give not_buildable_reasons.\n"
        "Keep every criterion small enough to satisfy with a few lines of Python."
    )


TESTER_SYSTEM = (
    "You are a meticulous test engineer practising RED-FIRST development. You write a SINGLE pytest "
    "file that encodes a success criterion as executable assertions BEFORE the implementation "
    "exists. Tests import the pure core and call it directly — no network, no services, no gradio. "
    "Output ONLY the test file content in one fenced ```python block; no prose."
)


def tester_prompt(criteria, goal: str, interface: str, core_module: str = "core") -> str:
    if isinstance(criteria, str):
        criteria = [criteria]
    crit_block = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    plural = "these criteria" if len(criteria) > 1 else "this criterion"
    return (
        f"# PoC goal\n{goal}\n\n"
        f"# Interface under test (fixed)\n{interface}\n"
        f"(import it as `from {core_module} import generate_reply`)\n\n"
        f"# Success criteria to encode as tests\n{crit_block}\n\n"
        "# Task\n"
        f"Write ONE pytest file (functions named `test_*`, one or more per criterion) that asserts "
        f"{plural} against `generate_reply`. Make the assertions specific and deterministic so a "
        "naive echo stub would FAIL them. Use only the stdlib + pytest. Do not import gradio or open "
        "any network connection. Output only the file in a single ```python block."
    )


CRITIC_SYSTEM = (
    "You are a rigorous test-adequacy reviewer (the gate critic). You judge ONE thing: does PASSING "
    "the given test actually demonstrate the stated success criterion, or is the test trivial / "
    "gameable / not really exercising the criterion? You are NOT grading style or coverage breadth — "
    "only whether a passing result is TRUSTWORTHY evidence for the criterion. Be lenient on form, "
    "strict on substance. Default to adequate unless the test is clearly weak."
)


def critic_adequacy_prompt(criterion: str, test_src: str, interface: str) -> str:
    return (
        f"# Success criterion\n{criterion}\n\n"
        f"# Interface under test\n{interface}\n\n"
        f"# The staged test\n```python\n{test_src}\n```\n\n"
        "# Task\n"
        "Decide whether passing this test is trustworthy evidence FOR the criterion. Mark it "
        "INADEQUATE (adequate=false) only if it is clearly gameable, e.g.: it asserts nothing "
        "meaningful (`assert True`, only `is not None`, only checks the return type), it does not call "
        "the interface with inputs relevant to the criterion, or a trivial/echo stub unrelated to the "
        "criterion would satisfy it. Otherwise mark it adequate=true. Give a one-sentence `reason`; if "
        "inadequate, add a concrete `suggestion` for a stronger assertion."
    )


SCRIBE_SYSTEM = (
    "You are a technical writer producing a short, accurate DEMO note for a runnable PoC. Be "
    "concrete and brief. No marketing. Output GitHub-flavoured markdown only."
)


def scribe_demo_prompt(goal: str, demo_scenario: str, criteria: list[str]) -> str:
    crit = "\n".join(f"- {c}" for c in criteria)
    return (
        f"Write a `DEMO.md` (<= 25 lines) for this PoC.\n\n"
        f"Goal: {goal}\n"
        f"Demo scenario: {demo_scenario}\n"
        f"Success criteria:\n{crit}\n\n"
        "Include: a one-line what-this-is, the exact steps to launch (refer to RUN.md), and what to "
        "type to see the core criterion succeed. Markdown only."
    )
