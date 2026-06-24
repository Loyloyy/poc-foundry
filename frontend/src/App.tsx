import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useEventStream } from "./useEventStream";
import type { BuildDetail, BuildSummary, RunStatus } from "./types";
import Sidebar from "./components/Sidebar";
import SliceBoard from "./components/SliceBoard";
import LogPanel from "./components/LogPanel";
import DocsPanel from "./components/DocsPanel";
import DescopePanel from "./components/DescopePanel";

const POLL_MS = 2500;
const RESUMABLE = new Set(["stopped", "incomplete"]);

export default function App() {
  const live = useEventStream();
  const [builds, setBuilds] = useState<BuildSummary[]>([]);
  const [status, setStatus] = useState<RunStatus>({ state: "idle", busy: false });
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<BuildDetail | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [stopping, setStopping] = useState(false);
  const [coderRole, setCoderRole] = useState("");
  const prevActive = useRef("");

  const activeId = status.busy ? status.build_id || live.buildId : "";

  // poll status + history
  useEffect(() => {
    let alive = true;
    const tick = () => {
      api.status().then((s) => alive && setStatus(s)).catch(() => {});
      api.listBuilds().then((b) => alive && setBuilds(b)).catch(() => {});
    };
    tick();
    const h = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [refreshKey]);

  // auto-follow a newly started run (but let the user click away afterwards)
  useEffect(() => {
    if (activeId && activeId !== prevActive.current) {
      setSelectedId(activeId);
      prevActive.current = activeId;
      setStopping(false); // a fresh run clears any leftover stopping state
    }
  }, [activeId]);

  // once the build is no longer running, the cooperative stop has landed — clear the "Stopping…" state
  useEffect(() => {
    if (!status.busy) setStopping(false);
  }, [status.busy]);

  // refresh history + the active build's detail when a run ends
  useEffect(() => {
    if (live.terminal || live.error) setRefreshKey((k) => k + 1);
  }, [live.terminal, live.error]);

  const isLiveView = !!selectedId && selectedId === activeId;

  // load the emitted detail for a historical (non-live) selection
  useEffect(() => {
    if (!selectedId || isLiveView) {
      if (!selectedId) setDetail(null);
      return;
    }
    let alive = true;
    api.buildDetail(selectedId).then((d) => alive && setDetail(d)).catch(() => alive && setDetail(null));
    return () => {
      alive = false;
    };
  }, [selectedId, isLiveView, refreshKey]);

  // ── derive what to render ────────────────────────────────────────────────
  const art = detail?.artifact ?? null;
  const board = isLiveView
    ? {
        goal: live.snapshot?.goal ?? "",
        criteria: live.snapshot?.criteria ?? [],
        records: live.snapshot?.iteration_records ?? [],
        descope: live.snapshot?.descope_report ?? [],
        caps: live.snapshot?.caps_hit ?? [],
      }
    : {
        goal: art?.spec_summary ?? "",
        criteria: art?.success_criteria ?? [],
        records: art?.iterations ?? [],
        descope: art?.descope_report ?? [],
        caps: art?.caps_hit ?? [],
      };

  const phase = isLiveView ? live.snapshot?.phase || status.phase || live.node : art?.status;
  const selStatus = isLiveView ? status.status || "running" : art?.status ?? "";
  const demonstrates = isLiveView
    ? live.terminal?.demonstrates
    : art?.final_verdict?.demonstrates_core_value;
  const canResume = !status.busy && !!art && RESUMABLE.has(art.status);
  // Refine re-attacks a FINISHED build's descoped backlog on a stronger coder — offered whenever a
  // non-live build has descoped criteria and nothing else is running.
  const canRefine = !status.busy && !isLiveView && !!art && (art.descope_report?.length ?? 0) > 0;
  const hasAbandoned = !!detail?.files.includes("abandoned.patch");

  const onStop = () => {
    setStopping(true); // immediate feedback — the cooperative stop lands at the next node boundary
    api.stop().catch(() => setStopping(false));
  };
  const onResume = () =>
    api.resume(selectedId).then(() => setRefreshKey((k) => k + 1)).catch(() => {});
  const onRefine = () =>
    api.refine(selectedId, coderRole.trim()).then(() => setRefreshKey((k) => k + 1)).catch(() => {});

  return (
    <div className="app">
      <Sidebar
        builds={builds}
        busy={status.busy}
        activeId={activeId}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onStarted={() => setRefreshKey((k) => k + 1)}
      />

      <main className="main">
        {!selectedId ? (
          <div className="empty">
            <h2>Watch a build</h2>
            <p className="muted">
              Start a PoC from the sidebar, or pick a build from history. A run streams its slice board,
              log, and descope report live over this tunnel.
            </p>
            <p className="small muted">
              SSE: {live.connected ? <span className="ok">connected</span> : <span className="bad">disconnected</span>}
            </p>
          </div>
        ) : (
          <>
            <header className="run-head">
              <div>
                <h2 className="run-id">{selectedId.replace(/^poc-/, "")}</h2>
                <div className="run-sub muted small">
                  {isLiveView && live.kind && <span className="pill info">{live.kind}</span>}
                  <span className="pill muted">{selStatus || "—"}</span>
                  {phase && <span>phase: {phase}</span>}
                  {demonstrates && <span>· core value: <b>{demonstrates}</b></span>}
                  {isLiveView && (
                    <span>· SSE {live.connected ? <span className="ok">●</span> : <span className="bad">●</span>}</span>
                  )}
                </div>
              </div>
              <div className="run-controls">
                {isLiveView && status.busy && (
                  <button className="ghost" onClick={onStop} disabled={stopping}>
                    {stopping ? "Stopping…" : "■ Stop"}
                  </button>
                )}
                {canResume && <button onClick={onResume}>▶ Resume</button>}
                {canRefine && (
                  <span className="refine-ctl">
                    <input
                      className="coder-input"
                      placeholder="coder role (e.g. frontier)"
                      value={coderRole}
                      onChange={(e) => setCoderRole(e.target.value)}
                      title="A .env role whose triple points at a stronger/frontier coder endpoint (a per-call rebind). Leave blank to re-run on the same coder."
                    />
                    <button onClick={onRefine}>✦ Refine descopes</button>
                  </span>
                )}
                {detail?.langfuse_host && (
                  <a className="ghost btn" href={detail.langfuse_host} target="_blank" rel="noreferrer">
                    Traces ↗
                  </a>
                )}
              </div>
            </header>

            {stopping && status.busy && (
              <div className="banner">
                Stop requested — the build finishes the current step, then checkpoints at the next node
                boundary (an in-flight model call can take a minute). It then becomes resumable.
              </div>
            )}
            {live.error && isLiveView && <p className="error">build error: {live.error}</p>}
            {detail?.artifact_error && !isLiveView && (
              <p className="muted small">artifact not loaded: {detail.artifact_error}</p>
            )}

            <SliceBoard
              goal={board.goal}
              criteria={board.criteria}
              records={board.records}
              final={!isLiveView}
            />

            <DescopePanel items={board.descope} hasAbandonedPatch={hasAbandoned} capsHit={board.caps} />

            {!isLiveView && art && (art.caveats.length > 0 || art.security?.degraded_critic) && (
              <section className="card">
                <div className="card-head">
                  <h2>Caveats &amp; quality notes</h2>
                  {art.security?.degraded_critic && (
                    <span className="pill warn" title="critic shared the coder's model family → adequacy gate was advisory, not blocking">
                      degraded critic
                    </span>
                  )}
                </div>
                {art.security?.degraded_critic && (
                  <p className="muted small">
                    The reviewer ran on the same model family as the coder, so its adequacy checks were
                    advisory (it couldn't block). Bind a different model to the <code>critic</code> role
                    for a stronger gate.
                  </p>
                )}
                <ul className="caveats">
                  {art.caveats.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </section>
            )}

            {isLiveView ? (
              <LogPanel lines={live.log} live={status.busy} />
            ) : (
              detail && <DocsPanel buildId={selectedId} files={detail.files} />
            )}
          </>
        )}
      </main>
    </div>
  );
}
