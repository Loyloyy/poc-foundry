// Thin fetch helpers over the FastAPI surface (all under /api). The web layer holds no pipeline logic
// (rule #5) and neither does this — it just calls core via the server.
import type { BuildDetail, BuildSummary, RunStatus, SourceInfo } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface StartBody {
  source: string;
  brief?: string;
  driver?: string;
  template?: string | null;
  runtime?: string | null;
}

export const api = {
  status: () => fetch("/api/status").then((r) => json<RunStatus>(r)),

  listBuilds: () => fetch("/api/builds").then((r) => json<BuildSummary[]>(r)),

  listSources: () => fetch("/api/sources").then((r) => json<SourceInfo[]>(r)),

  buildDetail: (id: string) =>
    fetch(`/api/builds/${encodeURIComponent(id)}`).then((r) => json<BuildDetail>(r)),

  fileText: (id: string, path: string) =>
    fetch(`/api/builds/${encodeURIComponent(id)}/file?path=${encodeURIComponent(path)}`).then((r) =>
      json<{ path: string; truncated: boolean; text: string }>(r)
    ),

  start: (body: StartBody) =>
    fetch("/api/builds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<RunStatus>(r)),

  resume: (id: string) =>
    fetch(`/api/builds/${encodeURIComponent(id)}/resume`, { method: "POST" }).then((r) =>
      json<RunStatus>(r)
    ),

  // Re-attack a finished build's descoped backlog on a stronger coder (M4 S1). `coder` is a role name
  // whose .env triple points at the frontier endpoint (a per-call rebind; empty = unchanged coder).
  refine: (id: string, coder?: string) =>
    fetch(`/api/builds/${encodeURIComponent(id)}/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coder: coder || null }),
    }).then((r) => json<RunStatus>(r)),

  // No-id stop: the single-slot RunManager stops whatever is running (the Stop button).
  stop: () => fetch("/api/stop", { method: "POST" }).then((r) => json<{ stopped: boolean }>(r)),

  // Run the 3 live security beats (M4 S2c) in the single slot; the SPA renders streamed `beat` events.
  // `canary` is a planted secret proven absent from the VM (empty = the server's PF_DEMO_CANARY).
  securityDemo: (canary?: string) =>
    fetch("/api/security-demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canary: canary || null }),
    }).then((r) => json<RunStatus>(r)),
};

export const eventsUrl = "/api/events";
