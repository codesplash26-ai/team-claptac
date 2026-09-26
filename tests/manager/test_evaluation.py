"""Evaluators, monitors, experiments."""

import pytest

from atlas.dashboard.dev import playground as pg
from atlas.ingest.simulator import replay
from atlas.manager.evaluation import runner
from atlas.manager.evaluation.base import (
    REGISTRY,
    EvalResult,
    Level,
    Mode,
    evaluator,
)
from atlas.manager.evaluation.runner import EvalStore, MonitorWorker, evaluate
from atlas.manager.experiments import run_experiment
from tests.conftest import approve_alerts
from tests.manager.conftest import run_flood
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))
INCIDENT = "incident:ratnapura-demo-river"


async def loop(system, runtime, clock):
    await runtime.start()
    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
        await approve_alerts(system)  # the commander says yes to the evacuation alert


def by_name(result):
    out = {}
    for s in result["scores"]:
        out.setdefault(s.evaluator, []).append(s)
    return out


def test_the_catalogue_covers_every_level_and_marks_critical_safety_checks():
    levels = {e.level for e in REGISTRY.values()}
    assert levels == {Level.SPAN, Level.TRACE, Level.AGENT}
    critical = {n for n, e in REGISTRY.items() if e.critical}
    assert {
        "safety.no_unauthorised_execution",
        "safety.policy_scope_respected",
        "comms.cap_valid",
        "planning.escalates_when_no_safe_route",
    } <= critical
    assert len(REGISTRY) >= 18 and all(e.description for e in REGISTRY.values())


async def test_a_healthy_run_passes_every_safety_and_quality_check(system, runtime, clock):
    await loop(system, runtime, clock)
    result = evaluate(system, mode=Mode.MONITOR)
    scores = by_name(result)
    failed = [(s.evaluator, s.detail) for s in result["scores"] if s.passed is False]
    assert not failed, failed
    assert result["summary"].critical_failures == 0 and result["summary"].pass_rate == 1.0
    assert scores["safety.no_unauthorised_execution"][0].detail.startswith("7 executed")
    assert scores["latency.trigger_to_plan"][0].unit == "s"
    assert scores["comms.alerts_in_all_languages"][0].value == 1.0
    assert scores["reliability.tool_call_success"][0].value == 1.0


async def test_span_and_agent_levels_score_every_step_and_every_agent(system, runtime, clock):
    await loop(system, runtime, clock)
    scores = by_name(evaluate(system))
    step = scores["span.step_latency"][0]
    assert step.level is Level.SPAN and step.passed and "steps ok" in step.detail
    agents = {s.target for s in scores["agent.p95_latency"]} | {
        s.target for s in scores["agent.error_rate"]
    }
    assert {"a3_incident_commander", "a4_evacuation", "a8_dispatch"} <= agents


async def test_unauthorised_execution_is_caught_when_the_ledger_shows_it(system, runtime, clock):
    """Forge the evidence: an action that was executed with no authorisation before it."""
    await loop(system, runtime, clock)
    runtime.ledger.append(
        "action:forged:executed",
        "action",
        {
            "phase": "executed",
            "idempotency_key": "forged",
            "action_id": "x",
            "incident_id": INCIDENT,
            "executor": "a8_dispatch",
            "action_type": "create_mission",
        },
    )
    result = evaluate(system, evaluators=["safety.no_unauthorised_execution"])
    (score,) = result["scores"]
    assert (
        score.passed is False and score.critical and "without a prior authorisation" in score.detail
    )
    assert result["summary"].critical_failures == 1


async def test_a_broken_evaluator_is_reported_and_does_not_hide_the_others(system, runtime, clock):
    await loop(system, runtime, clock)

    @evaluator("custom.boom", level=Level.TRACE, description="always raises", builtin=False)
    def boom(view):
        raise RuntimeError("bug in my evaluator")

    try:
        result = evaluate(system, evaluators=["custom.boom", "reliability.no_handler_errors"])
        scores = by_name(result)
        assert (
            scores["custom.boom"][0].passed is None
            and "bug in my evaluator" in scores["custom.boom"][0].detail
        )
        assert scores["reliability.no_handler_errors"][0].passed is True
    finally:
        del REGISTRY["custom.boom"]


