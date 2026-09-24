"""Responder dashboard: data assembly and HTML rendering.

Read-only. It never mutates state; the approve/edit/reject buttons call the API.
Sensor history comes from the ledger's raw sensor OBSERVATION events, so it works for
hardware, ``mock`` and replayed readings alike.
"""

from __future__ import annotations

import json
from html import escape

SENSOR_THRESHOLDS = {
    "water_level": ("water_threshold_cm", "cm"),
    "rainfall": ("rainfall_threshold_mm", "mm"),
    "soil_moisture": ("soil_threshold_pct", "%"),
}
HISTORY_POINTS = 40
FEED_LIMIT = 40


def _recent_events(db, limit):
    rows = db.connection.execute(
        "SELECT body FROM ledger WHERE kind='event' ORDER BY seq DESC LIMIT ?", (limit,)
    ).fetchall()
    return [json.loads(r["body"]) for r in rows][::-1]


def sensor_panel(runtime, offline_after_seconds: int = 60) -> list[dict]:
    """Per sensor: current value, history for a sparkline, threshold, online/offline."""
    history: dict[tuple, list[float]] = {}
    location: dict[tuple, dict] = {}
    for event in _recent_events(runtime.db, 2000):
        payload = event["payload"]
        if event["type"] != "observation" or payload.get("kind") != "sensor":
            continue
        key = (payload["node_id"], payload["type"])
        history.setdefault(key, []).append(payload["value"])
        if payload.get("location"):
            location[key] = payload["location"]
    config = runtime.settings.sentinel
    panel = []
    for status in runtime.sentinel.sensor_status(offline_after_seconds):
        key = (status["node_id"], status["sensor_type"])
        attr = SENSOR_THRESHOLDS.get(status["sensor_type"])
        panel.append(
            {
                **status,
                "history": history.get(key, [])[-HISTORY_POINTS:],
                "threshold": getattr(config, attr[0]) if attr else None,
                "location": location.get(key),
            }
        )
    return panel


def _sparkline(values, threshold, width=140, height=32) -> str:
    if len(values) < 2:
        return '<span class="muted">no history</span>'
    top = max(max(values), threshold or 0) * 1.05 or 1
    low = min(min(values), threshold or min(values), 0)
    span = (top - low) or 1

    def y(v):
        return round(height - 2 - (v - low) / span * (height - 4), 1)

    step = width / (len(values) - 1)
    points = " ".join(f"{round(i * step, 1)},{y(v)}" for i, v in enumerate(values))
    line = (
        (
            f'<line x1="0" x2="{width}" y1="{y(threshold)}" y2="{y(threshold)}" '
            f'stroke="#c0392b" stroke-dasharray="3 3"/>'
        )
        if threshold
        else ""
    )
    return (
        f'<svg width="{width}" height="{height}" role="img" aria-label="recent readings">'
        f'{line}<polyline fill="none" stroke="#1f6feb" stroke-width="1.5" points="{points}"/>'
        "</svg>"
    )


def _sensor_rows(panel) -> str:
    if not panel:
        return '<tr><td colspan="5" class="muted">No sensor readings received yet.</td></tr>'
    rows = []
    for s in panel:
        over = s["threshold"] is not None and s["value"] >= s["threshold"]
        state = ("online", "ok") if s["online"] else ("offline", "bad")
        stale = ' <span class="tag warn">stale reading</span>' if s["reading_stale"] else ""
        limit = f"{s['threshold']} {escape(s['unit'])}" if s["threshold"] is not None else "-"
        rows.append(
            f"<tr><td>{escape(s['node_id'])}<br><small>{escape(s['sensor_type'])}</small></td>"
            f'<td class="{"bad" if over else ""}"><b>{s["value"]:g}</b> {escape(s["unit"])}</td>'
            f"<td>{_sparkline(s['history'], s['threshold'])}</td><td>{limit}</td>"
            f'<td><span class="tag {state[1]}">{state[0]}</span>{stale}</td></tr>'
        )
    return "".join(rows)


