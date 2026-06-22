#!/usr/bin/env python3
"""M0(c) — Ingest probe. Load a REAL Stage-2 run folder, validate the contract, sketch the
capability manifest, and (optionally) run a detect-only freshness pass.

    python3 scripts/m0_ingest_probe.py <path/to/artifacts/dra-...>            # load + validate
    python3 scripts/m0_ingest_probe.py <run-folder> --freshness               # + GitHub drift (egress)

Stdlib + pydantic only (rule #3): runnable on the dev box for the SAMPLE, and on the server against a
real run folder. The P1 spec-generation half of M0(c) needs the architect LLM + the P1 phase, so it
lands with M1 (see ROADMAP note) — this probe covers deterministic P0: load, validate, manifest,
freshness-detect.

NOTE: freshness records drift as CAVEATS only — it NEVER mutates pins (design §5.3 P0).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from poc_foundry.ingest import clamp_confidence, load_run, validate_semantics  # noqa: E402

# Reachable build substrate (design §2; server-wide allowlist). node/npm is BLOCKED in v1.
_REACHABLE = ["github.com", "huggingface.co", "pypi.org", "container registries", "google/bing"]
_VETTED_SERVICES = {"milvus", "pgvector", "postgres", "opensearch", "elasticsearch"}


def _gh_api(owner_repo: str, timeout: float = 12.0):
    import os
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner_repo}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "poc-foundry-m0c"},
    )
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # honors http(s)_proxy env
        return json.loads(r.read().decode())


def _owner_repo(url: str) -> str | None:
    if "github.com/" not in url:
        return None
    tail = url.split("github.com/", 1)[1].strip("/")
    parts = tail.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_folder", help="path to a Stage-2 run folder (artifacts/<id>/)")
    ap.add_argument("--freshness", action="store_true", help="detect-only GitHub drift (needs egress)")
    args = ap.parse_args()

    rf = load_run(args.run_folder)
    a = rf.artifact
    print(f"== loaded {rf.path}  (v{rf.version}) ==")
    print(f"  id={a.id}  topic={a.topic!r}")
    print(f"  findings={len(a.findings)} sources={len(a.sources)} "
          f"reference_repos={len(a.reference_repos)} tech_stack={len(a.tech_stack)} "
          f"impl_steps={len(a.implementation_steps)}")
    print(f"  companions: report.md={'Y' if rf.report_md else 'N'} "
          f"comparison.md={'Y' if rf.comparison_md else 'N'} "
          f"code_files={len(rf.code_files)} manifest={'Y' if rf.manifest else 'N'} "
          f"coverage={'Y' if rf.coverage else 'N'}")

    print("\n== semantic validation ==")
    issues = validate_semantics(a)
    errs = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity == "warn"]
    for i in issues:
        print(f"  {i}")
    if not issues:
        print("  (no issues)")
    n = clamp_confidence(a)
    if n:
        print(f"  defensively clamped {n} out-of-range confidence value(s)")

    print("\n== capability manifest (sketch) ==")
    print(f"  python: yes   node/npm: BLOCKED (no JS form factor in v1)")
    print(f"  reachable substrate: {', '.join(_REACHABLE)}")
    hits = sorted({t.choice.lower() for t in a.tech_stack if t.choice.lower() in _VETTED_SERVICES})
    print(f"  vetted sibling services implied by tech_stack: {hits or '(none detected)'}")
    repro = {r.name: r.reproducibility for r in a.reference_repos}
    print(f"  reference-repo reproducibility: {repro or '(none)'}")

    if args.freshness:
        print("\n== freshness pass (detect-only; never mutates pins) ==")
        for r in a.reference_repos:
            orp = _owner_repo(r.url)
            if not orp:
                print(f"  {r.name}: not a github repo — skipped")
                continue
            try:
                meta = _gh_api(orp)
                drift = []
                if r.archived is not None and bool(meta.get("archived")) != r.archived:
                    drift.append(f"archived {r.archived}→{meta.get('archived')}")
                pushed = (meta.get("pushed_at") or "")[:10]
                if r.last_commit and pushed and pushed != r.last_commit:
                    drift.append(f"last_commit {r.last_commit}→{pushed}")
                if meta.get("full_name", "").lower() != orp.lower():
                    drift.append(f"moved → {meta.get('full_name')}")
                print(f"  {orp}: {'DRIFT: ' + '; '.join(drift) if drift else 'no drift'}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"  {orp}: unchecked ({type(e).__name__}) — caveat, not a failure")

    print(f"\nRESULT: {'GREEN (no semantic errors)' if not errs else f'{len(errs)} ERROR(s) — investigate'}"
          f"  ({len(warns)} warning(s))")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
