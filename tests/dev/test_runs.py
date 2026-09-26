"""Runs, spans and metrics: the agent manager's data."""

import pytest

from atlas.core.events import Event, EventType
from atlas.dashboard.dev.runs import list_runs, metrics, run_spans, span_detail
from atlas.ingest.simulator import replay
from tests.conftest import approve_alerts
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))
INCIDENT = "incident:ratnapura-demo-river"


async def loop(system, runtime, clock):
    await runtime.start()
    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
        await approve_alerts(system)  # the commander says yes to the evacuation alert


async def test_a_run_is_one_incident_with_timings(system, runtime, clock):
    await loop(system, runtime, clock)
    (run,) = list_runs(runtime)
    assert run.incident_id == INCIDENT and run.state == "active" and run.cycles == 1
    assert run.spans > 20 and run.errors == 0 and run.duration_ms > 0
    assert {"a3_incident_commander", "a4_evacuation", "action_gateway", "a8_dispatch"} <= set(
        run.agents
    )


async def test_spans_form_a_tree_from_the_reading_to_the_mission(system, runtime, clock):
    await loop(system, runtime, clock)
    spans = run_spans(runtime, INCIDENT)
    by_id = {s.id: s for s in spans}
    assert all(s.duration_ms >= 0 for s in spans)
    assert all(s.parent_id is None or s.parent_id in by_id for s in spans)  # no dangling parents
    roots = [s for s in spans if s.parent_id is None]
    assert roots and all(s.kind == "input" for s in roots)  # every chain starts at an outside input

    def path(span):
        names = []
        while span:
            names.append(span.event_type)
            span = by_id.get(span.parent_id)
        return list(reversed(names))

    mission = next(s for s in spans if s.event_type == "mission_status" and s.kind == "handler")
    chain = path(mission)
    for expected in (
        "observation",
        "trigger",
        "task_assigned",
        "candidate_plan",
        "plan_proposed",
        "action_authorised",
        "mission_status",
    ):
        assert expected in chain, chain
    assert chain.index("trigger") < chain.index("plan_proposed") < chain.index("mission_status")
    depths = {s.id: s.depth for s in spans}
    assert all(depths[s.id] == depths.get(s.parent_id, -1) + 1 for s in spans)


async def test_executor_calls_are_spans_under_the_authorisation(system, runtime, clock):
    await loop(system, runtime, clock)
    executor = [s for s in run_spans(runtime, INCIDENT) if s.kind == "executor"]
    assert {s.agent for s in executor} == {"a8_dispatch", "a9_comms"}
    assert all(s.parent_id and s.status == "ok" for s in executor)


async def test_span_detail_has_input_outputs_and_cause(system, runtime, clock):
    await loop(system, runtime, clock)
    spans = run_spans(runtime, INCIDENT)
    trigger_span = next(
        s for s in spans if s.event_type == "trigger" and s.agent == "a3_incident_commander"
    )
    detail = span_detail(runtime, trigger_span.id)
    assert detail["input"]["type"] == "trigger" and detail["input"]["payload"]["area_id"]
    assert {o["type"] for o in detail["outputs"]} == {"task_assigned"}  # A3 woke and asked A5
    assert detail["caused_by"]["by"] == "a1_sentinel"
    assert detail["metadata"]["ok"] and detail["metadata"]["chain"]
    assert span_detail(runtime, "nope:a1:handler") is None


async def test_a_failing_handler_leaves_an_error_span_and_still_raises(runtime, clock):
    async def boom(event):
        raise RuntimeError("kaput")

    runtime.bus.subscribe("flaky", [EventType.INFORMATION_GAP], boom)
    with pytest.raises(RuntimeError, match="kaput"):
        await runtime.bus.publish(Event(type=EventType.INFORMATION_GAP, source="x", payload={}))
    row = runtime.db.connection.execute(
        "SELECT * FROM event_spans WHERE subscriber='flaky'"
    ).fetchone()
    assert row["ok"] == 0 and "kaput" in row["error"]
    assert runtime.db.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


async def test_metrics_summarise_agents_latency_and_control(system, runtime, clock):
    await loop(system, runtime, clock)
    data = metrics(runtime)
    agents = {(a["agent"], a["kind"]): a for a in data["agents"]}
    assert agents[("a3_incident_commander", "handler")]["runs"] >= 5
    assert agents[("a8_dispatch", "executor")]["runs"] == 5
    assert all(a["p50_ms"] <= a["p95_ms"] <= a["max_ms"] for a in data["agents"])
    assert data["events_by_type"]["trigger"] >= 1
    (latency,) = data["latency"]
    assert {"trigger", "plan_proposed", "authorised", "alert_sent"} <= set(latency["milestones"])
    assert latency["milestones"]["plan_proposed"]["sim_seconds_after_trigger"] >= 0
    control = data["control"]
    assert (
        control["actions"] == 7 and control["escalated"] == 1
    )  # the evacuation alert needed a human
    assert control["resolved"]["approved"] == 1 and control["autonomy_rate"] == 0.857
    assert data["llm"] == {"calls": 0, "tokens": 0, "cost_usd": 0.0}


async def test_the_situation_map_names_things_plainly_and_shows_who_is_going_where(
    system, runtime, clock
):
    from atlas.dashboard.dev.geo_view import friendly, situation_map

    await loop(system, runtime, clock)
    m = situation_map(system)
    (area,) = [a for a in m["areas"] if a["id"] == "ratnapura-demo-river"]
    assert area["level"] and area["incident_id"] and area["population"] == 4200
    (s1,) = [s for s in m["shelters"] if s["id"] == "ratnapura-shelter-01"]
    assert s1["name"] == "Shelter 01" and s1["expected"] == 120  # three groups of 40 on their way
    assert {t["kind"] for t in m["teams"]} == {"vehicle", "rescue_team"} and any(
        t["busy"] for t in m["teams"]
    )
    route = next(r for r in m["routes"] if r["people"])
    assert route["destination"] == "Shelter 01" and route["team"].startswith("Vehicle")
    assert friendly("ratnapura-rescue-team-01") == "Rescue team 01"
