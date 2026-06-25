import { useEffect, useRef, useState } from "react";
import { eventsUrl } from "./api";
import type {
  BeatEvent,
  BeatResult,
  EndEvent,
  ErrorEvent,
  LogEvent,
  NodeEvent,
  Snapshot,
  StartEvent,
} from "./types";

// The live state accumulated from the single-slot /api/events SSE stream. Because the backend is
// single-slot (one build at a time) the stream is GLOBAL — we subscribe once on mount and the server
// replays the current run's events on connect (so a reload mid-build still shows the live board).
export interface LiveState {
  connected: boolean;
  buildId: string;
  kind: string; // build | resume | refine
  source: string;
  node: string; // last node entered
  snapshot: Snapshot | null; // latest slice-board snapshot
  log: string[];
  beats: BeatResult[]; // security-demo: one per beat as they stream in
  terminal: EndEvent | null;
  error: string | null;
}

const EMPTY: LiveState = {
  connected: false,
  buildId: "",
  kind: "",
  source: "",
  node: "",
  snapshot: null,
  log: [],
  beats: [],
  terminal: null,
  error: null,
};

const LOG_CAP = 1000;

export function useEventStream(): LiveState {
  const [state, setState] = useState<LiveState>(EMPTY);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(eventsUrl);
    esRef.current = es;
    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onerror = () => setState((s) => ({ ...s, connected: false }));

    const on = (name: string, fn: (d: any) => void) =>
      es.addEventListener(name, (e) => {
        try {
          fn(JSON.parse((e as MessageEvent).data));
        } catch {
          /* ignore malformed / comment frames */
        }
      });

    // `start` opens a fresh run → reset everything but keep the connection.
    on("start", (d: StartEvent) =>
      setState({
        ...EMPTY,
        connected: true,
        buildId: d.build_id,
        kind: d.kind,
        source: d.source ?? "",
      })
    );

    on("node", (d: NodeEvent) =>
      setState((s) => ({
        ...s,
        buildId: d.build_id || s.buildId,
        node: d.node,
        snapshot: d.snapshot,
      }))
    );

    on("log", (d: LogEvent) =>
      setState((s) => ({
        ...s,
        buildId: d.build_id || s.buildId,
        log: cap([...s.log, d.line], LOG_CAP),
      }))
    );

    on("beat", (d: BeatEvent) =>
      setState((s) => ({
        ...s,
        buildId: d.build_id || s.buildId,
        beats: [
          ...s.beats,
          { beat: d.beat, passed: d.passed, summary: d.summary, detail: d.detail },
        ],
      }))
    );

    on("end", (d: EndEvent) =>
      setState((s) => ({ ...s, terminal: d, buildId: d.build_id || s.buildId }))
    );

    on("error", (d: ErrorEvent) =>
      setState((s) => ({ ...s, error: d.error, buildId: d.build_id || s.buildId }))
    );

    return () => {
      es.close();
      esRef.current = null;
    };
  }, []);

  return state;
}

function cap<T>(arr: T[], n: number): T[] {
  return arr.length > n ? arr.slice(arr.length - n) : arr;
}
