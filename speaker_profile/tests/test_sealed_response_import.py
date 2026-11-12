"""Synthetic explicit import: exact responses, sealed provenance, no provider replay."""
import copy
import fcntl
from pathlib import Path

import pytest

from knowledge import discovery_runner as core, parallel_discovery
from speaker_profile import sealed_response_import as importer
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_corpus_discovery import cfg, root, ready, no_provider, pin, SyntheticDiscovery


@pytest.fixture
def source(ready, tmp_path):
    old = importer.source_adapter
    cfg = read_json(ready["path"])
    cfg.update(schema_version=old.CONFIG_VERSION, packet_strategy=old.planner.STRATEGY, run_id="synthetic_source_episode")
    atomic_write_json(ready["path"], cfg)

    class Model(SyntheticDiscovery):
        def complete(self, *args):
            response, usage = super().complete(*args)
            if self.calls == 1:
                row = next(row for row in response["evidence_dispositions"] if row["memberships"])
                row["memberships"].append({**row["memberships"][0], "role": "context_only", "reason": "Synthetic second role"})
            return response, usage

    model = Model()
    result = parallel_discovery.execute(old, pin(ready["path"]), command="run", workers=1, backend_factory=lambda: model)
    assert result["admitted_calls"] == 2 and result["completed_packets"] == 1
    source_root = Path(cfg["output_root"]) / cfg["run_id"]
    target = {**cfg, "schema_version": importer.target_adapter.CONFIG_VERSION, "run_id": "synthetic_import_target",
              "membership_policy": "unique_family_role.v1"}
    target_path = tmp_path / "target_config.json"
    atomic_write_json(target_path, target)
    return {"source_ref": pin(ready["path"]), "target_ref": pin(target_path), "target_path": target_path,
            "source_root": source_root, "target_root": Path(target["output_root"]) / target["run_id"],
            "manifest_sha": sha256_file(source_root / "run_manifest.json"), "ready": ready}


def execute(source, **kwargs):
    return importer.import_responses(source["source_ref"], source["manifest_sha"], source["target_ref"],
                                     expected_responses=kwargs.pop("expected_responses", 2), **kwargs)


def reseal(source, *, change_result=None, change_request=None):
    folder = sorted(source["source_root"].glob("packet_*"))[0]
    req, res, seal = [folder / ("attempt_0001." + part + ".json") for part in ("request", "result", "seal")]
    if change_request:
        value = read_json(req)
        change_request(value)
        atomic_write_json(req, value)
    value = read_json(res)
    if change_result:
        change_result(value)
    value["request_sha256"] = sha256_file(req)
    atomic_write_json(res, value)
    manifest = read_json(source["source_root"] / "run_manifest.json")
    atomic_write_json(seal, core._seal(manifest, folder.name, 1, sha256_file(req), sha256_file(res)))


def test_import_exact_provenance_zero_calls_replay_and_aggregation(source, monkeypatch):
    before = {p: p.read_bytes() for p in source["source_root"].rglob("*") if p.is_file()}
    inventory = importer.source_adapter.base.inventory
    load = inventory.load_checked
    loads = []

    def checked(ref):
        loads.append(ref)
        return load(ref)

    monkeypatch.setattr(inventory, "load_checked", checked)
    result = execute(source)
    assert len(loads) == 1
    assert result["imported_admissions"] == 2 and result["new_provider_calls"] == 0
    assert all(p.read_bytes() == value for p, value in before.items())
    receipt = read_json(Path(result["receipt"]["path"]))
    assert receipt["imported_completed_source_responses"] == 1
    assert receipt["imported_validation_failed_source_responses"] == 1
    assert receipt["new_provider_calls"] == receipt["provider_calls_this_import"] == 0
    assert receipt["source_execution"]["reported_provider_calls"] == 2
    assert not receipt["publication_authorized"] and not receipt["semantic_review_performed"]
    assert len(receipt["mappings"]) == 2
    for row in receipt["mappings"]:
        for refs in (row["source"], row["destination"]):
            assert all(sha256_file(Path(ref["path"])) == ref["sha256"] for ref in refs.values())
        original = read_json(Path(row["source"]["result"]["path"]))
        adopted = read_json(Path(row["destination"]["result"]["path"]))
        assert adopted["response"] == original["response"] and adopted["usage"] == original["usage"]
        assert adopted["status"] == "completed" and adopted["error"] is None
        old_request = read_json(Path(row["source"]["request"]["path"]))
        new_request = read_json(Path(row["destination"]["request"]["path"]))
        assert old_request["context"] == new_request["context"]
        assert old_request["schema_version"] != new_request["schema_version"]
    # No fake parallel execution receipt: this is explicitly recovery provenance.
    assert not (source["target_root"] / "parallel_executions").exists()
    with pytest.raises(FileExistsError):
        execute(source)
    # The untouched runner skips all imported packets. Default provider is forbidden.
    resumed = parallel_discovery.execute(importer.target_adapter, source["target_ref"], command="run", workers=2)
    assert resumed["status"] == "completed" and resumed["model_calls_this_execution"] == 0
    assert resumed["admitted_calls"] == 2
    assert importer.target_adapter.execute(source["target_ref"], command="validate")["status"] == "validated"
    artifact = read_json(source["target_root"] / "discovery.json")
    assert any({"uncertain", "context_only"} <= {m["role"] for m in row["memberships"]}
               for row in artifact["evidence_accounting"].values())
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("failure", ["missing_finish", "unsealed", "interrupted", "provider", "source_validation",
    "undefined_family", "duplicate_role", "tampered_request", "wrong_count", "wrong_manifest_sha", "raw_pin"])
