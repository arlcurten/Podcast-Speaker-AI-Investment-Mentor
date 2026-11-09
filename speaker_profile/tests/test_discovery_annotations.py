"""Synthetic annotation subsets; no corpus, providers or extraction required."""
import copy
import json

import pytest

from speaker_profile import corpus_discovery as discovery, discovery_annotations as refs


def context():
    annotations = [{"annotation_id": f"A{i}", "kind": "ambiguity", "severity": "warning",
                    "note": f"Synthetic reviewer note {i}. " * 80, "extension": {"nullable": None},
                    "anchors": [{"segment_id": i, "start": i, "end": i + 1, "raw_text": f"Raw source {i}"}]}
                   for i in range(1, 4)]
    envelope = {"sha256": "c" * 64, "review": {"method": "synthetic"},
                "extension": None, "annotations": annotations}
    source = {"episode_id": "SYN", "raw_sha256": "a" * 64, "normalized_sha256": "b" * 64,
              "reviewed_annotations": envelope}
    def grounding(ids):
        return {"episode_id": "SYN", "source_raw_sha256": "a" * 64,
                "source_normalized_sha256": "b" * 64, "merged_ids": [1],
                "source_segment_ids": [1], "source_segment_id_ranges": [{"start_id": 1, "end_id": 1}],
                "timestamp_regions": [{"start": 1, "end": 2, "merged_ids": [1]}],
                "reviewed_annotations": {**copy.deepcopy(envelope),
                                         "annotations": [copy.deepcopy(annotations[i - 1]) for i in ids]},
                "source_issues": [{"issue_id": "A1", "merged_ids": [1], "note": "Exact issue",
                                   "authority": "reviewer_commentary_not_source", "extension": None}]}
    primary = grounding([2, 1, 2])
    primary["supplementary_context"] = [{"authority": "model_analyst_not_speaker", "note": "Analyst context",
                                         "grounding": grounding([3, 2])}]
    primary["supplementary_context"][0]["grounding"]["supplementary_context"] = [
        {"authority": "model_analyst_not_speaker", "grounding": grounding([1, 3])}]
    return {"sources": {"SYN": {"source": source, "unselected_regions": [{"grounding": grounding([1, 2])}]}},
            "evidence": [{"episode_id": "SYN", "evidence_id": "SYN:E1", "grounding": primary,
                          "quotes": [{"segment_id": 1, "start": 1, "end": 2, "text": "Raw source 1"}]}],
            "raw_regions": {"SYN": [{"segment_id": 1, "text": "Raw source 1"}]},
            "known_relationship_closure": {"counterevidence": ["SYN:E1"]}}


def primary(value):
    return value["evidence"][0]["grounding"]


def reference(value):
    return primary(value)["reviewed_annotations"][refs.MARKER]


def test_full_transport_roundtrip_subsets_order_repeats_and_authority():
    plain = context()
    before = copy.deepcopy(plain)
    factored = refs.factor(plain)
    assert reference(factored)["annotation_ids"] == ["A2", "A1", "A2"]
    assert reference(factored)["header_mode"] == "source"
    for grounding in refs._groundings(factored):
        assert set(grounding["reviewed_annotations"]) == {refs.MARKER}
        assert grounding["source_issues"] == primary(plain)["source_issues"]
    assert refs.same(refs.restore(factored), before)
    packed = discovery._pack(plain)
    assert refs.same(discovery._expand(packed), before)
    assert refs.same(plain, before)
    baseline = discovery.serialization.compact({**plain, "sources": list(plain["sources"].values())})
    assert len(json.dumps(packed)) < len(json.dumps(baseline))


@pytest.mark.parametrize("variant", ["missing", "null", "empty", "annotations_null", "empty_list",
                                    "header_missing", "header_null", "header_extension", "note_extension",
                                    "note_changed", "bool_number", "int_float"])
