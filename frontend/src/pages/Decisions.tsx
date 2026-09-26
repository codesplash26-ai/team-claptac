import { useState } from "react";
import { api, ApiError, usePoll, type Approval } from "../api";
import { ago, describeAction, place, readable, whyHuman } from "../plain";

/* The human in the loop. Each item is something ATLAS wants to do that the safety rules say a person
 * must approve first. Nothing here happens until someone says yes. */
export function DecisionsPage() {
  const pending = usePoll<Approval[]>("/approvals", 2000);
  const approved = usePoll<Approval[]>("/approvals?status=approved", 5000);
  const rejected = usePoll<Approval[]>("/approvals?status=rejected", 5000);
  const [who, setWho] = useState(() => { try { return localStorage.getItem("atlas-reviewer") ?? ""; } catch { return ""; } });
  const [note, setNote] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const decide = async (a: Approval, verb: "approve" | "reject") => {
    if (!who.trim()) return setMsg("Please type your name first. It is written into the permanent record with your decision.");
    try { localStorage.setItem("atlas-reviewer", who); } catch { /* private window */ }
    setBusy(a.id);
    try {
      await api(`/approvals/${a.id}/${verb}`, { method: "POST", json: { reviewer: who, note: note[a.id] ?? "" } });
      setMsg(null);
      pending.reload(); approved.reload(); rejected.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? (e.status === 401 || e.status === 403 ? "You are not signed in as someone allowed to decide. Sign in at the bottom of the menu." : e.message) : String(e));
    } finally { setBusy(null); }
  };

  const history = [...(approved.data ?? []).map((a) => ({ ...a, verdict: "Approved" })), ...(rejected.data ?? []).map((a) => ({ ...a, verdict: "Rejected" }))]
    .sort((x, y) => Date.parse(y.created_at) - Date.parse(x.created_at)).slice(0, 10);

  return (
    <div className="page ops">
      <p className="lead">ATLAS acts on its own for routine work. Anything that reaches the public or commits many teams waits here until a person says yes. Your name and decision are kept in a record that cannot be changed.</p>
      <label className="f" style={{ maxWidth: 320, marginBottom: 12 }}>Your name<input value={who} onChange={(e) => setWho(e.target.value)} placeholder="e.g. District Officer Perera" /></label>
      {msg && <div className="banner warn" style={{ marginBottom: 12 }}>{msg}</div>}

      {pending.data?.length === 0 && <div className="banner ok">Nothing is waiting for you.</div>}
      {pending.data?.map((a) => (
        <div key={a.id} className="card decision">
          <div className="muted sm">{place(a.incident_id)} · asked {ago(a.created_at)}</div>
          <div className="decision-what">{describeAction(a.action)}</div>
          <div className="decision-why"><b>Why ATLAS suggests it:</b> {a.action.rationale ? readable(a.action.rationale) : "Part of the current response plan."}</div>
          <div className="decision-why"><b>Why it needs you:</b> {whyHuman(a.action)}</div>
          <input placeholder="Optional note (kept with your decision)" value={note[a.id] ?? ""} onChange={(e) => setNote({ ...note, [a.id]: e.target.value })} style={{ width: "100%", margin: "8px 0" }} />
          <div className="row">
            <button className="btn primary big" disabled={busy !== null} onClick={() => decide(a, "approve")}>✓ Approve</button>
            <button className="btn danger big" disabled={busy !== null} onClick={() => decide(a, "reject")}>✕ Reject</button>
            <span className="muted sm">To change the details before approving, use Technical → Approvals (advanced).</span>
          </div>
        </div>
      ))}

      {history.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <h2>Recent decisions</h2>
          <table className="t">
            <tbody>
              {history.map((a) => (
                <tr key={a.id}><td><span className={`chip ${a.verdict === "Approved" ? "ok" : "bad"}`}>{a.verdict}</span></td><td>{describeAction(a.action)}</td><td className="muted sm">{ago(a.created_at)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
