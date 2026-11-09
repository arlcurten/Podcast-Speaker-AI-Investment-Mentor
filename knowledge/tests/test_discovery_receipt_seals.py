"""Synthetic receipt corruption and interrupted close tests, without providers."""
import copy

import pytest

from knowledge import item_family_challenge as challenge
from transcription.common import atomic_write_json, read_json
from .test_item_family_challenge import ready, cfg, no_provider, Inspect, pin


@pytest.mark.parametrize("field", ["response", "usage", "status"])
def test_changed_closed_receipt_cannot_become_resumed_history(ready, field):
    ref = pin(ready["path"])
    challenge.execute(ref, command="run", backend=Inspect(), max_packets=1)
    path = ready["root"] / "packet_000000001/attempt_0001.result.json"
    value = read_json(path)
    if field == "response":
        value["response"]["pair_dispositions"][0]["disposition"] = "potential_support"
    elif field == "usage":
        value["usage"] = {"provider": "changed"}
    else:
        value.update(status="failed", error={"phase": "provider", "message": "changed"})
    atomic_write_json(path, value)
    model = Inspect()
    with pytest.raises(ValueError, match="seal mismatch"):
        challenge.execute(ref, command="run", backend=model, retry_failed=True)
    assert model.calls == 0


def test_missing_seal_requires_inspection_not_automatic_recreation(ready):
    ref = pin(ready["path"])
    challenge.execute(ref, command="run", backend=Inspect(), max_packets=1)
    seal = ready["root"] / "packet_000000001/attempt_0001.seal.json"
    seal.unlink()
    model = Inspect()
    with pytest.raises(ValueError, match="Unsealed.*inspection"):
        challenge.execute(ref, command="run", backend=model)
    assert not seal.exists() and model.calls == 0


def test_close_interruption_preserves_unsealed_result(ready, monkeypatch):
    ref = pin(ready["path"])
    original = challenge.runner.atomic_write_json

    def interrupt(path, value, **kwargs):
        if path.name.endswith(".seal.json"):
            raise OSError("Synthetic close interruption")
        return original(path, value, **kwargs)

    model = Inspect()
    with monkeypatch.context() as patch:
        patch.setattr(challenge.runner, "atomic_write_json", interrupt)
        with pytest.raises(OSError, match="close interruption"):
            challenge.execute(ref, command="run", backend=model)
    assert model.calls == 1
    path = ready["root"] / "packet_000000001/attempt_0001.result.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsealed"):
        challenge.execute(ref, command="run", backend=model, retry_failed=True)
    assert path.read_bytes() == before and model.calls == 1


def test_failed_receipt_sealed_and_retry_keeps_both_anchors(ready):
    ref = pin(ready["path"])
    bad = Inspect(lambda value, context: {**value, "pair_dispositions": []})
    assert challenge.execute(ref, command="run", backend=bad)["status"] == "failed_needs_explicit_retry"
    folder = ready["root"] / "packet_000000001"
    seal = folder / "attempt_0001.seal.json"
    before = seal.read_bytes()
    assert challenge.execute(ref, command="run", backend=Inspect(), retry_failed=True)["status"] == "completed"
    assert seal.read_bytes() == before
    assert (folder / "attempt_0002.seal.json").exists()
    assert challenge.execute(ref, command="validate")["status"] == "validated"


def test_seal_cannot_belong_to_another_attempt(ready):
    ref = pin(ready["path"])
    challenge.execute(ref, command="run", backend=Inspect(), max_packets=1)
    path = ready["root"] / "packet_000000001/attempt_0001.seal.json"
    seal = copy.deepcopy(read_json(path))
    seal["attempt"] = 2
    atomic_write_json(path, seal)
    with pytest.raises(ValueError, match="seal mismatch"):
        challenge.execute(ref, command="run", backend=Inspect())
