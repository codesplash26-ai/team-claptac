import { usePoll, type Guardrails } from "../api";

// The policy's conditions, in words.
function when(action: string, rule: { consequence: string; when: Record<string, unknown> }): string {
  const w = rule.when as Record<string, unknown>;
  if (rule.consequence !== "high") return "Never: routine, done automatically";
  if (!Object.keys(w).length) return "Always";
  const parts: string[] = [];
  if ("resources_committed_gt" in w) parts.push(`When it commits more than ${w.resources_committed_gt} teams or vehicles`);
  if ("scope" in w) parts.push(`When the scope is ${w.scope}`);
  if ("unless_local_advisory" in w) parts.push(`Always, unless it is a local "${(w.unless_local_advisory as string[]).map((x) => x.replace(/_/g, " ")).join('" or "')}" advisory. Every evacuation order goes to a person`);
  return parts.join("; ") || JSON.stringify(w) + ` (${action})`;
}

export function GuardrailsPage() {
  const { data, error } = usePoll<Guardrails>("/am/v1/guardrails", 0);
  if (error) return <div className="page"><div className="err">{error}</div></div>;
  if (!data) return <div className="page muted">Loading…</div>;
  return (
    <div className="page">
      <div className="card" style={{ marginBottom: 12 }}>
        <h2>Principles, enforced in code rather than in a prompt</h2>
        <ul style={{ margin: 0, paddingLeft: 18 }}>{data.principles.map((p) => <li key={p}>{p}</li>)}</ul>
        <div className="sm muted" style={{ marginTop: 8 }}>Only <b>{data.proposers.join(", ")}</b> may propose. Retry limit {String(data.gateway.max_retries)}; stale plans {data.gateway.require_current_snapshot ? "are refused" : "are allowed"}. Try to break these in Playground → Red team.</div>
      </div>
      <div className="grid g2">
        <div className="card">
          <h2>Agent identity · what each executor is allowed to do</h2>
          <table className="t">
            <thead><tr><th>Executor</th><th>May do on its own or with approval</th><th>Always needs a human</th></tr></thead>
            <tbody>{data.identities.map((i) => (
              <tr key={i.agent}><td><b>{i.agent}</b></td>
                <td className="sm">{i.allowed_actions.filter((a) => !i.high_consequence.includes(a)).map((a) => <span key={a} className="chip ok" style={{ marginRight: 4 }}>{a}</span>)}</td>
                <td className="sm">{i.high_consequence.map((a) => <span key={a} className="chip warn" style={{ marginRight: 4 }}>{a}</span>)}</td></tr>
            ))}</tbody>
          </table>
          <div className="sm muted" style={{ marginTop: 6 }}>Anything not listed is refused. An agent cannot grant itself more.</div>
        </div>
        <div className="card">
          <h2>Action rules · consequence and the conditions that raise it</h2>
          <table className="t">
            <thead><tr><th>Action</th><th>Consequence</th><th>When a person must approve</th></tr></thead>
            <tbody>{Object.entries(data.actions).map(([name, r]) => (
              <tr key={name}><td>{name}</td><td><span className={`chip ${r.consequence === "high" ? "warn" : "ok"}`}>{r.consequence}</span></td>
                <td className="sm">{when(name, r)}</td></tr>
            ))}</tbody>
          </table>
          <div className="sm muted" style={{ marginTop: 6 }}>A condition whose parameter is missing or malformed counts as met: unknown means high.</div>
        </div>
      </div>
    </div>
  );
}
