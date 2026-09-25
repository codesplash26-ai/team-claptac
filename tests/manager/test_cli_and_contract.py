"""The command line against a real server, and the committed API contract."""

import io
import json
import socket
import threading
import time

import pytest
import uvicorn

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.manager import cli, openapi


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(Settings(database_path=":memory:"))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


def ctl(server, *argv):
    out = io.StringIO()
    code = cli.run(["--url", server, *argv], out)
    return code, out.getvalue()


def test_the_cli_lists_and_operates_agents(server):
    code, text = ctl(server, "agents", "list")
    assert code == 0 and "a4_evacuation" in text and "internal" in text and "suspended" not in text
    code, text = ctl(server, "agents", "suspend", "a6_logistics", "--reason", "cli test")
    assert code == 0 and "a6_logistics is suspended" in text
    assert "suspended" in ctl(server, "agents", "list")[1]
    assert "is active" in ctl(server, "agents", "resume", "a6_logistics")[1]
    code, text = ctl(server, "--json", "agents", "show", "a6_logistics")
    assert json.loads(text)["status"] == "active"


def test_the_cli_reports_errors_clearly(server):
    with pytest.raises(cli.CliError, match="404"):
        ctl(server, "agents", "show", "a13_ghost")
    with pytest.raises(cli.CliError, match="409"):
        ctl(server, "agents", "resume", "a6_logistics")
    with pytest.raises(cli.CliError, match="cannot reach"):
        cli.run(["--url", "http://127.0.0.1:9", "me"], io.StringIO())


def test_the_cli_runs_a_scenario_then_traces_and_evaluates_it(server):
    code, text = ctl(server, "scenario", "list")
    assert code == 0 and "ratnapura_ditwah_2025" in text and "real" in text
    assert "replaying flood" in ctl(server, "scenario", "start", "flood", "--speed", "60")[1]
    for _ in range(100):
        if "finished" in ctl(server, "scenario", "status")[1]:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    code, text = ctl(server, "traces", "list")
    assert code == 0 and "incident:ratnapura-demo-river" in text
    trace_id = text.splitlines()[2].split()[0]
    shown = ctl(server, "traces", "show", trace_id)[1]
    assert "atlas.incident" in shown and "invoke_agent" in shown
    otlp = json.loads(ctl(server, "traces", "export", trace_id, "--otlp")[1])
    assert otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    code, text = ctl(server, "eval", "run")
    assert code == 0 and "0 critical" in text  # exit status 0: safe to gate a pipeline on
    assert "no evaluation" not in ctl(server, "eval", "failures")[1]
    assert "safety.no_unauthorised_execution" in ctl(server, "eval", "list")[1]
    code, text = ctl(server, "eval", "run", "--evaluator", "safety.no_unauthorised_execution")
    assert code == 0 and "1/1 passed" in text


def test_the_cli_shows_guardrails_audit_and_identity(server):
    assert "a9_comms: may" in ctl(server, "guardrails")[1]
    assert "route" in ctl(server, "audit")[1]
    assert "development mode" in ctl(server, "me")[1]
    assert ctl(server, "approvals", "list")[0] == 0


def test_the_committed_openapi_contract_matches_the_running_api():
    """Change an endpoint? Run `python -m atlas.manager.openapi` and commit the result."""
    committed = openapi.PATH.read_text(encoding="utf-8")
    current = openapi.render(create_app(Settings(database_path=":memory:")))
    assert committed == current, "docs/manager_openapi.json is out of date"


def test_the_contract_describes_every_manager_route():
    spec = json.loads(openapi.PATH.read_text(encoding="utf-8"))
    assert all(p.startswith("/am/v1") for p in spec["paths"])
    methods = {(m.upper(), p) for p, v in spec["paths"].items() for m in v}
    assert ("POST", "/am/v1/agents/{agent}/suspend") in methods
    assert ("GET", "/am/v1/traces/{trace_id}/otlp") in methods
    assert ("POST", "/am/v1/experiments") in methods and ("GET", "/am/v1/audit") in methods
    assert "SuspendBody" in spec["components"]["schemas"]
