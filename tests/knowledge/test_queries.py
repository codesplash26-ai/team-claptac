"""impact() and neighbourhood(): what a closure affects, and a JSON view around an entity.

The hand-drawn network (minutes on each road):

        r12 (2)      r23 (2)
    J1 ----------> J2 ----------> J3        Area A sits at J1, shelter S at J3.
     \\                             ^        The short way is J1-J2-J3 = 4 minutes.
      \\  r14 (3)        r43 (3)   /         The long way  is J1-J4-J3 = 6 minutes.
       +-------------> J4 -------+
"""

import json
from datetime import datetime, timedelta, timezone

from atlas.knowledge.graph import ProjectedWorldGraph
from atlas.knowledge.ontology import YamlOntology
from atlas.knowledge.queries import impact_report, neighbourhood

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
ROADS = {"r12": (1, 2, 2.0), "r23": (2, 3, 2.0), "r14": (1, 4, 3.0), "r43": (4, 3, 3.0)}


def road(name):
    u, v, _ = ROADS[name]
    return f"road:{u}-{v}"


def make_world(*, closed=(), missions=True):
    g = ProjectedWorldGraph(YamlOntology.read())
    for j in (1, 2, 3, 4):
        g.add_entity(f"junction:{j}", "Junction")
    for name, (u, v, minutes) in ROADS.items():
        r = road(name)
        g.add_entity(r, "Road", label=name)
        g.set_attribute(r, "minutes", minutes, unit="min")
        g.add_fact(r, "from_junction", f"junction:{u}")
        g.add_fact(r, "to_junction", f"junction:{v}")
        if name in closed:
            g.set_attribute(r, "belief_open", 0.05)
    g.add_entity("area:A", "Area", label="Area A", anchor_junction="junction:1")
    g.add_entity("shelter:S", "Shelter", label="Shelter S")
    g.add_fact("shelter:S", "located_at", "junction:3")
    g.add_fact("shelter:S", "reachable_from", "area:A", safe=True, minutes=4.0)
    g.add_entity("incident:I", "Incident")
    g.add_fact("area:A", "part_of", "incident:I")
    g.add_entity("resource:R", "Resource")
    if missions:
        g.add_entity("mission:M", "Mission")
        g.add_entity("group:I/g1", "Group")
        g.add_fact("mission:M", "uses", road("r12"))
        g.add_fact("mission:M", "part_of", "incident:I")
        g.add_fact("mission:M", "destined_for", "shelter:S")
        g.add_fact("mission:M", "evacuates", "group:I/g1")
        g.add_fact("group:I/g1", "routed_over", road("r12"))
        g.add_fact("group:I/g1", "placed_at", "shelter:S")
        g.add_fact("resource:R", "assigned_to", "mission:M")
    return g


# ================================================================================= dependants


def test_closing_a_road_lists_the_missions_groups_and_units_that_depend_on_it():
    report = impact_report(make_world(), road("r12"))
    assert report.dependants == {
        "Group": ["group:I/g1"],
        "Mission": ["mission:M"],
        "Resource": ["resource:R"],
    }
    assert report.reasons["mission:M"] == f"uses {road('r12')}"
    assert report.reasons["group:I/g1"] == f"routed_over {road('r12')}"
    assert report.reasons["resource:R"] == "assigned_to mission:M"  # found through the mission


def test_the_shelter_those_missions_are_heading_to_is_flagged_as_expecting_arrivals():
    report = impact_report(make_world(), road("r12"))
    assert report.expecting == ["shelter:S"]
    # Explained by the first affected thing (in id order) that points at it, the same way every time.
    assert report.reasons["shelter:S"] == "placed_at of affected group:I/g1"
    assert impact_report(make_world(), road("r12")).reasons == report.reasons


def test_a_shelter_does_not_depend_on_the_road_so_it_is_not_a_dependant():
    report = impact_report(make_world(), road("r12"))
    assert "Shelter" not in report.dependants


def test_a_part_that_is_affected_makes_its_whole_affected_too():
    report = impact_report(make_world(), road("r12"))
    assert report.rollups == {"Incident": ["incident:I"]}
    assert "part_of" in report.reasons["incident:I"]


def test_a_road_nothing_uses_has_no_dependants():
    report = impact_report(make_world(), road("r43"))
    assert report.dependants == {} and report.rollups == {} and report.expecting == []


def test_a_relation_that_is_not_a_dependency_does_not_spread_the_impact():
    """The road merely lies in the area; that must not make the area 'depend on' the road."""
    g = make_world(missions=False)
    g.add_fact(road("r43"), "located_in", "area:A")
    report = impact_report(g, road("r43"))
    assert "area:A" not in report.flat() and report.dependants == {}


def test_impact_flows_only_one_way_along_a_dependency():
    """A mission depends on its road, not the other way round."""
    report = impact_report(make_world(), "mission:M")
    assert road("r12") not in report.flat()
    assert report.dependants == {"Resource": ["resource:R"]}


