import types

import pytest

from atlas.orchestration.reconcile import reconcile
from atlas.tools.llm import LLMClient, LLMSettings, LLMUnavailable, RateLimiter, ToolSpec

# ------------------------------------------------------------------ LLM client


class FakeMessages:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def block(kind, **kw):
    return types.SimpleNamespace(type=kind, **kw)


def reply(*blocks):
    return types.SimpleNamespace(content=list(blocks))


def client_with(responses, **settings):
    llm = LLMClient(LLMSettings(llm_api_key="k", llm_max_retries=1, **settings))
    llm._client = types.SimpleNamespace(messages=FakeMessages(responses))
    return llm


def test_without_a_key_the_client_is_unavailable(monkeypatch):
    for key in ("LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert not LLMClient().available


async def test_unavailable_client_raises_a_specific_error():
    with pytest.raises(LLMUnavailable):
        await LLMClient(LLMSettings(llm_api_key=None)).complete(system="s", messages=[])


async def test_tool_loop_runs_tools_and_returns_final_text():
    seen = []

    async def lookup(area):
        seen.append(area)
        return {"level": "warning"}

    tool = ToolSpec(
        "lookup", "d", {"type": "object", "properties": {"area": {"type": "string"}}}, lookup
    )
    llm = client_with(
        [
            reply(block("tool_use", id="1", name="lookup", input={"area": "x"})),
            reply(block("text", text="done")),
        ]
    )
    result = await llm.complete(
        system="s", messages=[{"role": "user", "content": "go"}], tools=[tool]
    )
    assert result.text == "done" and seen == ["x"] and len(result.tool_calls) == 1
    assert [e.tools for e in llm.prompt_log] == [["lookup"], ["lookup"]]  # every call is logged


async def test_unknown_tool_and_tool_errors_are_reported_to_the_model_not_raised():
    def boom():
        raise RuntimeError("bad")

    llm = client_with(
        [
            reply(
                block("tool_use", id="1", name="nope", input={}),
                block("tool_use", id="2", name="boom", input={}),
            ),
            reply(block("text", text="ok")),
        ]
    )
    result = await llm.complete(
        system="s", messages=[], tools=[ToolSpec("boom", "d", {"type": "object"}, boom)]
    )
    assert result.text == "ok"
    sent = llm._client.messages.calls[1]["messages"][-1]["content"]
    assert "unknown tool" in sent[0]["content"] and "bad" in sent[1]["content"]


async def test_retries_then_gives_up_as_unavailable(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("atlas.tools.llm.asyncio.sleep", no_sleep)
    llm = client_with([ConnectionError("x"), ConnectionError("y")])
    with pytest.raises(LLMUnavailable):
        await llm.complete(system="s", messages=[])
    assert len(llm.prompt_log) == 2 and llm.prompt_log[0].error == "x"


async def test_rate_limiter_allows_a_burst_under_the_cap():
    limiter = RateLimiter(5)
    for _ in range(5):
        await limiter.acquire()


# ------------------------------------------------------------------- reconcile

ROUTE_OK = {"status": "ok", "planned_at": "t", "options": [{"shelter_id": "s1"}]}
TRIAGE = {"planned_at": "t"}


def placement(gid="g1", shelter="s1", route=(1, 2), people=40):
    return {
        "group_id": gid,
        "shelter_id": shelter,
        "people": people,
        "route_nodes": list(route),
        "minutes": 5.0,
        "weakest_belief": 0.8,
    }


def assignment(job, unit="v1"):
    return {
        "job_id": job,
        "unit_id": unit,
        "kind": "vehicle",
        "depot_node": 1,
        "route_nodes": [1, 2],
        "minutes": 3.0,
        "weakest_belief": 0.9,
    }


def run(
    *, route=ROUTE_OK, shelter=None, logistics=None, level="warning", previous=None, alerted=None
):
    plans = {
        "a5_triage": TRIAGE,
        "a4_evacuation": route,
        "a7_shelter": shelter or {"status": "ok", "placements": [placement()]},
        "a6_logistics": logistics or {"status": "ok", "assignments": [assignment("g1")]},
    }
    return reconcile(
        incident_id="i",
        area_id="a",
        area_name="Area",
        hazard="flood",
        level=level,
        plans=plans,
        previous_missions=previous or {},
        cycle=1,
        alerted=alerted,
    )


def types_of(result):
    return [a.action_type for a in result.actions]


def test_a_clean_plan_creates_a_mission_an_alert_and_a_sitrep():
    result = run()
    assert types_of(result) == ["create_mission", "publish_alert", "publish_sitrep"]
    mission = result.actions[0]
    assert mission.params["resources_committed"] == 1 and mission.params["group_id"] == "g1"
    assert mission.grounds and not result.escalate


def test_no_safe_route_escalates_and_creates_no_mission():
    result = run(
        route={"status": "no_safe_route", "planned_at": "t"},
        shelter={"status": "missing"},
        logistics={"status": "missing"},
    )
    assert result.escalate and "no_safe_route" in result.conflicts
    assert "create_mission" not in types_of(result)
    assert "HUMAN DECISION NEEDED" in result.summary


def test_a_group_without_units_gets_no_mission_and_escalates():
    result = run(logistics={"status": "none_served", "assignments": []})
    assert "create_mission" not in types_of(result) and "no_units:g1" in result.conflicts


def test_unplaced_people_are_reported_not_hidden():
    result = run(
        shelter={
            "status": "partial",
            "placements": [placement()],
            "unplaced": [{"group_id": "g2", "people": 40, "reason": "no_capacity"}],
        }
    )
    assert result.escalate and "unplaced:g2" in result.conflicts


def test_a4s_full_shelter_is_overridden_by_a7s_capacity_check():
    result = run(shelter={"status": "ok", "placements": [placement(shelter="s2")]})
    assert "route_preference_overridden_by_capacity" in result.conflicts
    assert result.actions[0].params["destination"] == "s2"
    assert not result.escalate  # a resolved conflict, not a reason to bother a human


def test_replan_with_a_new_route_reroutes_the_same_mission():
    previous = {"g1": {"mission_id": "m1", "shelter_id": "s1", "route": [9, 9]}}
    result = run(previous=previous)
    assert "create_mission" not in types_of(result)
    reroute = next(a for a in result.actions if a.action_type == "reroute_group")
    assert reroute.params == {"mission_id": "m1", "route": [1, 2]}


def test_replan_with_an_unchanged_route_proposes_nothing_new_for_the_mission():
    previous = {"g1": {"mission_id": "m1", "shelter_id": "s1", "route": [1, 2]}}
    assert not {"create_mission", "reroute_group", "cancel_mission"} & set(
        types_of(run(previous=previous))
    )


def test_replan_to_a_different_shelter_cancels_the_old_mission_for_a_human():
    previous = {"g1": {"mission_id": "m1", "shelter_id": "s9", "route": [1, 2]}}
    cancel = next(a for a in run(previous=previous).actions if a.action_type == "cancel_mission")
    assert cancel.params["mission_id"] == "m1"


def test_missions_in_flight_are_never_cancelled_just_because_routes_vanished():
    previous = {"g1": {"mission_id": "m1", "shelter_id": "s1", "route": [1, 2]}}
    result = run(
        route={"status": "no_safe_route", "planned_at": "t"},
        shelter={"status": "missing"},
        logistics={"status": "missing"},
        previous=previous,
    )
    assert "cancel_mission" not in types_of(result) and result.escalate
    assert "missions_without_safe_route" in result.conflicts


@pytest.mark.parametrize(
    "level,alerted,expected",
    [
        ("normal", [], False),
        ("watch", [], False),
        ("warning", [], True),
        ("warning", ["Moderate"], False),
        ("warning", ["Severe"], False),
        ("severe", ["Moderate"], True),
        ("severe", ["Severe"], False),
    ],
)
def test_alerts_only_when_warranted_never_repeated_never_softened(level, alerted, expected):
    assert ("publish_alert" in types_of(run(level=level, alerted=alerted))) is expected


def test_reconcile_is_deterministic():
    assert run().model_dump() == run().model_dump()


def test_standby_and_resupply_runs_become_missions():
    result = run(
        logistics={
            "status": "ok",
            "assignments": [
                assignment("g1"),
                assignment("rescue:a", "t1"),
                assignment("resupply:s1", "v2"),
            ],
        }
    )
    titles = [a.params["title"] for a in result.actions if a.action_type == "create_mission"]
    assert any("standby" in t for t in titles) and any("Resupply s1" in t for t in titles)
