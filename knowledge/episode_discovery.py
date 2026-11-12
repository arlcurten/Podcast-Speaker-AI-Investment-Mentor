"""Full-inventory Knowledge episode transport adapter; no greedy-run migration.

Use a NEW config/run: schema_version=knowledge_episode_discovery_config.v1 and
packet_strategy=whole_episode_adjacent_midpoint_split.v1. Other fields match
knowledge_discovery_config.v1. All L2 cases and L3 navigation items are retained;
batching does not change evidence authority, closure, prompts or response schema.
CLI uses the existing parallel executor. --progress reports only phase/count/time
to stderr. Checked inventory replay is unchanged; avoid separate plan then run
on large inventories. No source, semantic or publication approval is implied.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_discovery as base, discovery_runner, episode_packet_planner as planner, provenance
from .schemas import enum

CONFIG_VERSION = "knowledge_episode_discovery_config.v1"
RUN_VERSION = "knowledge_episode_discovery_run.v1"
REQUEST_VERSION = "knowledge_episode_discovery_request.v1"
RESULT_VERSION = base.RESULT_VERSION
PROPOSAL, POLICY, PROMPT_PATH = base.PROPOSAL, base.POLICY, base.PROMPT_PATH
CONFIG = copy.deepcopy(base.CONFIG)
CONFIG["properties"]["schema_version"] = enum(CONFIG_VERSION)
CONFIG["properties"]["packet_strategy"] = enum(planner.STRATEGY)
CONFIG["required"].append("packet_strategy")
_size, _derive, _aggregate, _counts = base._size, base._derive, base._aggregate, base._counts
_materialize, _check_pins = base._materialize, base._check_pins


def _implementation():
    return {"episode_adapter_sha256": sha256_file(Path(__file__)),
            "episode_planner_sha256": sha256_file(Path(planner.__file__)),
            "packet_strategy": planner.STRATEGY, "base": base._implementation(),
            "inventory": base.inventory._implementation()}


def _packets(cfg, catalog, pins, prompt, progress=None):
    groups = [catalog["episodes"][eid]["level2_record_ids"] for eid in catalog["expected_episode_ids"]]
    if {rid for group in groups for rid in group} != set(catalog["cases"]):
        raise ValueError("Episode ownership differs from the complete case inventory.")
    materials = base._materials(catalog, pins)
    owners = {pid: min(row["supporting_records"]) for pid, row in catalog["principles"].items()}

    def build(ids):
        return base._context(ids, catalog, materials, owners, prompt=prompt, max_chars=cfg["max_input_chars"])

    def describe(context):
        return {"context_sha256": provenance.digest(context), "input_chars": _size(prompt, context),
                "owned_case_ids": context["owned_case_ids"], "owned_principle_ids": context["owned_principle_ids"]}

    packets = planner.plan(groups, build=build, describe=describe, too_large=base._PacketTooLarge,
                           owned_key="owned_case_ids", progress=progress)
    principles = [pid for packet in packets for pid in packet["owned_principle_ids"]]
    if len(principles) != len(set(principles)) or set(principles) != set(catalog["principles"]):
        raise ValueError("Episode packets must own every inventory principle exactly once.")
    return packets


def _prepare(config_ref, progress=None):
    pins = {}
    config_ref = base.inventory._ref(config_ref)
    cfg = base.inventory._read(config_ref, pins)
    base._validate(cfg, CONFIG)
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])),
           "inventory": base.inventory._ref(cfg["inventory"])}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    started = time.monotonic()
    if progress:
        progress("inventory_replay_started", 0, 0, 0, 0.0)
    catalog = base.inventory.load_checked(cfg["inventory"])
    if progress:
        progress("inventory_replay_completed", 0, 0, 0, time.monotonic() - started)
    if not catalog["inventory_complete"]:
        raise ValueError("Discovery requires a complete explicit inventory; retain partial inventory gaps separately.")
    for ref in [cfg["inventory"], *catalog["dependencies"]]:
        base.inventory._pin_file(ref, pins)
    if any(path.is_relative_to(root) for path in pins):
        raise ValueError("Discovery run directory cannot contain an input dependency.")
    for eid, entry in catalog["episodes"].items():
        original = base.case_release._snapshot(entry["config"]["path"], pins,
                                               entry["config"]["sha256"], yaml_config=True)
        protected = [child_path(private_path(original["output_root"]), original["run_id"])]
        for state in entry["stages"].values():
            folder = Path(state["artifact"]["path"]).parent
            protected.append(folder.parent if folder.name == eid else folder)
        if any(root.is_relative_to(folder) for folder in protected):
            raise ValueError("Discovery output must be outside extraction runs and referenced artifact directories.")
    packets = _packets(cfg, catalog, pins, prompt, progress=(
        lambda done, total, probes: progress("episode_packing", done, total, probes, time.monotonic() - started)
    ) if progress else None)
    manifest = {"schema_version": RUN_VERSION, "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "policy": copy.deepcopy(POLICY), "packets": packets}
    _check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Knowledge episode discovery implementation changed while planning.")
    return cfg, root, catalog, packets, manifest, pins, prompt


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    return discovery_runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                    max_packets=max_packets, retry_failed=retry_failed)


def main(argv=None):
    from . import parallel_discovery
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "validate"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--max-packets", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)

    class Adapter:
        __name__ = "knowledge.episode_discovery"

        def __getattr__(self, name):
            return getattr(sys.modules[__name__], name)

        def _prepare(self, ref):
            def progress(phase, done, total, probes, elapsed):
                print(json.dumps({"phase": phase, "episodes_done": done, "episodes_total": total,
                                  "context_probes": probes, "elapsed_seconds": round(elapsed, 2)}),
                      file=sys.stderr, flush=True)
            return _prepare(ref, progress if args.progress else None)

    result = parallel_discovery.execute(Adapter(), {"path": args.config, "sha256": args.sha256},
                                        command=args.command, workers=args.workers,
                                        max_packets=args.max_packets, retry_failed=args.retry_failed)
    print(json.dumps({key: value for key, value in result.items() if key != "packet_plan"}))
    return 0 if result["status"] in {"planned", "completed", "validated"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