def test_a_failed_shelter_takes_its_missions_groups_and_units_with_it():
    report = impact_report(make_world(), "shelter:S")
    assert report.dependants == {
        "Group": ["group:I/g1"],
        "Mission": ["mission:M"],
        "Resource": ["resource:R"],
    }
    assert [(c.area, c.status) for c in report.access] == [("area:A", "lost")]


def test_the_flat_list_is_sorted_deduplicated_and_never_contains_the_entity():
    report = impact_report(make_world(), road("r12"))
    flat = report.flat()
    assert flat == sorted(set(flat)) and road("r12") not in flat
    assert {"mission:M", "group:I/g1", "resource:R", "shelter:S", "incident:I"} <= set(flat)


def test_the_protocol_method_returns_the_same_flat_list():
    g = make_world()
    assert g.impact(road("r12")) == impact_report(g, road("r12")).flat()


# ============================================================================ access changes


def test_closing_the_short_way_makes_the_shelter_slower_to_reach():
    (change,) = impact_report(make_world(missions=False), road("r12")).access
    assert (change.area, change.shelter, change.status) == ("area:A", "shelter:S", "longer")
    assert (change.before_minutes, change.after_minutes, change.delta_minutes) == (4.0, 6.0, 2.0)


def test_closing_the_only_way_cuts_the_shelter_off():
    g = make_world(missions=False, closed=("r14",))  # the long way is already believed closed
    (change,) = impact_report(g, road("r12")).access
    assert (change.status, change.after_minutes, change.before_minutes) == ("lost", None, 4.0)
    report = impact_report(g, road("r12"))
    assert report.shelters_affected == ["shelter:S"] and report.areas_affected == ["area:A"]


def test_a_road_off_the_shortest_way_changes_nothing():
    assert impact_report(make_world(missions=False), road("r43")).access == []
    assert impact_report(make_world(missions=False), road("r14")).access == []


def test_an_area_that_loses_access_pulls_in_the_incident_it_is_part_of():
    g = make_world(missions=False, closed=("r14",))
    report = impact_report(g, road("r12"))
    assert report.rollups == {"Incident": ["incident:I"]} and "area:A" in report.flat()


def test_a_road_already_believed_closed_changes_nothing_further_and_says_so():
    g = make_world(missions=False, closed=("r12",))
    report = impact_report(g, road("r12"))
    assert report.already_unusable and report.access == []
    # and the world, with r12 already unusable, never counted the short way to begin with:
    assert impact_report(g, road("r23")).access == []


def test_a_hard_closure_counts_as_unusable_too():
    g = make_world(missions=False)
    g.set_attribute(road("r12"), "closed", True)
    assert impact_report(g, road("r12")).already_unusable


def test_only_safe_pairs_are_considered_for_access():
    g = make_world(missions=False)
    g.add_fact("shelter:S", "reachable_from", "area:A", safe=False, minutes=4.0)
    assert impact_report(g, road("r12")).access == []  # the fact is now "not safe": nothing to lose


def test_the_road_network_is_read_from_the_graph_not_assumed():
    g = make_world(missions=False)
    before = impact_report(g, road("r12")).access[0].after_minutes
    g.set_attribute(road("r14"), "minutes", 10.0, unit="min")  # the long way gets much longer
    assert before == 6.0 and impact_report(g, road("r12")).access[0].after_minutes == 13.0


# ================================================================================ edge cases


def test_an_unknown_entity_is_reported_unknown_not_empty():
    report = impact_report(make_world(), "road:99-100")
    assert report.known is False and report.flat() == []
    assert make_world().impact("shelter:ghost") == []


def test_the_edge_prefix_is_the_same_road():
    g = make_world()
    assert impact_report(g, "edge:1-2").flat() == impact_report(g, road("r12")).flat()
    assert impact_report(g, "edge:1-2").entity == "road:1-2"


def test_an_entity_nothing_depends_on_has_an_empty_impact():
    report = impact_report(make_world(), "incident:I")
    assert (
        report.known and report.dependants == {} and report.access == [] and report.expecting == []
    )


def test_the_report_summary_is_json_ready():
    summary = impact_report(make_world(), road("r12")).summary()
    encoded = json.loads(json.dumps(summary))
    assert encoded["entity"] == road("r12") and encoded["entity_class"] == "Road"
    assert encoded["access"][0]["delta_minutes"] == 2.0
    assert set(encoded["affected"]) == set(impact_report(make_world(), road("r12")).flat())


def test_the_graph_is_not_changed_by_asking():
    g = make_world()
    before = (sorted(g.graph.nodes), sorted(g.graph.edges(keys=True)))
    impact_report(g, road("r12"))
    g.neighbourhood("incident:I")
    assert (sorted(g.graph.nodes), sorted(g.graph.edges(keys=True))) == before


def test_asking_again_after_the_graph_changes_gives_the_new_answer():
    g = make_world(missions=False)
    assert len(impact_report(g, road("r12")).access) == 1
    g.set_attribute(
        road("r43"), "belief_open", 0.05
    )  # the alternative dies: cached topology must not linger
    assert impact_report(g, road("r12")).access[0].status == "lost"


