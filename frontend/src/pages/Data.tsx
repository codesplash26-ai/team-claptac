import { useMemo, useState } from "react";
import { usePoll, type HazardEvent } from "../api";

interface History {
  retrieved: string;
  region: string;
  sources: { key: string; title: string; url: string; licence: string; verified: boolean; count: number; notes: string; window: string[] }[];
  events: HazardEvent[];
}

const COLORS: Record<string, string> = { flood: "#2563eb", cyclone: "#7c3aed", earthquake: "#b45309", tsunami: "#0e7490", drought: "#ca8a04", landslide: "#65a30d", volcano: "#dc2626", wildfire: "#ea580c", other: "#64748b" };

export function DataPage() {
  const { data, error } = usePoll<History>("/dev/history", 0);
  const [hazard, setHazard] = useState<string | null>(null);
  const [pick, setPick] = useState<HazardEvent | null>(null);
  const events = useMemo(() => (data?.events ?? []).filter((e) => !hazard || e.hazard === hazard), [data, hazard]);
  const hazards = useMemo(() => [...new Set((data?.events ?? []).map((e) => e.hazard))], [data]);
  if (error) return <div className="page"><div className="err">{error}</div></div>;
  if (!data) return <div className="page muted">Loading…</div>;

  const lanes = hazards;
  const t0 = Math.min(...events.map((e) => +new Date(e.start)), Date.now());
  const t1 = Date.now();
  const W = 1000, H = 40 + lanes.length * 44;
  const x = (iso: string) => 60 + ((+new Date(iso) - t0) / (t1 - t0 || 1)) * (W - 80);
  const maxSev: Record<string, number> = {};
  data.events.forEach((e) => { if (e.severity != null) maxSev[e.hazard] = Math.max(maxSev[e.hazard] ?? 0, e.severity); });
  const years: number[] = [];
  for (let y = Math.ceil(new Date(t0).getFullYear() / 10) * 10; y <= new Date().getFullYear(); y += 10) years.push(y);

  return (
    <div className="page">
      <div className="card" style={{ marginBottom: 12 }}>
        <h2>Hazard history · {data.region} · {data.events.length} real events from {data.sources.filter((s) => s.count).length} sources</h2>
        <div className="row" style={{ marginBottom: 8 }}>
          <button className={`btn ${hazard === null ? "primary" : ""}`} onClick={() => setHazard(null)}>all</button>
          {hazards.map((h) => (
            <button key={h} className={`btn ${hazard === h ? "primary" : ""}`} onClick={() => setHazard(hazard === h ? null : h)}>
              <span className="sw" style={{ background: COLORS[h] }} />{h} {data.events.filter((e) => e.hazard === h).length}
            </button>
          ))}
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
          {years.map((y) => {
            const px = x(`${y}-01-01`);
            return <g key={y}><line x1={px} x2={px} y1={10} y2={H - 20} stroke="var(--line)" /><text x={px} y={H - 6} fontSize={11} fill="var(--mut)" textAnchor="middle">{y}</text></g>;
          })}
          {lanes.map((h, i) => (
            <g key={h}>
              <text x={4} y={38 + i * 44} fontSize={11} fill="var(--mut)">{h}</text>
              {events.filter((e) => e.hazard === h).map((e) => {
                const r = 3 + (e.severity != null && maxSev[h] ? (e.severity / maxSev[h]) * 8 : 2);
                return (
                  <circle key={e.id} cx={x(e.start)} cy={34 + i * 44} r={r} fill={COLORS[h]} fillOpacity={0.55} stroke={COLORS[h]} strokeWidth={pick?.id === e.id ? 3 : 1} style={{ cursor: "pointer" }} onClick={() => setPick(e)}>
                    <title>{`${e.name} · ${e.start.slice(0, 10)} · ${e.severity_label || e.severity}`}</title>
                  </circle>
                );
              })}
            </g>
          ))}
        </svg>
        <div className="sm muted">Dot size is the event's severity within its own hazard (magnitude, wind, run-up height, alert level or dryness). Click a dot.</div>
        {pick && (
          <div className="card" style={{ marginTop: 8, background: "var(--panel2)" }}>
            <b>{pick.name}</b> <span className="chip">{pick.hazard}</span> <span className="chip">{pick.source}</span>
            <div className="sm">{pick.start.slice(0, 16).replace("T", " ")} UTC · severity {pick.severity ?? "—"} {pick.severity_unit} {pick.severity_label && `(${pick.severity_label})`}</div>
            <div className="sm muted">{pick.lat != null ? `${pick.lat.toFixed(2)}, ${pick.lon?.toFixed(2)}` : "no location"}{pick.url && <> · <a href={pick.url} target="_blank" rel="noreferrer">source</a></>}</div>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Sources · what each is, and whether we checked it against the real service</h2>
        <table className="t">
          <thead><tr><th>Source</th><th>Verified</th><th>Events</th><th>Window</th><th>Licence</th></tr></thead>
          <tbody>
            {data.sources.map((s, i) => (
              <tr key={s.key + i}>
                <td><a href={s.url} target="_blank" rel="noreferrer">{s.title}</a><div className="sm muted">{s.notes}</div></td>
                <td><span className={`chip ${s.verified ? "ok" : "bad"}`}>{s.verified ? "verified" : "unverified"}</span></td>
                <td>{s.count}</td><td className="sm">{s.window.join(" → ")}</td><td className="sm">{s.licence}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="sm muted" style={{ marginTop: 8 }}>Retrieved {data.retrieved.slice(0, 10)}. Landslide records are missing: the only free catalogue (NASA COOLR) was unreachable from our network.</div>
      </div>
    </div>
  );
}
