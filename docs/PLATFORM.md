# PLATFORM.md — poc-foundry as a worked example of the wiki's patterns

> A teaching artifact. `poc-foundry` (Stage 3 of the GenAI-scout system) turns ONE grounded research
> artifact into a verified, runnable, documented PoC. It is also a deliberate, end-to-end implementation
> of the engineering patterns the wiki teaches. This document walks each pattern and points at the real
> module that embodies it — so the codebase doubles as the worked example for a workshop.
>
> Nothing here is host- or model-specific (rule #1): models are role-bound via `.env`, endpoints and ids
> live only in gitignored config. The patterns are what travel.

---

## The shape of the system (so the citations make sense)

A build is a **deterministic LangGraph spine** (`graph.py`) over eight phases (`phases/pipeline.py`,
`P0…P7`): ingest → spec → plan → scaffold → iterate → clean-room → docs → emit. The *outside* is
deterministic and checkpointed; only the *inside* of an iteration is agentic (the coder's bounded loop).
Every iteration runs inside a fresh, network-contained VM sandbox provisioned by a broker
(`sandbox/broker.py`) that is the sole holder of the container runtime socket. The stable contract the
whole product is built on is one function — `core.build_poc(...)` — with the CLI (`cli.py`) and web UI
(`web/`) holding no pipeline logic.

That structure is not incidental; it is the patterns made concrete.

---

## 1. Vertical Slices — ship a thin end-to-end path, then widen it

**Pattern.** Don't build a layer at a time; build the narrowest path that goes all the way through, prove
it, then add the next slice. Each slice is independently verifiable.

**Where it lives.** The milestone history *is* this pattern: M1 was a **walking skeleton** — one fixture
→ `builds/<id>/` with `status=done`, scaffold→coder→clean-room all green, on real infra, before any gate
existed. Only then did M2a add the integrity walls (`phases/integrity.py`), M2a/S4 move the broker
out-of-process (`sandbox/daemon.py` + `sandbox/client.py` + `sandbox/rpc.py`), M2b add hygiene + budgets,
M3 add the web UI as a pure seam, and M4 add `refine` and the security demo. Each was a vertical slice
proven on the server before the next began (`ROADMAP.md` records the acceptance check per milestone;
`DECISIONS.md` records why).

**The discipline that makes it real.** `core.refine_build` (M4 S1) is a textbook example: rather than a
new pipeline, it is a *backlog-only* re-entry of the SAME phases through a second graph
(`graph.build_refine_graph`) — the slice reused every node and added only wiring (rule #5). Widening
without rewriting.

---

## 2. Validation Contract — define "done" as a checkable artifact, not a vibe

**Pattern.** Success must be expressed as something a machine can check. The spec is a contract; the build
is judged against it, field by field.

**Where it lives.** `phases/pipeline.py:p1_spec` turns the input artifact into a `Spec` whose
`success_criteria[]` each carry `core` and `status` (`state.py`). One criterion is the **core** criterion
that gates `done`. The output is itself a typed contract — `PoCBuildArtifact` (`artifact/schema.py`) — a
flat, additive-only record of what was met, descoped, or abandoned, with a `final_verdict`
(`demonstrates_core_value`) and an honest `descope_report[]`. The input side is contract-checked too: the
vendored Stage-2 schema has semantic invariants enforced by `run_contract_checks.py` (citation
resolution, reproducibility enum, ISO timestamps).

**Why it matters here.** Because "done" is a checkable artifact, the system can *refuse* it. A build that
games its way to green is reported `incomplete`, never `done` — see Verifiers Rule.

---

## 3. Verifiers Rule — trust the verifier, not the generator

**Pattern.** An LLM's claim that it succeeded is worthless; an independent check is everything. Put the
authority in verifiers and make them adversarially robust.

**Where it lives.** This is the spine of the whole product ("**Verification is the value.**"). Concretely:

- **Red-first, author-separated tests.** The tester authors a test that must FAIL against the scaffold
  stub before the coder may touch the code (`phases/pipeline.py:p4_iterate`). A test that's already green
  pre-coder is *tester inadequacy*, not a pass (`red_first_ok=False` → the criterion is not met).
- **The inventory ledger.** The tests collected pre-coder (`pytest --collect-only`) must all appear,
  passed, in the authoritative post-coder junit run (`phases/integrity.py:inventory_ok`). Deleting or
  silently skipping a test is caught by name.
- **The diff scanner** runs *inside* each coder attempt's `verify()` callable — a tampering edit
  (touching tests, hard-exit gaming) fails the attempt and forces a strategy change
  (`phases/integrity.py:scan_diff`).
- **The clean-room gate.** After the loop, the MET criteria's tests are re-run in a *fresh clone* with a
  from-scratch install (`p5`/`p6`); a red clean-room can never be `done`.
- **The critic.** A separate model role reviews a green iteration for *adequacy* (is the passing test
  trustworthy evidence, or gameable?) and routes failures up a verdict ladder (fix → replan → descope)
  (`p_critic`, `_after_critic` in `graph.py`). When the critic shares the coder's model family it is
  honestly **degraded** — its adequacy verdict becomes advisory, recorded as a caveat, never silently
  trusted (`models.same_family`; `security.degraded_critic`).

`_final_status` returns `done` only if *core met ∧ clean-room green ∧ trustworthy*. The verifier, not the
generator, has the final word.

---

## 4. Harness Engineering — the scaffold around the model is the product

**Pattern.** Most of the value is in the deterministic harness — the loop, the sandbox, the budgets, the
retries — not in the prompt. Engineer the harness like a system.

**Where it lives.**

- **Deterministic outside, agentic inside.** `graph.py` is a real state machine with cycles, a SQLite
  checkpointer keyed by `build_id`, and termination *guaranteed* by capped counters
  (`fix_count`/`respec_count`/`replan_count`) plus a recursion limit — not by hoping the model stops.
- **The bounded coder loop.** `coder.py:BespokeCoder` is a code-owned loop (prompt → apply edit → verify
  → feed the failure back), with **error-signature tracking** that forces a strategy change on a repeated
  failure (escalation ladder) — it is decoupled from the sandbox via an injected `verify()` callable, so
  it is unit-testable without Docker.
- **The sandbox harness.** `sandbox/broker.py` provisions a per-build internal network + a default-deny
  allowlisting egress proxy + a uv cache, then spins **fresh VMs per iteration**. The
  out-of-process split (`sandbox/daemon.py`/`client.py`/`rpc.py`) puts the runtime socket behind a Unix
  socket so the orchestrator never holds it.
- **Budgets + cooperative stop.** Spend is metered against a budget; a degenerate loop hits a cap and
  salvages honestly (abandoned patch + descope report). A cooperative-stop sentinel (`control.py`)
  checkpoints and exits at the next node boundary, resumable.

The model is one component in this harness; swapping it (`models.build_chat_model(role)`) changes nothing
structural.

---

## 5. Doom-Loop Avoidance — bound the retries and fail honestly

**Pattern.** Agentic systems love to spin: retry the same broken approach forever, or declare victory to
escape. Bound the loop and make the failure legible.

**Where it lives.**

- **Bounded everything.** Fix budget `K` (lower in degraded-critic mode), respec/replan caps, a recursion
  limit, and a run-wall budget. When a criterion can't be met within budget it is **descoped**, not
  retried into the ground (`p_critic` → `descope`), and the reason is written to `descope_report[]` with a
  `finish_path`.
- **Strategy change on repeats.** A repeated error signature in `coder.py` forces a *different* approach
  rather than another identical attempt.
- **Salvage, not collapse.** On a non-green iteration `p4_iterate` rolls the workspace back to the last
  green commit (`git reset --hard`) so a failed attempt never pollutes the emitted code (DECISIONS #17);
  the clean-room gate then runs only the published, green tests.
- **Honest verdicts.** `NOT_BUILDABLE` short-circuits a hopeless build to a real emitted artifact; a
  capped build reports `incomplete` with gaps. The system would rather say "I couldn't" than fake "done".

---

## 6. Skills / ACE — accumulate experience, inject it, reflect on it

**Pattern.** A system should get better with use: capture what worked, inject relevant guidance into the
next run's context, and reflect on outcomes to grow the playbook (the Agentic Context Engineering loop).

**Where it lives.** `playbooks.py` is the experience loop: a two-tier playbook is **injected** into the
relevant prompt (with the format suffix kept LAST so it can't be diluted), and a Tier-1 **reflection**
after a build distils a lesson into `lessons.md` for the next run. Research-on-gaps (`research/agent.py`
+ `research/tools.py`) is the just-in-time variant: when the build hits open questions or a stuck loop, a
bespoke search→fetch→synthesize agent produces a cited `research.md` the coder then consumes. Both are
context engineering: the right knowledge, assembled at the moment it's needed, grounded and cited.

---

## 7. Defense-in-Depth & honest security language (a pattern the wiki insists on)

**Pattern.** Never claim a control you don't enforce; layer controls; prove them with evidence, not
adjectives.

**Where it lives.** Three layers, each demonstrable (the `demo-security` CLI / Security-demo web tab run
them live as three **beats** — `security/demo.py`):

1. **Finding-0** — the throwaway VM holds NONE of the orchestrator's real secrets. The broker constructs
   the VM env explicitly (proxy address + a sacrificial token + sibling IPs) and never passes its own
   environment through; `security/findings.py:scan_sandbox_env` turns that into a *checked* claim. The
   key-proxy (`security/keyproxy.py`) is the general mechanism for key-requiring providers — the real key
   stays orchestrator-side, the VM presents only a per-build sacrificial token — demonstrated honestly
   with a planted **canary** (the on-prem deployment here is keyless, and we say so rather than claim a
   key it doesn't have).
2. **Egress containment** — the VM's only exit is a default-deny allowlisting proxy; an attempt to reach a
   non-allowlisted host is logged `TCP_DENIED` (`findings.egress_denied`).
3. **The broker invariant (rule #8)** — `create*()` parameters are harness-fixed and allowlisted; only
   `exec(cmd)` ever carries model-derived content. A rejected create is recorded append-only to a
   daemon-owned audit log (`sandbox/audit.py`), durable and readable independently of a possibly-
   compromised orchestrator.

All isolation language is defense-in-depth; the code never says "cannot be escaped" (rule #9). The
emitted bundle is also scrubbed of endpoints/ids/paths (`scrub.py`) so an artifact shared with a human
leaks nothing.

---

## 8. Observability & Evals — measure the harness, not just the output

**Pattern.** You can't improve what you can't see. Trace the run; score the early phases cheaply before
spending a sandbox.

**Where it lives.** `tracing.py` emits structured spans (build / node / coder.exec) to Langfuse, including
the manual spans the framework can't see (VERIFY/exec round-trips). `evals.py` (the `eval` CLI) scores
spec + plan quality against fixtures *without* a sandbox — a cheap gate before the expensive path. The web
UI's live slice board is the same data, streamed through a single event seam (`events.py`) so presentation
stays free of pipeline logic.

---

## How to use this in a workshop

Pick a pattern, open the cited module, and trace one build through it:

- Want **Verifiers Rule** made vivid? Run a build, then read `phases/integrity.py` and watch a planted
  gaming attempt get caught (the fakes in `tests/` plant exactly these).
- Want **Harness Engineering**? Read `graph.py` top-to-bottom: it's a state machine you can hold in your
  head, with the model quarantined inside one node.
- Want **defense-in-depth proven**? Run `python -m poc_foundry.cli demo-security` (or the web Security tab)
  and read the three beats' evidence.

The throughline: the model is a powerful but untrusted component; the engineering — the contract, the
verifiers, the bounded harness, the sandbox, the honest reporting — is what turns it into a product you can
trust. **Verification is the value.**
