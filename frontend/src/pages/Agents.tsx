import { useState } from "react";
import { api, ApiError, usePoll, type AgentRecord } from "../api";
import { LAYER_ICON } from "../graphDef";

interface Inventory {
  summary: { agents: number; suspended: number; external: number; site: string };
  agents: AgentRecord[];
}

export function AgentsPage() {
  const { data, error, reload } = usePoll<Inventory>("/am/v1/agents", 3000);
  const [msg, setMsg] = useState<string | null>(null);
  const [reason, setReason] = useState<Record<string, string>>({});

  const act = async (name: string, verb: "suspend" | "resume") => {
    try {
      await api(`/am/v1/agents/${name}/${verb}`, { method: "POST", ...(verb === "suspend" ? { json: { reason: reason[name] || "" } } : {}) });
      setMsg(null);
      reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  };

  if (error) return <div className="page"><div className="err">{error}</div></div>;
  return (
    <div className="page">
      {data && (
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="chip">{data.summary.agents} in the inventory</span>
          <span className={`chip ${data.summary.suspended ? "warn" : ""}`}>{data.summary.suspended} suspended</span>
          <span className="chip">{data.summary.external} external</span>
          <span className="chip">project {data.summary.site}</span>
          <span className="muted sm">Suspending an agent stops delivery to it. Nothing is lost: its work waits and is done, in order, when it resumes. A held plan that has gone stale is refused by the Gateway.</span>
        </div>
      )}
      {msg && <div className="err" style={{ marginBottom: 8 }}>{msg}</div>}
      <div className="grid g3">
        {(data?.agents ?? []).map((a) => (
          <div className="card" key={a.name} style={{ borderLeft: `4px solid var(--${a.layer})`, opacity: a.status === "suspended" ? 0.85 : 1 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b><span className={`icon i-${a.layer}`} style={{ marginRight: 6 }}>{LAYER_ICON[a.layer]}</span>{a.title}</b>
              <span className={`chip ${a.status === "suspended" ? "warn" : "ok"}`}>{a.status}</span>
            </div>
            <p style={{ margin: "6px 0" }}>{a.purpose}</p>
            <div className="row sm">
              <span className="chip">{a.kind}</span><span className="chip">v{a.version}</span><span className="chip">{a.owner}</span>
              <span className="chip">{a.uses_llm ? "uses an LLM" : "no LLM"}</span>
              <span className="chip">in {a.received} · out {a.produced}</span>
              {a.environments.map((e) => <span key={e} className="chip">{e}</span>)}
            </div>
            <div className="sm muted" style={{ marginTop: 6 }}><b>Reads</b> {a.reads.join(", ") || "—"}</div>
            <div className="sm muted"><b>Produces</b> {a.writes.join(", ") || "—"}</div>
            {a.last_received && <div className="sm" style={{ marginTop: 4 }}><span className="muted">last in</span> {a.last_received}</div>}
            {a.status === "suspended" && (
              <div className="sm" style={{ marginTop: 6 }}>
                <span className="chip warn">held by {a.suspended_by}: {a.suspended_reason}</span>{" "}
                <span className="chip">{a.pending} waiting</span>
              </div>
            )}
            {a.kind === "internal" && a.name !== "human" && (
              <div className="row" style={{ marginTop: 8 }}>
                {a.status === "active" ? (
                  <>
                    <input placeholder="reason (required)" value={reason[a.name] ?? ""} onChange={(e) => setReason({ ...reason, [a.name]: e.target.value })} style={{ flex: 1, minWidth: 100 }} />
                    <button className="btn danger" onClick={() => act(a.name, "suspend")}>Suspend</button>
                  </>
                ) : (
                  <button className="btn primary" onClick={() => act(a.name, "resume")}>Resume</button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
