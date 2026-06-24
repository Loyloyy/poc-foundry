import { useEffect, useState } from "react";
import { api } from "../api";
import Markdown from "./Markdown";

// Inline docs: pick any exposed build file (report.md / PROGRESS.md / iterations/*/lessons.md / …)
// and render it. Markdown files render formatted; .json/.log/.patch show as preformatted text.
interface Props {
  buildId: string;
  files: string[];
}

function rank(f: string): number {
  if (f === "report.md") return 0;
  if (f === "PROGRESS.md") return 1;
  if (f.endsWith(".md")) return 2;
  if (f.endsWith(".patch")) return 3;
  if (f.endsWith(".log")) return 4;
  return 5;
}

export default function DocsPanel({ buildId, files }: Props) {
  const ordered = [...files].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const [sel, setSel] = useState<string>(ordered[0] ?? "");
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    if (ordered.length && !ordered.includes(sel)) setSel(ordered[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildId, files.join("|")]);

  useEffect(() => {
    if (!sel) return;
    setErr("");
    api
      .fileText(buildId, sel)
      .then((r) => {
        setText(r.text);
        setTruncated(r.truncated);
      })
      .catch((e) => setErr(String(e)));
  }, [buildId, sel]);

  if (files.length === 0) return <p className="muted small">No documents emitted yet.</p>;

  const isMd = sel.endsWith(".md");
  return (
    <section className="card">
      <div className="card-head">
        <h2>Docs</h2>
        <select value={sel} onChange={(e) => setSel(e.target.value)}>
          {ordered.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>
      {err && <p className="error small">{err}</p>}
      {truncated && <p className="muted small">(showing the last 512 KB)</p>}
      {isMd ? <Markdown text={text} /> : <pre className="filebox">{text}</pre>}
    </section>
  );
}
