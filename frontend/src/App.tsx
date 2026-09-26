import { lazy, Suspense, useEffect, useState } from "react";
import { token, usePoll, type Approval } from "./api";
import { AgentsPage } from "./pages/Agents";
import { AlertsPage } from "./pages/Alerts";
import { ApprovalsPage } from "./pages/Approvals";
import { AskPage } from "./pages/Ask";
import { AuditPage } from "./pages/Audit";
import { BenchmarksPage } from "./pages/Benchmarks";
import { DataPage } from "./pages/Data";
import { DecisionsPage } from "./pages/Decisions";
import { EvaluationsPage } from "./pages/Evaluations";
import { GraphPage } from "./pages/Graph";
import { GuardrailsPage } from "./pages/Guardrails";
import { IntelligencePage } from "./pages/Intelligence";
import { LLMSettingsPage } from "./pages/LLMSettings";
import { MetricsPage } from "./pages/Metrics";
import { MissionsPage } from "./pages/Missions";
import { OverviewPage } from "./pages/Overview";
import { Playground } from "./pages/Playground";
import { PracticePage } from "./pages/Practice";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { TimelinePage } from "./pages/Timeline";

// The map pulls in a map engine; load it only when someone opens it.
const MapPage = lazy(() => import("./pages/MapView").then((m) => ({ default: m.MapPage })));

interface Item { path: string; label: string; icon: string; title: string }

// For the people running the response.
const OPERATIONS: Item[] = [
  { path: "overview", label: "Overview", icon: "◉", title: "Situation now" },
  { path: "map", label: "Map", icon: "⌖", title: "Situation map" },
  { path: "decisions", label: "Decisions", icon: "✓", title: "Decisions waiting for you" },
  { path: "alerts", label: "Alerts & reports", icon: "📢", title: "Public alerts and situation reports" },
  { path: "missions", label: "Teams & missions", icon: "⛑", title: "Teams and missions" },
  { path: "ask", label: "Ask ATLAS", icon: "?", title: "Ask ATLAS" },
  { path: "practice", label: "Practice drills", icon: "▶", title: "Practice with real past disasters" },
];

// For the people building and checking the system.
const TECHNICAL: Item[] = [
  { path: "runs", label: "Runs and traces", icon: "☰", title: "Runs: every incident, step by step" },
  { path: "timeline", label: "Event timeline", icon: "◷", title: "Every message between agents" },
  { path: "graph", label: "Agent graph", icon: "◎", title: "How the agents talk to each other" },
  { path: "agents", label: "Agents", icon: "◈", title: "The agents: what each does, pause and resume" },
  { path: "intelligence", label: "Intelligence (advanced)", icon: "◇", title: "AI advice with evidence" },
  { path: "evaluations", label: "Evaluations", icon: "✔", title: "Automatic quality and safety checks" },
  { path: "benchmarks", label: "Benchmarks", icon: "▦", title: "Ten repeatable test exercises" },
  { path: "playground", label: "Playground", icon: "▷", title: "Replays, what-if and red team" },
  { path: "metrics", label: "Metrics", icon: "▤", title: "Speed, autonomy and cost" },
  { path: "guardrails", label: "Safety rules", icon: "⛨", title: "What each agent may and may not do" },
  { path: "approvals", label: "Approvals (advanced)", icon: "✎", title: "Approvals with editable details" },
  { path: "audit", label: "Audit log", icon: "≣", title: "Who changed what, and when" },
  { path: "data", label: "Data & history", icon: "◫", title: "Where the data comes from" },
  { path: "llm-settings", label: "AI model settings", icon: "⚙", title: "Which AI model ATLAS may use" },
];

function useHash(): [string[], (h: string) => void] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState<string[]>(parse());
  useEffect(() => {
    const on = () => setParts(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [parts, (h) => (window.location.hash = h)];
}

function remember(key: string, fallback: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => { try { const s = localStorage.getItem(key); return s == null ? fallback : s === "1"; } catch { return fallback; } });
  return [v, (next) => { setV(next); try { localStorage.setItem(key, next ? "1" : "0"); } catch { /* private window */ } }];
}

function SignIn() {
  const me = usePoll<{ name: string; role: string; dev_mode: boolean }>("/am/v1/me", 15000);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(token.get());
  if (me.data?.dev_mode && !open) return <div className="who">Open demo mode <a onClick={() => setOpen(true)}>sign in</a></div>;
  return (
    <div className="who">
      {me.data && !me.data.dev_mode && <div>Signed in: <b>{me.data.name}</b> ({me.data.role})</div>}
      {(open || !me.data || !me.data.dev_mode) && (
        <form onSubmit={(e) => { e.preventDefault(); token.set(value.trim()); setOpen(false); window.location.reload(); }} className="row">
          <input type="password" placeholder="Access code" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: 120 }} />
          <button className="btn">OK</button>
        </form>
      )}
    </div>
  );
}

