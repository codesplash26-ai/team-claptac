import { fmtMs, shortAgent, usePoll, type Run } from "../api";

const STATE: Record<string, string> = { active: "ok", standby: "", awaiting_gateway: "warn", assessing: "acc", planning: "acc", replanning: "warn" };

export function Runs({ open }: { open: (id: string) => void }) {
  const { data, error, loading } = usePoll<Run[]>("/dev/runs", 3000);
  return (
    <div className="page">
      <div className="card">
        <h2>Runs · one per incident</h2>
        {error && <div className="err">{error}</div>}
        {loading && !data && <div className="muted">Loading…</div>}
        {data && data.length === 0 && (
          <div className="empty">
            No runs yet. Start a scenario in the Playground, or post a sensor reading to <code>/ingest/sensor</code>.
          </div>
        )}
        {data && data.length > 0 && (
          <table className="t">
            <thead>
              <tr>
                <th>Incident</th><th>Status</th><th>Hazard · area</th><th>Cycles</th><th>Spans</th><th>Errors</th><th>Wall time</th><th>Agents</th><th>Started</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.incident_id} className="click" onClick={() => open(r.incident_id)}>
                  <td><b>{r.incident_id.replace("incident:", "")}</b>{r.needs_human && <span className="chip warn" style={{ marginLeft: 6 }}>needs human</span>}</td>
                  <td><span className={`chip ${STATE[r.state ?? ""] ?? ""}`}>{r.state ?? "—"}</span></td>
                  <td>{r.hazard ?? "—"} <span className="muted">· {r.area_id ?? "—"}</span></td>
                  <td>{r.cycles}{r.replans ? <span className="muted"> ({r.replans} replan{r.replans > 1 ? "s" : ""})</span> : ""}</td>
                  <td>{r.spans}</td>
                  <td>{r.errors ? <span className="chip bad">{r.errors}</span> : <span className="muted">0</span>}</td>
                  <td>{fmtMs(r.duration_ms)}</td>
                  <td className="sm muted">{r.agents.map(shortAgent).join(" ")}</td>
                  <td className="sm muted">{new Date(r.started * 1000).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
