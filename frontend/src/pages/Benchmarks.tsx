import { useEffect, useState } from "react";
import { api, token, usePoll, type Me } from "../api";
import "./benchmarks.css";

type Task = {id: string; title: string; description: string; steps: unknown[]; random_events: {count: number}; expectations: {metric: string; operator: string; value: number; reason: string}[]};
type Check = {metric: string; operator: string; expected: number; actual: number; passed: boolean; reason: string};
type Result = {case_id: string; title: string; seed: number; status: string; error: string | null; passed: boolean; duration_seconds: number; checks: Check[]; metrics: Record<string, Record<string, number>>};
type Run = {id: string; state: string; total?: number; completed?: number; passed?: number; error?: string; results: Result[]; current?: {case_id: string; title: string; step: number; total_steps: number; note?: string} | null};
const running = (s?: string) => ["starting", "running", "cancelling"].includes(s ?? "");
const sum = (values: Record<string, number>) => Object.values(values).reduce((a, b) => a + b, 0);
const caseURL = (run: string, id: string) => `#/benchmarks/${encodeURIComponent(run)}/${encodeURIComponent(id)}`;
async function download(url: string, filename: string) {
  const response = await fetch(url, {headers: token.get() ? {"X-Operator-Token": token.get()} : {}});
  if (!response.ok) throw new Error(`Download unavailable (${response.status}). Try again after the run finishes.`);
  const blob = URL.createObjectURL(await response.blob());
  const a = document.createElement("a"); a.href = blob; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(blob), 10000);
}
export function BenchmarksPage({selectedRun}: {selectedRun?: string}) {
  const catalog = usePoll<Task[]>("/benchmark/tasks", 0);
  const history = usePoll<Run[]>("/benchmark/runs", 3000);
  const me = usePoll<Me>("/am/v1/me", 10000);
  const run = usePoll<Run>(selectedRun ? `/benchmark/runs/${encodeURIComponent(selectedRun)}` : null, 1000);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [seeds, setSeeds] = useState("42");
  const [random, setRandom] = useState(true);
  const [pace, setPace] = useState(500);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState(token.get());
  const [cancelled, setCancelled] = useState(false);
  const tasks = catalog.data ?? [], chosen = selected ?? tasks.map(t => t.id);
  const state = run.data, results = state?.results ?? [];
  const active = history.data?.find(r => running(r.state));
  const canRun = me.data?.permissions.includes("scenario:run") ?? false;
  const seedValues = seeds.split(",").map(s => s.trim());
  const validSeeds = seedValues.length <= 10 && seedValues.every(s => /^\d+$/.test(s) && Number(s) <= 2147483647) && new Set(seedValues.map(Number)).size === seedValues.length;
  const agents = new Set(results.flatMap(r => Object.keys(r.metrics.agent_activity).filter(a => /^a[1-9]_/.test(a))));
  useEffect(() => {if (state && !running(state.state)) void history.reload();}, [state?.state]);
  async function act(fn: () => Promise<unknown>) {
    setError(""); setBusy(true);
    try {await fn();} catch (e) {setError(e instanceof Error ? e.message : String(e));} finally {setBusy(false);}
  }
  async function start() {
    const next = await api<{id: string}>("/benchmark/runs", {method: "POST", json: {task_ids: chosen, seeds: seedValues.map(Number), random_enabled: random, step_delay_ms: pace}});
    window.location.hash = `#/benchmarks/${next.id}`;
  }
  const artifact = (file: string) => act(() => download(`/benchmark/files/${encodeURIComponent(state!.id)}/${file}`, `${state!.id}-${file.split("/").join("-")}`));
  return <div className="page bm-page">
    <header className="bm-heading"><div><p className="bm-eyebrow">ATLAS / SIMULATION LAB</p><h1>Put the response to the test.</h1><p className="muted">Ten scenarios. Real agent execution. A trace for every decision.</p></div><span className="chip acc">Synthetic · offline · isolated</span></header>
    <div className="bm-note">Every case runs in its own environment. The seed fixes random inputs; the agents decide what happens next. Human responses are scripted, messages stay local, and the advisor uses offline templates.</div>
    {(error || catalog.error || history.error || run.error || me.error) && <p className="err" role="alert">{error || catalog.error || history.error || run.error || me.error}</p>}
    {(me.error || (me.data && !me.data.dev_mode)) && <details className="card bm-auth"><summary>Operator access</summary><div className="row"><input aria-label="Operator token" type="password" autoComplete="off" value={auth} onChange={e => setAuth(e.target.value)} placeholder="Operator token" /><button className="btn" onClick={() => {token.set(auth); void catalog.reload(); void history.reload(); void me.reload(); void run.reload();}}>Use token</button></div></details>}
    <details className="bm-builder card" open={!selectedRun}><summary className="bm-builder-toggle">Choose scenarios & configure a run</summary><div className="bm-section-head"><div><h2>Build a run</h2><p className="muted sm">Choose the situations and repeat them across seeds.</p></div><button className="btn" onClick={() => setSelected(chosen.length === tasks.length ? [] : tasks.map(t => t.id))}>{chosen.length === tasks.length ? "Clear selection" : "Select all"}</button></div>
      <div className="bm-task-grid">{tasks.map((t, index) => <div className={`bm-task ${chosen.includes(t.id) ? "chosen" : ""}`} key={t.id}>
        <label><input type="checkbox" checked={chosen.includes(t.id)} onChange={e => setSelected(e.target.checked ? [...chosen, t.id] : chosen.filter(id => id !== t.id))} /><span className="bm-number">{String(index + 1).padStart(2, "0")}</span><strong>{t.title}</strong></label><p className="muted sm">{t.description}</p>
        <details><summary>{t.steps.length} scripted steps · {t.expectations.length} scenario checks</summary><ul>{t.expectations.map((e, i) => <li key={i}>{e.reason}<small className="mono">{e.metric} {e.operator} {e.value}</small></li>)}</ul></details>
      </div>)}</div>{catalog.loading && <p className="muted">Loading scenarios…</p>}
      <div className="bm-controls"><label className="f">Random seeds<input value={seeds} onChange={e => setSeeds(e.target.value)} placeholder="42, 43, 44" /><small className="muted">1–10 unique integers, separated by commas</small></label>
        <label className="f">Playback pace<select value={pace} onChange={e => setPace(Number(e.target.value))}><option value={0}>Fast · collect results</option><option value={500}>Watch · 0.5s between inputs</option><option value={1000}>Slow · 1s between inputs</option></select><small className="muted">Pacing does not change simulated time</small></label>
        <label className="bm-toggle"><input type="checkbox" checked={random} onChange={e => setRandom(e.target.checked)} /> Inject seeded random events<small className="muted">Extra readings, delays and duplicate messages</small></label></div>
      {!validSeeds && <p className="err">Enter unique integer seeds from 0 to 2147483647.</p>}
      <div className="bm-section-head"><p className="muted sm">{chosen.length} scenarios × {validSeeds ? seedValues.length : "?"} seeds · {random ? "random disruptions on" : "scripted inputs only"}</p><button className="btn primary" disabled={busy || !chosen.length || !validSeeds || !canRun || !!active || running(state?.state)} onClick={() => void act(start)}>{busy ? "Working…" : `Run ${chosen.length * seedValues.length} simulations →`}</button></div>
      {active && <p className="muted sm">A benchmark is already running. <a href={`#/benchmarks/${active.id}`}>View its progress →</a></p>}{me.data && !canRun && <p className="muted sm">Starting or cancelling a run requires scenario-run permission.</p>}
    </details>
    <section className="bm-results"><div className="bm-section-head"><div><p className="bm-eyebrow">RECORDED RUNS</p><h2>Results & activity</h2></div><label className="f">Run history<select value={selectedRun ?? ""} onChange={e => {window.location.hash = e.target.value ? `#/benchmarks/${e.target.value}` : "#/benchmarks";}}><option value="">Choose a run</option>{selectedRun && !history.data?.some(r => r.id === selectedRun) && <option value={selectedRun}>{selectedRun}</option>}{history.data?.map(r => <option key={r.id} value={r.id}>{r.id} · {r.state} · {r.passed ?? 0}/{r.total ?? "?"} passed</option>)}</select></label></div>
      {!selectedRun && <div className="card empty"><h3>A complete record, ready to inspect</h3><p className="muted">Start a run or choose a saved run. Compare expected behaviour with observed results, then follow a case through its timeline.</p></div>}
      {selectedRun && !state && <p className="muted">Loading run…</p>}
      {state && <><div className="bm-run-title"><span className={`chip ${state.state === "completed" ? "ok" : running(state.state) ? "acc" : "warn"}`}>{state.state}</span><span className="mono muted sm">{state.id}</span>{state.error && <p className="err">{state.error}</p>}</div>
        <div className="bm-stats"><div className="card"><strong>{state.completed ?? 0}<small>/{state.total ?? "…"}</small></strong><span>cases recorded</span></div><div className="card"><strong className="bm-green">{results.filter(r => r.passed).length}</strong><span>all checks passed</span></div><div className="card"><strong>{results.filter(r => !r.passed).length}</strong><span>failed / incomplete</span></div><div className="card"><strong>{agents.size}<small>/9</small></strong><span>core agents exercised</span></div></div>
        {state.current && <div className="card bm-progress" role="status"><div className="bm-section-head"><div><strong>{state.current.title}</strong><p className="muted sm">Input {state.current.step} / {state.current.total_steps} · {state.current.note}</p></div><a className="btn primary" href={caseURL(state.id, state.current.case_id)}>Watch timeline →</a></div><progress value={state.current.step} max={state.current.total_steps || 1} /></div>}
        <div className="row bm-downloads">{running(state.state) ? <button className="btn" disabled={busy || cancelled || !canRun} onClick={() => void act(async () => {await api(`/benchmark/runs/${state.id}/cancel`, {method: "POST"}); setCancelled(true);})}>{cancelled ? "Stopping after current input…" : "Stop run & save partial traces"}</button> : <>{["artifacts.zip", "results.csv", "summary.json"].map(file => <button className={`btn ${file.endsWith("zip") ? "primary" : ""}`} disabled={busy} key={file} onClick={() => void artifact(file)}>{file.endsWith("zip") ? "Download all traces & reports (.zip)" : file}</button>)}</>}</div>
        {results.map(r => <article className="card bm-result" key={r.case_id}><div className="bm-section-head"><div><span className={`chip ${r.passed ? "ok" : "bad"}`}>{r.passed ? "PASS" : r.status === "completed" ? "FAIL" : r.status.toUpperCase()}</span><h3>{r.title}</h3><p className="muted sm">Seed {r.seed} · {r.duration_seconds}s wall time · {r.checks.filter(c => c.passed).length}/{r.checks.length} checks</p></div><a className="btn" href={caseURL(state.id, r.case_id)}>Explore timeline →</a></div>
          <div className="bm-result-counts"><span><b>{sum(r.metrics.events)}</b> events</span><span><b>{r.metrics.inputs.random}</b> random inputs</span><span><b>{r.metrics.missions.total}</b> missions</span><span><b>{r.metrics.incidents.replans}</b> replans</span><span><b>{r.metrics.reasoning.planner_runs}</b> planner traces</span></div>{r.error && <p className="err">{r.error}</p>}
          <details open={!r.passed}><summary>Expected behaviour & checks</summary><div className="bm-checks">{r.checks.map((c, i) => <div key={i}><span className={`chip ${c.passed ? "ok" : "bad"}`}>{c.passed ? "✓" : "!"}</span><p>{c.reason}<small className="muted mono">{c.metric} · expected {c.operator} {c.expected} · observed {c.actual}</small></p></div>)}</div></details>
          <div className="row bm-artifacts">{["result.json", "schedule.json", "inputs.jsonl", "events.jsonl", "reasoning.json"].map(file => <button className="btn" disabled={busy} key={file} onClick={() => void artifact(`${r.case_id}/${file}`)}>{file}</button>)}</div>
        </article>)}<p className="muted sm">Unzip the bundle and open report.html to browse every linked report and trace. Passing means the declared checks passed; it does not establish field readiness or live-model quality.</p></>}
    </section>
    <section className="card bm-template"><div><p className="bm-eyebrow">ADD YOUR OWN TASK</p><h2>One template. A repeatable experiment.</h2><p className="muted">Copy the template into data/benchmarks/tasks, give it a unique ID, and define timed inputs, initial conditions, random events and expected outcomes. New tasks appear here after validation.</p><pre>.venv/bin/python -m atlas.benchmark validate</pre></div><div className="row"><button className="btn" disabled={busy} onClick={() => void act(() => download("/benchmark/template", "task-template.json"))}>Task template ↓</button><button className="btn" disabled={busy} onClick={() => void act(() => download("/benchmark/schema", "task.schema.json"))}>JSON schema ↓</button></div></section>
  </div>;
}
