import { useMemo, useState } from "react";
import { fmtMs, shortAgent, type Span } from "../api";
import { NODES } from "../graphDef";

const layerOf = (agent: string) => NODES.find((n) => n.id === agent)?.layer ?? "plan";
const ICON: Record<string, string> = { input: "→", handler: "●", executor: "⚙", llm: "✦" };

export function Waterfall({
  spans,
  selected,
  onSelect,
  upTo,
}: {
  spans: Span[];
  selected: string | null;
  onSelect: (id: string) => void;
  /** Only show spans that started at or before this index in start order (for the step scrubber). */
  upTo?: number;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const order = useMemo(() => [...spans].sort((a, b) => a.start - b.start).map((s) => s.id), [spans]);
  const limit = upTo === undefined ? Infinity : upTo;
  const rank = useMemo(() => new Map(order.map((id, i) => [id, i])), [order]);
  const hasChildren = useMemo(() => {
    const set = new Set<string>();
    spans.forEach((s) => s.parent_id && set.add(s.parent_id));
    return set;
  }, [spans]);

  const start = Math.min(...spans.map((s) => s.start));
  const end = Math.max(...spans.map((s) => s.start + s.duration_ms / 1000));
  const total = Math.max(end - start, 1e-6);

  // hide descendants of collapsed spans
  const hidden = new Set<string>();
  const byParent = new Map<string, Span[]>();
  spans.forEach((s) => s.parent_id && byParent.set(s.parent_id, [...(byParent.get(s.parent_id) ?? []), s]));
  const hide = (id: string) => (byParent.get(id) ?? []).forEach((c) => (hidden.add(c.id), hide(c.id)));
  collapsed.forEach(hide);

  return (
    <div>
      <div className="wf-head">
        <span>
          Span · {spans.length} total
          <a className="sm" style={{ marginLeft: 10 }} onClick={() => setCollapsed(new Set())}>
            expand all
          </a>
          <a className="sm" style={{ marginLeft: 8 }} onClick={() => setCollapsed(new Set([...hasChildren].filter((id) => (spans.find((s) => s.id === id)?.depth ?? 0) >= 1)))}>
            collapse
          </a>
        </span>
        <span>{fmtMs(total * 1000)} wall</span>
      </div>
      {spans.map((s) => {
        if (hidden.has(s.id) || (rank.get(s.id) ?? 0) > limit) return null;
        const layer = layerOf(s.agent);
        const left = ((s.start - start) / total) * 100;
        const width = Math.max((s.duration_ms / 1000 / total) * 100, 0.6);
        return (
          <div key={s.id} className={`wf-row ${selected === s.id ? "sel" : ""}`} onClick={() => onSelect(s.id)}>
            <div className="wf-name" style={{ paddingLeft: s.depth * 14 }}>
              <span
                className="tw"
                onClick={(e) => {
                  if (!hasChildren.has(s.id)) return;
                  e.stopPropagation();
                  const next = new Set(collapsed);
                  next.has(s.id) ? next.delete(s.id) : next.add(s.id);
                  setCollapsed(next);
                }}
              >
                {hasChildren.has(s.id) ? (collapsed.has(s.id) ? "▸" : "▾") : ""}
              </span>
              <span className={`icon i-${layer}`} title={s.kind}>
                {ICON[s.kind] ?? "●"}
              </span>
              <span className="label" title={s.summary}>
                <b>{shortAgent(s.agent)}</b> <span className="muted">{s.event_type}</span>
                {s.status !== "ok" && <span className={`chip ${s.status === "error" || s.status === "blocked" ? "bad" : "warn"}`} style={{ marginLeft: 6 }}>{s.status}</span>}
              </span>
            </div>
            <div className="bar" title={`${fmtMs(s.duration_ms)} · starts +${fmtMs((s.start - start) * 1000)}`}>
              <i className={s.status !== "ok" ? s.status : s.kind === "input" || s.kind === "executor" ? s.kind : ""} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
