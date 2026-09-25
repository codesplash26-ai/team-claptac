import { useState } from "react";
import { usePoll, type AgentCard, type FlowEdge, type TraceEvent } from "../api";
import { AgentGraph } from "../components/AgentGraph";
import { Json } from "../components/Json";

export function GraphPage() {
  const flow = usePoll<FlowEdge[]>("/dev/flow", 1500);
  const agents = usePoll<AgentCard[]>("/dev/agents", 3000);
  const trace = usePoll<TraceEvent[]>("/dev/trace?tail=200", 1500);
  const [focus, setFocus] = useState<string | null>(null);
  const [pick, setPick] = useState<TraceEvent | null>(null);
  const events = trace.data ?? [];
  const newest = events.length ? events[events.length - 1].seq : 0;
  const recent = events.filter((e) => e.seq > newest - 6);
  const active = new Set(recent.flatMap((e) => [e.source, ...e.handled_by]));
  const activeEdges = new Set(recent.flatMap((e) => e.handled_by.map((h) => `${e.source}>${h}`)));
  const shown = events.filter((e) => !focus || e.source === focus || e.handled_by.includes(focus)).slice(-120).reverse();
  const card = agents.data?.find((a) => a.name === focus);

  return (
    <div className="page flush">
      <div className="topbar">
        <h1>Agent graph</h1>
        <span className="muted sm">Live. Lit nodes handled one of the last few messages. Click an agent to filter.</span>
        <span className="sp" />
        {focus && <button className="btn" onClick={() => setFocus(null)}>Clear filter ({focus})</button>}
      </div>
      <div className="split stack">
        <div className="pane" style={{ display: "flex", flexDirection: "column" }}>
          <div className="graphwrap">
            <AgentGraph
              flow={flow.data ?? []}
              agents={agents.data ?? []}
              active={active}
              activeEdges={activeEdges}
              focus={focus ? new Set([focus, ...events.filter((e) => e.source === focus).flatMap((e) => e.handled_by), ...events.filter((e) => e.handled_by.includes(focus)).map((e) => e.source)]) : null}
              onNodeClick={(id) => setFocus(focus === id ? null : id)}
            />
          </div>
          {card && (
            <div className="card" style={{ margin: 10 }}>
              <h2>{card.title}</h2>
              <div>{card.purpose}</div>
              <div className="sm muted" style={{ marginTop: 4 }}>reads: {card.reads.join(", ") || "—"} · produces: {card.writes.join(", ") || "—"}</div>
              <div className="sm muted">tools: {card.deterministic_tools.join(", ") || "—"} · {card.uses_llm ? "uses an LLM" : "no LLM"}</div>
            </div>
          )}
        </div>
        <div className="pane right" style={{ padding: "8px 14px" }}>
          <div className="h2">Messages {focus ? `· ${focus}` : ""}</div>
          {shown.length === 0 && <div className="empty">No messages yet.</div>}
          {shown.map((e) => (
            <div key={e.id} className="tick" onClick={() => setPick(pick?.id === e.id ? null : e)}>
              <time>{e.created_at.slice(11, 19)}</time>
              <span>
                <span className="chip acc">{e.type}</span> {e.summary}
                {pick?.id === e.id && (
                  <>
                    <div className="sm muted">{e.source} → {e.handled_by.join(", ") || "nobody"}</div>
                    <Json value={e.payload} max={220} />
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
