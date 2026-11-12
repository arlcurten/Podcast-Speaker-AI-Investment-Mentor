"""Narrow config-pinned origin declaration/import for first-attempt Profile recovery."""
import argparse
from pathlib import Path
import sys
import types
from contextlib import ExitStack

from transcription.common import atomic_write_json, private_path
from . import sealed_response_import as original, import_aware_episode_discovery as adapter


def declare(source_config_ref, source_manifest_sha256, *, expected_responses, output):
    output = private_path(output)
    if output.exists():
        raise FileExistsError("Origin declarations are immutable; choose a new version.")
    pins = {}
    source_ref, cfg, root = original._configuration(source_config_ref, original.source_adapter, pins)
    if output.is_relative_to(root) or any(output == path for path in pins):
        raise ValueError("Origin output must not alter the original run/config.")
    with ExitStack() as stack:
        original._lock(stack, root / ".run.lock", existing=True)
        manifest_ref = {"path": str(root / "run_manifest.json"), "sha256": source_manifest_sha256}
        manifest = original.shared._read(manifest_ref, pins)
        mappings, histories = [], {}
        for packet in manifest["packets"]:
            pid = packet["packet_id"]
            folder = root / pid
            if not (folder / "attempt_0001.request.json").exists():
                continue
            refs = {part: original._ref(folder / ("attempt_0001." + part + ".json")) for part in ("request", "result", "seal")}
            result = original.shared._read(refs["result"], pins)
            histories[pid] = [{"status": result["status"]}]
            mappings.append({"packet_id": pid, "source": refs})
        execution = original._finished_execution(root, manifest, histories, len(mappings), pins)
        if len(mappings) != expected_responses:
            raise ValueError("Origin source count differs from explicitly expected responses.")
        origin = {"schema_version": adapter.ORIGIN_VERSION, "source_config": source_ref, "source_manifest": manifest_ref,
                  "source_execution": {key: execution[key] for key in ("start", "finish")},
                  "expected_responses": expected_responses, "mappings": mappings, "attempt": 1,
                  "response_policy": "unchanged_response_and_usage", "new_provider_calls": 0}
        original.shared._validate(origin, adapter.ORIGIN)
        atomic_write_json(output, origin, overwrite=False)
        ref = original._ref(output)
        adapter._load_origin(ref, pins)
        adapter._check_pins(pins)
        return ref


def import_responses(config_ref, *, progress=None):
    pins = {}
    config_ref, cfg, root = original._configuration(config_ref, adapter, pins)
    origin, *_ = adapter._load_origin(cfg["import_origin"], pins)
    prepared = None

    class Bootstrap:
        def __getattr__(self, key):
            return getattr(adapter, key)

        def _prepare(self, ref, callback=None):
            nonlocal prepared
            prepared = adapter._prepare_import(ref, callback)
            return prepared

    def write(path, payload, **kwargs):
        if path.name == "receipt.json" and path.parent.name == "recovery_import":
            payload = {**payload, "import_origin": cfg["import_origin"]}
        atomic_write_json(path, payload, **kwargs)

    def publish(stage, destination):
        if prepared is None or destination != root:
            raise ValueError("Missing exact prepared import target.")
        adapter.audit_import(prepared, stage)
        original._publish(stage, destination)
        # Remap the staging pins before auditing the published canonical paths.
        prepared[5].update({destination / path.relative_to(stage): checksum
                            for path, checksum in list(prepared[5].items()) if path.is_relative_to(stage)})
        for path in list(prepared[5]):
            if path.is_relative_to(stage):
                del prepared[5][path]
        adapter.audit_import(prepared)

    # Reuse the reviewed importer without changing its module globals or frozen
    # adapters. These code objects come only from the local trusted module, never
    # an artifact. The binding adds mandatory provenance before/after publication.
    fn = original.import_responses
    namespace = {**fn.__globals__, "target_adapter": Bootstrap(), "VERSION": adapter.IMPORT_VERSION,
                 "__file__": __file__, "atomic_write_json": write, "_publish": publish}
    bound = types.FunctionType(fn.__code__, namespace, fn.__name__)
    return bound(origin["source_config"], origin["source_manifest"]["sha256"], config_ref,
                 expected_responses=origin["expected_responses"], progress=progress)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    declaration = sub.add_parser("declare")
    declaration.add_argument("--source-config", required=True)
    declaration.add_argument("--source-sha256", required=True)
    declaration.add_argument("--source-manifest-sha256", required=True)
    declaration.add_argument("--expected-responses", type=int, required=True)
    declaration.add_argument("--output", required=True)
    importing = sub.add_parser("import")
    importing.add_argument("--config", required=True)
    importing.add_argument("--sha256", required=True)
    importing.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    import json
    if args.command == "declare":
        result = declare({"path": args.source_config, "sha256": args.source_sha256}, args.source_manifest_sha256,
                         expected_responses=args.expected_responses, output=args.output)
    else:
        def progress(phase, done, total, probes, elapsed):
            print(json.dumps({"phase": phase, "completed": done, "total": total,
                              "context_probes": probes, "elapsed_seconds": round(elapsed, 2)}), file=sys.stderr, flush=True)
        result = import_responses({"path": args.config, "sha256": args.sha256}, progress=progress if args.progress else None)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
