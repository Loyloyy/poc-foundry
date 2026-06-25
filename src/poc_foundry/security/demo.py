"""The security demo (M4 S2c, design §5.2 / §5.12) — three live red-team BEATS run against a REAL
provisioned broker, each proving one defense-in-depth control end-to-end:

  1. **canary / Finding-0** — the orchestrator's real secrets (Langfuse / a GitHub token / model
     paths) AND a planted ``canary`` never reach the throwaway VM (dump the VM env, scan it);
  2. **egress containment** — a VM's attempt to reach a NON-allowlisted host is DENIED at the sole
     exit (the squid proxy logs ``TCP_DENIED``);
  3. **broker rejection** — an attempt to ``create*`` off-allowlist is rejected (rule #8) AND recorded
     in the daemon-owned audit log.

``run_demo`` orchestrates the live beats (provisioned broker → create VM → ``exec`` → analyze →
``destroy``); the PURE analyzers (``parse_env`` / ``analyze_*``) are stdlib-only so the PASS/FAIL logic
is fakes-testable WITHOUT Docker — the live VM run is the server demo. All shared output is redacted of
the ``canary`` (defense-in-depth on top of ``scrub``; the analyzers already report secrets by
PLACEHOLDER, never raw value). Headless (rule #5): ``core.security_demo`` is the stable entrypoint;
CLI + web call it.
"""
from __future__ import annotations

import re

# The egress probe: reach a NON-allowlisted public host THROUGH the VM's only exit (the proxy, injected
# as HTTPS_PROXY). squid denies the CONNECT → curl fails (exit 56, http_code 000, and curl itself reports
# the proxy's 403) AND the proxy logs TCP_DENIED. ``-sS`` surfaces curl's error so the proxy 403 is
# captured in-band as denial evidence even if the proxy's file→stdout log lags the read. ``exec(cmd)`` is
# the only broker input that may carry this (rule #8).
_EGRESS_PROBE = (
    "curl -sS -m 8 -o /dev/null -w 'PF_HTTP=%{http_code}\\n' https://example.com 2>&1; echo PF_EXIT=$?"
)


# ── pure analyzers (stdlib only → fakes-testable) ─────────────────────────────
def parse_env(text: str) -> dict:
    """Parse a sandbox ``exec('env')`` dump into ``{VAR: value}``. Conservative: only lines that look
    like ``NAME=value`` with a plausible env-var name become keys (so a multi-line value never gets
    mis-read as a new variable)."""
    env: dict = {}
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if "=" not in line or line[:1] in (" ", "\t"):
            continue
        k, _, v = line.partition("=")
        if k and k.replace("_", "").isalnum() and not k[0].isdigit():
            env[k] = v
    return env


def _beat(name: str, passed: bool, summary: str, detail: dict | None = None) -> dict:
    return {"beat": name, "passed": bool(passed), "summary": summary, "detail": detail or {}}


def analyze_canary(vm_env: dict, secrets, canary: str = "") -> dict:
    """Beat 1: scan the VM env against the run's known secrets (+ canary). PASS = nothing leaked."""
    from poc_foundry.security.findings import scan_sandbox_env

    scan = scan_sandbox_env(vm_env, secrets)
    passed = scan.ok
    summary = ("no orchestrator secret reached the sandbox VM — Finding-0 holds" if passed
               else "LEAK: " + ", ".join(scan.leaked) + " reached the VM env (vars: "
                    + ", ".join(scan.leaked_keys) + ")")
    return _beat("canary / Finding-0", passed, summary,
                 {"leaked": scan.leaked, "leaked_keys": scan.leaked_keys, "scanned_keys": scan.scanned_keys,
                  "secret_count": scan.secret_count, "canary_absent": "<canary>" not in scan.leaked})


def analyze_egress(curl_output: str, denied_in_log: bool) -> dict:
    """Beat 2: PASS = the egress attempt was BLOCKED *and* there is affirmative proxy-denial evidence —
    either ``TCP_DENIED`` in the proxy log OR curl's own report of a 403 from the proxy (both mean the
    sole exit denied the CONNECT). Parses the probe's in-band ``PF_HTTP=``/``PF_EXIT=`` markers."""
    out = curl_output or ""
    m = re.search(r"PF_HTTP=(\d+)", out)
    code = m.group(1) if m else ""
    em = re.search(r"PF_EXIT=(\d+)", out)
    exit_code = int(em.group(1)) if em else (0 if code.startswith("2") else 1)
    curl_blocked = exit_code != 0 or not code.startswith("2")
    proxy_denied_via_curl = ("CONNECT tunnel failed" in out or "Received HTTP code 403" in out
                             or ("403" in out and "proxy" in out.lower()))
    denied = bool(denied_in_log or proxy_denied_via_curl)
    passed = bool(denied and curl_blocked)
    if passed:
        ev = "TCP_DENIED in the proxy log" if denied_in_log else "curl got a 403 from the proxy"
        summary = f"egress to a non-allowlisted host was DENIED at the proxy ({ev})"
    elif curl_blocked:
        summary = ("egress was blocked but no proxy-denial evidence was captured "
                   "(the proxy log may have lagged — re-run if this persists)")
    else:
        summary = f"egress containment FAILED — the VM reached a non-allowlisted host (HTTP {code or '?'})"
    return _beat("egress containment", passed, summary,
                 {"tcp_denied_in_proxy_log": bool(denied_in_log), "proxy_denied_via_curl": proxy_denied_via_curl,
                  "curl_blocked": curl_blocked, "http_code": code, "curl_exit": exit_code})


