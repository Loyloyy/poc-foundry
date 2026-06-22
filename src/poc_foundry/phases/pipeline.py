"""The deterministic phase pipeline P0→P7 (design §5.3). Each ``pN_*(state, ctx) -> dict`` returns
the ``BuildState`` field updates for that node; ``graph.py`` wires them with LangGraph + a SQLite
checkpointer. Heavy deps (langchain) are lazy-imported inside the phases so this module stays
``py_compile``-able on the 3.10 dev box.

Phase map:
  P0 ingest    — load + validate the Stage-2 run folder (wraps ``ingest.load_run``).
  P1 spec      — architect → ``Spec`` (3–6 criteria, exactly one core; NOT_BUILDABLE possible).
  P2 plan      — deterministic single iteration for M1, interface pinned from the template.
  P3 scaffold  — stamp the gradio template, git-init/commit, GREEN smoke in a fresh VM.
  P4 iterate   — tester writes a RED-first staged test; the CoderEngine makes it green (staged VERIFY).
  P5 docs      — seeded RUN/README/AGENTS + a scribe DEMO.md (optional, fallback-safe).
  P6 cleanroom — fresh VM + fresh clone; run RUN.md install/test/demo blocks.
  P7 emit      — assemble + persist the ``PoCBuildArtifact`` + 00_INDEX.md + the workspace copy.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from poc_foundry import prompts
from poc_foundry.phases.context import (
    Ctx,
    chown_to_builder,
    git_commit,
    git_init,
    parse_run_blocks,
    stamp_template,
    staged_tests_mount,
    ws_mount,
)

_CODE_BLOCK = re.compile(r"```(?:python)?\n(?P<body>.*?)\n```", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_code(resp: str) -> str:
    m = _CODE_BLOCK.search(resp)
    return (m.group("body") if m else resp).strip() + "\n"


# ── P0 ingest ────────────────────────────────────────────────────────────────
def p0_ingest(state, ctx: Ctx) -> dict:
    from poc_foundry.ingest import clamp_confidence, load_run, validate_semantics

    rf = load_run(ctx.run_dir)
    clamped = clamp_confidence(rf.artifact)
    issues = validate_semantics(rf.artifact)
    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity != "error"]
    ctx.run_folder = rf
    ctx.say(f"P0 ingest: loaded {rf.artifact.id} v{rf.version} "
            f"({len(errors)} errors, {len(warns)} warnings, {clamped} confidences clamped)")
    if errors:
        return {"phase": "ingest", "status": "failed",
                "error": "; ".join(str(e) for e in errors[:5]),
                "log": state.log + [f"P0 ingest FAILED: {len(errors)} contract errors"],
                "artifact_id": rf.artifact.id, "version": rf.version}
    return {"phase": "ingest", "artifact_id": rf.artifact.id, "run_id": rf.artifact.id,
            "version": rf.version,
            "caveats": state.caveats + [str(w) for w in warns],
            "log": state.log + [f"P0 ingest: {rf.artifact.id} v{rf.version} OK"]}


# ── P1 spec ──────────────────────────────────────────────────────────────────
def _normalize_spec(spec, template):
    spec.template = template.name
    crits = spec.success_criteria
    cores = [c for c in crits if c.core]
    if not cores and crits:
        crits[0].core = True
    elif len(cores) > 1:
        for c in cores[1:]:
            c.core = False
    for c in crits:
        if c.type not in ("met-by-test", "met-by-demo-evidence"):
            c.type = "met-by-test"
    return spec


def _lint_spec(spec) -> list[str]:
    out = []
    n = len(spec.success_criteria)
    if not (3 <= n <= 6):
        out.append(f"spec-lint: {n} success criteria (expected 3–6)")
    if sum(1 for c in spec.success_criteria if c.core) != 1:
        out.append("spec-lint: not exactly one core criterion")
    if not spec.goal.strip():
        out.append("spec-lint: empty goal")
    return out


def p1_spec(state, ctx: Ctx) -> dict:
    from poc_foundry.models import build_chat_model
    from poc_foundry.state import Spec

    art = ctx.run_folder.artifact
    llm = build_chat_model("architect").with_structured_output(Spec)
    spec = llm.invoke([("system", prompts.SPEC_SYSTEM),
                       ("human", prompts.spec_prompt(art, ctx.template.interface))])
    if isinstance(spec, dict):
        spec = Spec(**spec)
    spec = _normalize_spec(spec, ctx.template)
    lint = _lint_spec(spec)

    if not spec.buildable:
        ctx.say(f"P1 spec: NOT_BUILDABLE — {'; '.join(spec.not_buildable_reasons) or 'no reason given'}")
        return {"phase": "spec", "spec": spec, "status": "not-buildable",
                "caveats": state.caveats + lint,
                "log": state.log + ["P1 spec: NOT_BUILDABLE"]}

    core = next((c.text for c in spec.success_criteria if c.core), "(none)")
    ctx.say(f"P1 spec: goal={spec.goal!r}; {len(spec.success_criteria)} criteria; core={core!r}")
    return {"phase": "spec", "spec": spec, "caveats": state.caveats + lint,
            "log": state.log + [f"P1 spec: {len(spec.success_criteria)} criteria"]}


# ── P2 plan (deterministic single iteration for M1) ──────────────────────────
def p2_plan(state, ctx: Ctx) -> dict:
    from poc_foundry.state import IterationPlan, Plan

    spec = state.spec
    core = next((c for c in spec.success_criteria if c.core), None)
    acceptance = [c.text for c in spec.success_criteria if c.type == "met-by-test"]
    it = IterationPlan(
        goal=(core.text if core else spec.goal),
        acceptance=acceptance,
        interface=ctx.template.interface,
        files=list(ctx.template.editable_files),
    )
    ctx.say(f"P2 plan: 1 iteration (M1); interface pinned to {ctx.template.interface}")
    return {"phase": "plan", "plan": Plan(iterations=[it]),
            "log": state.log + ["P2 plan: 1 iteration"]}


# ── P3 scaffold ──────────────────────────────────────────────────────────────
def p3_scaffold(state, ctx: Ctx) -> dict:
    written = stamp_template(ctx.template, ctx.workspace_dir)
    git_init(ctx.workspace_dir)
    sha = git_commit(ctx.workspace_dir, "scaffold: stamp gradio-chatbot template (start-green)")
    chown_to_builder(ctx.workspace_dir)   # sandbox (uid 1000) must write caches into /work

    sbx = ctx.broker.create(mounts=[ws_mount(ctx.workspace_dir)], name="scaffold")
    try:
        res = sbx.exec(f"cd /work && python -m pytest {ctx.template.suite} -q", timeout_s=300)
    finally:
        sbx.destroy()

    if not res.ok:
        ctx.say("P3 scaffold: smoke RED — template suite did not pass in a fresh VM")
        return {"phase": "scaffold", "status": "failed", "scaffold_sha": sha,
                "error": "scaffold smoke failed:\n" + res.combined[-1500:],
                "log": state.log + ["P3 scaffold: smoke RED"]}
    ctx.say(f"P3 scaffold: stamped {len(written)} files; smoke GREEN @ {sha}")
    return {"phase": "scaffold", "scaffold_sha": sha, "commit_sha": sha,
            "log": state.log + [f"P3 scaffold: GREEN @ {sha}"]}


# ── P4 iterate (red-first tester + CoderEngine + staged VERIFY) ──────────────
def _tester_write(ctx: Ctx, criterion_text: str, goal: str, interface: str) -> str:
    from poc_foundry.models import chat_text
    resp = chat_text("tester", prompts.tester_prompt(criterion_text, goal, interface),
                     system=prompts.TESTER_SYSTEM)
    return _extract_code(resp)


def p4_iterate(state, ctx: Ctx) -> dict:
    from poc_foundry.artifact import IterationRecord

    spec, plan = state.spec, state.plan
    it = plan.iterations[0]
    core = next((c for c in spec.success_criteria if c.core), spec.success_criteria[0])

    test_src = _tester_write(ctx, core.text, it.goal, it.interface)
    staging_tests = ctx.staging_dir / "tests"
    if staging_tests.exists():
        shutil.rmtree(staging_tests)
    staging_tests.mkdir(parents=True, exist_ok=True)
    (staging_tests / "test_criterion.py").write_text(test_src)

    chown_to_builder(staging_tests)
    sbx = ctx.broker.create(
        mounts=[ws_mount(ctx.workspace_dir), staged_tests_mount(staging_tests)], name="iterate")
    crit_status, it_status, attempts, note = "pending", "pending", 0, ""
    try:
        def verify():
            # /staged is RO → don't write bytecode there; import the workspace core from /work.
            r = sbx.exec("cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                         "python -m pytest /staged -q", timeout_s=300)
            return r.ok, r.combined

        ok0, _ = verify()
        if ok0:
            it_status, crit_status = "green", "met"
            note = "criterion already met by scaffold (not red-first)"
            ctx.say("P4 iterate: staged test GREEN against scaffold (no code change needed)")
        else:
            res = ctx.coder.run(
                workspace=ctx.workspace_dir, goal=it.goal, editable_files=it.files,
                test_sources={"test_criterion.py": test_src}, verify=verify,
                edit_format="whole", max_attempts=getattr(ctx.cfg, "max_fix_attempts", 3))
            attempts = res.attempts
            if res.passed:
                it_status, crit_status = "green", "met"
                ctx.say(f"P4 iterate: RED→GREEN in {attempts} attempt(s); edited {res.edited}")
            else:
                it_status, crit_status = "abandoned", "descoped"
                note = res.note or "coder did not reach green"
                ctx.say(f"P4 iterate: criterion DESCOPED after {attempts} attempt(s) ({note})")
    finally:
        sbx.destroy()

    sha = state.commit_sha
    if it_status == "green":
        sha = git_commit(ctx.workspace_dir, f"iterate: {it.goal[:60]}")

    # reflect the outcome on the core criterion
    for c in spec.success_criteria:
        if c.core:
            c.status = crit_status

    rec = IterationRecord(goal=it.goal, status=it_status, attempts=attempts, tests_added=1)
    return {"phase": "iterate", "spec": spec, "commit_sha": sha,
            "iteration_records": state.iteration_records + [rec],
            "staged_tests": state.staged_tests + ["test_criterion.py"],
            "iteration": state.iteration + 1,
            "caveats": state.caveats + ([note] if note else []),
            "log": state.log + [f"P4 iterate: {it_status} (attempts={attempts})"]}


# ── P5 docs ──────────────────────────────────────────────────────────────────
def _scribe_demo(ctx: Ctx, spec) -> str:
    try:
        from poc_foundry.models import chat_text
        crit = [c.text for c in spec.success_criteria]
        md = chat_text("scribe", prompts.scribe_demo_prompt(spec.goal, spec.demo_scenario, crit),
                       system=prompts.SCRIBE_SYSTEM)
        if md.strip():
            return md.strip() + "\n"
    except Exception:  # noqa: BLE001 — docs never fail a build
        pass
    crit = "\n".join(f"- {c.text}" for c in spec.success_criteria)
    return (f"# Demo\n\n{spec.goal}\n\n## Run\nFollow `RUN.md` to install, test, and launch "
            f"(`python app.py`), then open http://localhost:7860.\n\n## What to look for\n"
            f"{spec.demo_scenario}\n\n## Success criteria\n{crit}\n")


def p5_docs(state, ctx: Ctx) -> dict:
    (ctx.workspace_dir / "DEMO.md").write_text(_scribe_demo(ctx, state.spec))
    sha = git_commit(ctx.workspace_dir, "docs: add DEMO.md")
    ctx.say("P5 docs: RUN.md/README/AGENTS (seeded) + DEMO.md")
    return {"phase": "docs", "commit_sha": sha, "demo_quality": "thin",
            "log": state.log + ["P5 docs: DEMO.md written"]}


# ── P6 cleanroom (fresh VM + fresh clone) ────────────────────────────────────
def p6_cleanroom(state, ctx: Ctx) -> dict:
    clone = ctx.staging_dir / "cleanroom"
    if clone.exists():
        shutil.rmtree(clone)
    subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q",
                    str(ctx.workspace_dir), str(clone)], check=True)
    chown_to_builder(clone)   # the clean-room VM (uid 1000) installs deps + writes caches here

    blocks = parse_run_blocks((clone / "RUN.md").read_text())
    result = {"quickstart_ok": False, "suite_ok": False, "demo_ok": False}
    sbx = ctx.broker.create(mounts=[ws_mount(clone)], name="cleanroom")
    try:
        if "install" in blocks:
            r = sbx.exec(f"cd /work && {blocks['install']}", timeout_s=1800)
            result["quickstart_ok"] = r.ok
            if not r.ok:
                ctx.say("P6 cleanroom: install FAILED\n" + r.combined[-600:])
        if "test" in blocks and result["quickstart_ok"]:
            r = sbx.exec(f"cd /work && {blocks['test']}", timeout_s=600)
            result["suite_ok"] = r.ok
        if "demo" in blocks and result["quickstart_ok"]:
            r = sbx.exec(f"cd /work && {blocks['demo']}", timeout_s=300)
            result["demo_ok"] = r.ok
    finally:
        sbx.destroy()

    ctx.say(f"P6 cleanroom: install={result['quickstart_ok']} test={result['suite_ok']} "
            f"demo={result['demo_ok']}")
    return {"phase": "cleanroom", "cleanroom": result,
            "tests_total": max(state.tests_total, len(state.staged_tests) + 1),
            "tests_passing": (len(state.staged_tests) + 1) if result["suite_ok"] else state.tests_passing,
            "log": state.log + [f"P6 cleanroom: suite_ok={result['suite_ok']}"]}


# ── P7 emit ──────────────────────────────────────────────────────────────────
def _final_status(state) -> str:
    if state.spec and not state.spec.buildable:
        return "not-buildable"
    if state.status == "failed":
        return "failed"
    core_met = any(c.core and c.status == "met" for c in (state.spec.success_criteria if state.spec else []))
    if core_met and state.cleanroom.get("suite_ok"):
        return "done"
    return "incomplete"


def p7_emit(state, ctx: Ctx) -> dict:
    from poc_foundry.artifact import (
        Budget,
        CleanroomResult,
        FinalVerdict,
        PoCBuildArtifact,
        SecurityInfo,
        SourceArtifact,
        StackItem,
        TemplateRef,
        TestsSummary,
        save,
    )

    art = ctx.run_folder.artifact if ctx.run_folder else None
    status = _final_status(state)
    core_met = bool(state.spec) and any(c.core and c.status == "met" for c in state.spec.success_criteria)
    demonstrates = "yes" if (core_met and state.cleanroom.get("suite_ok")) else (
        "partial" if core_met else "no")

    allowlist = []
    try:
        import yaml  # tolerated-absent
        y = yaml.safe_load((Path(ctx.cfg.builds_dir).parent / "config" / "pipeline.yaml").read_text())
        allowlist = list((y.get("egress_allowlist", {}) or {}).get("hosts", []))
    except Exception:  # noqa: BLE001
        allowlist = []

    pa = PoCBuildArtifact(
        id=ctx.build_id,
        generated_at=_now_iso(),
        source_artifact=SourceArtifact(id=(art.id if art else state.artifact_id),
                                       version=(art.version if art else 1)),
        driver=state.driver,
        spec_summary=(state.spec.goal if state.spec else ""),
        success_criteria=(state.spec.success_criteria if state.spec else []),
        iterations=state.iteration_records,
        tests=TestsSummary(total=state.tests_total, passing=state.tests_passing),
        cleanroom=CleanroomResult(**{k: bool(v) for k, v in state.cleanroom.items()}),
        demo_quality=state.demo_quality,
        final_verdict=FinalVerdict(demonstrates_core_value=demonstrates),
        stack=[StackItem(layer=s.get("layer", ""), choice=s.get("choice", ""),
                         pinned_version=s.get("pinned_version")) for s in ctx.template.stack],
        template=TemplateRef(name=ctx.template.name, version=ctx.template.version),
        licenses=[ctx.template.license] if ctx.template.license else [],
        security=SecurityInfo(sandbox="kata", egress_allowlist=allowlist,
                              incidents=[]),
        budget=Budget(),
        caveats=state.caveats,
        status=status,
    )

    build_dir = Path(state.build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    save(pa, build_dir)

    # copy the standalone PoC workspace (with .git history) into the build folder
    dest_ws = build_dir / "workspace"
    if dest_ws.exists():
        shutil.rmtree(dest_ws)
    shutil.copytree(ctx.workspace_dir, dest_ws)

    # egress evidence + progress + index + report
    logs = build_dir / "logs"
    logs.mkdir(exist_ok=True)
    try:
        (logs / "egress.log").write_text(ctx.broker.proxy_log())
    except Exception:  # noqa: BLE001
        pass
    (build_dir / "PROGRESS.md").write_text(
        "# Progress\n\n" + "\n".join(f"- {ln}" for ln in state.log) + "\n")
    report = _report_md(state, ctx, pa)
    (build_dir / "report.md").write_text(report)
    (build_dir / "00_INDEX.md").write_text(_index_md(pa, build_dir))

    ctx.say(f"P7 emit: status={status}; artifact + workspace written to {build_dir}")
    return {"phase": "emit", "status": status, "demonstrates_core_value": demonstrates,
            "log": state.log + [f"P7 emit: {status}"]}


def _report_md(state, ctx: Ctx, pa) -> str:
    lines = [f"# Build report — {pa.id}", "", f"**Status:** {pa.status}  ",
             f"**Demonstrates core value:** {pa.final_verdict.demonstrates_core_value}  ",
             f"**Source artifact:** {pa.source_artifact.id}", "", "## Phase trace", ""]
    lines += [f"- {ln}" for ln in ctx.report]
    lines += ["", "## Success criteria", ""]
    for c in pa.success_criteria:
        tag = " (core)" if c.core else ""
        lines.append(f"- [{c.status}] {c.text}{tag}")
    lines += ["", "## Clean-room", "",
              f"- install: {pa.cleanroom.quickstart_ok}",
              f"- suite: {pa.cleanroom.suite_ok}",
              f"- demo: {pa.cleanroom.demo_ok}"]
    if pa.caveats:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in pa.caveats]
    return "\n".join(lines) + "\n"


def _index_md(pa, build_dir: Path) -> str:
    return (f"# {pa.id}\n\n"
            f"Stage-3 PoC build from `{pa.source_artifact.id}` — **{pa.status}**.\n\n"
            f"- `v{pa.version:02d}.json` — the PoCBuildArtifact (this build's output contract)\n"
            f"- `workspace/` — the standalone, runnable PoC (see `workspace/RUN.md`)\n"
            f"- `report.md` — human-readable build report\n"
            f"- `PROGRESS.md` — phase trace\n"
            f"- `logs/egress.log` — proxy CONNECT log (egress security evidence)\n")
