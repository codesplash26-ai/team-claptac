"""Portable reports: no CDN, build step, server, or credentials needed to read them."""

import csv
import html
import json
from collections import Counter

from atlas.dashboard.dev.timeline import timeline

STYLE = """
:root{color-scheme:light dark;--bg:#f5f7fb;--panel:#fff;--ink:#182237;--mut:#65718a;--line:#dde3ed;--accent:#5048dc}
@media(prefers-color-scheme:dark){:root{--bg:#0d1320;--panel:#161f30;--ink:#edf1f8;--mut:#a5b2c8;--line:#2d3950;--accent:#a59eff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,sans-serif}main{max-width:1150px;margin:auto;padding:42px 24px}h1{font-size:34px;letter-spacing:-1px;margin:8px 0}h2{margin-top:36px}a{color:var(--accent)}.eyebrow{letter-spacing:.18em;font-size:11px;color:var(--accent);font-weight:700}.muted,small{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:28px 0}.card,details{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}.card b{display:block;font-size:30px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut)}.pass{color:#179568}.fail{color:#e05b64}.badge{font-size:12px;border:1px solid var(--line);border-radius:20px;padding:3px 10px}.timeline{border-left:2px solid var(--line);padding-left:18px}details{margin-bottom:10px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;max-height:450px;overflow:auto}input{font:inherit;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);width:100%;margin:15px 0}.scroll{overflow:auto}.links{display:flex;gap:18px;flex-wrap:wrap}.notice{border-left:3px solid var(--accent);padding:10px 15px;color:var(--mut)}
"""


def esc(value):
    return html.escape(str(value))


def dump(value):
    return f"<pre>{esc(json.dumps(value, indent=2, default=str))}</pre>"


