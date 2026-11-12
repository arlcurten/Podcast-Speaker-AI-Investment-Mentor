"""Profile recovery adapter with mandatory config-pinned import origin.

Normal preparation requires a published import, including on an otherwise empty
run. Only the explicit v2 importer uses _prepare_import. No optional provenance
fallback exists. Original usage is historical, never new-run provider spending.
"""
import copy
from pathlib import Path
import sys

from knowledge import corpus_inventory as shared, discovery_runner as core, provenance
from knowledge.schemas import TEXT, array, enum, obj
from transcription.common import child_path, sha256_file
from . import role_aware_episode_discovery as base, sealed_response_import as original_import

ADAPTER_NAME = "speaker_profile.import_aware_episode_discovery"
CONFIG_VERSION = "profile_import_aware_episode_discovery_config.v1"
RUN_VERSION = "profile_import_aware_episode_discovery_run.v1"
REQUEST_VERSION = "profile_import_aware_episode_discovery_request.v1"
RESULT_VERSION = base.RESULT_VERSION
ORIGIN_VERSION = "profile_saved_response_origin.v1"
IMPORT_VERSION = "profile_sealed_response_revalidation_import.v2"
ORIGIN = obj(schema_version=enum(ORIGIN_VERSION), source_config=shared.REF, source_manifest=shared.REF,
             source_execution=obj(start=shared.REF, finish=shared.REF),
             expected_responses={"type": "integer", "minimum": 1},
             mappings=array(obj(packet_id=TEXT, source=obj(request=shared.REF, result=shared.REF, seal=shared.REF))),
             attempt={"type": "integer", "const": 1}, response_policy=enum("unchanged_response_and_usage"),
             new_provider_calls={"type": "integer", "const": 0})
CONFIG = copy.deepcopy(base.CONFIG)
CONFIG["properties"]["schema_version"] = enum(CONFIG_VERSION)
CONFIG["properties"]["import_origin"] = shared.REF
CONFIG["required"].append("import_origin")
PROPOSAL, POLICY, PROMPT_PATH = base.PROPOSAL, base.POLICY, base.PROMPT_PATH
_size, _materialize, _derive, _counts = base._size, base._materialize, base._derive, base._counts
_check_pins = base._check_pins


def _implementation():
    return {"import_aware_adapter_sha256": sha256_file(Path(__file__)),
            "importer_sha256": sha256_file(Path(__file__).with_name("sealed_response_import_v2.py")),
            "original_importer_sha256": sha256_file(Path(original_import.__file__)),
            "role_adapter": base._implementation()}


def _load_origin(ref, pins):
    origin = shared._read(ref, pins)
    shared._validate(origin, ORIGIN)
    source = original_import.source_adapter
    cfg_ref, cfg, root = original_import._configuration(origin["source_config"], source, pins)
    manifest = shared._read(origin["source_manifest"], pins)
    if (origin["source_manifest"]["path"] != str(root / "run_manifest.json")
            or manifest.get("schema_version") != source.RUN_VERSION or manifest.get("config_ref") != cfg_ref
            or manifest.get("config") != cfg or manifest.get("implementation") != source._implementation()
            or manifest.get("policy") != source.POLICY):
        raise ValueError("Origin source manifest/config/implementation mismatch.")
    rows = origin["mappings"]
    ids = [row["packet_id"] for row in rows]
    if len(ids) != origin["expected_responses"] or len(ids) != len(set(ids)):
        raise ValueError("Origin requires exactly one mapping per imported packet.")
    available = {row["packet_id"] for row in manifest["packets"]}
    histories, expected_files = {}, set()
    for row in rows:
        pid = row["packet_id"]
        if pid not in available:
            raise ValueError("Origin packet is absent from its manifest.")
        values = {}
        for kind in ("request", "result", "seal"):
            path = root / pid / ("attempt_0001." + kind + ".json")
            if row["source"][kind]["path"] != str(path):
                raise ValueError("Origin must bind exact first-attempt source paths.")
            values[kind] = shared._read(row["source"][kind], pins)
            expected_files.add(path)
        request, result, seal = (values[kind] for kind in ("request", "result", "seal"))
        if (seal != core._seal(manifest, pid, 1, row["source"]["request"]["sha256"], row["source"]["result"]["sha256"])
                or request.get("schema_version") != source.REQUEST_VERSION or request.get("packet_id") != pid
                or request.get("attempt") != 1 or request.get("run_sha256") != provenance.digest(manifest)
                or result.get("schema_version") != source.RESULT_VERSION
                or result.get("request_sha256") != row["source"]["request"]["sha256"]
                or result.get("status") not in {"completed", "failed"}
                or (result["status"] == "failed" and (result.get("error") or {}).get("phase") != "validation")):
            raise ValueError("Origin requires sealed completed or validation-failed first attempts.")
        histories[pid] = [{"status": result["status"]}]
    if set(root.glob("packet_*/attempt_*.json")) != expected_files:
        raise ValueError("Origin must cover every original admission, with no interrupted or extra attempts.")
    for ref in origin["source_execution"].values():
        shared._read(ref, pins)
    execution = original_import._finished_execution(root, manifest, histories, len(ids), pins)
    if {key: execution[key] for key in ("start", "finish")} != origin["source_execution"]:
        raise ValueError("Origin execution references differ from drained source execution.")
    return origin, cfg, root, manifest, execution


