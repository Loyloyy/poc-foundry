import type { DescopeItem } from "../types";

// The honest-reporting view: what was cut, why, how many attempts, and the concrete finish-path.
// Flags the abandoned.patch pointer when the build left one (apply + finish by hand in OpenCode).
interface Props {
  items: DescopeItem[];
  hasAbandonedPatch: boolean;
  capsHit: string[];
}

export default function DescopePanel({ items, hasAbandonedPatch, capsHit }: Props) {
  if (items.length === 0 && !hasAbandonedPatch && capsHit.length === 0) {
    return null;
  }
  return (
    <section className="card">
      <div className="card-head">
        <h2>Descope report</h2>
        {capsHit.length > 0 && <span className="pill warn">caps: {capsHit.join(", ")}</span>}
      </div>

      {items.length === 0 ? (
        <p className="muted small">Nothing descoped.</p>
      ) : (
        <ul className="descope">
          {items.map((d, i) => (
            <li key={i}>
              <div className="descope-crit">{d.criterion}</div>
              <div className="muted small">
                {d.attempts_made ? `${d.attempts_made} attempt(s) · ` : ""}
                {d.why_failed}
              </div>
              {d.finish_path && (
                <div className="finish">
                  <span className="pill info">finish</span> {d.finish_path}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {hasAbandonedPatch && (
        <p className="small">
          <span className="pill warn">abandoned.patch</span> in-flight work was saved — apply it and
          finish by hand in OpenCode, or resume with a higher cap.
        </p>
      )}
    </section>
  );
}
