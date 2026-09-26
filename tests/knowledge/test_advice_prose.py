"""The advisor's free text goes through the knowledge graph's entity guard (integration of the two).

Claims are checked against the snapshot by grounding; prose is not a claim, so a summary could name a
shelter that does not exist. The intelligence service runs the guard over every sentence it publishes.
"""

from atlas.intelligence.service import IntelligenceService


async def test_real_advice_after_a_flood_names_nothing_unknown(live):
    service = live.intelligence
    await service.tick()
    (advice,) = service.list()
    assert advice.summary
    assert not [c for c in advice.concerns if c.startswith("Not verified")], advice.concerns
    assert not [s for s in advice.trace.steps if s.text.startswith("Entity guard")]


async def test_advice_naming_a_shelter_that_does_not_exist_is_flagged(live):
    service = live.intelligence
    await service.tick()
    (advice,) = service.list()
    advice.summary += " Send the second group to Shelter 99."
    service.check_prose(advice)
    assert any("Shelter 99" in c for c in advice.concerns if c.startswith("Not verified"))
    assert any(s.kind == "check" and "Shelter 99" in s.text for s in advice.trace.steps)


async def test_a_broken_guard_flags_the_advice_instead_of_passing_it(live, monkeypatch):
    service: IntelligenceService = live.intelligence
    await service.tick()
    (advice,) = service.list()
    service._projector = None
    monkeypatch.setattr(
        "atlas.knowledge.world.WorldProjector.from_system",
        classmethod(lambda cls, system: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    service.check_prose(advice)
    assert "could not be checked" in advice.concerns[-1]


async def test_a_role_title_is_not_mistaken_for_a_place(live):
    service = live.intelligence
    await service.tick()
    (advice,) = service.list()
    advice.concerns = [
        "publish_alert is waiting for the Incident Commander",
        "Ask the Area Coordinator.",
    ]
    advice.summary = "Shelter 99 is ready."
    service.check_prose(advice)
    flagged = [c for c in advice.concerns if c.startswith("Not verified")]
    assert flagged == ["Not verified (no such place is known): 'Shelter 99'."]
