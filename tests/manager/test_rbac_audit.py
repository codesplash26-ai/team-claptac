"""Permissions, roles and the audit trail."""

import json

import pytest

from atlas.manager import audit, rbac
from tests.manager.conftest import as_


def test_every_permission_is_granted_by_some_role_and_roles_only_use_known_ones():
    assert rbac.ungranted() == set()
    assert all(perms <= rbac.ALL for perms in rbac.ROLES.values())
    assert rbac.ROLES["admin"] == rbac.ALL
    assert rbac.APPROVAL_DECIDE not in rbac.ROLES["viewer"] | rbac.ROLES["developer"]
    assert rbac.AGENT_SUSPEND not in rbac.ROLES["developer"] | rbac.ROLES["ai_lead"]


def test_every_mutating_route_declares_a_permission(client):
    """Adding an endpoint without a permission fails the build, so it cannot ship un-audited."""
    missing = []
    for route in client.app.routes:
        methods = getattr(route, "methods", None) or set()
        if not methods & audit.MUTATING or route.path in audit.PUBLIC_MUTATING:
            continue
        perms = [getattr(d.call, "atlas_permission", None) for d in route.dependant.dependencies]
        if not any(perms):
            missing.append((sorted(methods), route.path))
    assert not missing, missing
    assert client.app.routes  # the scan saw something


def test_development_mode_is_open_and_says_so(client):
    me = client.get("/am/v1/me").json()
    assert me["dev_mode"] and me["role"] == "admin" or me["dev_mode"]
    r = client.post("/am/v1/agents/a6_logistics/suspend", json={"reason": "testing dev mode"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"


def test_a_token_is_required_and_the_role_decides(secured):
    assert secured.get("/am/v1/agents").status_code == 401  # reads are protected too
    assert secured.get("/am/v1/agents", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert secured.get("/am/v1/agents", headers=as_("view")).status_code == 200
    body = {"reason": "maintenance window"}
    url = "/am/v1/agents/a6_logistics/suspend"
    assert secured.post(url, json=body, headers=as_("view")).status_code == 403
    assert secured.post(url, json=body, headers=as_("dev")).status_code == 403
    assert secured.post(url, json=body, headers=as_("ai")).status_code == 403
    assert secured.post(url, json=body, headers=as_("ops")).status_code == 200
    assert secured.post("/am/v1/agents/a6_logistics/resume", headers=as_("cmd")).status_code == 200


def test_who_may_do_what(secured):
    assert secured.post("/play/redteam", headers=as_("view")).status_code == 403
    assert secured.post("/play/redteam", headers=as_("cmd")).status_code == 403
    assert secured.post("/play/redteam", headers=as_("dev")).status_code == 200
    assert (
        secured.post(
            "/approvals/ap-x/approve", json={"reviewer": "x"}, headers=as_("dev")
        ).status_code
        == 403
    )  # a developer cannot decide an escalation
    assert (
        secured.post(
            "/approvals/ap-x/approve", json={"reviewer": "x"}, headers=as_("cmd")
        ).status_code
        == 404
    )  # a commander can: the approval just does not exist
    assert secured.get("/am/v1/audit", headers=as_("view")).status_code == 403
    assert secured.get("/am/v1/audit", headers=as_("ops")).status_code == 200


def test_me_reports_the_callers_role_and_permissions(secured):
    me = secured.get("/am/v1/me", headers=as_("ai")).json()
    assert me["name"] == "ai" and me["role"] == "ai_lead" and not me["dev_mode"]
    assert "eval:run" in me["permissions"] and "agent:suspend" not in me["permissions"]


def test_the_older_single_operator_token_still_works_with_reads_open(client, monkeypatch):
    monkeypatch.setenv("ATLAS_OPERATOR_TOKEN", "old")
    assert client.get("/am/v1/agents").status_code == 200  # reads stay open
    body = {"reason": "legacy token"}
    assert client.post("/am/v1/agents/a7_shelter/suspend", json=body).status_code == 401
    assert (
        client.post(
            "/am/v1/agents/a7_shelter/suspend", json=body, headers={"X-Operator-Token": "old"}
        ).status_code
        == 200
    )


def test_bad_token_configuration_is_refused(monkeypatch):
    monkeypatch.setenv("ATLAS_TOKENS", '[{"token":"","role":"admin"}]')
    with pytest.raises(ValueError, match="empty token"):
        rbac._configured()
    monkeypatch.setenv("ATLAS_TOKENS", '[{"token":"x","role":"wizard"}]')
    with pytest.raises(ValueError, match="unknown role"):
        rbac._configured()


# ------------------------------------------------------------------------------ audit


def test_mutations_are_audited_with_who_what_and_outcome_but_no_bodies(secured):
    secured.post(
        "/am/v1/agents/a6_logistics/suspend", json={"reason": "SECRET-REASON"}, headers=as_("ops")
    )
    secured.post(
        "/am/v1/agents/a6_logistics/suspend", json={"reason": "second attempt"}, headers=as_("view")
    )  # refused: still recorded
    entries = secured.get("/am/v1/audit", headers=as_("ops")).json()
    ok = next(e for e in entries if e["actor"] == "ops" and e["route"].endswith("/suspend"))
    assert ok["role"] == "platform_engineer" and ok["method"] == "POST" and ok["status"] == 200
    assert ok["permission"] == "agent:suspend" and ok["params"] == {"agent": "a6_logistics"}
    assert ok["route"] == "/am/v1/agents/{agent}/suspend"  # the template, not user input
    denied = next(e for e in entries if e["actor"] == "vic")
    assert denied["status"] == 403
    blob = json.dumps(entries)
    assert "SECRET-REASON" not in blob and "t-ops" not in blob and "second attempt" not in blob


def test_reads_are_not_audited_but_reading_the_audit_log_is(secured):
    secured.get("/am/v1/agents", headers=as_("view"))
    secured.get("/am/v1/audit", headers=as_("ops"))
    entries = secured.get("/am/v1/audit", headers=as_("ops")).json()
    routes = [e["route"] for e in entries]
    assert "/am/v1/agents" not in routes and "/am/v1/audit" in routes


def test_sensor_ingest_is_not_behind_a_person_token(secured):
    r = secured.post(
        "/ingest/sensor",
        json={
            "node_id": "n",
            "sensor_id": "s",
            "type": "water_level",
            "value": 3,
            "unit": "cm",
            "timestamp": "2026-09-25T00:00:00+00:00",
        },
    )
    assert r.status_code == 202
    assert not [
        e
        for e in secured.get("/am/v1/audit", headers=as_("ops")).json()
        if e["route"] == "/ingest/sensor"
    ]


def test_the_audit_trail_is_in_the_tamper_evident_ledger(secured):
    secured.post(
        "/am/v1/agents/a6_logistics/suspend", json={"reason": "check ledger"}, headers=as_("ops")
    )
    assert secured.get("/ledger/verify", headers=as_("view")).json() == {"valid": True}
    kinds = {
        r["kind"] for r in secured.get("/ledger", params={"limit": 500}, headers=as_("view")).json()
    }
    assert "audit" in kinds
