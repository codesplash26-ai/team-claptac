import { useState } from "react";
import { api, usePoll } from "../api";
import { plainFact, readable } from "../plain";

type Claim = { text: string; accepted: boolean; violations: { message: string }[] };
type Advice = { incident_id: string; cycle: number; summary: string; source: string; claims: Claim[]; concerns: string[]; questions: string[]; recommend_replan: boolean; trace: { model: string | null } };
type Answer = { text: string; claims: Claim[]; used_llm: boolean };

const SUGGESTED = [
  "How many people are being evacuated?",
  "Which shelters are being used?",
  "What is the risk level?",
  "What is waiting for approval?",
];

/* Ask ATLAS questions in plain words. Every answer is checked against what the system has actually
 * recorded; anything it cannot back up is struck out rather than shown as fact. Works without an AI key
 * (it then answers with the matching records). */
export function AskPage() {
  const advice = usePoll<Advice[]>("/intelligence/advice", 5000);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const latest = advice.data?.[0];

  const ask = async (q: string) => {
    setQuestion(q);
    setBusy(true); setErr(null);
    try { setAnswer(await api<Answer>("/intelligence/ask", { method: "POST", json: { question: q } })); }
    catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const facts = (text: string) => {
    const seen = new Set<string>();
    return text.split("\n").filter(Boolean).map(plainFact).filter((f) => {
      const key = f.what.split(" › ").slice(1).join() + f.value;  // "Hazard: flood" once, not per subject
      return seen.has(key) ? false : (seen.add(key), true);
    });
  };
  const unverified = latest?.concerns.filter((c) => c.startsWith("Not verified")) ?? [];
  const concerns = latest?.concerns.filter((c) => !c.startsWith("Not verified")) ?? [];

  return (
    <div className="page ops">
      <p className="lead">Ask about the situation in your own words. ATLAS only answers from what it has recorded, and says so when it cannot back something up.</p>

      <form className="askbox" onSubmit={(e) => { e.preventDefault(); if (question.trim()) void ask(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. How many people are being evacuated?" maxLength={500} />
        <button className="btn primary big" disabled={busy || !question.trim()}>{busy ? "Checking…" : "Ask"}</button>
      </form>
      <div className="row" style={{ margin: "8px 0 14px" }}>
        {SUGGESTED.map((s) => <button key={s} className="chip click" onClick={() => void ask(s)}>{s}</button>)}
      </div>
      {err && <div className="banner warn">{err}</div>}

      {answer && (
        <div className="card" style={{ marginBottom: 14 }}>
          <h2>Answer</h2>
          <p className="muted sm">{answer.used_llm ? "Written by the AI assistant and checked against the records." : "No AI in use: these are the matching records, word for word."}</p>
          {answer.used_llm
            ? <p style={{ whiteSpace: "pre-wrap" }}>{readable(answer.text)}</p>
            : <table className="t"><tbody>{facts(answer.text).map((f, i) => <tr key={i}><td className="muted">{f.what}</td><td><b>{f.value}</b></td></tr>)}</tbody></table>}
          {answer.claims.some((c) => !c.accepted) && (
            <div className="banner warn sm" style={{ marginTop: 8 }}>
              Could not be backed up, so not shown as fact:
              <ul>{answer.claims.filter((c) => !c.accepted).map((c, i) => <li key={i}><s>{readable(c.text)}</s></li>)}</ul>
            </div>
          )}
        </div>
      )}

      <div className="card">
        <h2>ATLAS's own reading of the situation</h2>
        {!latest && <p className="muted">Nothing yet. ATLAS writes this after each planning round. Start a practice drill to see it.</p>}
        {latest && (
          <>
            <p className="muted sm">{latest.source === "llm" ? `Written by ${latest.trace.model ?? "the AI assistant"}, checked against the records` : "Written from the records (no AI in use)"} · planning round {latest.cycle}</p>
            {latest.source === "llm"
              ? <p style={{ whiteSpace: "pre-wrap" }}>{readable(latest.summary)}</p>
              : <table className="t"><tbody>{facts(latest.summary).map((f, i) => <tr key={i}><td className="muted">{f.what}</td><td>{f.value}</td></tr>)}</tbody></table>}
            <p className="sm" style={{ marginTop: 8 }}>
              <span className="chip ok">✓ {latest.claims.filter((c) => c.accepted).length} statements checked against the records</span>{" "}
              {latest.claims.some((c) => !c.accepted) && <span className="chip bad">✕ {latest.claims.filter((c) => !c.accepted).length} rejected</span>}
            </p>
            {unverified.length > 0 && <div className="banner bad sm">{unverified.map((u, i) => <div key={i}>{u}</div>)}</div>}
            {concerns.length > 0 && <><h3 className="sec">Things to look at</h3><ul>{[...new Set(concerns.map(readable))].slice(0, 6).map((c, i) => <li key={i}>{c}</li>)}</ul></>}
            {latest.questions.length > 0 && <><h3 className="sec">Open questions</h3><ul>{latest.questions.map((q, i) => <li key={i}>{readable(q)}</li>)}</ul></>}
            {latest.recommend_replan && <div className="banner warn">ATLAS suggests reviewing the plan. Nothing has been changed automatically.</div>}
          </>
        )}
      </div>
    </div>
  );
}
