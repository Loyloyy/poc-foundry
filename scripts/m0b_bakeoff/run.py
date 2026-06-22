#!/usr/bin/env python3
"""M0(b) bake-off runner (SERVER). Races the bespoke loop (whole-file + diff) vs scripted OpenCode
across the planted-break + red-first tasks, prints the attribution matrix, and saves a JSON trace.

    python3 -m scripts.m0b_bakeoff.run                         # all tasks, both engines, runc
    python3 -m scripts.m0b_bakeoff.run --runtime kata --tasks fix_logic feature_slug
    python3 -m scripts.m0b_bakeoff.run --engines bespoke       # skip the control arm

Prereqs: poc-foundry-sandbox image built; .env CODER_* (or PF_DEFAULT_ROLE) set; for the OpenCode arm
the `opencode` binary + an opencode.json provider config (bash/webfetch DENIED). DECISION RULE (fixed
up front): the winner takes the M1 CoderEngine seat, the loser is the fallback. This script reports;
the human logs the decision in DECISIONS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow `python3 scripts/m0b_bakeoff/run.py` as well as `-m scripts.m0b_bakeoff.run`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.m0b_bakeoff.engines import BespokeEngine, EngineResult, OpenCodeEngine  # noqa: E402
from scripts.m0b_bakeoff.sandbox import Sandbox  # noqa: E402
from scripts.m0b_bakeoff.tasks import TASKS, TASKS_DIR  # noqa: E402

WS_ROOT = Path(os.environ.get("PF_WORKSPACE_DIR", "/var/tmp/pf-m0b-ws")).expanduser()
TRACES = Path(__file__).resolve().parent / "traces"


def provision(task: str, label: str) -> Path:
    dst = WS_ROOT / f"{task}__{label}__{int(time.time()*1000)}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(TASKS_DIR / TASKS[task]["folder"], dst)
    return dst


def attribution(bespoke_pass: bool, oc: EngineResult | None) -> str:
    if oc is None or oc.skipped:
        return ("bespoke PASS (no control arm)" if bespoke_pass
                else "bespoke FAIL (no control arm — can't separate model-weak vs loop-weak)")
    if not bespoke_pass and not oc.passed:
        return "MODEL-WEAK — both engines fail ⇒ frontier reroute is the remedy"
    if not bespoke_pass and oc.passed:
        return "LOOP-WEAK — OpenCode passes, bespoke fails ⇒ fix our loop (don't pay for frontier)"
    if bespoke_pass and not oc.passed:
        return "bespoke stronger — bespoke passes, OpenCode fails"
    return "tie — both pass"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=sorted(TASKS), choices=sorted(TASKS))
    ap.add_argument("--engines", nargs="*", default=["bespoke", "opencode"], choices=["bespoke", "opencode"])
    ap.add_argument("--edit-formats", nargs="*", default=["whole", "diff"], choices=["whole", "diff"])
    ap.add_argument("--runtime", default="runc", choices=["runc", "kata"])
    ap.add_argument("--role", default="coder")
    ap.add_argument("--image", default="poc-foundry-sandbox")
    ap.add_argument("--max-attempts", type=int,
                    default=int(os.environ.get("PF_MAX_FIX_ATTEMPTS", "4")))
    ap.add_argument("--keep", action="store_true", help="keep workspaces for inspection")
    args = ap.parse_args(argv)

    WS_ROOT.mkdir(parents=True, exist_ok=True)
    bespoke = BespokeEngine(role=args.role)
    opencode = OpenCodeEngine(role=args.role)

    runs: list[dict] = []
    by_task: dict[str, dict] = {t: {"bespoke": {}, "opencode": None} for t in args.tasks}

    for task in args.tasks:
        meta = TASKS[task]
        plan: list[tuple] = []
        if "bespoke" in args.engines:
            plan += [(bespoke, fmt) for fmt in args.edit_formats]
        if "opencode" in args.engines:
            plan += [(opencode, "n/a")]
        for engine, fmt in plan:
            ws = provision(task, f"{engine.name}-{fmt}")
            print(f"\n=== {task} | {engine.name} | {fmt} (runtime={args.runtime}) ===")
            try:
                with Sandbox(ws, image=args.image, runtime=args.runtime) as sb:
                    res = engine.run(ws, meta, sb, fmt, args.max_attempts)
            except Exception as e:  # noqa: BLE001
                res = EngineResult(engine.name, fmt, False, 0, 0.0, -1, note=f"sandbox error: {e!r}")
            verdict = "SKIP" if res.skipped else ("PASS" if res.passed else "FAIL")
            print(f"    -> {verdict} attempts={res.attempts} wall={res.wall_s}s rc={res.last_rc} {res.note}")
            runs.append({"task": task, "kind": meta["kind"], **res.__dict__})
            if engine.name == "bespoke":
                by_task[task]["bespoke"][fmt] = res
            else:
                by_task[task]["opencode"] = res
            if not args.keep:
                shutil.rmtree(ws, ignore_errors=True)

    # ── report ──
    print("\n" + "=" * 72 + "\nATTRIBUTION MATRIX\n" + "=" * 72)
    fmt_pass = {"whole": 0, "diff": 0}
    bespoke_tasks = oc_tasks = 0
    for task in args.tasks:
        b = by_task[task]["bespoke"]
        oc = by_task[task]["opencode"]
        bespoke_pass = any(r.passed for r in b.values())
        if bespoke_pass:
            bespoke_tasks += 1
        if oc and oc.passed:
            oc_tasks += 1
        for fmt, r in b.items():
            if r.passed and fmt in fmt_pass:
                fmt_pass[fmt] += 1
        per_fmt = " ".join(f"{f}={'P' if r.passed else 'F'}({r.attempts})" for f, r in b.items())
        oc_str = "skip" if (not oc or oc.skipped) else (f"{'P' if oc.passed else 'F'}({oc.attempts})")
        print(f"  {task:14s} [{TASKS[task]['kind']:16s}] bespoke[{per_fmt}] opencode[{oc_str}]")
        print(f"       → {attribution(bespoke_pass, oc)}")

    print("\nEDIT-FORMAT (bespoke pass counts): "
          f"whole={fmt_pass['whole']}  diff={fmt_pass['diff']}  "
          f"→ prefer {'whole-file' if fmt_pass['whole'] >= fmt_pass['diff'] else 'unified-diff'} "
          "for this model (adapt edit format to model strength)")
    print(f"TASKS PASSED: bespoke={bespoke_tasks}/{len(args.tasks)}  opencode={oc_tasks}/{len(args.tasks)}")
    print("DECISION RULE (fixed): winner takes the M1 CoderEngine seat, loser = fallback. "
          "Log the call + reasoning in DECISIONS.md.")

    TRACES.mkdir(exist_ok=True)
    out = TRACES / f"m0b-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"runtime": args.runtime, "role": args.role, "max_attempts": args.max_attempts,
                   "engines": args.engines, "edit_formats": args.edit_formats},
        "runs": runs,
        "summary": {"bespoke_tasks": bespoke_tasks, "opencode_tasks": oc_tasks, "edit_format": fmt_pass},
    }, indent=2))
    print(f"\ntrace → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
