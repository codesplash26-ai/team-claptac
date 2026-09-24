import pytest

from hardware.pi.outbox import Outbox


def test_restart_preserves_fifo_and_overflow_is_explicit(tmp_path):
    path = str(tmp_path / "outbox.db")
    box = Outbox(path, capacity=2)
    box.append({"measurement_id": "one"})
    box.append({"measurement_id": "two"})
    with pytest.raises(BufferError):
        box.append({"measurement_id": "three"})
    box.close()
    box = Outbox(path, capacity=2)
    key, payload = box.peek()
    assert payload["measurement_id"] == "one"
    box.acknowledge(key)
    assert box.peek()[1]["measurement_id"] == "two"
    box.close()
