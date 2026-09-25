import { useEffect, useMemo, useState } from "react";
import { fmtMs, shortAgent, usePoll, type AgentCard, type FlowEdge, type Run, type Span } from "../api";
import { AgentGraph } from "../components/AgentGraph";
import { Inspector } from "../components/Inspector";
import { Waterfall } from "../components/Waterfall";

type View = "trace" | "graph";

export function RunDetail({ id, back }: { id: string; back: () => void }) {
  const [view, setView] = useState<View>("trace");
  const [selected, setSelected] = useState<string | null>(null);
  const [step, setStep] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const spans = usePoll<Span[]>(`/dev/runs/${encodeURIComponent(id)}/spans`, 2500);
  const runs = usePoll<Run[]>("/dev/runs", 5000);
  const flow = usePoll<FlowEdge[]>(`/dev/flow?incident_id=${encodeURIComponent(id)}`, 2500);
  const agents = usePoll<AgentCard[]>("/dev/agents", 5000);
  const run = runs.data?.find((r) => r.incident_id === id);

  const ordered = useMemo(() => [...(spans.data ?? [])].sort((a, b) => a.start - b.start), [spans.data]);
  const bySpan = useMemo(() => new Map(ordered.map((s) => [s.id, s])), [ordered]);
  const selectedSpan = selected ? bySpan.get(selected) ?? null : null;
  const total = ordered.length;
  const current = step === null ? total - 1 : Math.min(step, total - 1);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setStep((s) => {
        const next = (s ?? -1) + 1;
        if (next >= total) {
          setPlaying(false);
          return total - 1;
        }
        return next;
      });
    }, 350);
    return () => clearInterval(timer);
  }, [playing, total]);

  const { active, activeEdges, visited } = useMemo(() => {
    const visited = new Set<string>();
    for (let i = 0; i <= current && i < ordered.length; i++) visited.add(ordered[i].agent);
    const at = ordered[current];
    const parent = at?.parent_id ? bySpan.get(at.parent_id) : undefined;
    return {
      active: new Set(at ? [at.agent] : []),
      activeEdges: new Set(at && parent ? [`${parent.agent}>${at.agent}`, `${at.agent}>${parent.agent}`] : []),
      visited,
    };
  }, [ordered, bySpan, current]);

  const selectFromEvent = (eventId: string) => {
    const s = ordered.find((x) => x.event_id === eventId && x.kind !== "input") ?? ordered.find((x) => x.event_id === eventId);
    if (s) setSelected(s.id);
  };

  return (
    <div className="page flush">
      <div className="topbar">
        <a onClick={back}>← Runs</a>
        <h1>{id.replace("incident:", "")}</h1>
        {run && (
          <>
            <span className="chip">{run.state}</span>
            <span className="chip">{run.hazard} · {run.area_id}</span>
            <span className="chip">{run.spans} spans</span>
            <span className="chip">{fmtMs(run.duration_ms)} wall</span>
            <span className="chip">{run.cycles} cycle{run.cycles === 1 ? "" : "s"}</span>
            {run.errors > 0 && <span className="chip bad">{run.errors} errors</span>}
            {run.needs_human && <span className="chip warn">needs human</span>}
          </>
        )}
        <span className="sp" />
        <div className="tabs" style={{ border: 0, padding: 0, position: "static" }}>
          <button className={view === "trace" ? "on" : ""} onClick={() => setView("trace")}>Trace</button>
          <button className={view === "graph" ? "on" : ""} onClick={() => setView("graph")}>Graph replay</button>
        </div>
      </div>
      {spans.error && <div className="err" style={{ margin: 12 }}>{spans.error}</div>}
      {run?.summary && <div className="sm muted" style={{ padding: "6px 18px", borderBottom: "1px solid var(--line)" }}>{run.summary}</div>}
      {spans.data && (
        <div className="split stack">
          <div className="pane" style={{ display: "flex", flexDirection: "column" }}>
            {view === "trace" ? (
              <Waterfall spans={ordered.length ? spans.data : []} selected={selected} onSelect={setSelected} />
            ) : (
              <>
                <div className="graphwrap">
                  <AgentGraph
                    flow={flow.data ?? []}
                    agents={agents.data ?? []}
                    active={active}
                    activeEdges={activeEdges}
                    focus={visited}
                    onNodeClick={() => undefined}
                  />
                </div>
                <div className="scrub">
                  <button className="btn" onClick={() => { setPlaying(!playing); if (!playing && (step === null || step >= total - 1)) setStep(0); }}>
                    {playing ? "Pause" : "Play"}
                  </button>
                  <input type="range" min={0} max={Math.max(total - 1, 0)} value={current} onChange={(e) => { setPlaying(false); setStep(Number(e.target.value)); }} />
                  <span className="sm mono" style={{ minWidth: 150 }}>
                    step {current + 1}/{total}
                  </span>
                  <button className="btn" onClick={() => { setStep(null); setPlaying(false); }}>Live end</button>
                </div>
                {ordered[current] && (
                  <div className="sm" style={{ padding: "6px 14px" }}>
                    <b>{shortAgent(ordered[current].agent)}</b> handled <span className="chip">{ordered[current].event_type}</span> {ordered[current].summary}
                    <a style={{ marginLeft: 8 }} onClick={() => setSelected(ordered[current].id)}>inspect</a>
                  </div>
                )}
              </>
            )}
          </div>
          <div className="pane right">
            <Inspector span={selectedSpan} onSelectEvent={selectFromEvent} />
          </div>
        </div>
      )}
    </div>
  );
}
