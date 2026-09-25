import { useState } from "react";
import { api, ApiError, token, usePoll, type ScenarioInfo } from "../api";

interface Replay { state: string; scenario?: string; speed?: number; sent?: number; total?: number; error?: string | null }
interface Red { rows: { attack: string; expected: string; outcome: string; reason: string; passed: boolean }[]; attacks: number; passed: number; blocked: number; should_block: number; catch_rate: number }
interface Outcome { triggers: number; incidents: number; cycles: number; needs_human: boolean; final_states: string[]; missions: number; alerts: number; actions: number; actions_by_status: Record<string, number>; pending_approvals: number; notes: string[] }
interface WhatIfResult { baseline: Outcome; changed: Outcome; diff: Record<string, { baseline: unknown; variant: unknown }> }

const show = (v: unknown) => (typeof v === "object" ? JSON.stringify(v) : String(v));

export function Playground() {
  const scenarios = usePoll<ScenarioInfo[]>("/play/scenarios", 0);
  const status = usePoll<Replay>("/play/scenarios/status", 1200);
  const [pick, setPick] = useState("");
  const [speed, setSpeed] = useState(1);
  const [tok, setTok] = useState(token.get());
  const [msg, setMsg] = useState<string | null>(null);
  const [red, setRed] = useState<Red | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [wi, setWi] = useState<WhatIfResult | null>(null);
  const [variant, setVariant] = useState({ label: "what if", drop_types: [] as string[], drop_fraction: 0, human: "approve", strict_policy: false });
  const [reading, setReading] = useState({ node_id: "manual-node", sensor_id: "wl-01", type: "water_level", value: 26, unit: "cm", area_id: "ratnapura-demo-river" });

  const chosen = scenarios.data?.find((s) => s.key === (pick || scenarios.data?.[0]?.key));
  const run = async (name: string, fn: () => Promise<void>) => {
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
  const running = status.data?.state === "running";

  return (
    <div className="page">
      <div className="card row" style={{ marginBottom: 12 }}>
        <label className="f" style={{ minWidth: 260 }}>Operator token (only if the server has ATLAS_OPERATOR_TOKEN set)
          <input type="password" value={tok} onChange={(e) => { setTok(e.target.value); token.set(e.target.value); }} placeholder="not required in development" />
        </label>
        {msg && <span className="err">{msg}</span>}
        <span className="muted sm" style={{ marginLeft: "auto" }}>Replay and inject change the live system. Red team and what-if run in throwaway sandboxes.</span>
      </div>

      <div className="grid g2">
        <div className="card">
          <h2>Replay a disaster into the live system</h2>
          <label className="f">Scenario
            <select value={chosen?.key ?? ""} onChange={(e) => setPick(e.target.value)}>
              {(scenarios.data ?? []).map((s) => <option key={s.key} value={s.key}>{s.real ? "● real · " : "○ synthetic · "}{s.title}</option>)}
            </select>
          </label>
          {chosen && (
            <div className="sm" style={{ margin: "8px 0" }}>
              <div>{chosen.story || "A synthetic rising-water story."}</div>
              <div className="muted">{chosen.events} readings · {chosen.replay_minutes} min at 1× · {chosen.hazard ?? "flood"} · {chosen.area_id}</div>
              {chosen.assumptions.length > 0 && <details><summary className="muted">Assumptions ({chosen.assumptions.length})</summary><ul>{chosen.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>}
            </div>
          )}
          <div className="row">
            <label className="f" style={{ width: 130 }}>Speed ×{speed}<input type="range" min={0.5} max={30} step={0.5} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} /></label>
            <button className="btn primary" disabled={!chosen || running || busy !== null} onClick={() => run("start", async () => { await api("/play/scenarios/start", { method: "POST", json: { scenario: chosen!.key, speed } }); status.reload(); })}>Start</button>
            <button className="btn danger" disabled={!running} onClick={() => run("stop", async () => { await api("/play/scenarios/stop", { method: "POST" }); status.reload(); })}>Stop</button>
          </div>
          <div className="sm muted" style={{ marginTop: 6 }}>Faster replay compresses the time the Sentinel sees, so its rate-of-rise rule becomes more sensitive. Keep it low for the real floods.</div>
          {status.data && status.data.state !== "idle" && (
            <div style={{ marginTop: 8 }}>
              <span className={`chip ${running ? "acc" : status.data.state === "error" ? "bad" : ""}`}>{status.data.state}</span>{" "}
              {status.data.scenario} · {status.data.sent}/{status.data.total} readings sent {status.data.error && <span className="err">{status.data.error}</span>}
              <div className="bar" style={{ marginTop: 6 }}><i style={{ left: 0, width: `${((status.data.sent ?? 0) / Math.max(status.data.total ?? 1, 1)) * 100}%` }} /></div>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Inject one reading (as if from a sensor node)</h2>
          <div className="grid g2">
            <label className="f">Node<input value={reading.node_id} onChange={(e) => setReading({ ...reading, node_id: e.target.value })} /></label>
            <label className="f">Area<input value={reading.area_id} onChange={(e) => setReading({ ...reading, area_id: e.target.value })} /></label>
            <label className="f">Type
              <select value={reading.type} onChange={(e) => setReading({ ...reading, type: e.target.value, unit: e.target.value === "rainfall" ? "mm" : e.target.value === "soil_moisture" ? "%" : "cm" })}>
                <option value="water_level">water level</option><option value="rainfall">rainfall</option><option value="soil_moisture">soil moisture</option>
              </select>
            </label>
            <label className="f">Value ({reading.unit})<input type="number" value={reading.value} onChange={(e) => setReading({ ...reading, value: Number(e.target.value) })} /></label>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn primary" disabled={busy !== null} onClick={() => run("inject", async () => {
              const q = new URLSearchParams({ node_id: reading.node_id, sensor_id: reading.sensor_id, type: reading.type, value: String(reading.value), unit: reading.unit, area_id: reading.area_id });
              await api(`/play/inject/now?${q}`, { method: "POST" });
              setMsg("Reading accepted. Open Runs or Graph to watch it.");
            })}>Send reading</button>
            <span className="sm muted">The Sentinel alarms at 25 cm, 20 mm of rain in its window, or 80% soil saturation.</span>
          </div>
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>Red team · try to make the system do what it must not</h2>
            <button className="btn primary" disabled={busy !== null} onClick={() => run("red", async () => setRed(await api<Red>("/play/redteam", { method: "POST" })))}>{busy === "red" ? "Running…" : "Run attacks"}</button>
          </div>
          {red && (
            <>
              <div className="row" style={{ margin: "8px 0" }}>
                <span className={`chip ${red.catch_rate === 1 ? "ok" : "bad"}`}>out-of-scope actions blocked: {red.blocked}/{red.should_block} ({Math.round(red.catch_rate * 100)}%)</span>
                <span className="chip">{red.passed}/{red.attacks} outcomes exactly as policy promises</span>
                <span className="chip ok">nothing was executed</span>
              </div>
              <table className="t">
                <thead><tr><th>Attack</th><th>Policy says</th><th>Outcome</th><th>Reason</th><th /></tr></thead>
                <tbody>{red.rows.map((r) => (
                  <tr key={r.attack}><td>{r.attack}</td><td>{r.expected}</td><td><span className={`chip ${r.outcome === "blocked" ? "bad" : r.outcome === "escalated" ? "warn" : "ok"}`}>{r.outcome}</span></td><td className="mono sm">{r.reason}</td><td>{r.passed ? "✓" : "✗"}</td></tr>
                ))}</tbody>
              </table>
            </>
          )}
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>What-if · replay the same disaster with one thing changed</h2>
          <div className="grid g4">
            <label className="f">Scenario
              <select value={chosen?.key ?? ""} onChange={(e) => setPick(e.target.value)}>{(scenarios.data ?? []).map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}</select>
            </label>
            <label className="f">The Human IC…
              <select value={variant.human} onChange={(e) => setVariant({ ...variant, human: e.target.value })}>
                <option value="approve">approves every escalation</option><option value="reject">rejects every escalation</option><option value="ignore">never answers</option>
              </select>
            </label>
            <label className="f">Sensors lost
              <span className="row">{["water_level", "rainfall", "soil_moisture"].map((t) => (
                <label key={t} className="sm"><input type="checkbox" checked={variant.drop_types.includes(t)} onChange={(e) => setVariant({ ...variant, drop_types: e.target.checked ? [...variant.drop_types, t] : variant.drop_types.filter((x) => x !== t) })} /> {t.replace("_", " ")}</label>
              ))}</span>
            </label>
            <label className="f">Link loses {Math.round(variant.drop_fraction * 100)}% of readings
              <input type="range" min={0} max={0.9} step={0.05} value={variant.drop_fraction} onChange={(e) => setVariant({ ...variant, drop_fraction: Number(e.target.value) })} />
            </label>
          </div>
          <div className="row" style={{ margin: "8px 0" }}>
            <label className="sm"><input type="checkbox" checked={variant.strict_policy} onChange={(e) => setVariant({ ...variant, strict_policy: e.target.checked })} /> Stricter policy: every mission needs a human</label>
            <button className="btn primary" disabled={busy !== null || !chosen} onClick={() => run("whatif", async () => setWi(await api<WhatIfResult>("/play/whatif", { method: "POST", json: { scenario: chosen!.key, variant } })))}>{busy === "whatif" ? "Running two replays…" : "Compare"}</button>
            <span className="sm muted">Runs the disaster twice in a sandbox (takes a few seconds for the real ones).</span>
          </div>
          {wi && (
            <table className="t">
              <thead><tr><th>Outcome</th><th>As recorded</th><th>With your change</th></tr></thead>
              <tbody>
                {(["triggers", "cycles", "missions", "alerts", "actions", "needs_human", "pending_approvals"] as const).map((k) => (
                  <tr key={k} style={wi.diff[k] ? { background: "var(--warn-soft)" } : undefined}><td>{k.replace("_", " ")}</td><td>{show(wi.baseline[k])}</td><td><b>{show(wi.changed[k])}</b></td></tr>
                ))}
                <tr style={wi.diff.actions_by_status ? { background: "var(--warn-soft)" } : undefined}><td>actions by status</td><td className="sm">{show(wi.baseline.actions_by_status)}</td><td className="sm"><b>{show(wi.changed.actions_by_status)}</b></td></tr>
              </tbody>
            </table>
          )}
          {wi && Object.keys(wi.diff).length === 0 && <div className="muted sm" style={{ marginTop: 6 }}>No difference: the system's response did not depend on that change.</div>}
        </div>
      </div>
    </div>
  );
}
