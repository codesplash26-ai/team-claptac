"""Fixtures for the ontology and world-graph tests.

`world` / `ontology`: a small hand-built world, so each bad claim in the validator tests isolates
exactly one thing that is wrong with it.
`system` / `live`: the whole ATLAS system after a rising-water replay, for tests of the projection.
"""

import pytest

from atlas.ingest.simulator import replay
from atlas.knowledge.graph import InMemoryWorldGraph
from atlas.knowledge.ontology import YamlOntology
from atlas.system import attach_all
from tests.control.conftest import fast_policy
from tests.orchestration.conftest import reading, scenario

WORLD = {
    "area:ratnapura-demo-river": "Area",
    "area:ratnapura-gn-12": "Area",
    "junction:10": "Junction",
    "junction:20": "Junction",
    "junction:30": "Junction",
    "road:10-20": "Road",
    "road:20-30": "Road",
    "shelter:ratnapura-shelter-01": "Shelter",
    "shelter:ratnapura-shelter-02": "Shelter",
    "gauge:rtp-node-01": "Gauge",
    "incident:ratnapura-demo-river": "Incident",
    "mission:m-abc123": "Mission",
    "resource:ratnapura-rescue-team-01": "Resource",
    "group:g-1": "Group",
}

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))


@pytest.fixture
def world():
    graph = InMemoryWorldGraph()
    for entity_id, entity_class in WORLD.items():
        graph.add_entity(entity_id, entity_class)
    return graph


@pytest.fixture
def ontology(world):
    return YamlOntology.load(graph=world)


@pytest.fixture
def system(runtime):
    return attach_all(runtime, policy=fast_policy())


@pytest.fixture
async def live(system, runtime, clock):
    """The whole system after a rising-water replay: an incident with five missions, three groups."""
    await runtime.start()
    await replay(runtime, RISING, clock)
    for _ in range(3):  # executors publish events that can start more work
        await system.control.gateway.run_pending()
    return system


@pytest.fixture(scope="session")
def multisite_world():
    """Ratnapura's real network with shelters from two sites, so 'shelter 1' is genuinely ambiguous."""
    from atlas.knowledge.world import WorldSources, build_world, load_areas
    from atlas.tools import geo
    from atlas.tools.reference import load_resources, load_shelters

    sources = WorldSources(
        road_graph=geo.load_site("ratnapura"),
        shelters=load_shelters("ratnapura") + load_shelters("kandy"),
        resources=load_resources("ratnapura"),
        areas=load_areas(),
    )
    return build_world(sources, YamlOntology.read())
