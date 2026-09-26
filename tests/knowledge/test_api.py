"""The knowledge routes: read-only, permissioned, and honest about what does not exist."""

import pytest
from fastapi.testclient import TestClient

from atlas.api.knowledge_routes import router as knowledge_router
from atlas.api.main import create_app
from atlas.core.config import Settings
from tests.manager.conftest import as_

RSH1 = "shelter:ratnapura-shelter-01"
AREA = "area:ratnapura-demo-river"


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


@pytest.fixture
def secured(monkeypatch):
    from tests.manager.conftest import TOKENS

    monkeypatch.setenv("ATLAS_TOKENS", TOKENS)
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


@pytest.fixture
def some_road(client):
    return client.get("/knowledge/entities", params={"class": "Road", "limit": 1}).json()[
        "entities"
    ][0]["id"]


# ------------------------------------------------------------------------------ what is there


def test_the_vocabulary_lists_classes_relations_attributes_and_hazards(client):
    body = client.get("/knowledge/ontology").json()
    assert {"Shelter", "Road", "Mission", "Group", "Gauge", "Hazard"} <= set(body["classes"])
    assert body["relations"]["uses"]["dependency"] is True
    assert body["attributes"]["water_level"]["units"]
    assert body["hazards"]["flood"]["status"] == "live"
    assert body["hazards"]["earthquake"]["status"] == "history_only"
    assert set(body["hazards"]) >= {"flood", "landslide", "earthquake", "tsunami", "drought"}


def test_the_summary_counts_what_is_in_the_world(client):
    body = client.get("/knowledge/summary").json()
    assert body["entities"]["Road"] > 100 and body["entities"]["Shelter"] >= 1
    assert body["facts"] > 0 and body["skipped"] == {}


def test_entities_can_be_listed_by_class_and_the_list_says_when_it_is_cut(client):
    body = client.get("/knowledge/entities", params={"class": "Shelter", "limit": 1}).json()
    assert body["total"] >= 2 and len(body["entities"]) == 1 and body["truncated"] is True
    assert body["entities"][0]["class"] == "Shelter" and body["entities"][0]["label"]
    assert client.get("/knowledge/entities", params={"class": "Bridge"}).status_code == 422
    assert client.get("/knowledge/entities", params={"limit": 0}).status_code == 422


def test_one_entity_shows_its_attributes_and_facts_with_where_they_came_from(client):
    body = client.get("/knowledge/entity", params={"id": RSH1}).json()
    assert body["id"] == RSH1 and body["class"] == "Shelter"
    capacity = body["attributes"]["capacity"]
    assert capacity["unit"] == "people" and capacity["source"] and capacity["provenance"]
    assert any(f["predicate"] == "located_in" for f in body["out"])


def test_an_edge_id_is_understood_as_a_road_id(client, some_road):
    edge = some_road.replace("road:", "edge:")
    assert client.get("/knowledge/entity", params={"id": edge}).json()["id"] == some_road


def test_an_entity_that_does_not_exist_is_a_404_not_an_empty_answer(client):
    for path in ("entity", "neighbourhood", "impact"):
        key = "id" if path == "entity" else "entity"
        response = client.get(f"/knowledge/{path}", params={key: "shelter:ratnapura-shelter-99"})
        assert response.status_code == 404, path
        assert "shelter-99" in response.json()["detail"]


def test_the_neighbourhood_is_a_drawable_graph_and_respects_its_limits(client):
    body = client.get("/knowledge/neighbourhood", params={"entity": RSH1, "depth": 1}).json()
    ids = {n["id"] for n in body["nodes"]}
    assert RSH1 in ids and body["root"] == RSH1 and len(ids) > 1
    assert all(e["source"] in ids and e["target"] in ids for e in body["edges"])
    cut = client.get(
        "/knowledge/neighbourhood", params={"entity": AREA, "depth": 2, "max_nodes": 3}
    ).json()
    assert len(cut["nodes"]) <= 3 and cut["truncated"] is True
    assert (
        client.get("/knowledge/neighbourhood", params={"entity": RSH1, "depth": 9}).status_code
        == 422
    )


def test_impact_of_a_road_reports_what_depends_on_it(client, some_road):
    body = client.get("/knowledge/impact", params={"entity": some_road}).json()
    assert body["entity"] == some_road and body["known"] is True
    assert body["entity_class"] == "Road"
    for key in ("dependants", "expecting", "access", "already_unusable"):
        assert key in body


# ---------------------------------------------------------------------------- the guard routes


