"""Mandatory audit continuity across import, resume, validate and bridge export."""
from pathlib import Path

import pytest

from knowledge import parallel_discovery, postdiscovery_bridge_v1 as bridge
from speaker_profile import import_aware_episode_discovery as adapter, sealed_response_import_v2 as importer
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_sealed_response_import import source, cfg, root, ready, no_provider, pin


@pytest.fixture
def declared(source, tmp_path):
    origin = importer.declare(source["source_ref"], source["manifest_sha"], expected_responses=2,
                              output=tmp_path / "origin.json")
    value = read_json(source["target_path"])
    value.update(schema_version=adapter.CONFIG_VERSION, import_origin=origin)
    atomic_write_json(source["target_path"], value)
    source["target_ref"] = pin(source["target_path"])
    source["origin"] = origin
    return source


def completed(data):
    result = importer.import_responses(data["target_ref"])
    assert result["new_provider_calls"] == 0
    resumed = parallel_discovery.execute(adapter, data["target_ref"], command="run", workers=2)
    assert resumed["model_calls_this_execution"] == 0 and resumed["admitted_calls"] == 2
    spec = {"adapter": adapter.__name__, "adapter_sha256": sha256_file(Path(adapter.__file__)),
            "run_version": adapter.RUN_VERSION, "request_version": adapter.REQUEST_VERSION,
            "config": data["target_ref"], "artifact": pin(data["target_root"] / "discovery.json")}
    return spec


def reseal_import(data):
    path = data["target_root"] / "recovery_import/receipt.json"
    seal_path = path.with_name("seal.json")
    seal = read_json(seal_path)
    seal["receipt_sha256"] = sha256_file(path)
    atomic_write_json(seal_path, seal)


def reject_all(data, spec):
    with pytest.raises((ValueError, OSError)):
        parallel_discovery.execute(adapter, data["target_ref"], command="run", workers=2)
    with pytest.raises((ValueError, OSError)):
        adapter.execute(data["target_ref"], command="validate")
    with pytest.raises((ValueError, OSError)):
        bridge.replay_completed(spec)
    with pytest.raises((ValueError, OSError)):
        adapter.project_completed(spec)


def test_no_target_or_origin_sidecar_never_allows_ordinary_bootstrap(declared, monkeypatch):
    def forbidden(*_):
        raise AssertionError("Missing import should fail before inventory replay")
    monkeypatch.setattr(adapter.base.base.base.inventory, "load_checked", forbidden)
    with pytest.raises(ValueError, match="Mandatory recovery"):
        adapter._prepare(declared["target_ref"])
    assert not declared["target_root"].exists()


def test_import_resume_validate_project_preserve_origin_and_zero_new_calls(declared):
    original = {p: p.read_bytes() for p in declared["source_root"].rglob("*") if p.is_file()}
    spec = completed(declared)
    assert adapter.execute(declared["target_ref"], command="validate")["status"] == "validated"
    value = adapter.project_completed(spec)
    assert value["import_origin"]["declaration"] == declared["origin"]
    assert value["import_origin"]["imported_admissions"] == 2
    assert value["import_origin"]["new_provider_calls_during_import"] == 0
    parent = bridge.replay_completed(spec)
    for key in ("declaration", "receipt", "seal"):
        ref = value["import_origin"][key]
        assert parent.pins[Path(ref["path"])] == ref["sha256"]
    assert all(path.read_bytes() == content for path, content in original.items())
    receipt = read_json(declared["target_root"] / "recovery_import/receipt.json")
    assert receipt["schema_version"] == adapter.IMPORT_VERSION and receipt["import_origin"] == declared["origin"]
    for row in receipt["mappings"]:
        old = read_json(Path(row["source"]["result"]["path"]))
        new = read_json(Path(row["destination"]["result"]["path"]))
        assert old["response"] == new["response"] and old["usage"] == new["usage"]
    with pytest.raises(ValueError):
        adapter.base._prepare(declared["target_ref"])


@pytest.mark.parametrize("remove", ["origin", "receipt", "seal"])
def test_delete_required_provenance_blocks_all_consumers(declared, remove):
    spec = completed(declared)
    parent = bridge.replay_completed(spec)
    path = Path(declared["origin"]["path"]) if remove == "origin" else declared["target_root"] / "recovery_import" / (remove + ".json")
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        parent.check()
    reject_all(declared, spec)


