import { useState } from "react";
import { api, ApiError, usePoll, type ScenarioInfo } from "../api";

const HAZARD: Record<string, string> = { flood: "🌊 Flood", landslide: "⛰ Landslide", earthquake: "〽 Earthquake" };

interface Replay { state: string; scenario: string | null; sent: number; total: number; error?: string | null }
interface Red { attacks: number; passed: number; blocked: number; should_block: number; catch_rate: number; rows: { attack: string; outcome: string; passed: boolean; reason: string }[] }

/* Practice with real past disasters, safely. A drill replays recorded sensor readings (for example the
 * Ratnapura river during Cyclone Ditwah, November 2025) at high speed so you can watch ATLAS respond,
 * then answer its decisions yourself. */
export function PracticePage() {
  const scenarios = usePoll<ScenarioInfo[]>("/play/scenarios", 0);
  const status = usePoll<Replay>("/play/scenarios/status", 1200);
  const [speed, setSpeed] = useState(30);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [red, setRed] = useState<Red | null>(null);
  const running = status.data?.state === "running";

  const run = async (name: string, fn: () => Promise<void>) => {
    setBusy(name); setMsg(null);
    try { await fn(); } catch (e) { setMsg(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(null); }
  };

  return (
    <div className="page ops">
      <p className="lead">Replay a real past disaster to see how ATLAS responds, and practise making the decisions. Drills use recorded data; nothing is sent to the public.</p>
      {msg && <div className="banner warn">{msg}</div>}
      {running && (
        <div className="banner info">
          <b>Drill running:</b> {status.data?.sent} of {status.data?.total} readings replayed.
          <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${((status.data?.sent ?? 0) / Math.max(1, status.data?.total ?? 1)) * 100}%` }} /></div>
          <div className="row" style={{ marginTop: 8 }}>
            <a className="btn" href="#/overview">Watch the overview</a><a className="btn" href="#/map">Watch the map</a><a className="btn" href="#/decisions">Go to decisions</a>
            <button className="btn danger" onClick={() => run("stop", async () => { await api("/play/scenarios/stop", { method: "POST" }); status.reload(); })}>Stop drill</button>
          </div>
        </div>
      )}
      {status.data?.state === "finished" && <div className="banner ok">Drill finished. Check <a href="#/decisions">Decisions</a> for anything waiting, and <a href="#/alerts">Alerts</a> for what went out.</div>}

      <div className="row" style={{ margin: "12px 0" }}>
        <label className="f">Replay speed
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            <option value={10}>10× (slow, easy to follow)</option><option value={30}>30× (a few minutes)</option><option value={60}>60× (fastest)</option>
          </select>
        </label>
      </div>
      <div className="grid g3">
        {(scenarios.data ?? []).map((s) => (
          <div key={s.key} className="card drill">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b>{s.title}</b>
              {s.real ? <span className="chip ok">real recorded data</span> : <span className="chip">made-up example</span>}
            </div>
            {s.hazard && <div><span className={`chip hz-${s.hazard}`}>{HAZARD[s.hazard] ?? s.hazard}</span></div>}
            <p>{s.story}</p>
            <p className="muted sm">About {Math.max(1, Math.round((s.replay_minutes * 60) / speed / 60))} min at this speed · {s.events} readings</p>
            <button className="btn primary big" disabled={running || busy !== null} onClick={() => run("start", async () => { await api("/play/scenarios/start", { method: "POST", json: { scenario: s.key, speed } }); status.reload(); })}>Start this drill</button>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Test the safety rules</h2>
        <p>Tries {red?.attacks ?? 16} ways to trick ATLAS into doing something it must not: an agent acting outside its job, skipping approval, acting on stale information, sending the same order twice. Each should be refused.</p>
        <button className="btn" disabled={busy !== null} onClick={() => run("red", async () => setRed(await api<Red>("/play/redteam", { method: "POST" })))}>{busy === "red" ? "Testing…" : "Run the safety test"}</button>
        {red && (
          <>
            <div className={`banner ${red.catch_rate === 1 ? "ok" : "bad"}`} style={{ marginTop: 10 }}>
              <b>{red.passed} of {red.attacks} handled correctly.</b> {red.catch_rate === 1 ? "Every attempt that should be refused was refused." : "Some attempts got through: tell the technical team."}
            </div>
            <details><summary>See each attempt</summary>
              <table className="t"><tbody>{red.rows.map((r, i) => <tr key={i}><td>{r.passed ? "✓" : "✕"}</td><td>{r.attack}</td><td className="muted sm">{r.outcome}</td></tr>)}</tbody></table>
            </details>
          </>
        )}
      </div>
    </div>
  );
}