def test_unfit_sources_never_publish_target(source, failure):
    folder = sorted(source["source_root"].glob("packet_*"))[0]
    if failure == "missing_finish":
        next(source["source_root"].glob("parallel_executions/*/finish.json")).unlink()
    elif failure == "unsealed":
        (folder / "attempt_0001.seal.json").unlink()
    elif failure == "interrupted":
        (folder / "attempt_0001.result.json").unlink()
        (folder / "attempt_0001.seal.json").unlink()
    elif failure in {"provider", "source_validation"}:
        reseal(source, change_result=lambda row: row["error"].update(phase=failure))
    elif failure == "undefined_family":
        reseal(source, change_result=lambda row: row["response"]["evidence_dispositions"][0]["memberships"][0].update(family_id="F999"))
    elif failure == "duplicate_role":
        reseal(source, change_result=lambda row: row["response"]["evidence_dispositions"][0]["memberships"][-1].update(role="uncertain"))
    elif failure == "tampered_request":
        reseal(source, change_request=lambda row: row.update(prompt="Changed prompt"))
    elif failure == "wrong_manifest_sha":
        source["manifest_sha"] = "0" * 64
    elif failure == "raw_pin":
        atomic_write_json(Path(source["ready"]["source_cfg"]["episodes"][0]["raw"]), {"changed": True})
    with pytest.raises((ValueError, OSError)):
        execute(source, expected_responses=3 if failure == "wrong_count" else 2)
    assert not source["target_root"].exists()


@pytest.mark.parametrize("change", ["model", "inventory", "max_input_chars", "budget"])
def test_generation_mismatch_and_budget_rejected(source, change):
    cfg = read_json(source["target_path"])
    if change == "model":
        cfg["model"] = "different"
    elif change == "inventory":
        cfg["inventory"]["sha256"] = "0" * 64
    elif change == "max_input_chars":
        cfg["max_input_chars"] += 1
    else:
        cfg["max_calls"] = 1
    atomic_write_json(source["target_path"], cfg)
    source["target_ref"] = pin(source["target_path"])
    with pytest.raises(ValueError):
        execute(source)
    assert not source["target_root"].exists()


def test_active_source_lock_rejected(source):
    with (source["source_root"] / ".run.lock").open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            execute(source)
    assert not source["target_root"].exists()


def test_publication_failure_preserves_staging_without_partial_target(source, monkeypatch):
    def fail(*args):
        raise OSError("Synthetic publication interruption")
    monkeypatch.setattr(importer, "_publish", fail)
    with pytest.raises(OSError, match="interruption"):
        execute(source)
    assert not source["target_root"].exists()
    stages = list(source["target_root"].parent.glob(".synthetic_import_target.sealed-import-*"))
    assert len(stages) == 1
    assert read_json(stages[0] / "recovery_import/receipt.json")["new_provider_calls"] == 0


def test_publish_never_overwrites_concurrent_target(source, monkeypatch):
    publish = importer._publish

    def race(stage, destination):
        destination.mkdir()
        atomic_write_json(destination / "other.json", {"owner": "other worker"})
        publish(stage, destination)

    monkeypatch.setattr(importer, "_publish", race)
    with pytest.raises(OSError):
        execute(source)
    assert read_json(source["target_root"] / "other.json") == {"owner": "other worker"}
    assert not (source["target_root"] / "run_manifest.json").exists()


def test_cli_calls_importer_without_backend(source, monkeypatch, capsys):
    seen = []

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        return {"status": "imported_ready_to_resume", "new_provider_calls": 0}

    monkeypatch.setattr(importer, "import_responses", fake)
    assert importer.main(["--source-config", source["source_ref"]["path"], "--source-sha256", source["source_ref"]["sha256"],
        "--source-manifest-sha256", source["manifest_sha"], "--config", source["target_ref"]["path"],
        "--sha256", source["target_ref"]["sha256"], "--expected-responses", "2", "--progress"]) == 0
    assert seen[0][1]["expected_responses"] == 2 and "new_provider_calls" in capsys.readouterr().out