def shell(title, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{STYLE}</style><main><div class="eyebrow">ATLAS / SIMULATION BENCHMARK</div>{body}</main></html>'


def checks_table(checks):
    rows = "".join(
        f'<tr><td class="{"pass" if c["passed"] else "fail"}">{"PASS" if c["passed"] else "FAIL"}</td><td>{esc(c["metric"])}<br><small>{esc(c["reason"])}</small></td><td>{esc(c["operator"])} {esc(c["expected"])}</td><td>{esc(c["actual"])}</td></tr>'
        for c in checks
    )
    return (
        '<div class="scroll"><table><thead><tr><th>Result</th><th>Check</th><th>Expected</th><th>Observed</th></tr></thead><tbody>'
        + rows
        + "</tbody></table></div>"
    )


def case_report(folder, result, inputs, runtime):
    events, cursor = [], None
    while True:
        page = timeline(runtime, limit=500, before_seq=cursor)
        events = page["events"] + events
        if not page["has_older"]:
            break
        cursor = page["oldest_seq"]

    def write(name, data):
        (folder / name).write_text(json.dumps(data, indent=2, default=str) + "\n")

    write("timeline.json", events)
    write("reasoning.json", [r["payload"] for r in runtime.ledger.records("reasoning")])
    write(
        "evidence.json",
        {
            r["id"]: json.loads(r["body"])
            for r in runtime.db.connection.execute("SELECT * FROM intelligence_evidence")
        },
    )
    body = f'<h1>{esc(result["title"])}</h1><p>Seed {result["seed"]} · <span class="badge">{esc(result["status"])}</span> · <strong class="{"pass" if result["passed"] else "fail"}">{"Checks passed" if result["passed"] else "Needs investigation"}</strong></p>'
    body += '<p class="notice">Synthetic scenario; agent decisions and traces come from actual local execution. Random inputs are reproducible from the saved seed and schedule. Model advice uses offline templates. These checks do not establish field readiness.</p>'
    body += '<div class="links"><a href="task.json">Task</a><a href="schedule.json">Frozen schedule</a><a href="result.json">Result JSON</a><a href="inputs.jsonl">Input log</a><a href="events.jsonl">Event log</a><a href="spans.jsonl">Agent timings</a><a href="ledger.jsonl">Hash-linked ledger</a><a href="reasoning.json">Planner traces</a><a href="evidence.json">Evidence</a><a href="state.sqlite">SQLite checkpoint</a></div>'
    body += f'<div class="cards"><div class="card"><b>{len(inputs)}</b>inputs attempted</div><div class="card"><b>{len(events)}</b>events recorded</div><div class="card"><b>{result["duration_seconds"]}s</b>wall time</div><div class="card"><b>{result["simulated_seconds"]}s</b>simulated time</div></div>'
    if result["error"]:
        body += f'<p class="fail">{esc(result["error"])}</p>'
    body += "<h2>Expected behaviour and results</h2>" + checks_table(result["checks"])
    body += "<h2>Inputs and random disruptions</h2>" + "".join(
        f"<details><summary>{esc(i['step']['at_seconds'])}s · {esc(i['step']['id'])} · {esc(i['step']['kind'])}</summary>{dump(i)}</details>"
        for i in inputs
    )
    body += '<h2>Event timeline</h2><p class="muted">Ledger order; expand a row for payload, measured work and causal references. Planner simulations are stored separately in Planner traces.</p><input id="search" aria-label="Search timeline" placeholder="Search events, agents, decisions…"><div class="timeline">'
    for e in events:
        work = ", ".join(
            f"{a['agent']}: {a['duration_ms']:.2f} ms ({a['status']})" for a in e["activity"]
        )
        body += f'<details class="event"><summary>#{e["seq"]} · {esc(e["summary"])}</summary><p class="muted">{esc(e["created_at"])} · {esc(e["category"])}<br>{esc(work)}</p>{dump(e)}</details>'
    body += "</div><h2>All metrics</h2>" + dump(result["metrics"])
    body += '<script>document.getElementById("search").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll(".event").forEach(e=>e.hidden=!e.textContent.toLowerCase().includes(q));});</script>'
    (folder / "report.html").write_text(shell(result["title"], body))


def suite_report(folder, state):
    results = state["results"]
    passed = sum(r["passed"] for r in results)
    agents = Counter()
    for r in results:
        agents.update(r["metrics"]["agent_activity"])
    body = f'<h1>Response simulation results</h1><p class="muted">{esc(state["id"])} · {esc(state["state"])} · seeds {esc(state["seeds"])}</p><p class="notice">{esc(state["limitations"])}</p>'
    body += f'<div class="cards"><div class="card"><b>{len(results)}/{state["total"]}</b>cases recorded</div><div class="card"><b>{passed}</b>all checks passed</div><div class="card"><b>{len(results) - passed}</b>failed / incomplete</div><div class="card"><b>{len([a for a in agents if a.startswith("a") and a[1:2].isdigit()])}/9</b>core agents exercised</div></div>'
    body += '<div class="links"><a href="summary.json">Summary JSON</a><a href="results.csv">Results CSV</a><a href="manifest.json">Reproduction manifest</a></div><h2>Scenarios</h2><div class="scroll"><table><thead><tr><th>Task</th><th>Seed</th><th>Result</th><th>Checks</th><th>Events</th><th>Missions</th><th>Replans</th><th>Wall time</th></tr></thead><tbody>'
    for r in results:
        m = r["metrics"]
        body += f'<tr><td><a href="{esc(r["case_id"])}/report.html">{esc(r["title"])}</a></td><td>{r["seed"]}</td><td class="{"pass" if r["passed"] else "fail"}">{"PASS" if r["passed"] else esc(r["status"] if r["status"] != "completed" else "FAIL")}</td><td>{sum(c["passed"] for c in r["checks"])}/{len(r["checks"])}</td><td>{sum(m["events"].values())}</td><td>{m["missions"]["total"]}</td><td>{m["incidents"]["replans"]}</td><td>{r["duration_seconds"]}s</td></tr>'
    body += (
        "</tbody></table></div><h2>Agent coverage</h2>"
        + dump(dict(agents))
        + '<p class="muted">Counts include handler invocations that intentionally emitted no output. Metrics are per scenario; unplaced/unserved counts are summed across planning cycles and are not distinct people or jobs. Live model quality, calibrated hazard accuracy and physical delivery are not scored.</p>'
    )
    (folder / "report.html").write_text(shell("ATLAS benchmark results", body))
    with (folder / "results.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "task",
                "seed",
                "status",
                "passed",
                "checks_passed",
                "checks_total",
                "events",
                "missions",
                "replans",
                "wall_seconds",
                "schedule_sha256",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r["task_id"],
                    r["seed"],
                    r["status"],
                    r["passed"],
                    sum(c["passed"] for c in r["checks"]),
                    len(r["checks"]),
                    sum(r["metrics"]["events"].values()),
                    r["metrics"]["missions"]["total"],
                    r["metrics"]["incidents"]["replans"],
                    r["duration_seconds"],
                    r["schedule_sha256"],
                ]
            )
