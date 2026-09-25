"""``atlasctl``: the agent manager from a terminal.  (Owner: Dinuka)

Talks to the same ``/am/v1`` API as the web console, so anything you can click you can script.

    python -m atlas.manager.cli agents list
    python -m atlas.manager.cli agents suspend a6_logistics --reason "fleet maintenance"
    python -m atlas.manager.cli traces list
    python -m atlas.manager.cli traces export <trace_id> --otlp > trace.json
    python -m atlas.manager.cli eval run
    python -m atlas.manager.cli eval failures
    python -m atlas.manager.cli approvals list
    python -m atlas.manager.cli scenario start ratnapura_ditwah_2025 --speed 5
    python -m atlas.manager.cli audit

Settings: ``--url`` or ``ATLAS_URL`` (default http://127.0.0.1:8000) and ``--token`` or ``ATLAS_TOKEN``.
Exit status is 1 on any error and, for ``eval run``, 2 when a critical safety check fails (so CI can
gate on it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


class CliError(RuntimeError):
    pass


class Client:
    def __init__(self, url: str, token: str | None):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read() or "null")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", exc.reason)
            except Exception:
                detail = exc.reason
            raise CliError(f"{exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CliError(f"cannot reach {self.url}: {exc.reason}") from exc


def table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "(none)"
    cells = [[str(r.get(c, "")) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    line = lambda parts: "  ".join(p.ljust(w) for p, w in zip(parts, widths)).rstrip()  # noqa: E731
    return "\n".join([line(columns), line(["-" * w for w in widths]), *(line(r) for r in cells)])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atlasctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", default=os.getenv("ATLAS_URL", "http://127.0.0.1:8000"))
    p.add_argument("--token", default=os.getenv("ATLAS_TOKEN"))
    p.add_argument("--json", action="store_true", help="print raw JSON")
    sub = p.add_subparsers(dest="group", required=True)

    agents = sub.add_parser("agents").add_subparsers(dest="action", required=True)
    agents.add_parser("list")
    for name in ("show", "resume"):
        agents.add_parser(name).add_argument("agent")
    s = agents.add_parser("suspend")
    s.add_argument("agent")
    s.add_argument("--reason", required=True)

    traces = sub.add_parser("traces").add_subparsers(dest="action", required=True)
    traces.add_parser("list")
    t = traces.add_parser("show")
    t.add_argument("trace_id")
    e = traces.add_parser("export")
    e.add_argument("trace_id")
    e.add_argument(
        "--otlp", action="store_true", help="print OTLP/JSON instead of pushing to the endpoint"
    )

    ev = sub.add_parser("eval").add_subparsers(dest="action", required=True)
    ev.add_parser("list")
    r = ev.add_parser("run")
    r.add_argument("--evaluator", action="append", dest="evaluators")
    ev.add_parser("failures")
    ex = ev.add_parser("experiment")
    ex.add_argument("scenario")
    ex.add_argument("--drop", action="append", default=[], help="a sensor type to lose")
    ex.add_argument("--human", choices=["approve", "reject", "ignore"], default="approve")
    ex.add_argument("--strict-policy", action="store_true")

    ap = sub.add_parser("approvals").add_subparsers(dest="action", required=True)
    ap.add_parser("list")
    for verb in ("approve", "reject"):
        a = ap.add_parser(verb)
        a.add_argument("approval_id")
        a.add_argument("--reviewer", required=True)

    sc = sub.add_parser("scenario").add_subparsers(dest="action", required=True)
    sc.add_parser("list")
    st = sc.add_parser("start")
    st.add_argument("scenario")
    st.add_argument("--speed", type=float, default=1.0)
    sc.add_parser("stop")
    sc.add_parser("status")

    sub.add_parser("audit").add_argument("--limit", type=int, default=30)
    sub.add_parser("guardrails")
    sub.add_parser("me")
    return p


def run(argv: list[str], out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    client = Client(args.url, args.token)
    say = lambda text: print(text, file=out)  # noqa: E731

    def emit(data: Any, text: str) -> None:
        say(json.dumps(data, indent=1) if args.json else text)

    g, a = args.group, getattr(args, "action", None)
    base = "/am/v1"
    code = 0
    if (g, a) == ("agents", "list"):
        data = client.call("GET", f"{base}/agents")
        rows = [
            {
                "name": x["name"],
                "kind": x["kind"],
                "layer": x["layer"],
                "status": x["status"],
                "in": x["received"],
                "out": x["produced"],
                "pending": x["pending"],
            }
            for x in data["agents"]
        ]
        emit(data, table(rows, ["name", "kind", "layer", "status", "in", "out", "pending"]))
    elif (g, a) == ("agents", "show"):
        data = client.call("GET", f"{base}/agents/{args.agent}")
        emit(data, "\n".join(f"{k}: {v}" for k, v in data.items() if not isinstance(v, list)))
    elif (g, a) == ("agents", "suspend"):
        data = client.call("POST", f"{base}/agents/{args.agent}/suspend", {"reason": args.reason})
        emit(data, f"{data['name']} is {data['status']}")
    elif (g, a) == ("agents", "resume"):
        data = client.call("POST", f"{base}/agents/{args.agent}/resume")
        emit(data, f"{data['name']} is {data['status']}")
    elif (g, a) == ("traces", "list"):
        data = client.call("GET", f"{base}/traces")
        emit(
            data,
            table(
                [{**t, "duration": f"{t['duration_ms']:.0f} ms"} for t in data],
                ["trace_id", "incident_id", "spans", "errors", "duration", "status"],
            ),
        )
    elif (g, a) == ("traces", "show"):
        data = client.call("GET", f"{base}/traces/{args.trace_id}")
        emit(
            data,
            "\n".join(
                f"{'  ' * s['depth']}{s['name']}  [{s['kind']}] "
                f"{(s['end_ns'] - s['start_ns']) / 1e6:.2f} ms  {s['status']}"
                for s in data["spans"]
            ),
        )
    elif (g, a) == ("traces", "export"):
        if args.otlp:
            say(json.dumps(client.call("GET", f"{base}/traces/{args.trace_id}/otlp")))
        else:
            data = client.call("POST", f"{base}/traces/{args.trace_id}/export")
            emit(
                data,
                f"exported {data['exported']} spans (endpoint status {data['endpoint_status']})",
            )
    elif (g, a) == ("eval", "list"):
        data = client.call("GET", f"{base}/evaluators")
        emit(data, table(data, ["name", "level", "threshold", "unit", "critical"]))
    elif (g, a) == ("eval", "run"):
        data = client.call("POST", f"{base}/evaluations", {"evaluators": args.evaluators})
        s = data["summary"]
        emit(
            data,
            f"run {data['run_id']}: {s['passed']}/{s['scored']} passed, {s['failed']} failed, "
            f"{s['critical_failures']} critical, over {data['traces']} trace(s)",
        )
        code = 2 if s["critical_failures"] else 0
    elif (g, a) == ("eval", "failures"):
        runs = client.call("GET", f"{base}/evaluations")
        if not runs:
            emit([], "no evaluation has run yet")
        else:
            data = client.call("GET", f"{base}/evaluations/{runs[0]['id']}/scores?failed_only=true")
            emit(data, table(data, ["evaluator", "level", "critical", "detail"]))
    elif (g, a) == ("eval", "experiment"):
        variant = {
            "label": "cli",
            "drop_types": args.drop,
            "human": args.human,
            "strict_policy": args.strict_policy,
        }
        data = client.call(
            "POST", f"{base}/experiments", {"scenario": args.scenario, "variant": variant}
        )
        emit(
            data,
            table(
                data["evaluators"],
                ["evaluator", "baseline_pass_rate", "variant_pass_rate", "regressed"],
            ),
        )
        code = 2 if data["critical_regressions"] else 0
    elif (g, a) == ("approvals", "list"):
        data = client.call("GET", "/approvals")
        emit(
            data,
            table(
                [
                    {
                        "id": x["id"],
                        "incident": x["incident_id"],
                        "action": x["action"]["action_type"],
                        "why": x["reason"],
                    }
                    for x in data
                ],
                ["id", "incident", "action", "why"],
            ),
        )
    elif g == "approvals":
        data = client.call(
            "POST", f"/approvals/{args.approval_id}/{a}", {"reviewer": args.reviewer}
        )
        emit(data, f"{data['idempotency_key']}: {data['status']}")
    elif (g, a) == ("scenario", "list"):
        data = client.call("GET", "/play/scenarios")
        emit(
            data,
            table(
                [{**s, "real": "real" if s["real"] else "synthetic"} for s in data],
                ["key", "real", "hazard", "events", "replay_minutes"],
            ),
        )
    elif (g, a) == ("scenario", "start"):
        data = client.call(
            "POST", "/play/scenarios/start", {"scenario": args.scenario, "speed": args.speed}
        )
        emit(data, f"replaying {data['scenario']} ({data['total']} readings) at {data['speed']}x")
    elif (g, a) in (("scenario", "stop"), ("scenario", "status")):
        path = "/play/scenarios/" + ("stop" if a == "stop" else "status")
        data = client.call("POST" if a == "stop" else "GET", path)
        emit(data, f"{data['state']} {data.get('sent', '')}/{data.get('total', '')}")
    elif g == "audit":
        data = client.call("GET", f"{base}/audit?limit={args.limit}")
        emit(data, table(data, ["actor", "role", "method", "route", "permission", "status"]))
    elif g == "guardrails":
        data = client.call("GET", f"{base}/guardrails")
        emit(
            data,
            "\n".join(
                [f"proposers: {', '.join(data['proposers'])}"]
                + [
                    f"{i['agent']}: may {', '.join(i['allowed_actions'])} (human needed for "
                    f"{', '.join(i['high_consequence']) or 'nothing'})"
                    for i in data["identities"]
                ]
            ),
        )
    elif g == "me":
        data = client.call("GET", f"{base}/me")
        emit(
            data,
            f"{data['name']} ({data['role']}){' [development mode]' if data['dev_mode'] else ''}",
        )
    return code


def main() -> None:
    try:
        sys.exit(run(sys.argv[1:]))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
