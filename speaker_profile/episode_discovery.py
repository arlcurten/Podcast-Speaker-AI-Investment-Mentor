"""Versioned full-inventory Profile episode transport planner, not core greedy v5.

Use a NEW config/run: schema_version=profile_episode_discovery_config.v1 and
packet_strategy=whole_episode_adjacent_midpoint_split.v1; remaining fields match
the original Profile config. No automatic conversion or old-history adoption.
CLI runs the existing parallel executor; plan/validate are read-only. --progress
prints only phase, counts and elapsed seconds to stderr. Inventory replay remains
unchanged and may dominate startup; avoid separate plan then run on large inputs.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

from knowledge import corpus_inventory as shared, discovery_runner, episode_packet_planner as planner, provenance
from knowledge.schemas import enum
from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_discovery as base

CONFIG_VERSION = "profile_episode_discovery_config.v1"
RUN_VERSION = "profile_episode_discovery_run.v1"
REQUEST_VERSION = "profile_episode_discovery_request.v1"
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
            "packet_strategy": planner.STRATEGY, "base": base._implementation()}


def _packets(cfg, catalog, pins, prompt, progress=None):
    groups = [catalog["episodes"][eid]["evidence_ids"] for eid in catalog["expected_episode_ids"]]
    order = [rid for group in groups for rid in group]
    if set(order) != set(catalog["evidence"]):
        raise ValueError("Episode ownership differs from the complete evidence inventory.")
    materials = base._Materials(catalog, pins)

    def build(ids):
        return base._context(ids, catalog, materials, cfg["inventory"],
                             prompt=prompt, max_chars=cfg["max_input_chars"])

    def describe(context):
        return {"inventory": cfg["inventory"], "context_sha256": provenance.digest(context),
                "input_chars": _size(prompt, context), "owned_evidence_ids": context["owned_evidence_ids"],
                "owned_hypothesis_keys": context["owned_hypothesis_keys"]}

    packets = planner.plan(groups, build=build, describe=describe, too_large=base._PacketTooLarge,
                           owned_key="owned_evidence_ids", progress=progress)
    hypotheses = [key for packet in packets for key in packet["owned_hypothesis_keys"]]
    if len(hypotheses) != len(set(hypotheses)) or set(hypotheses) != set(catalog["hypotheses"]):
        raise ValueError("Episode packets must own every inventory hypothesis exactly once.")
    return packets


def _prepare(config_ref, progress=None):
    pins = {}
    config_ref = shared._ref(config_ref)
    cfg = shared._read(config_ref, pins)
    shared._validate(cfg, CONFIG)
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "inventory": shared._ref(cfg["inventory"])}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    started = time.monotonic()
    if progress:
        progress("inventory_replay_started", 0, 0, 0, 0.0)
    catalog = base.inventory.load_checked(cfg["inventory"])
    if progress:
        progress("inventory_replay_completed", 0, 0, 0, time.monotonic() - started)
    if not catalog["inventory_complete"]:
        raise ValueError("Profile discovery requires a complete explicit inventory; preserve gaps separately.")
    for ref in [cfg["inventory"], *catalog["dependencies"]]:
        if Path(ref["path"]).resolve() == base.inventory.pipeline.common.TERMINOLOGY.resolve():
            pins[Path(ref["path"])] = ref["sha256"]
        else:
            shared._pin_file(ref, pins)
    protected = [Path(p) for p in catalog["protected_output_roots"]]
    protected.extend(Path(row["artifact"]["path"]).parent for row in
                     [*catalog["evidence"].values(), *catalog["hypotheses"].values()])
    if any(path.is_relative_to(root) for path in pins) or any(root.is_relative_to(p) for p in protected):
        raise ValueError("Discovery output must be outside original runs and input artifact directories.")
    packets = _packets(cfg, catalog, pins, prompt, progress=(
        lambda done, total, probes: progress("episode_packing", done, total, probes, time.monotonic() - started)
    ) if progress else None)
    manifest = {"schema_version": RUN_VERSION, "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "policy": copy.deepcopy(POLICY), "packets": packets}
    _check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Profile episode discovery implementation changed while planning.")
    return cfg, root, catalog, packets, manifest, pins, prompt


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    return discovery_runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                    max_packets=max_packets, retry_failed=retry_failed)


def main(argv=None):
    from knowledge import parallel_discovery
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
        # Only preparation logging differs; packet and implementation pins are identical.
        __name__ = "speaker_profile.episode_discovery"

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