def _prepare_materials(config_ref, progress=None, *, importing=False):
    pins = {}
    config_ref, cfg, root = original_import._configuration(config_ref, sys.modules[__name__], pins)
    if importing:
        if root.exists():
            raise FileExistsError("Import bootstrap requires a nonexistent destination.")
    elif not (root / "recovery_import" / "receipt.json").is_file() or not (root / "recovery_import" / "seal.json").is_file():
        raise ValueError("Mandatory recovery receipt/seal missing; ordinary preparation cannot bootstrap imports.")
    origin, source_cfg, source_root, source_manifest, _ = _load_origin(cfg["import_origin"], pins)
    prepared = base.shared.prepare(sys.modules[__name__], base.base, config_ref, progress)
    new_cfg, new_root, catalog, packets, manifest, material_pins, prompt = prepared
    if cfg != new_cfg or root != new_root:
        raise ValueError("Import-aware configuration changed during preparation.")
    material_pins.update(pins)
    for key in ("inventory", "model", "reasoning_effort", "timeout_seconds", "max_input_chars", "max_response_chars", "packet_strategy"):
        if source_cfg[key] != cfg[key]:
            raise ValueError("Origin generation inputs differ from target: " + key)
    if source_manifest["packets"] != packets:
        raise ValueError("Origin and target plans must preserve exact context closure.")
    histories, count = core.read_history(original_import.source_adapter, source_root, source_manifest, packets, prompt, catalog, material_pins)
    if count != origin["expected_responses"] or any(len(h) != 1 or h[0]["status"] == "interrupted" for h in histories.values() if h):
        raise ValueError("Origin history differs from its exact first-attempt declaration.")
    if not importing:
        audit_import(prepared)
    _check_pins(material_pins)
    if _implementation() != manifest["implementation"]:
        raise ValueError("Import-aware implementation changed during preparation.")
    return prepared


def _prepare(config_ref, progress=None):
    return _prepare_materials(config_ref, progress)


def _prepare_import(config_ref, progress=None):
    return _prepare_materials(config_ref, progress, importing=True)