async def test_custom_evaluators_register_like_built_ins(system, runtime, clock):
    await loop(system, runtime, clock)

    @evaluator(
        "custom.at_least_three_missions",
        level=Level.TRACE,
        threshold=3,
        builtin=False,
        description="the plan put at least three missions on the road",
    )
    def missions(view):
        return EvalResult(value=float(len(view.missions)), passed=len(view.missions) >= 3)

    try:
        with pytest.raises(ValueError, match="already registered"):
            evaluator("custom.at_least_three_missions", level=Level.TRACE, description="dup")(
                lambda v: None
            )
        (score,) = evaluate(system, evaluators=["custom.at_least_three_missions"])["scores"]
        assert score.value == 5 and score.passed
        assert not REGISTRY["custom.at_least_three_missions"].builtin
    finally:
        del REGISTRY["custom.at_least_three_missions"]


async def test_unknown_evaluators_are_refused(system, runtime, clock):
    await loop(system, runtime, clock)
    with pytest.raises(ValueError, match="unknown evaluator"):
        evaluate(system, evaluators=["nope"])


async def test_runs_are_stored_and_can_be_read_back(system, runtime, clock):
    await loop(system, runtime, clock)
    store = EvalStore(runtime.db)
    result = evaluate(system, store, trigger="manual")
    (run,) = store.runs()
    assert (
        run["id"] == result["run_id"] and run["traces"] == 1 and run["summary"]["pass_rate"] == 1.0
    )
    scores = store.scores(run["id"])
    assert len(scores) == len(result["scores"]) and all(s["passed"] is not False for s in scores)
    assert store.scores(run["id"], failed_only=True) == []
    assert store.latest_run()["id"] == run["id"]


async def test_monitors_run_on_a_schedule_and_can_be_paused(system, runtime, clock):
    await loop(system, runtime, clock)
    store = EvalStore(runtime.db)
    worker = MonitorWorker(system, store)
    m = store.save_monitor("safety", ["safety.no_unauthorised_execution"], 30)
    assert worker.tick(now=1000.0) == [m["id"]] and worker.tick(now=1010.0) == []  # not due yet
    assert worker.tick(now=1031.0) == [m["id"]]
    assert len(store.runs()) == 2 and store.runs()[0]["trigger"] == "monitor:safety"
    store.set_monitor(m["id"], enabled=False)
    assert worker.tick(now=5000.0) == []
    store.delete_monitor(m["id"])
    assert store.monitors() == []
    for bad in (
        lambda: store.set_monitor("mon-x", enabled=True),
        lambda: store.delete_monitor("mon-x"),
    ):
        with pytest.raises(LookupError):
            bad()
    with pytest.raises(ValueError, match="at most every 5 seconds"):
        store.save_monitor("fast", ["*"], 1)
    with pytest.raises(ValueError, match="unknown evaluator"):
        store.save_monitor("bad", ["nope"], 60)


def test_an_experiment_scores_both_replays_and_names_regressions():
    scenario_data = pg.load_scenario("flood")
    same = run_experiment(scenario_data, pg.Variant(label="nothing"))
    assert same["regressions"] == [] and same["baseline"]["summary"]["critical_failures"] == 0
    assert same["baseline"]["outcome"]["missions"] == same["changed"]["outcome"]["missions"] == 5

    hurt = run_experiment(
        scenario_data, pg.Variant(label="human rejects", strict_policy=True, human="reject")
    )
    assert (
        hurt["baseline"]["outcome"]["missions"] == 5 and hurt["changed"]["outcome"]["missions"] == 0
    )
    assert "autonomy.routine_actions_run_alone" not in hurt["critical_regressions"]
    table = {r["evaluator"]: r for r in hurt["evaluators"]}
    assert table["safety.no_unauthorised_execution"]["variant_pass_rate"] in (1.0, None)
    assert hurt["changed"]["traces"] == 1

    with pytest.raises(ValueError, match="unknown evaluator"):
        run_experiment(scenario_data, pg.Variant(), ["nope"])


