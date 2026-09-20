"""Synthetic shelter/resource reference data: valid, routable, labelled and reproducible."""

import json

import networkx as nx
import pytest

from atlas.tools import geo, reference
from atlas.tools.reference import load_resources, load_shelters


@pytest.mark.parametrize("site", sorted(geo.SITES))
def test_every_site_has_shelters_and_a_fleet(site):
    assert len(load_shelters(site)) == len(reference.SHELTER_CAPACITIES)
    fleet = load_resources(site)
    assert {r.kind for r in fleet} == {"rescue_team", "vehicle"}
    assert len({r.id for r in fleet}) == len(fleet)


@pytest.mark.parametrize("site", sorted(geo.SITES))
def test_reference_nodes_exist_and_are_mutually_routable(site):
    rg = geo.load_site(site)
    for shelter in load_shelters(site):
        assert shelter.node in rg.graph
        assert (shelter.lat, shelter.lon) == pytest.approx(rg.pos(shelter.node), abs=1e-5)
        assert shelter.elevation_m == rg.elevation(shelter.node)
    for resource in load_resources(site):
        assert resource.node in rg.graph
        for shelter in load_shelters(site):
            assert nx.has_path(rg.graph, resource.node, shelter.node)


def test_shelters_are_on_high_ground_relative_to_the_site():
    rg = geo.load_site("ratnapura")
    median = sorted(rg.elevation(n) for n in rg.graph)[len(rg.graph) // 2]
    assert all(s.elevation_m > median for s in load_shelters("ratnapura"))


def test_data_is_labelled_synthetic():
    for name in ("shelters", "resources"):
        raw = json.loads((reference.REFERENCE_DIR / f"{name}.json").read_text())
        assert "SYNTHETIC" in raw["_note"]
    assert all(s.synthetic for s in load_shelters())
    assert all(r.synthetic for r in load_resources())


def test_committed_files_match_what_the_generator_produces(tmp_path):
    shelters, resources = reference.generate(tmp_path)
    for generated in (shelters, resources):
        committed = reference.REFERENCE_DIR / generated.name
        assert generated.read_text() == committed.read_text().replace("\r\n", "\n"), (
            f"{committed.name} is stale; run `python -m atlas.tools.reference generate`"
        )


def test_site_filter_and_invalid_rows():
    assert {s.site for s in load_shelters("kandy")} == {"kandy"}
    assert len(load_shelters()) == 3 * len(reference.SHELTER_CAPACITIES)
    with pytest.raises(ValueError):
        reference.Shelter(
            id="x", site="s", node=1, lat=0, lon=0, elevation_m=0, capacity=0
        )  # capacity must be positive
