#!/usr/bin/env python3
"""Tiered evals v1 runner (design §5.11) — spec + plan evals against recorded fixtures.

The CHEAP eval rung: ingest+spec+plan on each fixture (NO sandbox / clean-room / Langfuse), scored
deterministically + with a recorded human-grading rubric. Mirrors ``run_contract_checks.py`` as a
plain no-pytest script, BUT unlike the contract check it calls the real **architect** LLM in P1 — so
it runs **on the server** (a configured ``.env``), not on the local green bar (the deterministic
SCORING is proven locally by ``tests/test_m2c_evals.py`` with a fake architect).

    python3 scripts/run_evals.py                                   # the committed sample fixture
    python3 scripts/run_evals.py path/to/run_folder ...            # explicit fixtures
    python3 scripts/run_evals.py --json builds/evals/latest.json   # also persist the reports
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from poc_foundry.evals import format_report, run_evals, save_reports  # noqa: E402


def main(argv: list[str]) -> int:
    args = argv[1:]
    json_out: str | None = None
    if "--json" in args:
        i = args.index("--json")
        json_out = args[i + 1] if i + 1 < len(args) else str(ROOT / "builds" / "evals" / "latest.json")
        del args[i:i + 2]
    fixtures = args or [str(ROOT / "tests" / "fixtures" / "sample_artifact")]

    reports = run_evals(fixtures)
    for rep in reports:
        print(format_report(rep))
        print()

    if json_out:
        save_reports(reports, Path(json_out))
        print(f"→ wrote {len(reports)} report(s) to {json_out}")

    ran = sum(1 for r in reports if r.ok)
    print(f"\nSUMMARY: {ran}/{len(reports)} eval(s) ran clean")
    return 1 if ran != len(reports) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
