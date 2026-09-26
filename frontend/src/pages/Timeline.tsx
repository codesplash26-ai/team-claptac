import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtMs, shortAgent, usePoll, type TraceEvent } from "../api";
import { Json } from "../components/Json";
import "./timeline.css";

type Category = "input" | "work" | "reasoning" | "decision" | "outcome";
type Activity = {agent: string; kind: string; started_at: number; duration_ms: number; status: string; error: string | null; outputs: number};
type Entry = TraceEvent & {category: Category; activity: Activity[]; payload: Record<string, unknown>};
type Page = {events: Entry[]; has_older: boolean; oldest_seq: number | null};
type Chain = {ancestors: TraceEvent[]; descendants: TraceEvent[]};
type Planning = {action: {id: string} | null; configuration: {mode?: string; objective?: string}; simulations: number; model_calls: number; elapsed_seconds: number; stop_reason: string; fallback_used: boolean; assumptions: string[]; alternatives: {action: {id: string}; score?: number; mean?: number; worst?: number; samples?: number}[]};
type Advice = {source: string; concerns: string[]; questions: string[]; claims: {text: string; accepted: boolean; claim: {confidence: number | null; evidence_ids: string[]}; violations: {message: string}[]}[]; trace: {model: string | null; latency_ms: number | null; tokens_in: number | null; tokens_out: number | null; steps: {kind: string; text: string; refs: string[]}[]}};
const stages: {key: Category; label: string; icon: string}[] = [
  {key: "input", label: "Inputs", icon: "↓"}, {key: "work", label: "Agent work", icon: "◈"},
  {key: "reasoning", label: "Reasoning", icon: "◇"}, {key: "decision", label: "Decisions", icon: "✓"},
  {key: "outcome", label: "Outcomes", icon: "↗"},
];
const name = (s: string) => s === "intelligence_advisor" ? "Intelligence advisor" : s === "action_gateway" ? "Action Gateway" : s.replace(/_/g, " ");
const time = (s: string) => new Date(s).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
function title(e: Entry) {
  if (e.type === "reasoning_completed" && e.source === "intelligence_advisor") return `Cycle ${e.payload.cycle ?? "—"}: ${e.payload.source === "llm" ? "model advice" : "snapshot analysis"} ready`;
  if (e.type === "reasoning_completed" && e.payload.reasoning_id) {
    const action = e.payload.action as {id?: string} | null;
    return `${shortAgent(e.source)} evaluated alternatives${action?.id ? `: ${action.id.replace(/_/g, " ")}` : ""}`;
  }
  return e.summary;
}

// Display only explanations actually present in structured outputs; never invent agent thoughts.
function reasons(value: unknown, path = "", depth = 0): {path: string; text: string}[] {
  if (!value || typeof value !== "object" || depth > 5) return [];
  return Object.entries(value).flatMap(([k, v]) => {
    if (["claims", "trace"].includes(k)) return [];
    const p = path ? `${path}.${k}` : k;
    if (["reason", "rationale", "explanation", "recommendation", "summary"].includes(k) && typeof v === "string") return [{path: p, text: v}];
    return reasons(v, p, depth + 1);
  }).slice(0, 30);
}