def audit_import(prepared, storage_root=None):
    """Check deterministic origin/destination correspondence; pin every file."""
    cfg, root, catalog, packets, manifest, pins, prompt = prepared
    storage = root if storage_root is None else storage_root
    origin, _, _, source_manifest, execution = _load_origin(cfg["import_origin"], pins)
    ref = original_import._ref
    on_disk_manifest = shared._read(ref(storage / "run_manifest.json"), pins)
    if on_disk_manifest != manifest:
        raise ValueError("Import target manifest differs from exact prepared identity.")
    receipt_path, seal_path = storage / "recovery_import/receipt.json", storage / "recovery_import/seal.json"
    receipt = shared._read(ref(receipt_path), pins)
    seal = shared._read(ref(seal_path), pins)
    if seal != {"schema_version": "profile_revalidation_import_seal.v1", "receipt_sha256": pins[receipt_path],
                "target_run_sha256": provenance.digest(manifest)}:
        raise ValueError("Import receipt seal mismatch.")
    expected = {"schema_version": IMPORT_VERSION, "status": "sealed_responses_revalidated_ready_to_resume",
        "import_origin": cfg["import_origin"], "source_config": origin["source_config"], "source_manifest": origin["source_manifest"],
        "source_execution": execution, "target_config": manifest["config_ref"],
        "target_manifest": {"path": str(root / "run_manifest.json"), "sha256": pins[storage / "run_manifest.json"]},
        "source_implementation": source_manifest["implementation"], "target_implementation": manifest["implementation"],
        "importer_sha256": manifest["implementation"]["importer_sha256"], "imported_admissions": origin["expected_responses"],
        "new_provider_calls": 0, "provider_calls_this_import": 0, "new_provider_usage": None,
        "usage_authority": "Each destination usage is copied exactly from its pinned original provider result, not new usage.",
        "admission_accounting": "Core admitted_calls includes these imported durable requests. Subtract imported_admissions when reporting new-run provider admissions; do not subtract them from the configured lifetime cap.",
        "remaining_packets": len(packets) - origin["expected_responses"],
        "source_acceptance": "not_granted", "semantic_review_performed": False, "publication_authorized": False}
    if (set(receipt) != set(expected) | {"mappings", "imported_completed_source_responses", "imported_validation_failed_source_responses"}
            or any(receipt.get(key) != value for key, value in expected.items())):
        raise ValueError("Import receipt differs from required config-pinned origin/accounting.")
    by_packet = {p["packet_id"]: p for p in packets}
    expected_mappings, completed = [], 0
    for row in origin["mappings"]:
        pid = row["packet_id"]
        source_request = shared._read(row["source"]["request"], pins)
        source_result = shared._read(row["source"]["result"], pins)
        context = source_request["context"]
        if provenance.digest(context) != by_packet[pid]["context_sha256"]:
            raise ValueError("Origin context digest differs from target packet.")
        request = core.build_request(sys.modules[__name__], manifest, by_packet[pid], context, 1, prompt)
        paths = {kind: storage / pid / ("attempt_0001." + kind + ".json") for kind in ("request", "result", "seal")}
        actual = {kind: shared._read(ref(path), pins) for kind, path in paths.items()}
        result = {"schema_version": RESULT_VERSION, "request_sha256": pins[paths["request"]], "status": "completed",
                  "response": source_result["response"], "usage": source_result["usage"], "error": None}
        expected_seal = core._seal(manifest, pid, 1, pins[paths["request"]], pins[paths["result"]])
        if provenance.digest(actual) != provenance.digest({"request": request, "result": result, "seal": expected_seal}):
            raise ValueError("Imported request/result/seal differs from its pinned original response and usage.")
        core.check_response(sys.modules[__name__], source_result["response"], context, cfg)
        expected_mappings.append({"packet_id": pid, "kind": "sealed_response_revalidation_not_provider_execution",
            "source_status": source_result["status"], "source": row["source"],
            "destination": {kind: {"path": str(root / path.relative_to(storage)), "sha256": pins[path]} for kind, path in paths.items()},
            "context_sha256": provenance.digest(context), "response_sha256": provenance.digest(source_result["response"]),
            "usage_sha256": provenance.digest(source_result["usage"]), "response_and_usage_unchanged": True})
        completed += source_result["status"] == "completed"
    if (receipt.get("mappings") != expected_mappings or receipt.get("imported_completed_source_responses") != completed
            or receipt.get("imported_validation_failed_source_responses") != origin["expected_responses"] - completed):
        raise ValueError("Import mappings/counts differ from the required one-to-one origin declaration.")
    _check_pins(pins)
    return {"declaration": copy.deepcopy(cfg["import_origin"]), "receipt": ref(receipt_path), "seal": ref(seal_path),
            "imported_admissions": origin["expected_responses"], "new_provider_calls_during_import": 0,
            "original_usage_is_historical_not_new_spend": True}


def _aggregate(catalog, packets, histories, manifest):
    cfg = manifest["config"]
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    origin = audit_import((cfg, root, catalog, packets, manifest, {}, PROMPT_PATH.read_text(encoding="utf-8")))
    return {**base._aggregate(catalog, packets, histories, manifest), "import_origin": origin}


def project_completed(spec):
    """Explicit downstream projection with direct import-provenance hashes."""
    from knowledge.postdiscovery_bridge_v1 import replay_completed
    from .postdiscovery_bridge_v1 import project
    parent = replay_completed(spec, adapter=sys.modules[__name__])
    result = {**project(parent), "import_origin": copy.deepcopy(parent.artifact["import_origin"])}
    parent.check()
    return result


def execute(config_ref, **kwargs):
    return base.shared.execute(sys.modules[__name__], config_ref, **kwargs)


def main(argv=None):
    return base.shared.main(sys.modules[__name__], argv)


if __name__ == "__main__":
    raise SystemExit(main())
