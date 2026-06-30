"""CoderEngine — the bounded coder loop that turns RED tests GREEN (design §5.3 P4, §5.8).

``BespokeCoder`` is promoted from ``scripts/m0b_bakeoff/engines.py`` (it WON the seat, DECISIONS #8:
bespoke 4/4 vs OpenCode 1/4). A code-owned loop: prompt → apply edit (whole-file default OR unified
diff) → verify in the sandbox → feed the failure back; bounded attempts; error-signature tracking
with a **forced strategy change** when the same failure repeats (the escalation ladder).

Separation of concerns (the design's load-bearing claim — orchestrator-writes / sandbox-executes):
  • the coder edits files ORCHESTRATOR-SIDE (writes into the host workspace dir, no shell);
  • VERIFY runs INSIDE the sandbox VM via an injected ``verify`` callable (the phase wires it to the
    broker), so this module never imports the broker and stays decoupled + unit-testable;
  • the coder MUST NOT edit the test (defense-in-depth: the staged tests are also mounted RO).

LLM calls go through ``models.chat_text("coder", …)`` (stdlib raw — no langchain in the inner loop,
no model names in code, rule #4). The seam (`CoderEngine`) lets OpenCode slot in as the M4 fallback.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

SYSTEM = (
    "You are a precise software engineer making a failing Python test pass. You are given a goal, the "
    "failing test(s) (which you MUST NOT edit), and the current source of the editable files. Make "
    "the smallest correct change so the tests pass. Keep the code stdlib-only unless the goal needs a "
    "dependency. Think briefly, then output ONLY the edit in the exact format requested — no prose "
    "outside the code blocks."
)


@dataclass
class CoderResult:
    passed: bool
    attempts: int
    wall_s: float
    edit_format: str
    last_output: str = ""
    signatures: list[str] = field(default_factory=list)
    note: str = ""
    edited: list[str] = field(default_factory=list)
    last_response: str = ""   # the coder's RAW last model response (forensics when it never greened)


# A verify step: run the staged tests in the sandbox, return (ok, combined_output).
VerifyFn = Callable[[], "tuple[bool, str]"]


class CoderEngine(Protocol):
    name: str

    def run(self, *, workspace: Path, goal: str, editable_files: list[str],
            test_sources: dict[str, str], verify: VerifyFn,
            edit_format: str = "whole", max_attempts: int = 3, playbook: str = "") -> CoderResult: ...


# ── edit parsing / application (host-side, no shell) ─────────────────────────
_FILE_BLOCK = re.compile(
    r"\*\*\*\s*FILE:\s*(?P<path>\S+)\s*\n```[a-zA-Z0-9]*\n(?P<body>.*?)\n```", re.DOTALL)
_ANY_BLOCK = re.compile(r"```[a-zA-Z0-9]*\n(?P<body>.*?)\n```", re.DOTALL)


def _summarize(output: str) -> str:
    keep = [ln for ln in output.splitlines()
            if ln.startswith(("E ", "FAILED", "ERROR", ">")) or "Error" in ln or "assert" in ln]
    return "\n".join(keep[-14:])[:1800] or output[-900:]


def _signature(output: str) -> str:
    return hashlib.sha1(_summarize(output).encode()).hexdigest()[:12]


def _apply_whole(workspace: Path, resp: str, editable: set[str],
                 forbidden: set[str]) -> tuple[bool, str, list[str]]:
    edits = {m.group("path").strip().lstrip("./"): m.group("body")
             for m in _FILE_BLOCK.finditer(resp)}
    if not edits and len(editable) == 1:
        # No `*** FILE:` tag, one editable file: take the LARGEST fenced code block. A reasoning model
        # working a big change often emits SEVERAL fences while thinking (snippets, the diff, the final
        # file); the full-file answer is the biggest. (Was: require EXACTLY one block — which silently
        # rejected every multi-block response, capping the loop with no edit ever applied.)
        blocks = _ANY_BLOCK.findall(resp)
        if blocks:
            edits = {next(iter(editable)): max(blocks, key=len)}
    if not edits:
        return False, "no applicable code block (no '*** FILE:' tag and no fenced code found)", []
    written: list[str] = []
    for path, body in edits.items():
        if path in forbidden:
            return False, f"refused to edit a test/protected file: {path}", []
        if path not in editable:
            return False, f"refused to edit a non-allowlisted file: {path}", []
        dest = workspace / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body if body.endswith("\n") else body + "\n")
        written.append(path)
    return True, f"wrote {sorted(written)}", written


def _apply_diff(workspace: Path, resp: str, forbidden: set[str]) -> tuple[bool, str, list[str]]:
    import subprocess
    m = re.search(r"```(?:diff|patch)?\n(?P<body>.*?)\n```", resp, re.DOTALL)
    diff = (m.group("body") if m else resp).strip() + "\n"
    for f in forbidden:  # cheap guard: a diff that touches a test path is rejected wholesale
        if re.search(rf"^\+\+\+\s+\S*{re.escape(f)}", diff, re.MULTILINE):
            return False, f"diff touches a protected file: {f}", []
    patch_file = workspace / ".pf-coder.patch"
    patch_file.write_text(diff)
    touched = re.findall(r"^\+\+\+\s+[ab]?/?(\S+)", diff, re.MULTILINE)
    for p in ("-p1", "-p0"):
        r = subprocess.run(["patch", p, "--batch", "--forward", "-i", str(patch_file)],
                           cwd=workspace, capture_output=True, text=True)
        if r.returncode == 0:
            patch_file.unlink(missing_ok=True)
            return True, f"patch {p}", touched
    patch_file.unlink(missing_ok=True)
    return False, "patch did not apply (-p1/-p0)", []


def _read_sources(workspace: Path, editable: list[str]) -> str:
    parts = []
    for f in editable:
        p = workspace / f
        body = p.read_text() if p.exists() else "(file does not exist yet — create it)"
        parts.append(f"*** FILE: {f}\n```python\n{body}\n```")
    return "\n\n".join(parts)


def _fmt_instruction(editable: list[str], edit_format: str) -> str:
    if edit_format == "whole":
        return ("For EACH file you change, output a line `*** FILE: <relative path>` then a fenced "
                "code block with the ENTIRE new file contents. You may only edit these files: "
                f"{editable}.")
    return ("Output a single fenced ```diff block: a unified diff (`--- a/<f>` / `+++ b/<f>`, paths "
            f"relative to the workspace root). You may only edit these files: {editable}.")


def _prompt(workspace: Path, goal: str, editable: list[str], test_sources: dict[str, str],
            edit_format: str, failure: str | None, repeated: bool, playbook: str = "") -> str:
    tests = "\n\n".join(f"# Test (DO NOT EDIT) — {name}\n```python\n{src}\n```"
                        for name, src in test_sources.items())
    # playbook guidance lands in the BODY, BEFORE the "# Task" format suffix, so its hard rules win.
    pb = f"{playbook}\n\n" if playbook else ""
    head = (f"# Goal\n{goal}\n\n{tests}\n\n# Current source of editable files\n"
            f"{_read_sources(workspace, editable)}\n\n{pb}# Task\n")
    if failure is None:
        return head + f"Make the test(s) pass. {_fmt_instruction(editable, edit_format)}"
    strat = ("\nYou produced the SAME failure as before — change your APPROACH structurally; do not "
             "repeat the previous edit." if repeated else "")
    return head + (f"The test(s) still fail. Output:\n```\n{failure[-2500:]}\n```\nDiagnose the root "
                   f"cause and fix it. {_fmt_instruction(editable, edit_format)}{strat}")


class BespokeCoder:
    """The code-owned bounded loop (took the CoderEngine seat at M0(b))."""

    name = "bespoke"

    def __init__(self, role: str = "coder", *, llm: Callable[..., str] | None = None):
        self.role = role
        self._llm = llm   # injectable for tests; defaults to models.chat_text

    def _chat(self, prompt: str) -> str:
        if self._llm is not None:
            return self._llm(self.role, prompt, system=SYSTEM)
        from poc_foundry.models import chat_text  # lazy: keep import light
        return chat_text(self.role, prompt, system=SYSTEM)

    def run(self, *, workspace: Path, goal: str, editable_files: list[str],
            test_sources: dict[str, str], verify: VerifyFn,
            edit_format: str = "whole", max_attempts: int = 3, playbook: str = "") -> CoderResult:
        t0 = time.time()
        editable = list(editable_files)
        editable_set = set(editable)
        forbidden = set(test_sources)
        sigs: list[str] = []
        failure: str | None = None
        last_output = ""
        last_response = ""
        edited: list[str] = []

        for attempt in range(1, max_attempts + 1):
            repeated = bool(failure) and _signature(failure) in sigs
            try:
                resp = self._chat(_prompt(workspace, goal, editable, test_sources,
                                          edit_format, failure, repeated, playbook))
            except Exception as e:  # noqa: BLE001 — record, never crash the build
                return CoderResult(False, attempt, round(time.time() - t0, 1), edit_format,
                                   last_output, sigs, note=f"model error: {e!r}", edited=edited,
                                   last_response=last_response)
            last_response = resp

            if edit_format == "whole":
                applied, why, written = _apply_whole(workspace, resp, editable_set, forbidden)
            else:
                applied, why, written = _apply_diff(workspace, resp, forbidden)
            if not applied:
                if failure:
                    sigs.append(_signature(failure))
                failure = f"(edit not applied: {why})"
                # surface the apply failure as the running 'output' so the incident/forensics never go
                # blank when NO edit ever applies (the symptom that hid the thin-template failure).
                last_output = failure
                continue
            edited = sorted(set(edited) | set(written))

            ok, output = verify()
            last_output = output
            if ok:
                return CoderResult(True, attempt, round(time.time() - t0, 1), edit_format,
                                   output, sigs, edited=edited, last_response=last_response)
            if failure:
                sigs.append(_signature(failure))
            failure = output

        return CoderResult(False, max_attempts, round(time.time() - t0, 1), edit_format,
                           last_output, sigs, note="fix-attempt cap reached", edited=edited,
                           last_response=last_response)