def _approvals(control) -> str:
    pending = control.approvals.pending()
    if not pending:
        return '<p class="muted">Nothing awaiting approval.</p>'
    out = []
    for item in pending:
        action = item.action
        out.append(
            f'<div class="card"><b>{escape(action["action_type"])}</b> '
            f"<small>{escape(action['executor'])} &middot; incident {escape(item.incident_id)}"
            f"</small><pre>{escape(json.dumps(action['params'], indent=1, ensure_ascii=False))}"
            f"</pre><p>{escape(action.get('rationale') or '')}</p>"
            f"<button onclick=\"review('{item.id}','approve')\">Approve</button> "
            f"<button onclick=\"editReview('{item.id}')\">Edit &amp; approve</button> "
            f"<button class=\"danger\" onclick=\"review('{item.id}','reject')\">Reject</button>"
            f'<textarea id="params-{item.id}" hidden>'
            f"{escape(json.dumps(action['params'], ensure_ascii=False))}</textarea></div>"
        )
    return "".join(out)


def _missions(control) -> str:
    columns = {"created": [], "acknowledged": [], "in_progress": [], "done": []}
    for m in control.dispatch.list():
        bucket = m.status.value if m.status.value in columns else "done"
        flag = f' <span class="tag bad">{escape(m.exception)}</span>' if m.exception else ""
        state = "" if bucket != "done" else f" <small>({m.status.value})</small>"
        columns[bucket].append(
            f'<div class="card"><b>{escape(m.title)}</b>{state}<br><small>'
            f"{escape(m.team or 'unassigned')} &middot; {m.progress}%</small>{flag}</div>"
        )
    return "".join(
        f'<div class="col"><h3>{name.replace("_", " ")}</h3>{"".join(cards) or "<small class=muted>-</small>"}</div>'
        for name, cards in columns.items()
    )


