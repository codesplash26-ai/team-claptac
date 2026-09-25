import { LAYER_ICON } from "../graphDef";
import { usePoll, type AgentCard } from "../api";

export function AgentsPage() {
  const { data, error } = usePoll<AgentCard[]>("/dev/agents", 4000);
  if (error) return <div className="page"><div className="err">{error}</div></div>;
  return (
    <div className="page">
      <div className="muted" style={{ marginBottom: 10, maxWidth: 820 }}>
        Nine agents and four control components. Agents never call each other: they read and write events on the bus and the shared picture of the situation. Only A3 delegates, and
        only the Gateway lets anything act. Counts are the most recent 1000 events.
      </div>
      <div className="grid g3">
        {(data ?? []).map((a) => (
          <div className="card" key={a.name} style={{ borderLeft: `4px solid var(--${a.layer})` }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b><span className={`icon i-${a.layer}`} style={{ marginRight: 6 }}>{LAYER_ICON[a.layer]}</span>{a.title}</b>
              <span className="chip">{a.layer}</span>
            </div>
            <p style={{ margin: "6px 0" }}>{a.purpose}</p>
            <div className="sm muted"><b>Reads</b> {a.reads.join(", ") || "—"}</div>
            <div className="sm muted"><b>Produces</b> {a.writes.join(", ") || "—"}</div>
            <div className="sm muted"><b>Tools</b> {a.deterministic_tools.join(", ") || "—"}</div>
            <div className="row sm" style={{ marginTop: 6 }}>
              <span className="chip">in {a.received}</span><span className="chip">out {a.produced}</span>
              <span className="chip">{a.uses_llm ? "uses an LLM" : "no LLM"}</span><span className="chip">{a.owner}</span>
            </div>
            {(a.last_received || a.last_produced) && (
              <div className="sm" style={{ marginTop: 6 }}>
                <div><span className="muted">last in</span> {a.last_received ?? "—"}</div>
                <div><span className="muted">last out</span> {a.last_produced ?? "—"}</div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
