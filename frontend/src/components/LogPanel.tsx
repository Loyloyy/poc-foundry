import { useEffect, useRef } from "react";

// The live build/test log — the Ctx.say stream. Auto-scrolls to the tail while a run is active.
interface Props {
  lines: string[];
  live: boolean;
}

export default function LogPanel({ lines, live }: Props) {
  const boxRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (live && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines.length, live]);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Build / test log</h2>
        {live && <span className="pill live">streaming</span>}
      </div>
      {lines.length === 0 ? (
        <p className="muted small">No log lines yet.</p>
      ) : (
        <pre className="logbox" ref={boxRef}>
          {lines.map((l, i) => (
            <div key={i} className="logline">
              {l}
            </div>
          ))}
        </pre>
      )}
    </section>
  );
}
