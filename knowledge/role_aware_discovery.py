"""Shared versioned membership validation; preserve mixed roles and every reason."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import types

from . import corpus_inventory, discovery_runner
from .schemas import enum

MEMBERSHIP_POLICY = "unique_family_role.v1"
FIELDS = {
    "knowledge": ("cases", "record_id", "case_dispositions", "principles", "principle_id", "principle_dispositions"),
    "profile": ("evidence", "evidence_id", "evidence_dispositions", "hypotheses", "hypothesis_key", "hypothesis_dispositions"),
}


def configuration(original, version):
    schema = copy.deepcopy(original)
    schema["properties"]["schema_version"] = enum(version)
    schema["properties"]["membership_policy"] = enum(MEMBERSHIP_POLICY)
    schema["required"].append("membership_policy")
    return schema


def derive(response, context, *, proposal, domain):
    """Same accounting/attribution rules as the original domain validators.

    The sole acceptance change is membership uniqueness by (family_id, role).
    No membership is collapsed, sorted, rewritten or stripped of its reason.
    """
    corpus_inventory._validate(response, proposal)
    records, record_key, dispositions, navigation, nav_key, nav_dispositions = FIELDS[domain]
    families = [row["family_id"] for row in response["families"]]
    if len(families) != len(set(families)) or any(not re.fullmatch(r"F[1-9][0-9]*", fid) for fid in families):
        raise ValueError("Family IDs must be unique local F1, F2, ... identifiers.")
    available = {row[record_key]: row for row in context[records]}
    seen, used = set(), set()
    for row in response[dispositions]:
        rid = row[record_key]
        if rid not in available or rid in seen:
            raise ValueError("Every supplied evidence record needs exactly one disposition.")
        seen.add(rid)
        refs = [member["family_id"] for member in row["memberships"]]
        pairs = [(member["family_id"], member["role"]) for member in row["memberships"]]
        if len(pairs) != len(set(pairs)) or not set(refs) <= set(families):
            raise ValueError("Memberships require unique (family_id, role) pairs and defined families.")
        if (row["disposition"] == "assigned") != bool(refs):
            raise ValueError("Evidence disposition disagrees with its memberships.")
        if available[rid]["speaker_attribution"] != "host" and any(
                member["role"] in {"potential_support", "potential_counter"} for member in row["memberships"]):
            raise ValueError("Non-host evidence cannot support or contradict a host pattern.")
        used.update(refs)
    if seen != available.keys() or used != set(families):
        raise ValueError("All evidence must remain accounted for; families require actual memberships.")
    expected = {row[nav_key] for row in context[navigation]}
    seen = set()
    for row in response[nav_dispositions]:
        key, refs = row[nav_key], row["family_ids"]
        if key not in expected or key in seen or len(refs) != len(set(refs)) or not set(refs) <= set(families):
            raise ValueError("Every supplied navigation item needs unique supplied-family accounting.")
        if (row["disposition"] == "navigation_only") != bool(refs):
            raise ValueError("Navigation disposition disagrees with its references.")
        seen.add(key)
    if seen != expected:
        raise ValueError("Missing navigation disposition, including ineligible parents.")
    return copy.deepcopy(response)


def prepare(adapter, original, config_ref, progress=None):
    # Bind the trusted, pinned preparation function to successor version constants
    # in a private globals copy. Do not patch a running module or create old-schema
    # compatibility files. Context construction and all source guards stay exact.
    original_prepare = original._prepare
    namespace = {**original_prepare.__globals__, "CONFIG": adapter.CONFIG,
                 "CONFIG_VERSION": adapter.CONFIG_VERSION, "RUN_VERSION": adapter.RUN_VERSION,
                 "_implementation": adapter._implementation}
    bound = types.FunctionType(original_prepare.__code__, namespace, original_prepare.__name__)
    return bound(config_ref, progress)


def execute(adapter, config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    return discovery_runner.execute(adapter, config_ref, command=command, backend=backend,
                                    max_packets=max_packets, retry_failed=retry_failed)


def main(adapter, argv=None):
    from . import parallel_discovery
    parser = argparse.ArgumentParser(description=adapter.__doc__)
    parser.add_argument("command", choices=("plan", "run", "validate"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--max-packets", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)

    class WithProgress:
        __name__ = adapter.ADAPTER_NAME

        def __getattr__(self, name):
            return getattr(adapter, name)

        def _prepare(self, ref):
            def progress(phase, done, total, probes, elapsed):
                print(json.dumps({"phase": phase, "episodes_done": done, "episodes_total": total,
                                  "context_probes": probes, "elapsed_seconds": round(elapsed, 2)}),
                      file=sys.stderr, flush=True)
            return adapter._prepare(ref, progress if args.progress else None)

    result = parallel_discovery.execute(WithProgress(), {"path": args.config, "sha256": args.sha256},
                                        command=args.command, workers=args.workers,
                                        max_packets=args.max_packets, retry_failed=args.retry_failed)
    print(json.dumps({key: value for key, value in result.items() if key != "packet_plan"}))
    return 0 if result["status"] in {"planned", "completed", "validated"} else 1
