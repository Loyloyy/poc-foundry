import { useState } from "react";
import { api } from "../api";
import type { LiveState } from "../useEventStream";
import type { RunStatus } from "../types";

// The Security-Demo tab (M4 S2d). POSTs to /api/security-demo (→ RunManager single slot) and renders
// the 3 live beats as they stream over the global SSE: canary/Finding-0, egress containment, broker
// rejection. The web layer holds no logic — `core.security_demo` produces every result + event.
const BEAT_BLURB: Record<string, string> = {
  "canary / Finding-0": "Dump the throwaway VM's env and scan it: the orchestrator's real secrets (and a planted canary) must be ABSENT — the sandbox only ever gets a proxy address + a sacrificial token.",
  "egress containment": "From inside the VM, try to reach a non-allowlisted host. The sole exit (the squid proxy) must DENY it (TCP_DENIED) — the VM has no other route out.",
  "broker rejection": "Ask the broker to launch an off-allowlist image. The rule-#8 invariant must REJECT it and the daemon-owned audit log must record the attempt.",
};

export default function SecurityDemo({ live, status }: { live: LiveState; status: RunStatus }) {
  const [canary, setCanary] = useState("");
  const [error, setError] = useState("");

  const isDemo = live.kind === "security-demo";
  const running = status.busy && status.kind === "security-demo";
  const beats = isDemo ? live.beats : [];
  const done = isDemo && !!live.terminal;
  const allPass = beats.length > 0 && beats.every((b) => b.passed);

  const onRun = () => {
    setError("");
    api.securityDemo(canary.trim()).catch((e) => setError(String(e?.message || e)));
  };

  return (
    <div className="secdemo">
      <header className="run-head">
        <div>
          <h2 className="run-id">Security demo</h2>
          <div className="run-sub muted small">
            Three live red-team beats against a freshly provisioned sandbox — defense-in-depth proven,
            not asserted. <span>· SSE {live.connected ? <span className="ok">●</span> : <span className="bad">●</span>}</span>
          </div>
        </div>
        <div className="run-controls">
          <input
            className="coder-input"
            placeholder="canary (optional)"
            value={canary}
            onChange={(e) => setCanary(e.target.value)}
            title="A planted stand-in secret the demo proves the VM never sees. Leave blank to use the server's PF_DEMO_CANARY."
            disabled={running}
          />
          <button onClick={onRun} disabled={status.busy}>
            {running ? "Running…" : "▶ Run security demo"}
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {live.error && isDemo && <p className="error">demo error: {live.error}</p>}

      {!isDemo && !running && (
        <p className="muted">
          Nothing has run yet this session. Click <b>Run security demo</b> to provision a sandbox and
          execute the three beats live over this tunnel. (Needs the broker daemon up.)
        </p>
      )}

      <section className="beats">
        {["canary / Finding-0", "egress containment", "broker rejection"].map((name) => {
          const b = beats.find((x) => x.beat === name);
          const state = b ? (b.passed ? "pass" : "fail") : running || isDemo ? "pending" : "idle";
          return (
            <div key={name} className={`beat-card ${state}`}>
              <div className="beat-head">
                <span className={`pill ${b ? (b.passed ? "ok" : "bad") : "muted"}`}>
                  {b ? (b.passed ? "PASS" : "FAIL") : state === "pending" ? "…" : "—"}
                </span>
                <h3>{name}</h3>
              </div>
              <p className="muted small">{BEAT_BLURB[name]}</p>
              {b && <p className="beat-summary">{b.summary}</p>}
              {b && b.detail && Object.keys(b.detail).length > 0 && (
                <pre className="beat-detail">{JSON.stringify(b.detail, null, 2)}</pre>
              )}
            </div>
          );
        })}
      </section>

      {done && (
        <p className={allPass ? "ok" : "bad"}>
          {allPass
            ? "✓ All three controls held — no secret reached the sandbox, egress was denied, the bad create was rejected + audited."
            : "✗ One or more beats did not pass — see the detail above."}
        </p>
      )}
    </div>
  );
}
