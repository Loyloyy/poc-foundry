// Backend↔frontend contract. Mirrors poc_foundry/events.py (the SSE event shapes) + artifact/schema.py
// (PoCBuildArtifact) + core.list_builds() / web/server.py (the JSON API rows).

// ── SSE events (events.make_event) ───────────────────────────────────────────
export interface Criterion {
  text: string;
  status: string; // pending | met | partial | descoped | (transient spec values)
  core: boolean;
}
export interface PlanIteration {
  goal: string;
  acceptance: string[];
}
export interface IterationRecord {
  goal: string;
  status: string; // pending | green | descoped | abandoned
  attempts: number;
}
export interface Snapshot {
  phase: string;
  status: string;
  iteration: number;
  verdict: string;
  goal: string;
  criteria: Criterion[];
  iterations: PlanIteration[];
  iteration_records: IterationRecord[];
  descope_report: DescopeItem[];
  caps_hit: string[];
}

export type StartEvent = {
  type: "start";
  build_id: string;
  kind: "build" | "resume" | "refine" | "security-demo";
  source?: string;
  template?: string;
  driver?: string;
  coder?: string;
};
export type NodeEvent = { type: "node"; build_id: string; node: string; snapshot: Snapshot };
export type LogEvent = { type: "log"; build_id: string; line: string };
export type EndEvent = {
  type: "end";
  build_id: string;
  status: string;
  artifact_id?: string;
  demonstrates?: string;
  ok?: boolean; // security-demo terminal flag
};

// ── security demo (M4 S2c/S2d) — one beat per defense-in-depth control ─────────
export interface BeatResult {
  beat: string;
  passed: boolean;
  summary: string;
  detail: Record<string, any>;
}
export type BeatEvent = {
  type: "beat";
  build_id: string;
  beat: string;
  passed: boolean;
  summary: string;
  detail: Record<string, any>;
};
export type ErrorEvent = { type: "error"; build_id: string; error: string };

// ── REST domain ──────────────────────────────────────────────────────────────
export interface BuildSummary {
  id: string;
  status: string;
  source: string;
  demonstrates: string;
}

// A discoverable Stage-2 build source (fixtures + PF_ARTIFACTS_ROOT) — the picker shows `topic`.
export interface SourceInfo {
  id: string;
  topic: string;
  brief: string;
  version: number;
  path: string;
}

export interface DescopeItem {
  criterion: string;
  attempts_made?: number;
  why_failed?: string;
  finish_path?: string;
}

// A loose view of PoCBuildArtifact — only the fields the UI renders (additive-safe).
export interface PoCArtifact {
  id: string;
  status: string;
  generated_at?: string;
  spec_summary?: string;
  success_criteria: Criterion[];
  iterations: IterationRecord[];
  tests?: { total: number; passing: number; inventory_ok: boolean };
  cleanroom?: { quickstart_ok: boolean; suite_ok: boolean; demo_ok: boolean };
  final_verdict?: { demonstrates_core_value: string; gaps: string[] };
  descope_report: DescopeItem[];
  caps_hit: string[];
  caveats: string[];
  security?: { sandbox: string; incidents: string[]; degraded_critic: boolean };
  source_artifact?: { id: string; version: number };
}

export interface BuildDetail {
  id: string;
  artifact: PoCArtifact | null;
  artifact_error?: string;
  files: string[];
  langfuse_host: string;
}

export interface RunStatus {
  state: string; // idle | running | finished | error
  busy: boolean;
  build_id?: string;
  phase?: string;
  status?: string;
  error?: string;
  kind?: string;
  source?: string;
}