function EventDetails({entry, onSelect, basePath, evidencePath}: {entry: Entry; onSelect: (id: string) => void; basePath: string; evidencePath: string}) {
  const [chain, setChain] = useState<Chain | null>(null);
  const [evidence, setEvidence] = useState<{title: string; value: unknown} | null>(null);
  const [error, setError] = useState("");
  const [planning, setPlanning] = useState<Planning | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    let alive = true;
    api<Chain>(`${basePath}/chain/${encodeURIComponent(entry.id)}`).then(x => {if (alive) setChain(x);}).catch(e => {if (alive) setError(String(e));});
    return () => {alive = false;};
  }, [entry, basePath]);
  useEffect(() => {
    let alive = true;
    if (entry.payload.reasoning_id) api<Planning>(`${basePath}/timeline/${encodeURIComponent(entry.id)}/planning`).then(p => {if (alive) setPlanning(p);}).catch(e => {if (alive) setError(String(e));});
    return () => {alive = false; generation.current++;};
  }, [entry.id, entry.payload.reasoning_id, basePath]);
  async function inspect(url: string, title: string) {
    const request = ++generation.current;
    setError(""); setEvidence(null);
    try {const value = await api(url); if (request === generation.current) setEvidence({title, value});}
    catch (e) {if (request === generation.current) setError(String(e));}
  }
  const recorded = reasons(entry.payload);
  const advice = entry.type === "reasoning_completed" && Array.isArray(entry.payload.claims) && entry.payload.trace
    ? entry.payload as unknown as Advice : null;
  return <aside className="tl-inspector" aria-label="Event details">
    <div className="tl-inspector-head"><span className={`chip tl-${entry.category}`}>{entry.type.replace(/_/g, " ")}</span>
      <h2>{title(entry)}</h2><p className="muted">{name(entry.source)} · ledger #{entry.seq}</p></div>
    <section><h3>Event input</h3><dl className="kv"><dt>Event time</dt><dd>{new Date(entry.created_at).toLocaleString()}</dd><dt>Incident</dt><dd>{entry.incident_id ?? "Before incident assignment / standalone input"}</dd><dt>Source</dt><dd>{entry.raw_source}</dd></dl>
      {entry.caused_by && <button className="btn" onClick={() => onSelect(entry.caused_by!)}>← Inspect causing event</button>}
      <details><summary>Full event payload</summary><Json value={entry.payload} /></details>
    </section>
    <section><h3>Agent work on this event</h3>
      {entry.activity.length ? entry.activity.map((a, i) => <div className="tl-activity" key={`${a.agent}:${a.started_at}:${i}`}>
        <div className="row"><strong>{name(a.agent)}</strong><span className={`chip ${a.status === "error" ? "bad" : "ok"}`}>{a.status}</span></div>
        <p className="muted sm">{a.kind} · {fmtMs(a.duration_ms)} · {a.outputs} output events · {new Date(a.started_at * 1000).toLocaleTimeString()}</p>
        {a.error && <p className="err">{a.error}</p>}
        <button className="btn" onClick={() => inspect(`${basePath}/span?id=${encodeURIComponent(`${entry.id}:${a.agent}:${a.kind}`)}`, `${name(a.agent)} · input and output`)}>Inspect input / output</button>
      </div>) : <p className="muted">No handler execution recorded for this event. It may be an output for display or awaiting delivery.</p>}
    </section>
    <section><h3>Recorded reasoning</h3><p className="muted sm">Decision explanations and validation records. Durations above are measured execution times.</p>
      {recorded.map((r, i) => <div className="tl-reason" key={i}><small className="muted">{r.path}</small><p>{r.text}</p></div>)}
      {!recorded.length && !advice && !entry.payload.reasoning_id && <p className="muted">No explanation was recorded for this event. Inspect its payload or the events it caused.</p>}
      {entry.payload.reasoning_id != null && !planning && !error && <p className="muted">Loading planner trace…</p>}
      {planning && <><p className="chip acc">{planning.configuration.mode ?? "Planner"} · simulation-based analysis</p>
        <dl className="kv"><dt>Chosen action</dt><dd>{planning.action?.id.replace(/_/g, " ") ?? "None"}</dd><dt>Objective</dt><dd>{planning.configuration.objective ?? "Not recorded"}</dd><dt>Simulations</dt><dd>{planning.simulations}</dd><dt>Model calls</dt><dd>{planning.model_calls}</dd><dt>Duration</dt><dd>{fmtMs(planning.elapsed_seconds * 1000)}</dd><dt>Stopped because</dt><dd>{planning.stop_reason.replace(/_/g, " ")}</dd><dt>Fallback used</dt><dd>{planning.fallback_used ? "Yes" : "No"}</dd></dl>
        <h4>Alternatives considered</h4><p className="muted sm">Scores describe the planner's simulated outcomes under the assumptions below.</p>
        {planning.alternatives.map((a, i) => <div className="tl-reason" key={i}><strong>{a.action.id.replace(/_/g, " ")}</strong>{a.action.id === planning.action?.id && <span className="chip ok">Chosen</span>}<p className="muted sm">Score {a.score?.toFixed(3) ?? "—"} · mean {a.mean?.toFixed(3) ?? "—"} · worst {a.worst?.toFixed(3) ?? "—"} · {a.samples ?? 0} samples</p></div>)}
        {!!planning.assumptions.length && <><h4>Assumptions</h4><ul>{planning.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></>}
        <details><summary>Simulation trace and input state</summary><Json value={planning} /></details>
      </>}
      {advice && <>
        <p className="chip acc">{advice.source === "llm" ? `Model: ${advice.trace.model ?? "unspecified"}` : "Offline template · no model call"}</p>
        <p className="muted sm">{advice.trace.latency_ms != null && `${fmtMs(advice.trace.latency_ms)} analysis`}{advice.trace.tokens_in != null && ` · ${advice.trace.tokens_in} input / ${advice.trace.tokens_out ?? 0} output tokens`}</p>
        <ol className="tl-steps">{(advice.trace.steps ?? []).map((s, i) => <li key={i}><strong>{s.kind}</strong><p>{s.text}</p>{s.refs.map(ref => <button className="btn" key={ref} onClick={() => inspect(`${evidencePath}/${encodeURIComponent(ref)}`, "Evidence snapshot")}>View evidence</button>)}</li>)}</ol>
        <h4>Claim checks</h4>{advice.claims.map((c, i) => <div className="tl-reason" key={i}><span className={`chip ${c.accepted ? "ok" : "bad"}`}>{c.accepted ? "Verified against snapshot" : "Rejected claim"}</span><p>{c.accepted ? c.text : <s>{c.text}</s>}</p>
          {c.claim.confidence != null && <p className="muted sm">Confidence: {(c.claim.confidence * 100).toFixed(0)}%</p>}
          {c.violations.map((v, j) => <p key={j} className="err">{v.message}</p>)}
          {c.claim.evidence_ids.map(ref => <button className="btn" key={ref} onClick={() => inspect(`${evidencePath}/${encodeURIComponent(ref)}`, "Evidence snapshot")}>View evidence</button>)}</div>)}
        {!!advice.concerns?.length && <><h4>Uncertainty</h4><ul>{advice.concerns.map((s, i) => <li key={i}>{s}</li>)}</ul></>}
        {!!advice.questions?.length && <><h4>Questions for review</h4><ul>{advice.questions.map((s, i) => <li key={i}>{s}</li>)}</ul></>}
      </>}
    </section>
    <section><h3>Cause and effect</h3>{!chain && !error && <p className="muted">Loading links…</p>}
      {chain && <><h4>What led here · {chain.ancestors.length}</h4>{chain.ancestors.map(e => <button className="tl-link" key={e.id} onClick={() => onSelect(e.id)}>#{e.seq} · {e.summary}</button>)}
        <h4>What followed · {chain.descendants.length}</h4>{chain.descendants.slice(0, 40).map(e => <button className="tl-link" key={e.id} onClick={() => onSelect(e.id)}>#{e.seq} · {e.summary}</button>)}
        {chain.descendants.length > 40 && <p className="muted">Showing the first 40 linked outcomes.</p>}
        {!chain.ancestors.length && !chain.descendants.length && <p className="muted">No causal links recorded.</p>}</>}
    </section>
    {error && <p className="err" role="alert">{error}</p>}
    {evidence && <section aria-live="polite"><h3>{evidence.title}</h3><Json value={evidence.value} /><button className="btn" onClick={() => setEvidence(null)}>Close detail</button></section>}
  </aside>;
}

export function TimelinePage({basePath = "/dev", evidencePath = "/intelligence/evidence"}: {basePath?: string; evidencePath?: string}) {
  const [page, setPage] = useState<Page>({events: [], has_older: false, oldest_seq: null});
  const [incident, setIncident] = useState("");
  const [agent, setAgent] = useState("");
  const [stage, setStage] = useState("");
  const [query, setQuery] = useState("");
  const [live, setLive] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [step, setStep] = useState<number | null>(null);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [olderBusy, setOlderBusy] = useState(false);
  const [updated, setUpdated] = useState("");
  const requestGeneration = useRef(0);
  const selectionGeneration = useRef(0);
  const incidents = usePoll<{incident_id: string; events: number}[]>(`${basePath}/incidents`, 5000);
  const base = `${basePath}/timeline?limit=200${incident ? `&incident_id=${encodeURIComponent(incident)}` : ""}`;
  useEffect(() => {
    let alive = true, pending = false;
    const generation = ++requestGeneration.current;
    async function fetchPage() {
      if (pending) return;
      pending = true;
      try {
        const data = await api<Page>(base);
        if (alive && generation === requestGeneration.current) {setPage(data); setSelected(s => s ? data.events.find(e => e.id === s.id) ?? s : null); setError(""); setUpdated(new Date().toLocaleTimeString());}
      } catch (e) {if (alive) setError(String(e));}
      finally {pending = false; if (alive) setLoading(false);}
    }
    // A paused timeline keeps its exact snapshot; changing incident re-enables live mode.
    if (live) void fetchPage();
    const timer = live ? setInterval(fetchPage, 2000) : undefined;
    return () => {alive = false; clearInterval(timer);};
  }, [base, live]);
  const filtered = useMemo(() => page.events.filter(e =>
    (!agent || e.source === agent || e.activity.some(a => a.agent === agent)) &&
    (!stage || e.category === stage) &&
    (!query || `${e.summary} ${e.type} ${e.raw_source} ${e.incident_id ?? ""} ${e.id} ${JSON.stringify(e.payload)}`.toLowerCase().includes(query.toLowerCase()))
  ), [page.events, agent, stage, query]);
  const agents = useMemo(() => [...new Set(page.events.flatMap(e => [e.source, ...e.activity.map(a => a.agent)]))].sort(), [page.events]);
  const current = step === null ? filtered.length - 1 : Math.min(step, filtered.length - 1);
  const visible = step === null ? filtered : filtered.slice(0, current + 1);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setStep(s => {
      const next = (s ?? -1) + 1;
      if (next >= filtered.length - 1) setPlaying(false);
      return Math.min(next, filtered.length - 1);
    }), 800);
    return () => clearInterval(timer);
  }, [playing, filtered.length]);
  useEffect(() => {
    if (step !== null && filtered[current]) {selectionGeneration.current++; setSelected(filtered[current]);}
  }, [current, step, filtered]);
  async function select(id: string) {
    const generation = ++selectionGeneration.current;
    const existing = page.events.find(e => e.id === id);
    if (existing) {setSelected(existing); return;}
    try {const data = await api<Entry>(`${basePath}/timeline/${encodeURIComponent(id)}`); if (generation === selectionGeneration.current) setSelected(data);}
    catch (e) {if (generation === selectionGeneration.current) setError(String(e));}
  }
  async function older() {
    setLive(false); setPlaying(false); setStep(null); setOlderBusy(true);
    const scope = base;
    try {const data = await api<Page>(`${base}&before_seq=${page.oldest_seq}`);
      if (scope === currentBase.current) setPage(p => ({...data, events: [...data.events, ...p.events]}));
    } catch (e) {if (scope === currentBase.current) setError(String(e));}
    finally {setOlderBusy(false);}
  }
  const currentBase = useRef(base); currentBase.current = base;
  const resetReplay = () => {setPlaying(false); setStep(null);};
  return <div className="page tl-page">
    <header className="tl-heading"><div><p className="tl-eyebrow">SYSTEM ACTIVITY</p><h1>Event timeline</h1><p className="muted">Follow an input through agent work, reasoning, decisions and outcomes.</p></div>
      <div className="row"><span className={`chip ${live && !error ? "ok" : "warn"}`}>{error ? "Connection issue" : live ? "● Live · every 2s" : playing ? "▶ Replaying history" : "Ⅱ Paused"}</span>
        <button className="btn" onClick={() => {setLive(!live); resetReplay();}}>{live ? "Pause updates" : "Resume live"}</button></div></header>
    <div className="tl-stages" aria-label="Timeline stages">{stages.map(s => <button key={s.key} aria-pressed={stage === s.key} className={`tl-stage tl-${s.key} ${stage === s.key ? "selected" : ""}`} onClick={() => {setStage(stage === s.key ? "" : s.key); resetReplay();}}><span>{s.icon} {s.label}</span><strong>{page.events.filter(e => e.category === s.key).length}</strong></button>)}</div>
    <div className="tl-controls card"><label className="f">Incident<select value={incident} onChange={e => {selectionGeneration.current++; setIncident(e.target.value); setPage({events: [], has_older: false, oldest_seq: null}); setSelected(null); setAgent(""); setLoading(true); setLive(true); resetReplay();}}><option value="">All incidents and inputs</option>{incidents.data?.map(i => <option key={i.incident_id} value={i.incident_id}>{i.incident_id}</option>)}</select></label>
      <label className="f">Agent<select value={agent} onChange={e => {setAgent(e.target.value); resetReplay();}}><option value="">All agents</option>{agents.map(a => <option key={a} value={a}>{name(a)}</option>)}</select></label>
      <label className="f tl-search">Search events<input value={query} onChange={e => {setQuery(e.target.value); resetReplay();}} placeholder="Reading, road, mission, event ID…" /></label>
      {(agent || stage || query) && <button className="btn" onClick={() => {setAgent(""); setStage(""); setQuery(""); resetReplay();}}>Clear filters</button>}
    </div>
    <div className="tl-replay card"><button className="btn" disabled={!filtered.length || olderBusy} onClick={() => {setLive(false); setPlaying(!playing); if (!playing && (step === null || current >= filtered.length - 1)) setStep(0);}}>{playing ? "Pause replay" : "Replay steps"}</button>
      <input aria-label="Timeline replay position" type="range" min={0} max={Math.max(0, filtered.length - 1)} value={Math.max(0, current)} disabled={!filtered.length} onChange={e => {setLive(false); setPlaying(false); setStep(Number(e.target.value));}} />
      <span className="sm mono">{Math.max(0, current + 1)} / {filtered.length}</span><button className="btn" onClick={resetReplay}>Show all steps</button></div>
    <p className="muted sm">Ledger order · oldest first · {page.events.length} loaded{updated && ` · refreshed ${updated}`}. Event times may be historical during a scenario replay. Agent work is shown after execution is recorded.</p>
    {(error || incidents.error) && <div role="alert" className="err">{error || incidents.error}</div>}
    <div className="tl-layout"><div className="tl-feed">
      {page.has_older && <button className="btn tl-older" disabled={olderBusy} onClick={older}>{olderBusy ? "Loading…" : "Load 200 older events (pauses live)"}</button>}
      {loading && <p className="empty">Loading recorded activity…</p>}
      {!loading && !visible.length && <div className="card empty"><h3>{page.events.length ? "No matching events" : "Waiting for the first input"}</h3><p>{page.events.length ? "Try clearing the filters." : "Run a scenario to watch the agents respond."}</p><a href="#/playground">Open Playground →</a></div>}
      <ol className="tl-list">{visible.map(e => <li key={e.id} className={`tl-item tl-${e.category}`}><div className="tl-rail"><span>{stages.find(s => s.key === e.category)?.icon}</span></div>
        <button className={`tl-event ${selected?.id === e.id ? "selected" : ""}`} aria-pressed={selected?.id === e.id} onClick={() => void select(e.id)}>
          <div className="tl-event-meta"><span>{name(e.source)}</span><time dateTime={e.created_at}>{time(e.created_at)} · #{e.seq}</time></div>
          <h3>{title(e)}</h3><div className="row"><span className="chip">{e.type.replace(/_/g, " ")}</span>{e.type === "reasoning_completed" && <span className="chip acc">{e.payload.source === "llm" ? "Model advice" : "Recorded analysis"}</span>}
            {e.activity.some(a => a.status === "error") && <span className="chip bad">Execution error</span>}
            {e.type === "action_escalated" && <span className="chip warn">Review requested</span>}</div>
          {!!e.activity.length && <div className="tl-handlers">{e.activity.map((a, i) => <span key={i}>{a.status === "error" ? "!" : "✓"} {shortAgent(a.agent)} <small>{fmtMs(a.duration_ms)}</small></span>)}</div>}
          {e.type === "task_assigned" && typeof e.payload.assignee === "string" && <p className="muted sm">→ Assigned to {name(e.payload.assignee)}</p>}
        </button></li>)}</ol>
    </div><div className={`tl-details-wrap ${selected ? "has-selection" : ""}`}>{selected ? <><div className="tl-detail-toolbar"><button className="btn" onClick={() => {selectionGeneration.current++; setSelected(null); resetReplay();}}>Close event</button></div><EventDetails key={selected.id} entry={selected} basePath={basePath} evidencePath={evidencePath} onSelect={id => void select(id)} /></> : <aside className="card tl-empty-detail"><span>◎</span><h2>Follow the story</h2><p className="muted">Select any event to inspect its input, agent execution, recorded reasoning and the events it caused.</p><p className="muted sm">Use Replay steps to walk through recorded history. Playback does not run agents or change decisions.</p></aside>}</div></div>
  </div>;
}
