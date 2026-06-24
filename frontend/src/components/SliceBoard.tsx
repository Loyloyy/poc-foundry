import type { Criterion, IterationRecord } from "../types";

// The live slice board: success criteria flipping green as the loop advances + per-iteration records.
// Driven by the live SSE snapshot during a run, or the emitted artifact for a historical build.
interface Props {
  goal?: string;
  criteria: Criterion[];
  records: IterationRecord[];
}

function critClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "met") return "pill ok";
  if (s === "descoped") return "pill warn";
  if (s === "partial") return "pill info";
  return "pill muted"; // pending / transient
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
          {p.criteria.map((c, i) => (
            <li key={i} className="board-row">
              <span className={critClass(c.status)}>{c.status || "pending"}</span>
              {c.core && <span className="pill core" title="core criterion (DONE floor)">core</span>}
              <span className="board-text">{c.text}</span>
            </li>
          ))}
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
