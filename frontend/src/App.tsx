import { useEffect, useState } from "react";
import { usePoll, type Approval } from "./api";
import { AgentsPage } from "./pages/Agents";
import { ApprovalsPage } from "./pages/Approvals";
import { AuditPage } from "./pages/Audit";
import { EvaluationsPage } from "./pages/Evaluations";
import { GuardrailsPage } from "./pages/Guardrails";
import { DataPage } from "./pages/Data";
import { GraphPage } from "./pages/Graph";
import { MapPage } from "./pages/MapView";
import { MetricsPage } from "./pages/Metrics";
import { Playground } from "./pages/Playground";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { IntelligencePage } from "./pages/Intelligence";
import { LLMSettingsPage } from "./pages/LLMSettings";

const ITEMS: { path: string; label: string; icon: string }[] = [
  { path: "runs", label: "Runs", icon: "☰" },
  { path: "intelligence", label: "Intelligence", icon: "◇" },
  { path: "llm-settings", label: "LLM settings", icon: "⚙" },
  { path: "graph", label: "Agent graph", icon: "◎" },
  { path: "map", label: "Situation map", icon: "⌖" },
  { path: "approvals", label: "Approvals", icon: "✓" },
  { path: "metrics", label: "Metrics", icon: "▤" },
  { path: "playground", label: "Playground", icon: "▶" },
  { path: "data", label: "Data & history", icon: "◷" },
  { path: "agents", label: "Agents", icon: "◈" },
  { path: "evaluations", label: "Evaluations", icon: "✔" },
  { path: "guardrails", label: "Guardrails", icon: "⛨" },
  { path: "audit", label: "Audit", icon: "≣" },
];

function useHash(): [string[], (h: string) => void] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState<string[]>(parse());
  useEffect(() => {
    const on = () => setParts(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [parts, (h) => (window.location.hash = h)];
}

export function App() {
  const [parts, go] = useHash();
  const page = parts[0] ?? "runs";
  const pending = usePoll<Approval[]>("/approvals", 3000);
  const health = usePoll<{ status: string }>("/health", 5000);

  let body;
  if (page === "runs" && parts[1]) body = <RunDetail id={parts.slice(1).join("/")} back={() => go("#/runs")} />;
  else if (page === "runs") body = <Runs open={(id) => go(`#/runs/${encodeURIComponent(id)}`)} />;
  else if (page === "intelligence") body = <IntelligencePage />;
  else if (page === "llm-settings") body = <LLMSettingsPage />;
  else if (page === "graph") body = <GraphPage />;
  else if (page === "map") body = <MapPage />;
  else if (page === "approvals") body = <ApprovalsPage />;
  else if (page === "metrics") body = <MetricsPage />;
  else if (page === "playground") body = <Playground />;
  else if (page === "data") body = <DataPage />;
  else if (page === "agents") body = <AgentsPage />;
  else if (page === "evaluations") body = <EvaluationsPage />;
  else if (page === "guardrails") body = <GuardrailsPage />;
  else if (page === "audit") body = <AuditPage />;
  else body = <div className="page empty">Unknown page.</div>;

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">
          ATLAS
          <small>Agent manager</small>
        </div>
        {ITEMS.map((i) => (
          <a key={i.path} className={page === i.path ? "on" : ""} onClick={() => go(`#/${i.path}`)}>
            <span style={{ width: 16, textAlign: "center" }}>{i.icon}</span>
            {i.label}
            {i.path === "approvals" && (pending.data?.length ?? 0) > 0 && <span className="badge">{pending.data!.length}</span>}
          </a>
        ))}
        <div className="sp" />
        <a href="/dashboard" style={{ fontSize: 12 }}>Responder dashboard ↗</a>
        <div className="foot">
          <span className={`dot ${health.data?.status === "ok" ? "" : "bad"}`} /> {health.data?.status === "ok" ? "API connected" : "API unreachable"}
        </div>
      </nav>
      <div className="main">{body}</div>
    </div>
  );
}
