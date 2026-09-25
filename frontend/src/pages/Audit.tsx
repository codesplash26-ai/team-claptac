import { usePoll, type AuditEntry } from "../api";

export function AuditPage() {
  const { data, error } = usePoll<AuditEntry[]>("/am/v1/audit?limit=300", 3000);
  return (
    <div className="page">
      <div className="card">
        <h2>Audit trail · every change, who made it, and how it ended</h2>
        {error && <div className="err">{error}</div>}
        <div className="muted sm" style={{ marginBottom: 8 }}>Recorded automatically in the append-only ledger. Never contains a request body, a reason typed by a person, or a token.</div>
        {data && data.length === 0 && <div className="empty">Nothing has changed yet.</div>}
        {data && data.length > 0 && (
          <table className="t">
            <thead><tr><th>When</th><th>Who</th><th>Role</th><th>Action</th><th>Needed</th><th>Result</th></tr></thead>
            <tbody>{data.map((e) => (
              <tr key={e.seq}>
                <td className="sm">{new Date(e.at * 1000).toLocaleTimeString()}</td><td>{e.actor}</td><td className="sm">{e.role}</td>
                <td className="mono sm">{e.method} {e.route}{Object.keys(e.params).length ? ` (${Object.values(e.params).join(", ")})` : ""}</td>
                <td className="sm">{e.permission ?? "—"}</td>
                <td><span className={`chip ${e.status < 300 ? "ok" : e.status < 500 ? "warn" : "bad"}`}>{e.status}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </div>
  );
}
