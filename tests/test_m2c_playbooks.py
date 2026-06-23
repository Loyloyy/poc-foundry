"""M2c S3 — experience loop fakes suite: playbook/hint injection + Tier-1 reflection.

Proves (no vLLM, no Docker): the injection seam appends curated playbook + matching hint text into an
agent's prompt with the hard-rule/format suffix kept LAST; expired/oversized/mismatched hints are
skipped; the P4 close-step coder-interrogation writes a grounded ``lessons.md`` only on a struggling
iteration; and ``write_hint`` caps + frames a low-authority expiring hint the injector reads back.
"""
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry import playbooks, prompts
from poc_foundry.state import IterationPlan


def _seed(monkeypatch, tmp_path, *, curated: dict | None = None, hints: dict | None = None):
    pb_dir = tmp_path / "playbooks"
    h_dir = pb_dir / "hints"
    h_dir.mkdir(parents=True)
    for name, body in (curated or {}).items():
        (pb_dir / f"{name}.md").write_text(body)
    for fname, body in (hints or {}).items():
        (h_dir / fname).write_text(body)
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    monkeypatch.setattr(playbooks, "HINTS_DIR", h_dir)
    return pb_dir, h_dir


def _hint(body, *, applies_to="tester", expires=None, size_pad=0):
    exp = expires or (date.today() + timedelta(days=10)).isoformat()
    pad = ("\nfiller" * size_pad)
    return (f"---\ndate: 2026-06-24\nsource_build: poc-x\napplies_to: {applies_to}\n"
            f"expires: {exp}\n---\n{body}{pad}\n")


# ── injection ──────────────────────────────────────────────────────────────────
def test_playbook_section_injects_curated_and_hint(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path,
          curated={"testing": "CURATED-RED-FIRST-RULE"},
          hints={"poc-x.md": _hint("HINTBODY-PIN-TESTER", applies_to="tester")})
    section = playbooks.playbook_section("tester")
    assert "CURATED-RED-FIRST-RULE" in section
    assert "HINTBODY-PIN-TESTER" in section
    assert "Unverified hint (low authority" in section   # the framing


def test_expired_and_oversized_and_mismatched_hints_skipped(monkeypatch, tmp_path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _seed(monkeypatch, tmp_path, curated={"testing": "CUR"},
          hints={
              "expired.md": _hint("EXPIREDBODY", applies_to="tester", expires=yesterday),
              "big.md": _hint("OVERSIZEDBODY", applies_to="tester", size_pad=200),  # > HINT_MAX_CHARS
              "mismatch.md": _hint("MISMATCHBODY", applies_to="research"),
          })
    section = playbooks.playbook_section("tester")
    assert "EXPIREDBODY" not in section
    assert "OVERSIZEDBODY" not in section
    assert "MISMATCHBODY" not in section          # pinned to research, not tester
    assert "CUR" in section


def test_hint_pin_matches_playbook_name(monkeypatch, tmp_path):
    # a hint pinned to the playbook NAME "gotchas" must reach the coder (coder → [building, gotchas]).
    _seed(monkeypatch, tmp_path, curated={"building": "B", "gotchas": "G"},
          hints={"poc-x.md": _hint("GOTCHA-HINT", applies_to="gotchas")})
    section = playbooks.playbook_section("coder")
    assert "GOTCHA-HINT" in section


def test_tester_prompt_keeps_format_suffix_last(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, curated={"testing": "CURATED-TESTING-GUIDANCE"})
    p = prompts.tester_prompt(["reply includes a citation marker"], "the goal",
                              "core.generate_reply(message, history) -> str")
    assert "CURATED-TESTING-GUIDANCE" in p
    suffix = "Output only the file in a single ```python block."
    assert suffix in p
    # the hard-rule/format suffix must come AFTER the injected playbook (can't be displaced)
    assert p.index("CURATED-TESTING-GUIDANCE") < p.index(suffix)


def test_no_playbooks_means_no_injection(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)   # empty dirs
    assert playbooks.playbook_section("tester") == ""
    p = prompts.tester_prompt(["c"], "g", "iface")
    assert "## Playbook" not in p


# ── write_hint round-trip ────────────────────────────────────────────────────
def test_write_hint_caps_and_is_read_back(monkeypatch, tmp_path):
    _, h_dir = _seed(monkeypatch, tmp_path)
    long_body = "X" * (playbooks.HINT_MAX_CHARS + 500)
    path = playbooks.write_hint(long_body, source_build="poc-build-1", applies_to=["coder"], hints_dir=h_dir)
    assert path and path.exists()
    text = path.read_text()
    assert "expires:" in text and "source_build: poc-build-1" in text
    parsed = playbooks.parse_hint(text)
    assert parsed.size <= playbooks.HINT_MAX_CHARS + 20   # capped (+ truncation marker)
    # readable back for the coder, skipped for a non-matching role
    assert playbooks.load_hints("coder", ["building", "gotchas"], hints_dir=h_dir)
    assert not playbooks.load_hints("tester", ["testing"], hints_dir=h_dir)


# ── Tier-1 reflection (P4 close-step) ────────────────────────────────────────
def _fake_ctx(tmp_path):
    return SimpleNamespace(build_dir=tmp_path / "build", say=lambda *a, **k: None)


def test_reflect_writes_lessons_on_struggle(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    monkeypatch.setattr(M, "chat_text",
                        lambda role, prompt, system=None, **k: "- pin the citation to the retrieved doc")
    ctx = _fake_ctx(tmp_path)
    it = IterationPlan(goal="add citations", acceptance=["replies include a citation marker"], interface="x")
    pipeline._reflect(ctx, 1, it, "abandoned", 3, ["[high] coder did not reach green"], "note")

    f = ctx.build_dir / "iterations" / "1" / "lessons.md"
    assert f.exists()
    body = f.read_text()
    assert "Concrete incident" in body and "coder did not reach green" in body   # cites the incident
    assert "pin the citation" in body


def test_reflect_skips_clean_iteration(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    called = []
    monkeypatch.setattr(M, "chat_text", lambda *a, **k: called.append(1) or "x")
    ctx = _fake_ctx(tmp_path)
    it = IterationPlan(goal="g", acceptance=["c"], interface="x")
    pipeline._reflect(ctx, 0, it, "green", 1, [], "")   # clean: first-try green, no incident

    assert not (ctx.build_dir / "iterations").exists()
    assert called == []   # no interrogation call on a clean iteration


def test_force_reflect_hook(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    monkeypatch.setenv("PF_FORCE_REFLECT", "1")
    monkeypatch.setattr(M, "chat_text", lambda *a, **k: "- forced lesson")
    ctx = _fake_ctx(tmp_path)
    it = IterationPlan(goal="g", acceptance=["c"], interface="x")
    pipeline._reflect(ctx, 0, it, "green", 1, [], "")   # clean, but forced

    f = ctx.build_dir / "iterations" / "0" / "lessons.md"
    assert f.exists() and "forced lesson" in f.read_text()
