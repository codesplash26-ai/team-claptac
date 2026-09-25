import { useState } from "react";
import { fmtMs, usePoll, type Span, type SpanDetail } from "../api";
import { Copy, Json } from "./Json";

type Tab = "input" | "output" | "metadata" | "chain";

export function Inspector({ span, onSelectEvent }: { span: Span | null; onSelectEvent?: (eventId: string) => void }) {
  const [tab, setTab] = useState<Tab>("input");
  const { data, error } = usePoll<SpanDetail>(span ? `/dev/span?id=${encodeURIComponent(span.id)}` : null, 0);
  if (!span) return <div className="empty">Select a span to see what it received, what it produced and why.</div>;
  if (error) return <div className="insp err">{error}</div>;
  if (!data) return <div className="insp muted">Loading…</div>;
  return (
    <div>
      <div className="insp">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3>{span.agent}</h3>
          <span className={`chip ${span.status === "ok" ? "ok" : span.status === "escalated" ? "warn" : "bad"}`}>{span.status}</span>
        </div>
        <div className="muted">{data.input.summary}</div>
        <div className="row sm" style={{ marginTop: 6 }}>
          <span className="chip">{span.kind}</span>
          <span className="chip">{fmtMs(data.metadata.duration_ms)}</span>
          <span className="chip">in: {data.input.type}</span>
          <span className="chip">out: {data.outputs.length} event{data.outputs.length === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div className="tabs">
        {(["input", "output", "metadata", "chain"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t === "chain" ? "Why (chain)" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <div className="insp">
        {tab === "input" && (
          <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="h2">Event handled: {data.input.type}</span>
              <Copy text={JSON.stringify(data.input.payload, null, 2)} />
            </div>
            <dl className="kv">
              <dt>from</dt><dd>{data.input.source}</dd>
              <dt>incident</dt><dd>{data.input.incident_id ?? "—"}</dd>
              <dt>time</dt><dd>{data.input.created_at}</dd>
              <dt>event id</dt><dd className="mono sm">{data.input.id}</dd>
            </dl>
            <Json value={data.input.payload} />
          </>
        )}
        {tab === "output" &&
          (data.outputs.length ? (
            data.outputs.map((o) => (
              <div key={o.id} style={{ marginBottom: 10 }}>
                <div className="row">
                  <span className="chip acc">{o.type}</span>
                  <a onClick={() => onSelectEvent?.(o.id)}>{o.summary}</a>
                </div>
                <Json value={o.payload} max={200} />
              </div>
            ))
          ) : (
            <div className="muted">This step produced no events (it only read, or wrote to shared state).</div>
          ))}
        {tab === "metadata" && (
          <dl className="kv">
            <dt>span id</dt><dd className="mono sm">{data.id}</dd>
            <dt>kind</dt><dd>{data.kind}</dd>
            <dt>duration</dt><dd>{fmtMs(data.metadata.duration_ms)}</dd>
            <dt>started</dt><dd>{data.metadata.started_at ? new Date(data.metadata.started_at * 1000).toISOString() : "—"}</dd>
            <dt>ok</dt><dd>{String(data.metadata.ok)}</dd>
            <dt>error</dt><dd>{data.metadata.error ?? "none"}</dd>
            <dt>caused by</dt><dd>{data.caused_by ? `${data.caused_by.by} (event ${data.caused_by.event_id.slice(0, 10)}…)` : "an outside input"}</dd>
          </dl>
        )}
        {tab === "chain" && (
          <div className="chainlist">
            {data.metadata.chain.length ? data.metadata.chain.map((c, i) => <div key={i} className="muted">↳ {c}</div>) : <div className="muted">Nothing before this: an outside input started it.</div>}
            <div className="me">● {data.input.summary}</div>
          </div>
        )}
      </div>
    </div>
  );
}