def _activity(runtime, control) -> str:
    lines = []
    for event in _recent_events(runtime.db, FEED_LIMIT * 3):
        if event["type"] in ("observation", "observation_validated"):
            continue  # raw sensor chatter belongs in the sensor panel
        summary = event["payload"].get("reason") or event["payload"].get("change") or ""
        lines.append((event["created_at"], event["source"], event["type"], str(summary)))
    lines = lines[-FEED_LIMIT:][::-1]
    if not lines:
        return '<p class="muted">No agent activity yet.</p>'
    return (
        "<ul>"
        + "".join(
            f"<li><small>{escape(at[11:19])}</small> <b>{escape(src)}</b> {escape(kind)} "
            f"<small>{escape(summary)}</small></li>"
            for at, src, kind, summary in lines
        )
        + "</ul>"
    )


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>ATLAS responder dashboard</title><meta name="viewport" content="width=device-width">
<style>
body{{font:14px system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1a1a1a}}
header{{background:#12355b;color:#fff;padding:10px 20px;display:flex;justify-content:space-between}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px;padding:14px}}
section{{background:#fff;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px #0002}}
section.wide{{grid-column:1/-1}} h2{{margin:0 0 8px;font-size:16px}} h3{{margin:4px 0;font-size:13px;text-transform:capitalize}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:6px 4px;border-bottom:1px solid #eee;text-align:left;vertical-align:middle}}
.card{{border:1px solid #ddd;border-radius:6px;padding:8px;margin:6px 0}} pre{{margin:4px 0;font-size:12px;white-space:pre-wrap}}
.cols{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}} .muted{{color:#777}} ul{{margin:0;padding-left:16px}}
.tag{{border-radius:10px;padding:1px 8px;font-size:12px}} .ok{{background:#d4edda}} .bad{{color:#b00020}} .tag.bad{{background:#f8d7da}}
.tag.warn{{background:#fff3cd}} button{{padding:4px 10px;border:1px solid #888;border-radius:4px;background:#fff;cursor:pointer}}
button.danger{{border-color:#b00020;color:#b00020}} iframe{{width:100%;height:340px;border:0}}
</style></head><body>
<header><b>ATLAS &middot; Responder dashboard</b><span>{ledger} &middot; COP v{cop} &middot; {now}</span></header>
<main>
<section class="wide"><h2>Live sensors</h2><table><tr><th>Node</th><th>Current</th><th>Recent</th>
<th>Threshold</th><th>Status</th></tr>{sensors}</table></section>
<section><h2>Pending approvals ({pending})</h2>{approvals}</section>
<section><h2>Map</h2><iframe src="/dashboard/map" title="map"></iframe></section>
<section class="wide"><h2>Mission board</h2><div class="cols">{missions}</div></section>
<section class="wide"><h2>Agent activity</h2>{activity}</section>
</main>
<script>
const hdr=()=>{{const h={{'Content-Type':'application/json'}};const t=sessionStorage.getItem('opToken');if(t)h['X-Operator-Token']=t;return h}};
function who(){{let n=localStorage.getItem('reviewer');if(!n){{n=prompt('Your name (recorded in the ledger):');if(n)localStorage.setItem('reviewer',n)}}return n}}
async function send(id,verb,body){{const r=await fetch('/approvals/'+id+'/'+verb,{{method:'POST',headers:hdr(),body:JSON.stringify(body)}});
if(r.status==401){{const t=prompt('Operator token:');if(t){{sessionStorage.setItem('opToken',t);return send(id,verb,body)}}}}
if(!r.ok)alert((await r.json()).detail);location.reload()}}
function review(id,verb){{const n=who();if(n)send(id,verb,{{reviewer:n}})}}
function editReview(id){{const n=who();if(!n)return;const raw=prompt('Edited params (JSON):',document.getElementById('params-'+id).value);
if(raw===null)return;try{{send(id,'edit',{{reviewer:n,params:JSON.parse(raw)}})}}catch(e){{alert('Not valid JSON')}}}}
setInterval(()=>{{if(!document.hidden&&!document.querySelector('dialog[open]'))location.reload()}},8000);
</script></body></html>"""


def render_dashboard(runtime, control) -> str:
    return PAGE.format(
        ledger="ledger ok" if runtime.ledger.verify() else "LEDGER INTEGRITY FAILURE",
        cop=runtime.cop.version,
        now=runtime.clock().strftime("%H:%M:%S UTC"),
        sensors=_sensor_rows(sensor_panel(runtime)),
        pending=len(control.approvals.pending()),
        approvals=_approvals(control),
        missions=_missions(control),
        activity=_activity(runtime, control),
    )


def build_map_html(runtime, control) -> str:
    """Folium map of sensors and mission routes. Degrades if folium is not installed."""
    try:
        import folium
    except ImportError:
        return "<p>Install <code>folium</code> to see the map.</p>"
    panel = [s for s in sensor_panel(runtime) if s["location"]]
    center = (
        [panel[0]["location"]["lat"], panel[0]["location"]["lon"]] if panel else [6.6828, 80.3992]
    )  # Ratnapura, the default demo site
    fmap = folium.Map(location=center, zoom_start=13, tiles="OpenStreetMap")
    for s in panel:
        over = s["threshold"] is not None and s["value"] >= s["threshold"]
        folium.CircleMarker(
            [s["location"]["lat"], s["location"]["lon"]],
            radius=9,
            color="#b00020" if over else "#1f6feb",
            fill=True,
            popup=f"{s['node_id']} {s['sensor_type']}: {s['value']:g} {s['unit']}",
        ).add_to(fmap)
    for m in control.dispatch.list():
        points = [p for p in m.route if isinstance(p, (list, tuple)) and len(p) == 2]
        if len(points) >= 2:
            folium.PolyLine(points, color="#2e7d32", weight=4, tooltip=m.title).add_to(fmap)
    return fmap.get_root().render()
