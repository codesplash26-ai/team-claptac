import json

from atlas.agents.a9_comms import Comms
from atlas.control.models import DecisionStatus
from atlas.control.wiring import attach_control
from atlas.tools.cap import validate_cap_xml
from tests.conftest import events
from tests.control.conftest import fast_policy, proposal

ALERT = {"area": "Ratnapura Town", "event": "flood", "severity": "Severe", "scope": "local"}


async def publish(control, key="a1", **params):
    decision = control.gateway.submit(
        proposal(key, "a9_comms", "publish_alert", {**ALERT, **params})
    )
    if decision.status is DecisionStatus.ESCALATED:
        control.gateway.approve(decision.approval_id, "cmdr")
    await control.gateway.run_pending()
    return control.comms.list_alerts()


async def test_local_alert_publishes_multilingual_valid_cap(control, runtime):
    (alert,) = await publish(control)
    assert set(alert["texts"]) == {"si", "ta", "en"} and alert["missing"] == []
    assert validate_cap_xml(alert["cap_xml"]) == []
    assert "ගංවතුර" in alert["cap_xml"] and "வெள்ளம்" in alert["cap_xml"]
    assert set(alert["delivered"]) == {"si", "ta", "en"}
    sent = runtime.db.connection.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0]
    assert sent == 3
    assert events(runtime, "alert_published")[-1]["payload"]["kind"] == "alert"


async def test_district_alert_needs_human_and_sends_nothing_before(control, runtime):
    decision = control.gateway.submit(
        proposal("d1", "a9_comms", "publish_alert", {**ALERT, "scope": "district"})
    )
    assert decision.status is DecisionStatus.ESCALATED
    await control.gateway.run_pending()
    assert control.comms.list_alerts() == []
    control.gateway.approve(decision.approval_id, "cmdr")
    await control.gateway.run_pending()
    assert len(control.comms.list_alerts()) == 1


async def test_alert_without_scope_is_treated_as_district(control):
    params = {k: v for k, v in ALERT.items() if k != "scope"}
    decision = control.gateway.submit(proposal("n1", "a9_comms", "publish_alert", params))
    assert decision.status is DecisionStatus.ESCALATED


async def test_publish_is_idempotent(control, runtime):
    await publish(control)
    control.gateway.submit(proposal("a1", "a9_comms", "publish_alert", ALERT))  # replay
    await control.gateway.run_pending()
    assert len(control.comms.list_alerts()) == 1
    assert runtime.db.connection.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 3


async def test_unknown_event_without_translator_drops_only_non_english(control):
    (alert,) = await publish(control, key="u1", event="Sinkhole")
    assert set(alert["texts"]) == {"en"} and set(alert["missing"]) == {"si", "ta"}
    assert "Sinkhole" in alert["cap_xml"] and validate_cap_xml(alert["cap_xml"]) == []


async def test_translator_fills_free_text_languages(runtime):
    class Fake:
        async def translate(self, text, target, source="en"):
            return f"[{target}] {text}"

    control = attach_control(runtime, translator=Fake(), policy=fast_policy())
    (alert,) = await publish(
        control, key="t1", event="Sinkhole", instruction_text="Stay away from the pit"
    )
    assert set(alert["texts"]) == {"si", "ta", "en"}
    assert alert["texts"]["si"]["instruction"] == "[si] Stay away from the pit"


async def test_language_subset_and_bad_params_fail_safely(control):
    (alert,) = await publish(control, key="l1", languages=["si"])
    assert set(alert["texts"]) == {"si"}
    for key, bad in (
        ("b1", {"severity": "Catastrophic"}),
        ("b2", {"languages": ["fr"]}),
        ("b3", {"instruction": "run"}),
        ("b4", {"area": " "}),
    ):
        await publish(control, key=key, **bad)
        assert control.gateway.get(key).status is DecisionStatus.COMPENSATED
    assert len(control.comms.list_alerts()) == 1


