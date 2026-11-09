"""Lossless annotation-subset references for discovery transport only.

The existing episode source catalog remains authoritative. Issue objects, quotes,
reviewer/model authorship and unknown annotation/header fields are never inferred.
"""
from __future__ import annotations

import copy
import json
import re

FORMAT = "source_annotation_subset.v1"
MARKER = "$source_annotation_subset"
LEGEND = (
    "Discovery annotation subsets: a reviewed_annotations object containing only "
    "$source_annotation_subset refers to sources by episode_id and catalog_sha256. "
    "annotation_ids selects full source.reviewed_annotations.annotations records in the listed order, "
    "including repeats. header_mode=source copies every catalog field except annotations; "
    "header_mode=inline uses the exact supplied header instead. Restore annotations to that header. "
    "Missing fields and nulls remain distinct. Inline annotations are unchanged; source_issues are not synthesized. "
    "References retain original reviewer authority, not additional speaker evidence."
)


def same(left, right):
    """Compare JSON values without conflating booleans, integers and floats."""
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded(left) == encoded(right)


def _catalogs(context):
    catalogs = {}
    for eid, entry in context["sources"].items():
        source = entry["source"]
        if source["episode_id"] != eid:
            raise ValueError("Annotation source keys must match their episodes.")
        envelope = source.get("reviewed_annotations")
        if envelope is None:
            continue
        if (not isinstance(envelope, dict) or not isinstance(envelope.get("annotations"), list)
                or not isinstance(envelope.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", envelope["sha256"])):
            raise ValueError("Annotation catalogs require an exact source envelope and SHA-256.")
        records = {}
        for row in envelope["annotations"]:
            identifier = row.get("annotation_id") if isinstance(row, dict) else None
            if not isinstance(identifier, str) or not identifier or identifier in records:
                raise ValueError("Ambiguous or missing source annotation identity.")
            records[identifier] = row
        catalogs[eid] = (source, envelope, records)
    return catalogs


def _groundings(context):
    def visit(grounding):
        yield grounding
        for note in grounding.get("supplementary_context") or []:
            yield from visit(note["grounding"])
    for row in context["evidence"]:
        yield from visit(row["grounding"])
    for entry in context["sources"].values():
        for note in entry["unselected_regions"]:
            yield from visit(note["grounding"])


def _bound(grounding, source):
    return (grounding["source_raw_sha256"] == source["raw_sha256"]
            and grounding["source_normalized_sha256"] == source["normalized_sha256"])


def factor(context):
    value = copy.deepcopy(context)
    catalogs = _catalogs(value)
    for grounding in _groundings(value):
        envelope = grounding.get("reviewed_annotations")
        if isinstance(envelope, dict) and set(envelope) == {MARKER}:
            raise ValueError("Source grounding collides with the reserved annotation reference shape.")
        if not isinstance(envelope, dict) or not isinstance(envelope.get("annotations"), list):
            continue
        eid = grounding["episode_id"]
        if eid not in catalogs:
            continue
        source, catalog, records = catalogs[eid]
        rows = envelope["annotations"]
        if not _bound(grounding, source) or any(
                not isinstance(row, dict) or not isinstance(row.get("annotation_id"), str)
                or row["annotation_id"] not in records or not same(row, records[row["annotation_id"]]) for row in rows):
            continue
        header = {k: v for k, v in envelope.items() if k != "annotations"}
        catalog_header = {k: v for k, v in catalog.items() if k != "annotations"}
        ref = {"episode_id": eid, "catalog_sha256": catalog["sha256"],
               "annotation_ids": [row["annotation_id"] for row in rows],
               "header_mode": "source" if same(header, catalog_header) else "inline"}
        if ref["header_mode"] == "inline":
            ref["header"] = header
        grounding["reviewed_annotations"] = {MARKER: ref}
    return value


def restore(context):
    value = copy.deepcopy(context)
    catalogs = _catalogs(value)
    for grounding in _groundings(value):
        envelope = grounding.get("reviewed_annotations")
        if not isinstance(envelope, dict) or set(envelope) != {MARKER}:
            continue
        ref = envelope[MARKER]
        if not isinstance(ref, dict) or ref.get("header_mode") not in ("source", "inline"):
            raise ValueError("Invalid annotation subset reference.")
        required = {"episode_id", "catalog_sha256", "annotation_ids", "header_mode"}
        if set(ref) != required | ({"header"} if ref["header_mode"] == "inline" else set()):
            raise ValueError("Annotation reference fields differ from the transport contract.")
        eid = grounding["episode_id"]
        if ref["episode_id"] != eid or eid not in catalogs:
            raise ValueError("Annotation reference crosses or lacks its source episode.")
        source, catalog, records = catalogs[eid]
        if ref["catalog_sha256"] != catalog["sha256"] or not _bound(grounding, source):
            raise ValueError("Annotation reference source pins differ.")
        ids = ref["annotation_ids"]
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in records for i in ids):
            raise ValueError("Unresolved source annotation reference.")
        header = ({k: v for k, v in catalog.items() if k != "annotations"}
                  if ref["header_mode"] == "source" else ref["header"])
        if not isinstance(header, dict) or "annotations" in header:
            raise ValueError("Invalid annotation reference header.")
        grounding["reviewed_annotations"] = {**copy.deepcopy(header), "annotations": [copy.deepcopy(records[i]) for i in ids]}
    return value