export function App() {
  const [parts, go] = useHash();
  const page = parts[0] ?? "overview";
  const pending = usePoll<Approval[]>("/approvals", 3000);
  const health = usePoll<{ status: string }>("/health", 5000);
  const [techOpen, setTechOpen] = remember("atlas-tech-open", false);
  const isTech = TECHNICAL.some((i) => i.path === page);
  const item = [...OPERATIONS, ...TECHNICAL].find((i) => i.path === page);

  let body;
  if (page === "overview") body = <OverviewPage />;
  else if (page === "map") body = <Suspense fallback={<div className="page muted">Loading the map…</div>}><MapPage /></Suspense>;
  else if (page === "decisions") body = <DecisionsPage />;
  else if (page === "alerts") body = <AlertsPage />;
  else if (page === "missions") body = <MissionsPage />;
  else if (page === "ask") body = <AskPage />;
  else if (page === "practice") body = <PracticePage />;
  else if (page === "runs" && parts[1]) body = <RunDetail id={parts.slice(1).join("/")} back={() => go("#/runs")} />;
  else if (page === "runs") body = <Runs open={(id) => go(`#/runs/${encodeURIComponent(id)}`)} />;
  else if (page === "benchmarks" && parts[1] && parts[2]) {
    const base = `/benchmark/runs/${encodeURIComponent(parts[1])}/cases/${encodeURIComponent(parts[2])}`;
    body = <TimelinePage key={base} basePath={base} evidencePath={`${base}/evidence`} benchmarkRun={parts[1]} />;
  } else if (page === "benchmarks") body = <BenchmarksPage key={parts[1] ?? "catalog"} selectedRun={parts[1]} />;
  else if (page === "timeline") body = <TimelinePage />;
  else if (page === "intelligence") body = <IntelligencePage />;
  else if (page === "llm-settings") body = <LLMSettingsPage />;
  else if (page === "graph") body = <GraphPage />;
  else if (page === "approvals") body = <ApprovalsPage />;
  else if (page === "metrics") body = <MetricsPage />;
  else if (page === "playground") body = <Playground />;
  else if (page === "data") body = <DataPage />;
  else if (page === "agents") body = <AgentsPage />;
  else if (page === "evaluations") body = <EvaluationsPage />;
  else if (page === "guardrails") body = <GuardrailsPage />;
  else if (page === "audit") body = <AuditPage />;
  else body = <div className="page empty">That page does not exist. <a href="#/overview">Go to the overview</a>.</div>;

  const link = (i: Item) => (
    <a key={i.path} className={page === i.path ? "on" : ""} onClick={() => go(`#/${i.path}`)} title={i.title}>
      <span className="ni">{i.icon}</span>
      {i.label}
      {i.path === "decisions" && (pending.data?.length ?? 0) > 0 && <span className="badge">{pending.data!.length}</span>}
    </a>
  );

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">
          ATLAS
          <small>Disaster response coordinator</small>
        </div>
        {OPERATIONS.map(link)}
        <button className="navsec" onClick={() => setTechOpen(!techOpen)} aria-expanded={techOpen || isTech}>
          {techOpen || isTech ? "▾" : "▸"} For technical staff
        </button>
        {(techOpen || isTech) && <div className="tech">{TECHNICAL.map(link)}</div>}
        <div className="sp" />
        <SignIn />
        <div className="foot">
          <span className={`dot ${health.data?.status === "ok" ? "" : "bad"}`} /> {health.data?.status === "ok" ? "System online" : "Cannot reach the system"}
        </div>
      </nav>
      <div className="main">
        {item && page !== "map" && <div className="topbar"><h1>{item.title}</h1>{isTech && <span className="chip">technical</span>}</div>}
        {body}
      </div>
    </div>
  );
}
