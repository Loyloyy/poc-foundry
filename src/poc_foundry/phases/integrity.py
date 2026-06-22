"""Integrity walls (design §5.5) — the trust mechanisms that turn a build that *ran* into one you can
*trust*. M2a S1. Pure stdlib (no broker / no LLM / no heavy deps) so every function here is
``py_compile``-able and fakes-testable on the 3.10 dev box; the phase (``pipeline.p4_iterate``) wires
these to the sandbox.

The three S1 walls:
  • **Inventory ledger** (§5.5 #5) — the tester's authored test names are recorded BEFORE the coder
    runs; after the coder reaches green an authoritative junit run must show *collected ∧ passed ⊇
    recorded*. Catches a test that was deleted, renamed, skipped, or quietly errored out.
  • **Diff scanner** (§5.5 #6) — a deterministic scan of the coder's per-iteration diff for
    test-adjacent / gate-weakening edits (touching a staged test, new skip/xfail, ``sys.exit``,
    ``conftest``/``addopts``/``sitecustomize`` shenanigans, assertion deletions in a test file). A
    positive hit is an integrity INCIDENT → fails the attempt + feeds ``security.incidents[]``.
  • **Red-first enforcement** (§5.5, was best-effort at M1) — the staged test MUST be RED against the
    scaffold before the coder runs. A test that is already green is a tester-inadequacy signal, not a
    pass.

All security language here is defense-in-depth (rule #9): these are detective controls layered on the
structural walls (coder edits orchestrator-side; staged tests mounted read-only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ── incidents ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Incident:
    """A flagged integrity event (→ ``security.incidents[]`` + a forced coder strategy change)."""

    kind: str          # test-edit | skip-marker | hard-exit | pytest-config | assert-deleted
    detail: str
    severity: str = "high"   # high (blocks done) | low (recorded only)

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}: {self.detail}"


# ── pytest node-id / junit parsing (the ledger) ───────────────────────────────
# We compare by the *test function name* (the final ``::`` segment): within one staged file names are
# unique, and this sidesteps brittle rootdir/path/classname normalization between collect-only output
# (``test_x.py::test_foo``) and junit (``classname="test_x" name="test_foo"``).
_NODE_RE = re.compile(r"::([A-Za-z_]\w*)\s*$")


def collected_names(collect_only_output: str) -> set[str]:
    """Parse ``pytest --collect-only -q`` stdout → the set of collected test-function names."""
    names: set[str] = set()
    for line in collect_only_output.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        m = _NODE_RE.search(line)
        if m:
            names.add(m.group(1))
    return names


def junit_passed_names(junit_xml: str) -> tuple[set[str], set[str]]:
    """Parse a pytest junit-xml → (passed_names, nonpassed_names). A testcase is *passed* iff it has
    no ``<failure>``/``<error>``/``<skipped>`` child. Malformed/empty XML → two empty sets (the
    caller treats 'no evidence' as a ledger failure)."""
    import xml.etree.ElementTree as ET

    passed: set[str] = set()
    nonpassed: set[str] = set()
    try:
        root = ET.fromstring(junit_xml.strip())
    except Exception:  # noqa: BLE001 — malformed junit is itself a ledger failure
        return passed, nonpassed
    for tc in root.iter("testcase"):
        name = tc.get("name") or ""
        if not name:
            continue
        bad = any(tc.find(tag) is not None for tag in ("failure", "error", "skipped"))
        (nonpassed if bad else passed).add(name)
    return passed, nonpassed


def inventory_ok(recorded: set[str], passed: set[str]) -> bool:
    """The ledger gate: a non-empty recorded set, fully covered by the passed set."""
    return bool(recorded) and recorded <= passed


def inventory_gap(recorded: set[str], passed: set[str]) -> set[str]:
    """Recorded tests that did NOT pass (deleted / renamed / skipped / errored)."""
    return set(recorded) - set(passed)


# ── diff scanner ──────────────────────────────────────────────────────────────
_HUNK_FILE_RE = re.compile(r"^\+\+\+\s+[ab]?/?(?P<path>\S+)", re.MULTILINE)
# added-line patterns (defense-in-depth; the coder is already host-blocked from non-allowlisted files)
_ADDED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("skip-marker", re.compile(r"@pytest\.mark\.(skip|xfail)|pytest\.skip\s*\(|^\s*pytestmark\b")),
    ("hard-exit", re.compile(r"\b(sys\.exit|os\._exit)\s*\(|raise\s+SystemExit")),
    ("pytest-config", re.compile(r"\baddopts\b|sitecustomize|usercustomize|PYTEST_ADDOPTS")),
]
_TEST_PATH_RE = re.compile(r"(^|/)(test_[^/]+\.py|conftest\.py)$|(^|/)tests?/")


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def scan_diff(diff_text: str, staged_test_names: set[str] | None = None) -> list[Incident]:
    """Scan a unified diff (the coder's edits this iteration) for tampering / gate-weakening.

    Flags: any edit that touches a test file or ``conftest.py`` (the coder must never); added
    skip/xfail markers, hard exits, or pytest-config injection in ANY edited file; and assertion
    deletions inside a test file. Returns a list of :class:`Incident` (empty = clean)."""
    incidents: list[Incident] = []
    staged = staged_test_names or set()

    # which files does this diff touch?
    touched = _HUNK_FILE_RE.findall(diff_text)
    for path in touched:
        base = path.rsplit("/", 1)[-1]
        if _is_test_path(path) or base in staged:
            incidents.append(Incident("test-edit",
                                      f"coder diff touches a test/protected file: {path}"))

    # per-added/removed-line scanning
    cur_file = ""
    cur_is_test = False
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            m = _HUNK_FILE_RE.match(line)
            cur_file = m.group("path") if m else ""
            cur_is_test = _is_test_path(cur_file) or cur_file.rsplit("/", 1)[-1] in staged
            continue
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            for kind, pat in _ADDED_PATTERNS:
                if pat.search(body):
                    incidents.append(Incident(kind, f"{cur_file or '?'}: + {body.strip()[:120]}"))
        elif line.startswith("-") and not line.startswith("---"):
            if cur_is_test and re.search(r"\bassert\b", line[1:]):
                incidents.append(Incident("assert-deleted",
                                          f"{cur_file or '?'}: assertion removed from a test"))
    # de-dup while preserving order
    seen: set[tuple] = set()
    out: list[Incident] = []
    for inc in incidents:
        key = (inc.kind, inc.detail)
        if key not in seen:
            seen.add(key)
            out.append(inc)
    return out


def blocking(incidents: list[Incident]) -> bool:
    """True if any incident is severe enough to block a ``done`` verdict."""
    return any(i.severity == "high" for i in incidents)
