import { fmtMs, shortAgent, usePoll, type Metrics } from "../api";

function Bars({ rows, unit }: { rows: { label: string; a: number; b?: number; note?: string }[]; unit: string }) {
  const max = Math.max(...rows.map((r) => r.b ?? r.a), 1e-9);
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="brow" key={r.label}>
          <span title={r.label}>{r.label}</span>
          <span className="track">
            {r.b !== undefined && <i style={{ width: `${(r.b / max) * 100}%` }} />}
            <b style={{ width: `${(r.a / max) * 100}%` }} />
          </span>
          <span className="sm mono">{r.note ?? `${r.a.toFixed(1)} ${unit}`}</span>
        </div>
      ))}
    </div>
  );
}

export function MetricsPage() {
  const { data, error } = usePoll<Metrics>("/dev/metrics", 3000);
  if (error) return <div className="page"><div className="err">{error}</div></div>;
  if (!data) return <div className="page muted">Loading…</div>;
  const handlers = data.agents.filter((a) => a.kind === "handler");
  const executors = data.agents.filter((a) => a.kind === "executor");
  const c = data.control;
  const types = Object.entries(data.events_by_type);
  const totalEvents = types.reduce((s, [, n]) => s + n, 0);
  const totalRuns = data.agents.reduce((s, a) => s + a.runs, 0);
  const errors = data.agents.reduce((s, a) => s + a.errors, 0);

  return (
    <div className="page">
      <div className="grid g4" style={{ marginBottom: 12 }}>
        <div className="card"><div className="kpi">{totalEvents}<small>events on the bus</small></div></div>
        <div className="card"><div className="kpi">{totalRuns}<small>agent steps timed · {errors} failed</small></div></div>
        <div className="card"><div className="kpi">{c.autonomy_rate == null ? "—" : `${Math.round(c.autonomy_rate * 100)}%`}<small>actions run without a human (target: routine actions ≥ 85%)</small></div></div>
        <div className="card"><div className="kpi">{data.llm.calls}<small>LLM calls · {data.llm.tokens} tokens · ${data.llm.cost_usd.toFixed(2)}</small></div></div>
      </div>

      <div className="grid g2">
        <div className="card">
          <h2>Agent step latency · p50 (solid) and p95 (faint)</h2>
          <Bars unit="ms" rows={handlers.map((a) => ({ label: `${shortAgent(a.agent)} · ${a.runs} runs`, a: a.p50_ms, b: Math.max(a.p95_ms, a.p50_ms), note: `${fmtMs(a.p50_ms)} / ${fmtMs(a.p95_ms)}` }))} />
          <div className="sm muted" style={{ marginTop: 8 }}>
            Wall time of each handler, from the bus's own timers. The agents are deterministic, so steps are fast; the LLM, when used, appears as its own spans.
          </div>
        </div>
        <div className="card">
          <h2>Executor calls (Gateway → A8 / A9)</h2>
          {executors.length === 0 ? <div className="muted">None yet.</div> : (
            <Bars unit="ms" rows={executors.map((a) => ({ label: `${shortAgent(a.agent)} · ${a.runs} calls`, a: a.p50_ms, b: Math.max(a.p95_ms, a.p50_ms), note: `${fmtMs(a.p50_ms)} / ${fmtMs(a.p95_ms)}${a.errors ? ` · ${a.errors} failed` : ""}` }))} />
          )}
        </div>

        <div className="card">
          <h2>Latency from the trigger (simulated seconds)</h2>
          {data.latency.length === 0 ? <div className="muted">No incident has reached a trigger yet.</div> : (
            <table className="t">
              <thead><tr><th>Incident</th><th>Detect</th><th>Plan</th><th>Authorised</th><th>Alert</th></tr></thead>
              <tbody>
                {data.latency.map((l) => (
                  <tr key={l.incident_id}>
                    <td>{l.incident_id.replace("incident:", "")}</td>
                    <td>{l.detection_latency_seconds == null ? "—" : `${l.detection_latency_seconds.toFixed(1)} s`}</td>
                    {["plan_proposed", "authorised", "alert_sent"].map((m) => (
                      <td key={m}>{l.milestones[m] ? `+${l.milestones[m].sim_seconds_after_trigger.toFixed(1)} s` : "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="sm muted" style={{ marginTop: 6 }}>Target from the proposal: detection to trigger under 2 minutes, replanning under 2 minutes.</div>
        </div>

        <div className="card">
          <h2>Control boundary</h2>
          {c.actions === undefined ? <div className="muted">Not wired.</div> : (
            <div className="grid g2">
              <div><div className="kpi">{c.actions}<small>actions proposed</small></div></div>
              <div><div className="kpi">{c.blocked ?? 0}<small>blocked by policy</small></div></div>
              <div><div className="kpi">{c.escalated ?? 0}<small>sent to a human · {c.pending_approvals ?? 0} waiting</small></div></div>
              <div><div className="kpi">{c.median_decision_seconds == null ? "—" : `${c.median_decision_seconds.toFixed(1)} s`}<small>median human decision time</small></div></div>
              <div className="sm" style={{ gridColumn: "1 / -1" }}>
                {Object.entries(c.by_status ?? {}).map(([k, v]) => <span key={k} className="chip" style={{ marginRight: 4 }}>{k} {v}</span>)}
                <div className="muted" style={{ marginTop: 4 }}>
                  approved {c.resolved?.approved ?? 0} · edited {c.resolved?.edited ?? 0} · rejected {c.resolved?.rejected ?? 0}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Events by type</h2>
          <Bars unit="" rows={types.map(([t, n]) => ({ label: t, a: n, note: String(n) }))} />
        </div>
      </div>
    </div>
  );
}