async def test_cancel_alert_needs_human_and_references_the_original(control):
    (alert,) = await publish(control)
    decision = control.gateway.submit(
        proposal("x1", "a9_comms", "cancel_alert", {"alert_id": alert["id"]})
    )
    assert decision.status is DecisionStatus.ESCALATED
    control.gateway.approve(decision.approval_id, "cmdr")
    await control.gateway.run_pending()
    alerts = {a["msg_type"]: a for a in control.comms.list_alerts()}
    assert alerts["Alert"]["status"] == "cancelled"
    cancel = alerts["Cancel"]
    assert validate_cap_xml(cancel["cap_xml"]) == [] and alert["id"] in cancel["cap_xml"]
    assert "අවලංගු" in cancel["cap_xml"]
    assert set(cancel["delivered"]) == {"si", "ta", "en"}


async def test_cannot_cancel_unknown_or_already_cancelled_alert(control):
    (alert,) = await publish(control)
    for key, target in (("x2", "ATLAS-nope"), ("x3", alert["id"]), ("x4", alert["id"])):
        decision = control.gateway.submit(
            proposal(key, "a9_comms", "cancel_alert", {"alert_id": target})
        )
        control.gateway.approve(decision.approval_id, "cmdr")
        await control.gateway.run_pending()
    assert control.gateway.get("x2").status is DecisionStatus.COMPENSATED
    assert control.gateway.get("x3").status is DecisionStatus.EXECUTED
    assert control.gateway.get("x4").status is DecisionStatus.COMPENSATED  # already cancelled


async def test_failing_channel_retries_only_missing_languages_then_succeeds(runtime):
    class Flaky:
        def __init__(self):
            self.sent = []
            self.fail_next = True

        def send(self, alert_id, language, headline, body):
            if language == "ta" and self.fail_next:
                self.fail_next = False
                raise ConnectionError("gateway down")
            self.sent.append(language)

    channel = Flaky()
    control = attach_control(runtime, channel=channel, policy=fast_policy())
    await publish(control)
    assert sorted(channel.sent) == ["en", "si", "ta"]  # each language exactly once
    assert control.gateway.get("a1").status is DecisionStatus.EXECUTED


async def test_dead_channel_marks_alert_failed_for_human_review(runtime):
    class Dead:
        def send(self, *args):
            raise ConnectionError("down")

    control = attach_control(runtime, channel=Dead(), policy=fast_policy(max_retries=1))
    (alert,) = await publish(control)
    assert alert["status"] == "delivery_failed"
    assert control.gateway.get("a1").status is DecisionStatus.COMPENSATED
    assert len(events(runtime, "action_blocked")) == 1  # A3 is told


async def test_sitrep_counts_the_situation_in_three_languages(control):
    await publish(control)
    control.gateway.submit(
        proposal("mm", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0})
    )
    escalated = control.gateway.submit(
        proposal("esc", "a8_dispatch", "close_major_route", {"road_id": "r"})
    )
    await control.gateway.run_pending()
    decision = control.gateway.submit(
        proposal("s1", "a9_comms", "publish_sitrep", {"summary": "Water rising"})
    )
    assert decision.status is DecisionStatus.AUTHORISED  # routine: no human
    await control.gateway.run_pending()
    (sitrep,) = control.comms.list_sitreps()
    assert sitrep["data"] == {
        "active_missions": 1,
        "pending_approvals": 1,
        "active_alerts": 1,
        "cop_version": sitrep["data"]["cop_version"],
    }
    assert set(sitrep["texts"]) == {"si", "ta", "en"} and "Water rising" in sitrep["texts"]["en"]
    assert "Water rising" not in sitrep["texts"]["si"]  # no translator: never guess
    assert escalated.status is DecisionStatus.ESCALATED


def test_comms_is_not_a_bus_subscriber_so_nothing_can_bypass_the_gateway(control):
    assert Comms.subscribes_to == () and control.dispatch.subscribes_to == ()
    assert json.dumps(sorted(control.gateway.executors)) == '["a8_dispatch", "a9_comms"]'
