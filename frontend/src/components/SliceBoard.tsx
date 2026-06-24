import type { Criterion, IterationRecord } from "../types";

// The live slice board: success criteria flipping green as the loop advances + per-iteration records.
// Driven by the live SSE snapshot during a run, or the emitted artifact for a historical build.
interface Props {
  goal?: string;
  criteria: Criterion[];
  records: IterationRecord[];
  final?: boolean; // true once the build has finished — relabel un-met criteria as "gap", not "pending"
}

function critClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "met") return "pill ok";
  if (s === "descoped") return "pill warn";
  if (s === "partial") return "pill info";
  if (s === "gap") return "pill warn";
  return "pill muted"; // pending / transient
}

// On a FINISHED build, a still-"pending" criterion was never built — it's an honest GAP (the report's
// "Gaps vs spec"), not work-in-progress. Relabel so the board reads truthfully once the run is done.
function critLabel(status: string, final: boolean): string {
  const s = (status || "pending").toLowerCase();
  if (final && (s === "pending" || s === "")) return "gap";
  return s;
}

function recClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "green") return "pill ok";
  if (s === "descoped") return "pill warn";
  if (s === "abandoned") return "pill bad";
  return "pill muted";
}

export default function SliceBoard(p: Props) {
  const met = p.criteria.filter((c) => c.status.toLowerCase() === "met").length;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Slice board</h2>
        {p.criteria.length > 0 && (
          <span className="muted small">
            {met}/{p.criteria.length} criteria met
          </span>
        )}
      </div>
      {p.goal && <p className="goal">{p.goal}</p>}

      {p.criteria.length === 0 ? (
        <p className="muted small">Waiting for the spec…</p>
      ) : (
        <ul className="board">
          {p.criteria.map((c, i) => {
            const label = critLabel(c.status, !!p.final);
            return (
              <li key={i} className="board-row">
                <span className={critClass(label)}>{label}</span>
                {c.core && <span className="pill core" title="core criterion (DONE floor)">core</span>}
                <span className="board-text">{c.text}</span>
              </li>
            );
          })}
        </ul>
      )}

      {p.records.length > 0 && (
        <>
          <h3 className="sub">Iterations</h3>
          <ul className="board">
            {p.records.map((r, i) => (
              <li key={i} className="board-row">
                <span className={recClass(r.status)}>{r.status}</span>
                <span className="board-text">{r.goal}</span>
                {r.attempts > 0 && <span className="muted small">· {r.attempts} attempt(s)</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
