import { usePoll, type Approval, type ScenarioInfo } from "../api";
import { ago, describeAction, friendly, missionStatus, place, readable, type Alert, type Incident, type Mission, type Sitrep } from "../plain";

interface Sensor { node_id: string; sensor_type: string; value: number; unit: string; threshold: number | null; history?: number[]; observed_at?: number }
interface Replay { state: string; scenario: string | null; sent: number; total: number }

const SENSOR: Record<string, string> = { water_level: "River level", rainfall: "Rainfall", soil_moisture: "Soil wetness", tilt: "Ground movement (slope)" };

function Spark({ values, threshold }: { values: number[]; threshold: number | null }) {
  if (values.length < 2) return null;
  const all = threshold != null ? [...values, threshold] : values;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * 160, y = (v: number) => 34 - ((v - lo) / span) * 30;
  return (
    <svg viewBox="0 0 160 36" style={{ width: "100%", height: 36 }} aria-hidden>
      {threshold != null && <line x1={0} x2={160} y1={y(threshold)} y2={y(threshold)} stroke="var(--bad)" strokeDasharray="4 3" strokeWidth={1} />}
      <polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  );
}

export function OverviewPage() {
  const incidents = usePoll<Incident[]>("/incidents", 3000);
  const approvals = usePoll<Approval[]>("/approvals", 3000);
  const missions = usePoll<Mission[]>("/missions", 3000);
  const alerts = usePoll<Alert[]>("/alerts", 4000);
  const sitreps = usePoll<Sitrep[]>("/sitreps", 5000);
  const sensors = usePoll<Sensor[]>("/sensors", 3000);
  const replay = usePoll<Replay>("/play/scenarios/status", 2000);
  const scenarios = usePoll<ScenarioInfo[]>("/play/scenarios", 0);
  const drillName = scenarios.data?.find((s) => s.key === replay.data?.scenario)?.title ?? friendly(replay.data?.scenario);

  const open = incidents.data ?? [];
  const waiting = approvals.data ?? [];
  const active = (missions.data ?? []).filter((m) => !["completed", "cancelled", "failed"].includes(m.status));
  const people = active.reduce((n, m) => n + (Number(m.title.match(/Evacuate (\d+) people/)?.[1]) || 0), 0);
  const liveAlerts = (alerts.data ?? []).filter((a) => a.status === "active");
  const latestReport = (sitreps.data ?? []).slice(-1)[0];
  const over = (sensors.data ?? []).filter((s) => s.threshold != null && s.value >= s.threshold);

  const headline = open.length === 0
    ? { tone: "ok", text: "All quiet. No incident is open." }
    : waiting.length
      ? { tone: "warn", text: `${open.length === 1 ? "An incident is" : `${open.length} incidents are`} open, and ${waiting.length} decision${waiting.length > 1 ? "s are" : " is"} waiting for you.` }
      : { tone: "bad", text: `${open.length === 1 ? "An incident is" : `${open.length} incidents are`} open. ATLAS is responding; nothing is waiting for you.` };

  return (
    <div className="page ops">
      <p className="lead">What is happening now, and what needs you. This page updates by itself.</p>

      {replay.data?.state === "running" && (
        <div className="banner info">Practice drill running: replaying <b>{drillName}</b> ({replay.data.sent} of {replay.data.total} readings). Everything below is from the drill.</div>
      )}
      <div className={`banner ${headline.tone}`}><b>{headline.text}</b></div>

      <div className="grid g4" style={{ margin: "14px 0" }}>
        <a className="card stat" href="#/decisions"><div className="kpi" style={{ color: waiting.length ? "var(--warn)" : undefined }}>{waiting.length}<small>decisions waiting for you</small></div></a>
        <a className="card stat" href="#/missions"><div className="kpi">{active.length}<small>missions under way{people ? `, moving ${people} people` : ""}</small></div></a>
        <a className="card stat" href="#/alerts"><div className="kpi">{liveAlerts.length}<small>public alerts out now</small></div></a>
        <a className="card stat" href="#/map"><div className="kpi" style={{ color: over.length ? "var(--bad)" : undefined }}>{over.length}<small>readings above the danger line</small></div></a>
      </div>

      <div className="grid g2">
        <div className="card">
          <h2>Needs your decision</h2>
          {waiting.length === 0 && <p className="muted">Nothing. When an action reaches the public or commits many teams, it appears here first.</p>}
          {waiting.slice(0, 4).map((a) => (
            <div key={a.id} className="line-item">
              <span>{describeAction(a.action)}</span>
              <a className="btn primary" href="#/decisions">Decide</a>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Sensors</h2>
          {(sensors.data ?? []).length === 0 && <p className="muted">No readings yet. Start a practice drill to see the system work.</p>}
          {(sensors.data ?? []).map((s) => (
            <div key={s.node_id + s.sensor_type} className="sensor">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span>{SENSOR[s.sensor_type] ?? s.sensor_type} <span className="muted sm">· {friendly(s.node_id.split("/").pop())}</span></span>
                <b style={{ color: s.threshold != null && s.value >= s.threshold ? "var(--bad)" : undefined }}>
                  {s.value.toFixed(1)} {s.unit}{s.threshold != null && <span className="muted sm"> / danger {s.threshold}</span>}
                </b>
              </div>
              {s.history && <Spark values={s.history.slice(-40)} threshold={s.threshold} />}
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Incidents</h2>
          {open.length === 0 && <p className="muted">No incident open.</p>}
          {open.map((i) => (
            <div key={i.incident_id} className="line-item">
              <span><b>{place(i.incident_id)}</b><br /><span className="muted sm">opened {ago(i.first_seen)} · {i.active_missions} missions · last update {ago(i.last_seen)}</span></span>
              <a className="btn" href="#/map">See on map</a>
            </div>
          ))}
        </div>

        <div className="card">
          <h2>Latest situation report</h2>
          {!latestReport && <p className="muted">None yet. ATLAS writes one each time the plan changes.</p>}
          {latestReport && (
            <>
              <p className="muted sm">{ago(latestReport.generated_at)}</p>
              <pre className="report">{readable(latestReport.texts.en ?? Object.values(latestReport.texts)[0]).replace(/ \(\d{4}-\d\d-\d\dT[^)]*\)/, "")}</pre>
              <a href="#/alerts">All alerts and reports →</a>
            </>
          )}
        </div>

        {active.length > 0 && (
          <div className="card" style={{ gridColumn: "1 / -1" }}>
            <h2>Missions</h2>
            <table className="t">
              <thead><tr><th>What</th><th>Team</th><th>Status</th></tr></thead>
              <tbody>
                {active.slice(0, 6).map((m) => (
                  <tr key={m.id}><td>{m.title.replace(/ratnapura-shelter-(\d+)/g, "Shelter $1")}</td><td>{friendly(m.team.split(",")[0])}{m.team.includes(",") ? " +" : ""}</td><td>{missionStatus(m.status)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
