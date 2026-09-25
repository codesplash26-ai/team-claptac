import { useEffect, useRef, useState } from "react";
import { api, token, usePoll } from "../api";
import { Json } from "../components/Json";

type Claim = {text: string; accepted: boolean; claim: {confidence: number | null; evidence_ids: string[]}; violations: {message: string}[]};
type Advice = {incident_id: string; cycle: number; summary: string; source: string; claims: Claim[]; concerns: string[]; questions: string[]; recommend_replan: boolean; trace: {model: string | null; latency_ms: number | null; tokens_in: number | null; tokens_out: number | null; steps: {kind: string; text: string; refs: string[]}[]}};
type Answer = {text: string; claims: Claim[]; used_llm: boolean};

export function IntelligencePage() {
  const advice = usePoll<Advice[]>("/intelligence/advice", 3000);
  const [question, setQuestion] = useState("What is waiting for approval?");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [report, setReport] = useState("ratnapura-demo-river: 12 people stranded, need rescue");
  const [extraction, setExtraction] = useState<unknown>(null);
  const [evidence, setEvidence] = useState<unknown>(null);
  const evidenceDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (evidence != null && !evidenceDialog.current?.open) evidenceDialog.current?.showModal(); }, [evidence]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState(token.get());
  async function run(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const claims = (items: Claim[]) => <details><summary>Evidence and claim checks · {items.filter(c => c.accepted).length} accepted · {items.filter(c => !c.accepted).length} rejected</summary><ul>{items.map((c, i) => <li key={i} style={{marginBottom: 10}}>
    <strong>{c.accepted ? "Verified against snapshot" : "Rejected"}</strong>{" · "}
    {c.accepted ? c.text : <s>{c.text}</s>}
    {c.claim.confidence != null && <span> · confidence {(c.claim.confidence * 100).toFixed(0)}%</span>}
    {c.violations.map((v, j) => <div className="muted" key={j}>{v.message}</div>)}
    <div>{c.claim.evidence_ids.map(ref => <button key={ref} className="btn" style={{margin: "5px 5px 0 0"}} disabled={busy}
      onClick={() => run(async () => setEvidence(await api(`/intelligence/evidence/${encodeURIComponent(ref)}`)))}>View evidence</button>)}</div>
  </li>)}</ul></details>;
  return <div className="page">
    <div className="page-head"><div><h1>Intelligence</h1><p className="muted">Snapshot evidence, questions and report previews. Model suggestions require human review.</p></div></div>
    <label>Operator token (if configured) <input type="password" value={auth} autoComplete="off" onChange={e => {setAuth(e.target.value); token.set(e.target.value);}} /></label>
    {(error || advice.error) && <p role="alert">{error || advice.error}</p>}
    <section className="card" style={{padding: 20, marginTop: 20}}>
      <h2>Ask the system</h2>
      <form onSubmit={e => {e.preventDefault(); run(async () => setAnswer(await api<Answer>("/intelligence/ask", {method: "POST", json: {question}})));}}>
        <input aria-label="Question" maxLength={2000} style={{width: "75%"}} value={question} onChange={e => setQuestion(e.target.value)} />
        <button className="btn" disabled={busy || !question.trim()}>Ask</button>
      </form>
      {answer && <><p className="muted">{answer.used_llm ? "Model-assisted, verified against snapshot" : "Offline snapshot lookup"}</p>
        <p style={{whiteSpace: "pre-wrap"}}>{answer.text}</p>{claims(answer.claims)}</>}
    </section>
    <section className="card" style={{padding: 20, marginTop: 20}}>
      <h2>Field report preview</h2><p className="muted">Include a known area id. This preview does not submit a report or change triage.</p>
      <textarea aria-label="Field report" maxLength={280} style={{width: "100%", minHeight: 80}} value={report} onChange={e => setReport(e.target.value)} />
      <button className="btn" disabled={busy || !report.trim()} onClick={() => run(async () => setExtraction(await api("/intelligence/extract", {method: "POST", json: {node_id: "operator-preview", text: report}})))}>Extract preview</button>
      {extraction != null && <Json value={extraction} />}
    </section>
    <h2 style={{marginTop: 24}}>Incident advice</h2>
    {advice.loading && <p>Loading advice…</p>}
    {!advice.loading && !advice.data?.length && <p className="muted">Replay a flood from Playground to generate advice. No model key is required.</p>}
    {advice.data?.map(a => <section className="card" key={`${a.incident_id}:${a.cycle}`} style={{padding: 20, marginTop: 16}}>
      <h3>{a.incident_id} · cycle {a.cycle}</h3><p className="muted">{a.source === "llm" ? a.trace.model : "Offline template"} · {a.trace.latency_ms?.toFixed(0)} ms
        {a.trace.tokens_in != null && ` · ${a.trace.tokens_in} input / ${a.trace.tokens_out} output tokens`}</p>
      <p style={{whiteSpace: "pre-wrap"}}>{a.summary}</p>{claims(a.claims)}
      {a.concerns.length > 0 && <><h4>Uncertainty and concerns</h4><ul>{a.concerns.map((c, i) => <li key={i}>{c}</li>)}</ul></>}
      {a.questions.length > 0 && <><h4>Questions for review</h4><ul>{a.questions.map((q, i) => <li key={i}>{q}</li>)}</ul></>}
      {a.recommend_replan && <p>Model suggests reviewing the plan. No action has been changed.</p>}
      <details><summary>Decision and validation trace</summary><ol>{a.trace.steps.map((s, i) => <li key={i}><strong>{s.kind}</strong>: {s.text}{s.refs.map(ref => <button key={ref} className="btn" disabled={busy} onClick={() => run(async () => setEvidence(await api(`/intelligence/evidence/${encodeURIComponent(ref)}`)))}>View snapshot</button>)}</li>)}</ol></details>
    </section>)}
    {evidence != null && <dialog ref={evidenceDialog} aria-label="Evidence snapshot" onCancel={() => setEvidence(null)} style={{padding: 24, width: "min(800px, 90vw)", maxHeight: "85vh", overflow: "auto", background: "#151a28", color: "#e6e9ef", border: "1px solid #343c50", borderRadius: 12}}><h2>Evidence snapshot</h2><Json value={evidence} /><button className="btn" onClick={() => setEvidence(null)}>Close evidence</button></dialog>}
  </div>;
}
