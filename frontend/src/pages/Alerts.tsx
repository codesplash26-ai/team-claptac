import { useState } from "react";
import { usePoll } from "../api";
import { ago, languageName, place, readable, type Alert, type Sitrep } from "../plain";

/* What the public was told, and the reports for the operations room. Alerts are written in all three
 * languages from fixed, checked phrasing (no AI free text), and can be downloaded in the standard CAP
 * format that warning systems and broadcasters accept. */
export function AlertsPage() {
  const alerts = usePoll<Alert[]>("/alerts", 4000);
  const sitreps = usePoll<Sitrep[]>("/sitreps", 5000);
  const [lang, setLang] = useState("en");
  const list = [...(alerts.data ?? [])].reverse();
  const reports = [...(sitreps.data ?? [])].reverse();

  return (
    <div className="page ops">
      <p className="lead">Public alerts that went out, and the situation reports ATLAS writes for the operations room. Alerts only go out after a person approves them.</p>
      <div className="seg" style={{ marginBottom: 12 }}>
        {["en", "si", "ta"].map((l) => <button key={l} className={lang === l ? "on" : ""} onClick={() => setLang(l)}>{languageName(l)}</button>)}
      </div>

      <div className="grid g2">
        <div>
          <h3 className="sec">Public alerts · {list.length}</h3>
          {list.length === 0 && <div className="card muted">No alert has been sent. When ATLAS wants to warn the public, it asks you first under Decisions.</div>}
          {list.map((a) => {
            const t = a.texts[lang] ?? a.texts.en;
            return (
              <div key={a.id} className={`card alert-card sev-${a.severity.toLowerCase()}`}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className={`chip ${a.status === "active" ? "bad" : ""}`}>{a.status === "active" ? "Active" : a.status}</span>
                  <span className="muted sm">{a.severity} · {place(a.incident_id)}</span>
                </div>
                <div className="alert-head">{t?.headline}</div>
                <p>{t?.body}</p>
                <p><b>{t?.instruction}</b></p>
                <a className="sm" href={`/alerts/${a.id}/cap`} target="_blank" rel="noreferrer">Download for warning systems (CAP)</a>
              </div>
            );
          })}
        </div>
        <div>
          <h3 className="sec">Situation reports · {reports.length}</h3>
          {reports.length === 0 && <div className="card muted">No report yet.</div>}
          {reports.map((r, i) => (
            <details key={r.id} className="card" open={i === 0}>
              <summary><b>{i === 0 ? "Latest report" : "Report"}</b> <span className="muted sm">· {ago(r.generated_at)}</span></summary>
              <pre className="report">{readable(r.texts[lang] ?? r.texts.en).replace(/ \(\d{4}-\d\d-\d\dT[^)]*\)/, "")}</pre>
            </details>
          ))}
        </div>
      </div>
    </div>
  );
}
