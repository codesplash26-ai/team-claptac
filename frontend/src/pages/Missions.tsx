import { useState } from "react";
import { api, ApiError, usePoll } from "../api";
import { ago, friendly, missionStatus, place, teams, type Mission } from "../plain";

/* Work sent to field teams. Teams (or the operations room on their behalf) report progress here, and the
 * reports flow back into the plan: a failed mission makes ATLAS replan. */
export function MissionsPage() {
  const missions = usePoll<Mission[]>("/missions", 3000);
  const [msg, setMsg] = useState<string | null>(null);
  const list = [...(missions.data ?? [])].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const active = list.filter((m) => !["completed", "cancelled", "failed"].includes(m.status));
  const done = list.filter((m) => !active.includes(m));

  const update = async (m: Mission, verb: "acknowledge" | "progress" | "complete", body?: unknown) => {
    try {
      await api(`/missions/${m.id}/${verb}`, { method: "POST", json: body ?? {} });
      setMsg(null);
      missions.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };

  const Row = ({ m }: { m: Mission }) => (
    <div className="card mission">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{m.title.replace(/[a-z]+-shelter-(\d+)/g, "Shelter $1")}</b>
        <span className={`chip ${m.status === "completed" ? "ok" : m.status === "failed" || m.exception ? "bad" : "acc"}`}>{missionStatus(m.status)}</span>
      </div>
      <div className="muted sm">{teams(m.team)} · {place(m.incident_id)}{m.destination ? ` · to ${friendly(m.destination)}` : ""} · sent {ago(m.created_at)}</div>
      <div className="bar"><i style={{ width: `${m.status === "completed" ? 100 : m.progress}%` }} /></div>
      {m.exception && (
        <div className="banner bad sm">
          {m.exception.replace(/not acknowledged within (\d+)s/, (_, s) => `The team has not confirmed it within ${Math.round(Number(s) / 60)} minutes. Call them.`)}
        </div>
      )}
      {!["completed", "cancelled", "failed"].includes(m.status) && (
        <div className="row" style={{ marginTop: 6 }}>
          {!m.acknowledged_at && <button className="btn" onClick={() => update(m, "acknowledge", { by: "operations room" })}>Team confirmed</button>}
          <button className="btn" onClick={() => update(m, "progress", { percent: Math.min(90, (m.progress || 0) + 30), note: "update from operations room" })}>Report progress</button>
          <button className="btn primary" onClick={() => update(m, "complete")}>Mark done</button>
        </div>
      )}
    </div>
  );

  return (
    <div className="page ops">
      <p className="lead">Work ATLAS has sent to rescue teams and vehicles. Buttons here record what a team tells you by radio or phone.</p>
      {msg && <div className="banner warn" style={{ marginBottom: 10 }}>{msg}</div>}
      <h3 className="sec">Under way · {active.length}</h3>
      {active.length === 0 && <div className="card muted">No mission is under way.</div>}
      <div className="grid g2">{active.map((m) => <Row key={m.id} m={m} />)}</div>
      {done.length > 0 && <>
        <h3 className="sec" style={{ marginTop: 18 }}>Finished · {done.length}</h3>
        <div className="grid g2">{done.map((m) => <Row key={m.id} m={m} />)}</div>
      </>}
    </div>
  );
}
