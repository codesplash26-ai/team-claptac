import { useState } from "react";
import { api, ApiError, usePoll, type EvalRun, type EvalScore, type EvaluatorInfo, type Monitor, type ScenarioInfo } from "../api";

interface Experiment {
  id: string;
  variant: { label: string };
  scenario: string;
  regressions: string[];
  critical_regressions: string[];
  evaluators: { evaluator: string; critical: boolean; baseline_pass_rate: number | null; variant_pass_rate: number | null; regressed: boolean }[];
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export function EvaluationsPage() {
  const catalogue = usePoll<EvaluatorInfo[]>("/am/v1/evaluators", 0);
  const runs = usePoll<EvalRun[]>("/am/v1/evaluations", 4000);
  const monitors = usePoll<Monitor[]>("/am/v1/monitors", 4000);
  const scenarios = usePoll<ScenarioInfo[]>("/play/scenarios", 0);
  const [failed, setFailed] = useState<EvalScore[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [exp, setExp] = useState<Experiment | null>(null);
  const [form, setForm] = useState({ scenario: "", human: "reject", strict: true, drop: "" });
  const latest = runs.data?.[0];

  const guard = async (name: string, fn: () => Promise<void>) => {
    setBusy(name);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(null);
    }
  };

  const evaluateNow = () => guard("run", async () => {
    const r = await api<{ run_id: string }>("/am/v1/evaluations", { method: "POST", json: {} });
    setFailed(await api<EvalScore[]>(`/am/v1/evaluations/${r.run_id}/scores?failed_only=true`));
    runs.reload();
  });

  const chosenScenario = form.scenario || scenarios.data?.[0]?.key || "";
  return (
    <div className="page">
      {msg && <div className="err" style={{ marginBottom: 8 }}>{msg}</div>}
      <div className="grid g4" style={{ marginBottom: 12 }}>
        <div className="card"><div className="kpi">{pct(latest?.summary.pass_rate)}<small>checks passing in the latest run</small></div></div>
        <div className="card"><div className="kpi" style={{ color: latest?.summary.critical_failures ? "var(--bad)" : undefined }}>{latest?.summary.critical_failures ?? "—"}<small>critical safety failures</small></div></div>
        <div className="card"><div className="kpi">{latest?.traces ?? "—"}<small>traces scored</small></div></div>
        <div className="card row"><button className="btn primary" disabled={busy !== null} onClick={evaluateNow}>{busy === "run" ? "Evaluating…" : "Evaluate live traces now"}</button></div>
      </div>

      {failed && (
        <div className="card" style={{ marginBottom: 12 }}>
          <h2>Failures in that run · {failed.length}</h2>
          {failed.length === 0 ? <div className="chip ok">Every check passed.</div> : (
            <table className="t"><thead><tr><th>Check</th><th>Level</th><th /><th>What happened</th></tr></thead>
              <tbody>{failed.map((f, i) => <tr key={i}><td>{f.evaluator}</td><td>{f.level}</td><td>{f.critical && <span className="chip bad">critical</span>}</td><td className="sm">{f.detail}</td></tr>)}</tbody></table>
          )}
        </div>
      )}

      <div className="grid g2">
        <div className="card">
          <h2>Evaluators · what each one checks</h2>
          <table className="t">
            <thead><tr><th>Name</th><th>Level</th><th>Target</th><th /></tr></thead>
            <tbody>
              {(catalogue.data ?? []).map((e) => (
                <tr key={e.name} title={e.description}>
                  <td><b className="sm">{e.name}</b><div className="sm muted">{e.description}</div></td>
                  <td><span className="chip">{e.level}</span></td>
                  <td className="sm">{e.threshold == null ? "—" : `${e.threshold} ${e.unit}`}</td>
                  <td>{e.critical && <span className="chip bad">critical</span>}{!e.builtin && <span className="chip acc">custom</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="card">
            <h2>Monitors · evaluate live traces on a schedule</h2>
            {(monitors.data ?? []).length === 0 && <div className="muted sm">No monitors. Create one to keep watching the safety checks.</div>}
            {(monitors.data ?? []).map((m) => (
              <div key={m.id} className="row sm" style={{ padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
                <b>{m.name}</b><span className="chip">every {m.interval_s}s</span><span className="chip">{m.evaluators.join(", ")}</span>
                <span className={`chip ${m.enabled ? "ok" : ""}`}>{m.enabled ? "on" : "paused"}</span>
                <span style={{ marginLeft: "auto" }} className="row">
                  <a onClick={() => guard("mon", async () => { await api(`/am/v1/monitors/${m.id}`, { method: "PATCH", json: { enabled: !m.enabled } }); monitors.reload(); })}>{m.enabled ? "pause" : "resume"}</a>
                  <a onClick={() => guard("mon", async () => { await api(`/am/v1/monitors/${m.id}`, { method: "DELETE" }); monitors.reload(); })}>delete</a>
                </span>
              </div>
            ))}
            <button className="btn" style={{ marginTop: 8 }} disabled={busy !== null} onClick={() => guard("mon", async () => {
              await api("/am/v1/monitors", { method: "POST", json: { name: "safety watch", evaluators: (catalogue.data ?? []).filter((e) => e.critical).map((e) => e.name), interval_seconds: 30 } });
              monitors.reload();
            })}>Add a safety monitor (every 30 s)</button>
          </div>

          <div className="card">
            <h2>Recent evaluation runs</h2>
            <table className="t">
              <thead><tr><th>When</th><th>Trigger</th><th>Passed</th><th>Critical</th></tr></thead>
              <tbody>{(runs.data ?? []).slice(0, 8).map((r) => (
                <tr key={r.id}><td className="sm">{new Date(r.at * 1000).toLocaleTimeString()}</td><td className="sm">{r.trigger}</td>
                  <td>{r.summary.passed}/{r.summary.scored} ({pct(r.summary.pass_rate)})</td>
                  <td>{r.summary.critical_failures ? <span className="chip bad">{r.summary.critical_failures}</span> : <span className="muted">0</span>}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Experiment · replay a disaster with a change, score both, see what regressed</h2>
          <div className="row">
            <label className="f">Scenario
              <select value={chosenScenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>{(scenarios.data ?? []).map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}</select>
            </label>
            <label className="f">Human IC
              <select value={form.human} onChange={(e) => setForm({ ...form, human: e.target.value })}><option value="approve">approves</option><option value="reject">rejects</option><option value="ignore">never answers</option></select>
            </label>
            <label className="f">Lose a sensor
              <select value={form.drop} onChange={(e) => setForm({ ...form, drop: e.target.value })}><option value="">none</option><option value="water_level">water level</option><option value="rainfall">rainfall</option><option value="soil_moisture">soil moisture</option></select>
            </label>
            <label className="sm"><input type="checkbox" checked={form.strict} onChange={(e) => setForm({ ...form, strict: e.target.checked })} /> stricter policy</label>
            <button className="btn primary" disabled={busy !== null || !chosenScenario} onClick={() => guard("exp", async () => setExp(await api<Experiment>("/am/v1/experiments", {
              method: "POST", json: { scenario: chosenScenario, variant: { label: "console", human: form.human, strict_policy: form.strict, drop_types: form.drop ? [form.drop] : [] } } })))}>{busy === "exp" ? "Running two replays…" : "Run experiment"}</button>
          </div>
          {exp && (
            <>
              <div className="row" style={{ margin: "8px 0" }}>
                <span className={`chip ${exp.regressions.length ? "warn" : "ok"}`}>{exp.regressions.length} regression(s)</span>
                <span className={`chip ${exp.critical_regressions.length ? "bad" : "ok"}`}>{exp.critical_regressions.length} critical</span>
              </div>
              <table className="t"><thead><tr><th>Check</th><th>As recorded</th><th>With the change</th><th /></tr></thead>
                <tbody>{exp.evaluators.map((r) => (
                  <tr key={r.evaluator} style={r.regressed ? { background: "var(--warn-soft)" } : undefined}><td className="sm">{r.evaluator}{r.critical && <span className="chip bad" style={{ marginLeft: 6 }}>critical</span>}</td>
                    <td>{pct(r.baseline_pass_rate)}</td><td><b>{pct(r.variant_pass_rate)}</b></td><td>{r.regressed && "▼ regressed"}</td></tr>
                ))}</tbody></table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
