import { useState } from "react";
import { api, ApiError, type StartBody } from "../api";
import type { BuildSummary } from "../types";

// Left rail: a New-build form (single-slot — Start is disabled while a build runs; a 409 surfaces as
// an inline message) + the history list of emitted builds (click to open).
interface Props {
  builds: BuildSummary[];
  busy: boolean;
  activeId: string;
  selectedId: string;
  onSelect: (id: string) => void;
  onStarted: () => void;
}

function statusPill(s: string): string {
  if (s === "done") return "pill ok";
  if (s === "failed") return "pill bad";
  if (s === "stopped" || s === "incomplete") return "pill warn";
  if (s === "not-buildable") return "pill info";
  return "pill muted";
}

export default function Sidebar(p: Props) {
  const [source, setSource] = useState("/app/tests/fixtures/sample_artifact");
  const [brief, setBrief] = useState("");
  const [driver, setDriver] = useState("tech-scout");
  const [template, setTemplate] = useState("");
  const [err, setErr] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const start = async () => {
    setErr("");
    setSubmitting(true);
    try {
      const body: StartBody = { source: source.trim(), brief, driver };
      if (template.trim()) body.template = template.trim();
      await api.start(body);
      p.onStarted();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? "A build is already running (single-slot)." : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <aside className="sidebar">
      <h1 className="brand">poc-foundry</h1>

      <div className="sb-form">
        <label>
          Source <span className="muted small">(Stage-2 run folder / artifact id)</span>
          <input value={source} onChange={(e) => setSource(e.target.value)} spellCheck={false} />
        </label>
        <label>
          Brief <span className="muted small">(optional)</span>
          <input value={brief} onChange={(e) => setBrief(e.target.value)} />
        </label>
        <div className="sb-row">
          <label className="grow">
            Driver
            <select value={driver} onChange={(e) => setDriver(e.target.value)}>
              <option value="tech-scout">tech-scout</option>
              <option value="customer">customer</option>
              <option value="workshop">workshop</option>
            </select>
          </label>
          <label className="grow">
            Template
            <input value={template} onChange={(e) => setTemplate(e.target.value)} placeholder="default" />
          </label>
        </div>
        <button className="sb-new" disabled={p.busy || submitting || !source.trim()} onClick={start}>
          {p.busy ? "Build running…" : "＋ Build PoC"}
        </button>
        {err && <p className="error small">{err}</p>}
      </div>

      <div className="sb-runs">
        <div className="sb-section">History</div>
        {p.builds.length === 0 && <p className="muted small">No builds yet.</p>}
        <ul>
          {p.builds.map((b) => (
            <li key={b.id} className={b.id === p.selectedId ? "active" : ""}>
              <button className="sb-run" onClick={() => p.onSelect(b.id)} title={b.id}>
                <span className="sb-run-topic">{b.id.replace(/^poc-/, "")}</span>
                <span className="sb-run-meta">
                  <span className={statusPill(b.status)}>{b.status}</span>
                  {b.id === p.activeId && <span className="pill live">live</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