def test_a_claim_about_a_real_shelter_with_evidence_is_accepted_and_an_invented_one_is_not(client):
    real_evidence = "sensor_map:ratnapura-demo-river"
    good = {"subject": RSH1, "predicate": "capacity", "object": 120, "unit": "people"}
    body = client.post(
        "/knowledge/validate",
        json={
            "claims": [
                {**good, "evidence_ids": [real_evidence]},
                {
                    **good,
                    "subject": "shelter:ratnapura-shelter-99",
                    "evidence_ids": [real_evidence],
                },
                {**good, "evidence_ids": ["made-up"]},
                {**good, "evidence_ids": []},
            ]
        },
    ).json()
    verdicts = [(r["accepted"], [v["code"] for v in r["violations"]]) for r in body["results"]]
    assert verdicts == [
        (True, []),
        (False, ["unknown_entity"]),
        (False, ["fake_evidence"]),
        (False, ["no_evidence"]),
    ]
    assert body["accepted"] is False
    assert "shelter-99" in body["results"][1]["violations"][0]["message"]


def test_evidence_checking_can_be_left_to_the_caller(client):
    claim = {"subject": RSH1, "predicate": "capacity", "object": 120, "unit": "people"}
    body = client.post(
        "/knowledge/validate", json={"claims": [claim], "check_evidence": False}
    ).json()
    assert body["accepted"] is True


def test_a_malformed_claim_or_an_oversized_batch_is_refused(client):
    assert (
        client.post("/knowledge/validate", json={"claims": [{"subject": RSH1}]}).status_code == 422
    )
    too_many = [{"subject": RSH1, "predicate": "capacity", "object": 1}] * 101
    assert client.post("/knowledge/validate", json={"claims": too_many}).status_code == 422


def test_prose_that_names_something_invented_is_flagged_with_a_reason(client):
    body = client.post(
        "/knowledge/check", json={"text": "Send them to Ratnapura shelter 1 or to Shelter 99."}
    ).json()
    assert body["ok"] is False and body["problems"] == [
        "'Shelter 99' does not match anything known"
    ]
    assert [m["entity_id"] for m in body["mentions"]["resolved"]] == [RSH1]
    assert client.post("/knowledge/check", json={"text": "Ratnapura shelter 1 is open."}).json()[
        "ok"
    ]


def test_mentions_lists_resolved_ambiguous_and_unresolved(client):
    body = client.post("/knowledge/mentions", json={"text": "Shelter 99 and the Kalu Ganga"}).json()
    assert body["unresolved"] == ["Shelter 99"]
    assert [m["entity_id"] for m in body["resolved"]] == [AREA]


def test_link_resolves_a_name_and_never_guesses(client):
    found = client.get("/knowledge/link", params={"text": "Ratnapura shelter 1"}).json()
    assert found["entity_id"] == RSH1
    invented = client.get("/knowledge/link", params={"text": "shelter:ratnapura-shelter-99"}).json()
    assert invented["entity_id"] is None and invented["alternatives"] == []
    assert client.get("/knowledge/link", params={"text": "x" * 501}).status_code == 422


def test_oversized_text_is_refused(client):
    assert client.post("/knowledge/check", json={"text": "a" * 20_001}).status_code == 422


# ------------------------------------------------------------------------- safe and permissioned


def test_the_routes_change_nothing(client, some_road):
    """No belief changes and nothing but audit rows is written (the POSTs are audited, reads are not)."""
    runtime = client.app.state.runtime

    def state():
        rows = runtime.db.connection.execute(
            "SELECT kind, COUNT(*) FROM ledger GROUP BY kind ORDER BY kind"
        ).fetchall()
        return runtime.cop.version, [tuple(r) for r in rows if r[0] != "audit"]

    before = state()
    client.get("/knowledge/impact", params={"entity": some_road})
    client.post("/knowledge/check", json={"text": "Shelter 99"})
    client.post("/knowledge/validate", json={"claims": []})
    assert state() == before
    audits = runtime.ledger.records("audit")
    assert [a["payload"]["route"] for a in audits] == ["/knowledge/check", "/knowledge/validate"]


def test_every_knowledge_route_asks_for_a_permission(client):
    routes = knowledge_router.routes  # app.routes nests included routers, so read the router itself
    assert len(routes) == 10
    for route in routes:
        perms = [getattr(d.call, "atlas_permission", None) for d in route.dependant.dependencies]
        assert any(perms), route.path


def test_with_tokens_configured_a_reader_may_read_and_a_stranger_may_not(secured):
    assert secured.get("/knowledge/ontology").status_code == 401
    assert (
        secured.get("/knowledge/ontology", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )
    for role in ("view", "cmd", "dev", "admin"):
        assert secured.get("/knowledge/ontology", headers=as_(role)).status_code == 200, role
    assert secured.post("/knowledge/check", json={"text": "x"}).status_code == 401
    assert (
        secured.post("/knowledge/check", json={"text": "x"}, headers=as_("view")).status_code == 200
    )
