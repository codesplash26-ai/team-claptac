import { useMemo } from "react";
import { usePoll } from "../api";

interface MapData {
  site: string;
  bounds: [[number, number], [number, number]];
  edges: { id: string; from: [number, number]; to: [number, number]; belief_open: number | null; confidence: number | null; closed_by_decision: boolean }[];
  routes: { mission_id: string; title: string; status: string; points: [number, number][] }[];
  shelters: { id: string; lat: number; lon: number; capacity: number }[];
  gauges: { node: string; type: string; value: number; unit: string; threshold: number | null; lat: number; lon: number }[];
  observed_edges: number;
  likely_closed: number;
}

export function MapPage() {
  const { data, error } = usePoll<MapData>("/dev/map", 3000);
  const geo = useMemo(() => {
    if (!data) return null;
    const [[la0, lo0], [la1, lo1]] = data.bounds;
    const k = Math.cos(((la0 + la1) / 2) * (Math.PI / 180));
    const w = (lo1 - lo0) * k, h = la1 - la0;
    const S = 900 / Math.max(w, h), pad = 30;
    const px = (lon: number) => pad + (lon - lo0) * k * S;
    const py = (lat: number) => pad + (la1 - lat) * S;
    return { px, py, W: w * S + pad * 2, H: h * S + pad * 2 };
  }, [data]);
  if (error) return <div className="page"><div className="err">{error}</div></div>;
  if (!data || !geo) return <div className="page muted">Loading…</div>;
  const colour = (b: number | null) => (b === null ? "var(--mut)" : b >= 0.7 ? "#16a34a" : b >= 0.3 ? "#d97706" : "#dc2626");

  return (
    <div className="page">
      <div className="card">
        <h2>{data.site} · what the system believes about each road, and how sure it is</h2>
        <div className="legend" style={{ marginBottom: 8 }}>
          <span><i className="sw" style={{ background: "#16a34a" }} />probably open</span>
          <span><i className="sw" style={{ background: "#d97706" }} />uncertain</span>
          <span><i className="sw" style={{ background: "#dc2626" }} />probably closed</span>
          <span><i className="sw" style={{ background: "var(--mut)" }} />no evidence yet (prior)</span>
          <span>faint line = low confidence · solid = well supported</span>
          <span>▲ shelter · ● sensor · blue = mission route</span>
          <span className="chip">{data.observed_edges} roads with evidence</span><span className="chip bad">{data.likely_closed} likely closed</span>
        </div>
        <svg viewBox={`0 0 ${geo.W} ${geo.H}`} style={{ width: "100%", maxHeight: "74vh", background: "var(--panel2)", borderRadius: 8 }}>
          {data.edges.map((e) => (
            <line key={e.id} x1={geo.px(e.from[1])} y1={geo.py(e.from[0])} x2={geo.px(e.to[1])} y2={geo.py(e.to[0])}
              stroke={e.closed_by_decision ? "#111827" : colour(e.belief_open)} strokeWidth={e.belief_open === null ? 1 : 2.4}
              strokeDasharray={e.closed_by_decision ? "5 3" : undefined} strokeLinecap="round"
              opacity={e.belief_open === null ? 0.35 : 0.3 + 0.7 * (e.confidence ?? 0.5)}>
              <title>{`${e.id}: ${e.belief_open === null ? "no evidence" : `open ${(e.belief_open * 100).toFixed(0)}%, confidence ${((e.confidence ?? 0) * 100).toFixed(0)}%`}${e.closed_by_decision ? " · CLOSED by decision" : ""}`}</title>
            </line>
          ))}
          {data.routes.map((r) => (
            <polyline key={r.mission_id} points={r.points.map((p) => `${geo.px(p[1])},${geo.py(p[0])}`).join(" ")} fill="none" stroke="#2563eb" strokeWidth={4} opacity={0.75} strokeLinejoin="round">
              <title>{`${r.title} (${r.status})`}</title>
            </polyline>
          ))}
          {data.shelters.map((s) => (
            <g key={s.id} transform={`translate(${geo.px(s.lon)},${geo.py(s.lat)})`}>
              <polygon points="0,-10 9,7 -9,7" fill="var(--act)" stroke="var(--panel)" strokeWidth={2}><title>{`${s.id} · capacity ${s.capacity}`}</title></polygon>
              <text y={20} fontSize={10} textAnchor="middle" fill="var(--ink)">{s.id.replace(/^.*-shelter-/, "S")}</text>
            </g>
          ))}
          {data.gauges.map((g) => {
            const over = g.threshold != null && g.value >= g.threshold;
            return (
              <g key={g.node + g.type} transform={`translate(${geo.px(g.lon)},${geo.py(g.lat)})`}>
                <circle r={9} fill={over ? "#dc2626" : "#2563eb"} stroke="var(--panel)" strokeWidth={2}><title>{`${g.node} ${g.type}: ${g.value} ${g.unit}`}</title></circle>
                <text y={-13} fontSize={10} textAnchor="middle" fill="var(--ink)">{g.value} {g.unit}</text>
              </g>
            );
          })}
        </svg>
        <div className="sm muted" style={{ marginTop: 6 }}>No map tiles: the road graph draws the geography, so this works offline. Roads gain evidence as sensor readings arrive; try a scenario in the Playground.</div>
      </div>
    </div>
  );
}
