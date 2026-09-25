import { useState } from "react";
import { api, ApiError, usePoll, type Approval } from "../api";
import { Json } from "../components/Json";

export function ApprovalsPage() {
  const pending = usePoll<Approval[]>("/approvals", 2000);
  const done = usePoll<Approval[]>("/approvals?status=approved", 4000);
  const rejected = usePoll<Approval[]>("/approvals?status=rejected", 4000);
  const [who, setWho] = useState(localStorage.getItem("atlas-reviewer") ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, string>>({});

  const act = async (id: string, verb: "approve" | "edit" | "reject", params?: unknown) => {
    if (!who.trim()) return setMsg("Enter your name first: it is recorded in the ledger.");
    localStorage.setItem("atlas-reviewer", who);
    try {
      await api(`/approvals/${id}/${verb}`, { method: "POST", json: { reviewer: who, ...(params ? { params } : {}) } });
      setMsg(null);
      pending.reload();
      done.reload();
      rejected.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  };

  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 10 }}>
        <label className="f" style={{ width: 240 }}>Reviewer name (recorded in the ledger)<input value={who} onChange={(e) => setWho(e.target.value)} /></label>
        {msg && <span className="err">{msg}</span>}
      </div>
      <div className="card" style={{ marginBottom: 12 }}>
        <h2>Waiting for the Human Incident Commander · {pending.data?.length ?? 0}</h2>
        {pending.data?.length === 0 && <div className="empty">Nothing to approve. High-consequence actions (district alerts, committing many teams, cancelling a mission, closing a major road) appear here.</div>}
        {pending.data?.map((a) => (
          <div key={a.id} className="card" style={{ marginBottom: 8, background: "var(--panel2)" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b>{a.action.action_type}</b>
              <span className="chip warn">{a.reason}</span>
            </div>
            <div className="sm muted">{a.action.executor} · {a.incident_id}</div>
            {a.action.rationale && <p className="sm">{a.action.rationale}</p>}
            <Json value={a.action.params} max={160} />
            <textarea rows={3} style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} value={edit[a.id] ?? JSON.stringify(a.action.params)} onChange={(e) => setEdit({ ...edit, [a.id]: e.target.value })} />
            <div className="row" style={{ marginTop: 6 }}>
              <button className="btn primary" onClick={() => act(a.id, "approve")}>Approve</button>
              <button className="btn" onClick={() => { try { act(a.id, "edit", JSON.parse(edit[a.id] ?? JSON.stringify(a.action.params))); } catch { setMsg("The edited parameters are not valid JSON."); } }}>Edit and approve</button>
              <button className="btn danger" onClick={() => act(a.id, "reject")}>Reject</button>
              <span className="sm muted">An edit is re-checked against policy before it can run.</span>
            </div>
          </div>
        ))}
      </div>
      <div className="grid g2">
        <div className="card"><h2>Recently approved · {done.data?.length ?? 0}</h2>{(done.data ?? []).slice(-8).reverse().map((a) => <div key={a.id} className="sm"><span className="chip ok">approved</span> {a.action.action_type} <span className="muted">{a.incident_id}</span></div>)}</div>
        <div className="card"><h2>Recently rejected · {rejected.data?.length ?? 0}</h2>{(rejected.data ?? []).slice(-8).reverse().map((a) => <div key={a.id} className="sm"><span className="chip bad">rejected</span> {a.action.action_type} <span className="muted">{a.incident_id}</span></div>)}</div>
      </div>
    </div>
  );
}