def test_without_an_ontology_nothing_is_a_dependency():
    g = ProjectedWorldGraph()
    g.add_entity("road:1-2", "Road")
    g.add_entity("mission:M", "Mission")
    g.add_fact("mission:M", "uses", "road:1-2")
    assert impact_report(g, "road:1-2").dependants == {}


# ================================================================================ neighbourhood


def test_a_neighbourhood_of_depth_one_is_the_entity_and_what_touches_it():
    view = neighbourhood(make_world(), "mission:M", depth=1)
    assert view["known"] and view["root"] == "mission:M"
    assert {n["id"] for n in view["nodes"]} == {
        "mission:M",
        road("r12"),
        "incident:I",
        "shelter:S",
        "group:I/g1",
        "resource:R",
    }
    assert {n["id"]: n["distance"] for n in view["nodes"]}["mission:M"] == 0
    assert all(n["distance"] == 1 for n in view["nodes"] if n["id"] != "mission:M")


def test_depth_zero_is_just_the_entity_and_deeper_reaches_further():
    assert [n["id"] for n in neighbourhood(make_world(), "mission:M", depth=0)["nodes"]] == [
        "mission:M"
    ]
    two = {n["id"] for n in neighbourhood(make_world(), "mission:M", depth=2)["nodes"]}
    assert "area:A" in two  # mission -> incident <- area


def test_edges_are_only_between_the_nodes_shown_and_carry_their_provenance():
    view = neighbourhood(make_world(), "mission:M", depth=1)
    shown = {n["id"] for n in view["nodes"]}
    assert all(e["source"] in shown and e["target"] in shown for e in view["edges"])
    uses = next(e for e in view["edges"] if e["predicate"] == "uses")
    assert uses["target"] == road("r12") and uses["confidence"] == 1.0 and uses["provenance"] == []


def test_node_attributes_are_included_with_units_and_confidence():
    view = neighbourhood(make_world(closed=("r14",)), road("r14"), depth=0)
    (node,) = view["nodes"]
    assert node["class"] == "Road" and node["label"] == "r14"
    assert (
        node["attributes"]["minutes"]["value"] == 3.0
        and node["attributes"]["minutes"]["unit"] == "min"
    )
    assert node["attributes"]["belief_open"]["value"] == 0.05


def test_road_topology_is_left_out_unless_asked_for():
    plain = neighbourhood(make_world(), road("r12"), depth=1)
    assert "junction:1" not in {n["id"] for n in plain["nodes"]}
    full = neighbourhood(make_world(), road("r12"), depth=1, include_topology=True)
    assert {"junction:1", "junction:2"} <= {n["id"] for n in full["nodes"]}
    assert {"from_junction", "to_junction"} <= {e["predicate"] for e in full["edges"]}


def test_a_view_can_be_limited_to_some_relations():
    view = neighbourhood(make_world(), "mission:M", depth=1, predicates=["uses"])
    assert {n["id"] for n in view["nodes"]} == {"mission:M", road("r12")}
    assert {e["predicate"] for e in view["edges"]} == {"uses"}


def test_a_large_neighbourhood_is_cut_short_and_says_so():
    view = neighbourhood(make_world(), "mission:M", depth=2, max_nodes=3)
    assert len(view["nodes"]) == 3 and view["truncated"] is True
    assert neighbourhood(make_world(), "mission:M", depth=1)["truncated"] is False


def test_facts_can_be_viewed_as_they_stood_at_a_time_and_above_a_confidence():
    g = make_world(missions=False)
    g.add_entity("mission:late", "Mission")
    g.add_entity("mission:weak", "Mission")
    g.add_fact("mission:late", "part_of", "incident:I", valid_from=T0 + timedelta(hours=1))
    g.add_fact("mission:weak", "part_of", "incident:I", confidence=0.2)
    ids = lambda **o: {n["id"] for n in neighbourhood(g, "incident:I", depth=1, **o)["nodes"]}  # noqa: E731
    assert {"mission:late", "mission:weak"} <= ids(as_of=T0 + timedelta(hours=2))
    assert "mission:late" not in ids(as_of=T0)  # not yet true at 10:00
    assert "mission:weak" not in ids(min_confidence=0.5)


def test_an_unknown_entity_gives_an_empty_view_that_says_so():
    view = neighbourhood(make_world(), "shelter:ghost")
    assert view == {
        "root": "shelter:ghost",
        "known": False,
        "depth": 2,
        "nodes": [],
        "edges": [],
        "truncated": False,
    }


def test_the_view_is_plain_json_and_deterministic():
    g = make_world()
    once = neighbourhood(g, "incident:I", depth=2)
    assert json.loads(json.dumps(once)) == once
    assert neighbourhood(g, "incident:I", depth=2) == once


def test_the_edge_prefix_finds_the_same_neighbourhood():
    g = make_world()
    assert neighbourhood(g, "edge:1-2", depth=1) == neighbourhood(g, "road:1-2", depth=1)