def test_lossless_missing_null_headers_and_unequal_annotations(variant):
    plain = context()
    grounding = primary(plain)
    envelope = grounding["reviewed_annotations"]
    if variant == "missing": del grounding["reviewed_annotations"]
    elif variant == "null": grounding["reviewed_annotations"] = None
    elif variant == "empty": grounding["reviewed_annotations"] = {}
    elif variant == "annotations_null": envelope["annotations"] = None
    elif variant == "empty_list": envelope["annotations"] = []
    elif variant == "header_missing": del envelope["sha256"]
    elif variant == "header_null": envelope["review"] = None
    elif variant == "header_extension": envelope["future"] = {"empty": [], "null": None}
    elif variant == "note_extension": envelope["annotations"][0]["future"] = None
    elif variant == "note_changed": envelope["annotations"][0]["note"] += " different"
    else:
        catalog = plain["sources"]["SYN"]["source"]["reviewed_annotations"]["annotations"][1]
        catalog["future"] = 1
        envelope["annotations"][0]["future"] = True if variant == "bool_number" else 1.0
    factored = refs.factor(plain)
    if variant.startswith("header_"):
        assert reference(factored)["header_mode"] == "inline"
    elif variant.startswith("note_") or variant in {"bool_number", "int_float"}:
        assert primary(factored)["reviewed_annotations"] == envelope
    assert refs.same(discovery._expand(discovery._pack(plain)), plain)


@pytest.mark.parametrize("fault", ["episode", "hash", "raw_hash", "normalized_hash", "id", "ids_null",
                                  "mode", "extra", "header_annotations", "header_null"])
def test_invalid_references_fail_closed(fault):
    value = refs.factor(context())
    ref = reference(value)
    if fault == "episode": ref["episode_id"] = "OTHER"
    elif fault == "hash": ref["catalog_sha256"] = "f" * 64
    elif fault == "raw_hash": primary(value)["source_raw_sha256"] = "f" * 64
    elif fault == "normalized_hash": primary(value)["source_normalized_sha256"] = "f" * 64
    elif fault == "id": ref["annotation_ids"] = ["MISSING"]
    elif fault == "ids_null": ref["annotation_ids"] = None
    elif fault == "mode": ref["header_mode"] = "infer"
    elif fault == "extra": ref["extra"] = None
    else:
        ref["header_mode"] = "inline"
        ref["header"] = {"annotations": []} if fault == "header_annotations" else None
    with pytest.raises(ValueError): refs.restore(value)


@pytest.mark.parametrize("fault", ["duplicate", "missing_id", "bad_hash", "reserved_shape"])
def test_ambiguous_catalogs_and_reserved_shape_fail_closed(fault):
    value = context()
    catalog = value["sources"]["SYN"]["source"]["reviewed_annotations"]
    if fault == "duplicate": catalog["annotations"].append(copy.deepcopy(catalog["annotations"][0]))
    elif fault == "missing_id": del catalog["annotations"][0]["annotation_id"]
    elif fault == "bad_hash": catalog["sha256"] = "not-a-hash"
    else: primary(value)["reviewed_annotations"] = {refs.MARKER: {}}
    with pytest.raises(ValueError): discovery._pack(value)


@pytest.mark.parametrize("field", ["context_format", "base_context_format", "annotation_reference_format",
                                  "serialization_legend"])
def test_wire_versions_and_legend_are_canonical(field):
    packed = discovery._pack(context())
    packed[field] = "changed"
    with pytest.raises(ValueError): discovery._expand(packed)


def test_equivalent_but_noncanonical_inline_reference_header_is_rejected():
    plain = context()
    packed = discovery._pack(plain)
    # The base codec can decode tables without interpreting annotation subset refs.
    base = {k: v for k, v in packed.items() if k not in {"base_context_format", "annotation_reference_format"}}
    base["context_format"] = discovery.serialization.FORMAT
    factored = discovery.serialization.expand(base)
    ref = reference(factored)
    ref["header_mode"] = "inline"
    ref["header"] = {k: v for k, v in plain["sources"]["SYN"]["source"]["reviewed_annotations"].items()
                     if k != "annotations"}
    altered = discovery.serialization.compact(factored)
    for field in ("context_format", "base_context_format", "annotation_reference_format", "serialization_legend"):
        altered[field] = packed[field]
    with pytest.raises(ValueError, match="Noncanonical"):
        discovery._expand(altered)


@pytest.mark.parametrize("fault", ["unknown_id", "absent_catalog", "raw_hash", "normalized_hash"])
def test_unbound_grounding_is_never_replaced_by_catalog_data(fault):
    plain = context()
    if fault == "unknown_id": primary(plain)["reviewed_annotations"]["annotations"][0]["annotation_id"] = "UNKNOWN"
    elif fault == "absent_catalog": del plain["sources"]["SYN"]["source"]["reviewed_annotations"]
    elif fault == "raw_hash": primary(plain)["source_raw_sha256"] = "f" * 64
    else: primary(plain)["source_normalized_sha256"] = "f" * 64
    factored = refs.factor(plain)
    assert primary(factored)["reviewed_annotations"] == primary(plain)["reviewed_annotations"]
    assert refs.same(refs.restore(factored), plain)