def analyze_rejection(rejected: bool, reason: str, audit_entries) -> dict:
    """Beat 3: PASS = the off-allowlist ``create`` raised (rule #8) AND was recorded in the audit."""
    audited = any(e.get("event") == "rejected" and e.get("method") == "create"
                  for e in (audit_entries or []))
    passed = bool(rejected and audited)
    summary = ("an off-allowlist create* was rejected (rule #8) and recorded in the broker audit"
               if passed else "broker rejection FAILED — an off-allowlist create* was not blocked+audited")
    return _beat("broker rejection", passed, summary,
                 {"rejected": bool(rejected), "audited": audited, "reason": reason})


# ── canary redaction (defense-in-depth on shared output) ──────────────────────
def _redact(obj, canary: str):
    if not canary or canary.startswith("<"):
        return obj
    if isinstance(obj, str):
        return obj.replace(canary, "<canary>")
    if isinstance(obj, list):
        return [_redact(x, canary) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact(v, canary) for k, v in obj.items()}
    return obj


def _redact_beat(beat: dict, canary: str) -> dict:
    return {"beat": beat["beat"], "passed": beat["passed"],
            "summary": _redact(beat["summary"], canary), "detail": _redact(beat["detail"], canary)}


# ── live orchestration (the server demo; broker is REAL + already provisioned) ─
def run_demo(broker, cfg=None, *, canary: str = "", emit=None, build_id: str = "", secrets=None,
             proxy_poll_tries: int = 5, proxy_poll_delay: float = 1.0) -> dict:
    """Run the 3 beats against a PROVISIONED ``broker``. Returns ``{"build_id", "ok", "beats": [...]}``.
    ``emit`` is an optional event sink (the web tab streams a ``beat`` event each); ``secrets`` is the
    ``(value, placeholder)`` list to scan for — defaults to ``scrub.collect_secrets()`` + the canary
    (injectable so the analysis is fakes-testable without the run's real env). ``proxy_poll_*`` give the
    squid file→stdout log a moment to flush before deciding egress wasn't denied."""
    import time

    from poc_foundry import events as _ev
    from poc_foundry.security import findings
    from poc_foundry.sandbox.broker import BrokerInvariantError

    if secrets is None:
        from poc_foundry import scrub
        secrets = list(scrub.collect_secrets())
        if canary and not canary.startswith("<"):
            secrets.append((canary, "<canary>"))
    # The sacrificial inference token IS intended in the VM (sacrificial = buys inference, nothing else;
    # DECISIONS #31). Exclude its value so Finding-0 checks only the secrets that MUST be absent — a real
    # leak (Langfuse/GitHub/path) still fails, and ``leaked_keys`` names the offending VM var.
    sacrificial = (getattr(broker, "vllm_key", None) or getattr(broker, "_vllm_key", None) or "").strip()
    if sacrificial and not sacrificial.startswith("<"):
        secrets = [(v, p) for (v, p) in secrets if v != sacrificial]

    beats: list[dict] = []

    def _push(beat: dict) -> None:
        beat = _redact_beat(beat, canary)
        beats.append(beat)
        _ev.emit(emit, _ev.make_event("beat", build_id, beat=beat["beat"], passed=beat["passed"],
                                      summary=beat["summary"], detail=beat["detail"]))

    # Beats 1 + 2 share one fresh VM (env dump, then the egress probe); reaped before reading the log.
    sbx = broker.create(mounts=[], name="secdemo")
    try:
        env_out = sbx.exec("env", timeout_s=60)
        _push(analyze_canary(parse_env(env_out.combined), secrets, canary))
        curl_out = sbx.exec(_EGRESS_PROBE, timeout_s=30)
    finally:
        sbx.destroy()
    denied_log = False
    for i in range(max(1, proxy_poll_tries)):
        if findings.egress_denied(broker.proxy_log(tail=200)):
            denied_log = True
            break
        if i + 1 < proxy_poll_tries:
            time.sleep(proxy_poll_delay)
    _push(analyze_egress(curl_out.combined, denied_log))

    # Beat 3: an off-allowlist create* must be rejected (rule #8) AND land in the audit.
    rejected, reason = False, ""
    try:
        broker.create(mounts=[], name="evilcreate", image="attacker/evil:latest")
    except BrokerInvariantError as e:
        rejected, reason = True, str(e)
    _push(analyze_rejection(rejected, reason, broker.audit()))

    return {"build_id": build_id, "ok": all(b["passed"] for b in beats), "beats": beats}
