import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Render scrubbed build markdown (report.md, PROGRESS.md, lessons.md, research.md…) inline.
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