@pytest.mark.parametrize("change", ["origin", "source_result", "source_seal", "target_result", "target_seal",
    "mapping_count", "duplicate_mapping", "target_identity", "copied_usage", "new_calls", "wrong_origin_ref", "usage_attribution", "extra_key"])
def test_rehashed_local_sidecars_cannot_hide_tampering(declared, change):
    spec = completed(declared)
    receipt_path = declared["target_root"] / "recovery_import/receipt.json"
    receipt = read_json(receipt_path)
    if change == "origin":
        path = Path(declared["origin"]["path"])
        value = read_json(path)
        value["expected_responses"] = 1
        atomic_write_json(path, value)
    elif change in {"source_result", "source_seal", "target_result", "target_seal", "copied_usage"}:
        side = "source" if change.startswith("source") else "destination"
        part = "seal" if change.endswith("seal") else "result"
        path = Path(receipt["mappings"][0][side][part]["path"])
        value = read_json(path)
        if part == "seal":
            value["request_sha256"] = "0" * 64
        else:
            value["usage"] = {"changed": True}
        atomic_write_json(path, value)
        receipt["mappings"][0][side][part]["sha256"] = sha256_file(path)
        if change == "copied_usage":
            result_ref = receipt["mappings"][0]["destination"]["result"]
            seal_ref = receipt["mappings"][0]["destination"]["seal"]
            value = read_json(Path(seal_ref["path"]))
            value["result_sha256"] = result_ref["sha256"]
            atomic_write_json(Path(seal_ref["path"]), value)
            seal_ref["sha256"] = sha256_file(Path(seal_ref["path"]))
    elif change == "mapping_count":
        receipt["mappings"].pop()
        receipt["imported_admissions"] = 1
    elif change == "duplicate_mapping":
        receipt["mappings"][1] = receipt["mappings"][0]
    elif change == "target_identity":
        receipt["target_config"]["sha256"] = "0" * 64
    elif change == "new_calls":
        receipt["new_provider_calls"] = 2
    elif change == "usage_attribution":
        receipt["usage_authority"] = "Synthetic incorrect new-spend claim"
    elif change == "extra_key":
        receipt["unknown_extra_claim"] = True
    else:
        receipt["import_origin"]["sha256"] = "0" * 64
    atomic_write_json(receipt_path, receipt)
    reseal_import(declared)
    reject_all(declared, spec)


def test_v2_publication_rechecks_origin_before_no_replace(declared, monkeypatch):
    original = adapter.audit_import

    def reject(prepared, storage_root=None):
        if storage_root is not None:
            raise ValueError("Synthetic origin recheck failure")
        return original(prepared, storage_root)

    monkeypatch.setattr(adapter, "audit_import", reject)
    with pytest.raises(ValueError, match="origin recheck"):
        importer.import_responses(declared["target_ref"])
    assert not declared["target_root"].exists()


def test_later_native_packet_allowed_without_duplicate_import_accounting(source, tmp_path):
    from .test_corpus_discovery import SyntheticDiscovery
    # Synthetic one-admission source; second planned packet has never been called.
    folder = sorted(source["source_root"].glob("packet_*"))[1]
    for path in folder.iterdir():
        path.unlink()
    folder.rmdir()
    finish_path = next(source["source_root"].glob("parallel_executions/*/finish.json"))
    finish = read_json(finish_path)
    finish.update(admitted_calls=1, completed_packets=0, model_calls_this_execution=1)
    atomic_write_json(finish_path, finish)
    origin = importer.declare(source["source_ref"], source["manifest_sha"], expected_responses=1,
                              output=tmp_path / "one_origin.json")
    cfg = read_json(source["target_path"])
    cfg.update(schema_version=adapter.CONFIG_VERSION, import_origin=origin)
    atomic_write_json(source["target_path"], cfg)
    target = pin(source["target_path"])
    assert importer.import_responses(target)["imported_admissions"] == 1
    result = parallel_discovery.execute(adapter, target, command="run", workers=1, backend_factory=SyntheticDiscovery)
    assert result["status"] == "completed" and result["admitted_calls"] == 2
    assert result["model_calls_this_execution"] == 1
    assert adapter.execute(target, command="validate")["status"] == "validated"
    artifact = read_json(source["target_root"] / "discovery.json")
    assert artifact["import_origin"]["imported_admissions"] == 1