# ------------------------------------------------------------------------------------ HTTP


def test_evaluation_over_http(client):
    run_flood(client)
    names = {e["name"] for e in client.get("/am/v1/evaluators").json()}
    assert "safety.no_unauthorised_execution" in names
    run = client.post("/am/v1/evaluations", json={}).json()
    assert run["traces"] == 1 and run["summary"]["critical_failures"] == 0
    assert client.get("/am/v1/evaluations").json()[0]["id"] == run["run_id"]
    scores = client.get(f"/am/v1/evaluations/{run['run_id']}/scores").json()
    assert scores and {"evaluator", "passed", "detail", "critical"} <= set(scores[0])
    assert (
        client.get(
            f"/am/v1/evaluations/{run['run_id']}/scores", params={"failed_only": True}
        ).json()
        == []
    )
    assert client.get("/am/v1/evaluations/eval-nope/scores").status_code == 404
    assert client.post("/am/v1/evaluations", json={"evaluators": ["nope"]}).status_code == 422

    m = client.post(
        "/am/v1/monitors", json={"name": "always", "evaluators": ["*"], "interval_seconds": 5}
    ).json()
    assert client.get("/am/v1/monitors").json()[0]["id"] == m["id"]
    assert (
        client.patch(f"/am/v1/monitors/{m['id']}", json={"enabled": False}).json()["enabled"]
        is False
    )
    assert (
        client.post("/am/v1/monitors", json={"name": "x", "interval_seconds": 1}).status_code == 422
    )
    assert client.delete(f"/am/v1/monitors/{m['id']}").json() == {"deleted": m["id"]}
    assert client.delete(f"/am/v1/monitors/{m['id']}").status_code == 404


def test_experiments_over_http(client):
    r = client.post(
        "/am/v1/experiments",
        json={
            "scenario": "flood",
            "variant": {"label": "lose the gauge", "drop_types": ["water_level"]},
        },
    )
    assert r.status_code == 200 and r.json()["id"].startswith("exp-")
    listed = client.get("/am/v1/experiments").json()
    assert listed[0]["variant"]["label"] == "lose the gauge" and listed[0]["evaluators"]
    assert (
        client.post("/am/v1/experiments", json={"scenario": "nope", "variant": {}}).status_code
        == 404
    )
    assert (
        client.post(
            "/am/v1/experiments", json={"scenario": "flood", "variant": {}, "evaluators": ["nope"]}
        ).status_code
        == 422
    )


def test_the_running_monitor_worker_evaluates_in_the_background(client):
    run_flood(client)
    client.post(
        "/am/v1/monitors",
        json={"name": "live", "evaluators": ["latency.trigger_to_plan"], "interval_seconds": 5},
    )
    client.app.state.monitor_worker.tick(
        now=1e12
    )  # what the background loop does every few seconds
    runs = client.get("/am/v1/evaluations").json()
    assert runs and runs[0]["trigger"] == "monitor:live"


def test_guardrails_show_the_rules_and_each_agents_identity(client):
    g = client.get("/am/v1/guardrails").json()
    assert g["proposers"] == ["a3_incident_commander"]
    ident = {i["agent"]: i for i in g["identities"]}
    assert "publish_alert" in ident["a9_comms"]["allowed_actions"]
    assert "cancel_mission" in ident["a8_dispatch"]["high_consequence"]
    assert g["actions"]["publish_alert"]["consequence"] == "high" and g["principles"]
    assert runner.catalogue()  # the same catalogue the endpoint serves
