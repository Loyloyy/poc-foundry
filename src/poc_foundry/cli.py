"""Thin CLI over the headless contract (design §5.1: presentation holds NO pipeline logic).

    python -m poc_foundry.cli build <run-folder-or-artifact-id> [--brief ...] [--driver ...]
    python -m poc_foundry.cli list
    python -m poc_foundry.cli resume <build-id>
    python -m poc_foundry.cli stop <build-id>
    python -m poc_foundry.cli clean <build-id>
    python -m poc_foundry.cli eval [--fixture PATH ...] [--template NAME] [--min-score F] [--json PATH]

Everything delegates to ``poc_foundry.core``. Argparse only; no heavy imports at module load.
"""
from __future__ import annotations

import argparse
import sys


def _cmd_build(args) -> int:
    from poc_foundry.core import build_poc

    report, artifact = build_poc(args.source, brief=args.brief, driver=args.driver,
                                 template=args.template, runtime=args.runtime)
    print(report)
    print(f"\n→ {artifact.id}: status={artifact.status} "
          f"demonstrates_core_value={artifact.final_verdict.demonstrates_core_value}")
    return 0 if artifact.status in ("done", "not-buildable") else 1


def _cmd_list(args) -> int:
    from poc_foundry.core import list_builds

    rows = list_builds()
    if not rows:
        print("(no builds yet)")
        return 0
    for r in rows:
        print(f"{r['id']}  {r['status']:<13} core={r['demonstrates']:<7} from {r['source']}")
    return 0


def _cmd_resume(args) -> int:
    from poc_foundry.core import resume_build

    report, artifact = resume_build(args.build_id, runtime=args.runtime)
    print(report)
    print(f"\n→ {artifact.id}: status={artifact.status}")
    return 0 if artifact.status in ("done", "not-buildable") else 1


def _cmd_stop(args) -> int:
    from poc_foundry.core import request_stop_build

    path = request_stop_build(args.build_id)
    print(f"stop requested for {args.build_id} (sentinel: {path}); it will checkpoint + exit at the "
          f"next node boundary — resume with `resume {args.build_id}`")
    return 0


def _cmd_clean(args) -> int:
    from poc_foundry.core import clean_build

    removed = clean_build(args.build_id, workspaces=not args.keep_workspace)
    for p in removed:
        print(f"removed {p}")
    if not removed:
        print(f"nothing to remove for {args.build_id}")
    return 0


def _cmd_eval(args) -> int:
    from pathlib import Path

    from poc_foundry.evals import format_report, run_evals, save_reports

    repo_root = Path(__file__).resolve().parents[2]
    fixtures = args.fixture or [str(repo_root / "tests" / "fixtures" / "sample_artifact")]
    reports = run_evals(fixtures, template_name=args.template)

    for rep in reports:
        print(format_report(rep))
        print()

    if args.json:
        save_reports(reports, Path(args.json))
        print(f"→ wrote {len(reports)} report(s) to {args.json}")

    failed = [r for r in reports if not r.ok or r.overall_score < args.min_score]
    print(f"\nSUMMARY: {len(reports) - len(failed)}/{len(reports)} fixture(s) "
          f"at/above min-score {args.min_score}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="poc-foundry", description="Stage-3 PoC foundry (headless core)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a PoC from a Stage-2 run folder / artifact id")
    b.add_argument("source", help="run-folder path, or artifact id under PF_ARTIFACTS_ROOT")
    b.add_argument("--brief", default="", help="optional human brief to shape the spec")
    b.add_argument("--driver", default="tech-scout", choices=["tech-scout", "customer", "workshop"])
    b.add_argument("--template", default=None, help="template name (default from pipeline.yaml)")
    b.add_argument("--runtime", default=None, help="sandbox runtime (kata|runc); default kata")
    b.set_defaults(func=_cmd_build)

    sub.add_parser("list", help="list emitted builds").set_defaults(func=_cmd_list)

    r = sub.add_parser("resume", help="resume a checkpointed build from its last node")
    r.add_argument("build_id")
    r.add_argument("--runtime", default=None)
    r.set_defaults(func=_cmd_resume)

    s = sub.add_parser("stop", help="request a cooperative stop (checkpoints + exits; resume-able)")
    s.add_argument("build_id")
    s.set_defaults(func=_cmd_stop)

    c = sub.add_parser("clean", help="remove a build's folder + local workspace")
    c.add_argument("build_id")
    c.add_argument("--keep-workspace", action="store_true", help="keep the local-disk workspace")
    c.set_defaults(func=_cmd_clean)

    e = sub.add_parser("eval", help="cheap spec+plan evals against fixture artifacts (no sandbox)")
    e.add_argument("--fixture", action="append", default=[],
                   help="run-folder path (repeatable; default the committed sample fixture)")
    e.add_argument("--template", default=None, help="template name (default from pipeline.yaml)")
    e.add_argument("--min-score", type=float, default=0.0,
                   help="exit nonzero if any fixture's overall score is below this (default 0 = report only)")
    e.add_argument("--json", default=None, help="also write the structured reports to this JSON path")
    e.set_defaults(func=_cmd_eval)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
