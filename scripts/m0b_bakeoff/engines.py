"""The two coder engines behind a common seam (the M1 `CoderEngine`; winner takes the seat).

  BespokeEngine  — a code-owned bounded loop: prompt → apply edit (whole-file OR unified diff) →
                   verify in sandbox → feed failure back; bounded attempts; error-signature tracking
                   with a forced strategy change on repeats (the escalation ladder, design §5.8).
                   THIS is what M1 promotes if it wins.
  OpenCodeEngine — scripted headless `opencode run` in the workspace (the M0(b) control arm). Edits
                   files only (bash/webfetch denied via opencode.json — prework lesson: OpenCode bash
                   runs on the serve host). The harness owns verification in the sandbox.

Both edit files ORCHESTRATOR-SIDE (host workspace dir); the sandbox only executes (the design claim).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .model_client import chat
from .sandbox import Sandbox

SYSTEM = (
    "You are a precise software engineer fixing a small Python task. You will be given a one-line "
    "spec, the failing test (which you MUST NOT edit), and the current source. Make the test pass "
    "with the smallest correct change. Think step by step, then output ONLY the edit in the exact "
    "format requested — no prose, no explanation outside the code."
)


@dataclass
class EngineResult:
    engine: str
    edit_format: str          # "whole" | "diff" | "n/a"
    passed: bool
    attempts: int
    wall_s: float
    last_rc: int
    signatures: list[str] = field(default_factory=list)
    note: str = ""
    skipped: bool = False


# ───────────────────────── helpers ─────────────────────────
def _summarize(output: str) -> str:
    keep = [ln for ln in output.splitlines()
            if ln.startswith(("E ", "FAILED", "ERROR", ">")) or "Error" in ln or "assert" in ln]
    return "\n".join(keep[-12:])[:1500] or output[-800:]


def _signature(output: str) -> str:
    return hashlib.sha1(_summarize(output).encode()).hexdigest()[:12]


def _read_sources(ws: Path, meta: dict) -> str:
    parts = []
    for f in meta["src"]:
        p = ws / f
        parts.append(f"*** FILE: {f}\n```python\n{p.read_text() if p.exists() else ''}\n```")
    return "\n\n".join(parts)


_FILE_BLOCK = re.compile(r"\*\*\*\s*FILE:\s*(?P<path>\S+)\s*\n```[a-zA-Z0-9]*\n(?P<body>.*?)\n```", re.DOTALL)
_ANY_BLOCK = re.compile(r"```[a-zA-Z0-9]*\n(?P<body>.*?)\n```", re.DOTALL)


def _apply_whole(ws: Path, resp: str, meta: dict) -> tuple[bool, str]:
    edits = {m.group("path").strip().lstrip("./"): m.group("body") for m in _FILE_BLOCK.finditer(resp)}
    if not edits:  # fallback: a single code block + a single editable source file
        blocks = _ANY_BLOCK.findall(resp)
        if len(blocks) == 1 and len(meta["src"]) == 1:
            edits = {meta["src"][0]: blocks[0]}
    if not edits:
        return False, "no '*** FILE:' blocks found"
    for path, body in edits.items():
        if path == meta["test"]:
            return False, "attempted to edit the test (forbidden)"
        (ws / path).write_text(body if body.endswith("\n") else body + "\n")
    return True, f"wrote {sorted(edits)}"


def _apply_diff(ws: Path, resp: str) -> tuple[bool, str]:
    m = re.search(r"```(?:diff|patch)?\n(?P<body>.*?)\n```", resp, re.DOTALL)
    diff = (m.group("body") if m else resp).strip() + "\n"
    patch = ws / ".m0b.patch"
    patch.write_text(diff)
    for p in ("-p1", "-p0"):
        r = subprocess.run(["patch", p, "--batch", "--forward", "-i", str(patch)],
                           cwd=ws, capture_output=True, text=True)
        if r.returncode == 0:
            patch.unlink(missing_ok=True)
            return True, f"patch {p}"
    patch.unlink(missing_ok=True)
    return False, "patch did not apply (-p1/-p0)"


def _instruction(meta: dict, edit_format: str, failure: str | None, repeated: bool) -> str:
    fmt = ("For each file you change, output a line `*** FILE: <relative path>` then a fenced code "
           "block with the ENTIRE new file contents." if edit_format == "whole" else
           "Output a single fenced ```diff block: a unified diff (paths relative to the workspace "
           "root, `--- a/<f>` / `+++ b/<f>`). Nothing else.")
    if failure is None:
        return f"Make `python -m pytest {meta['test']}` pass. {fmt}"
    strat = ("\nYou produced the SAME failure as before — change your APPROACH structurally, do not "
             "repeat the previous edit." if repeated else "")
    return (f"The test still fails. pytest output:\n```\n{failure[-2500:]}\n```\nDiagnose the cause "
            f"and fix it. {fmt}{strat}")


def _prompt(ws: Path, meta: dict, edit_format: str, failure: str | None, repeated: bool) -> str:
    spec = (ws / "SPEC.md").read_text().strip()
    test = (ws / meta["test"]).read_text()
    return (f"# Spec\n{spec}\n\n# Failing test (DO NOT EDIT) — {meta['test']}\n```python\n{test}\n```\n\n"
            f"# Current source\n{_read_sources(ws, meta)}\n\n# Task\n"
            f"{_instruction(meta, edit_format, failure, repeated)}")


# ───────────────────────── engines ─────────────────────────
class BespokeEngine:
    name = "bespoke"

    def __init__(self, role: str = "coder"):
        self.role = role

    def run(self, ws: Path, meta: dict, sandbox: Sandbox, edit_format: str, max_attempts: int) -> EngineResult:
        t0 = time.time()
        sigs: list[str] = []
        failure: str | None = None
        last_rc = -1
        for attempt in range(1, max_attempts + 1):
            repeated = bool(failure) and _signature(failure) in sigs
            try:
                resp = chat(self.role, _prompt(ws, meta, edit_format, failure, repeated), system=SYSTEM)
            except Exception as e:  # noqa: BLE001 — record, don't crash the bake-off
                return EngineResult(self.name, edit_format, False, attempt, round(time.time() - t0, 1),
                                    last_rc, sigs, note=f"model error: {e!r}")
            applied, why = (_apply_whole(ws, resp, meta) if edit_format == "whole"
                            else _apply_diff(ws, resp))
            if not applied:
                failure = f"(edit not applied: {why})"
                continue
            res = sandbox.verify(meta["test"])
            last_rc = res.rc
            if res.ok:
                return EngineResult(self.name, edit_format, True, attempt, round(time.time() - t0, 1), 0, sigs)
            if failure:
                sigs.append(_signature(failure))
            failure = res.combined
        return EngineResult(self.name, edit_format, False, max_attempts, round(time.time() - t0, 1),
                            last_rc, sigs, note="cap reached")


class OpenCodeEngine:
    """Control arm: scripted `opencode run`. Requires the `opencode` binary + an opencode.json
    provider config (endpoint/key) — see prework docker/opencode.example.json. bash/webfetch must be
    DENIED in that config so OpenCode only edits files (its bash runs on the serve host)."""

    name = "opencode"

    def __init__(self, role: str = "coder"):
        import os
        from .model_client import _resolve
        self.provider = os.environ.get("OPENCODE_PROVIDER_ID", "vllm_api").strip()
        try:
            self.model = _resolve(role)[0]
        except SystemExit:
            self.model = ""

    def run(self, ws: Path, meta: dict, sandbox: Sandbox, edit_format: str, max_attempts: int) -> EngineResult:
        t0 = time.time()
        if not shutil.which("opencode"):
            return EngineResult(self.name, "n/a", False, 0, 0.0, -1, skipped=True, note="opencode binary not found")
        model_flag = f"{self.provider}/{self.model}" if self.model else None
        failure: str | None = None
        last_rc = -1
        for attempt in range(1, max_attempts + 1):
            instr = (f"Read SPEC.md and {meta['test']}, then make `python -m pytest {meta['test']}` pass by "
                     f"editing the source file(s) only — do NOT edit {meta['test']} and do NOT run shell "
                     f"commands.") if failure is None else (
                     f"The test still fails:\n{failure[-2000:]}\nFix the source so it passes.")
            cmd = ["opencode", "run"] + (["--model", model_flag] if model_flag else []) + [instr]
            try:
                subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=600)
            except Exception as e:  # noqa: BLE001
                return EngineResult(self.name, "n/a", False, attempt, round(time.time() - t0, 1),
                                    last_rc, note=f"opencode error: {e!r}")
            res = sandbox.verify(meta["test"])
            last_rc = res.rc
            if res.ok:
                return EngineResult(self.name, "n/a", True, attempt, round(time.time() - t0, 1), 0)
            failure = res.combined
        return EngineResult(self.name, "n/a", False, max_attempts, round(time.time() - t0, 1),
                            last_rc, note="cap reached")
