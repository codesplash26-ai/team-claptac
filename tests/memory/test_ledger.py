import sqlite3

import pytest

from tests.conftest import sensor


def test_record_identity_and_action_phases(runtime):
    ledger = runtime.ledger
    assert ledger.append("proposed:x", "action", {"idempotency_key": "x", "phase": "proposed"})
    assert not ledger.append("proposed:x", "action", {"idempotency_key": "x", "phase": "proposed"})
    ledger.append("approved:x", "action", {"idempotency_key": "x", "phase": "approved"})
    with pytest.raises(ValueError):
        ledger.append("proposed:x", "action", {"different": True})
    with pytest.raises(sqlite3.IntegrityError):
        runtime.db.connection.execute("DELETE FROM ledger")
    assert ledger.verify()


async def test_graph_rebuild_is_idempotent(runtime, clock):
    await runtime.bus.publish(sensor(clock, 26))
    runtime.graph.rebuild(runtime.ledger)
    first = (set(runtime.graph.graph.nodes), set(runtime.graph.graph.edges))
    runtime.graph.rebuild(runtime.ledger)
    assert first == (set(runtime.graph.graph.nodes), set(runtime.graph.graph.edges))
    assert runtime.graph.affected_by("incident:river")
