"""Lossless, inspectable synthesis tables with shared raw quotes and column values."""
from __future__ import annotations

import copy
import json

FORMAT = "compact_profile.v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _table(records):
    columns = list(dict.fromkeys(key for row in records for key in row))
    rows = [[copy.deepcopy(row.get(key)) for key in columns] for row in records]
    shared = {}
    for index, key in enumerate(columns):
        lookup, values, indices = {}, [], []
        for row in rows:
            signature = _json(row[index])
            if signature not in lookup:
                lookup[signature] = len(values)
                values.append(row[index])
            indices.append(lookup[signature])
        if len(_json([r[index] for r in rows])) - len(_json(values)) - len(_json(indices)) > 100:
            shared[key] = values
            for row, reference in zip(rows, indices):
                row[index] = reference
    return {"columns": columns, "rows": rows, "shared_columns": shared,
            "missing": {str(i): [j for j, key in enumerate(columns) if key not in row]
                        for i, row in enumerate(records) if set(row) != set(columns)}}


def _records(table):
    result = []
    for i, row in enumerate(table["rows"]):
        record = {}
        for j, key in enumerate(table["columns"]):
            if j not in table["missing"].get(str(i), []):
                value = table["shared_columns"][key][row[j]] if key in table["shared_columns"] else row[j]
                record[key] = copy.deepcopy(value)
        result.append(record)
    return result


def compact(context):
    """No semantic fields or strings are removed; expand() reconstructs the input."""
    value = copy.deepcopy(context)
    quote_rows, quote_lookup, groundings = [], {}, []
    sources = {s["source"]["episode_id"]: s["source"] for s in value["sources"]}

    def quote(eid, row, text_key):
        if set(row) != {"segment_id", "start", "end", text_key}:
            raise ValueError("Unsupported quote/annotation anchor shape; no fields may be silently dropped.")
        key = (eid, row["segment_id"])
        record = {"episode_id": eid, "segment_id": row["segment_id"], "start": row["start"],
                  "end": row["end"], "text": row[text_key]}
        if key in quote_lookup:
            index = quote_lookup[key]
            if quote_rows[index] != record:
                raise ValueError("Conflicting raw quote references.")
            return index
        quote_lookup[key] = len(quote_rows)
        quote_rows.append(record)
        return len(quote_rows) - 1

    def grounding(original):
        row = copy.deepcopy(original)
        eid = row.pop("episode_id")
        if (row.pop("source_raw_sha256") != sources[eid]["raw_sha256"]
                or row.pop("source_normalized_sha256") != sources[eid]["normalized_sha256"]):
            raise ValueError("Grounding/source hash mismatch.")
        row["source_ref"] = eid
        if any(set(r) != {"start_id", "end_id"} for r in row["source_segment_id_ranges"]):
            raise ValueError("Unsupported raw ID range shape.")
        if any(set(r) != {"start", "end", "merged_ids"} for r in row["timestamp_regions"]):
            raise ValueError("Unsupported timestamp-region shape.")
        row["source_segment_id_ranges"] = [[r["start_id"], r["end_id"]] for r in row["source_segment_id_ranges"]]
        row["timestamp_regions"] = [[r["start"], r["end"], r["merged_ids"]] for r in row["timestamp_regions"]]
        groundings.append(row)
        return len(groundings) - 1

    for row in value["evidence"]:
        row["quotes"] = [quote(row["episode_id"], q, "text") for q in row["quotes"]]
        row["grounding"] = grounding(row["grounding"])
    for source in value["sources"]:
        eid = source["source"]["episode_id"]
        annotations = source["source"].get("reviewed_annotations")
        if annotations is not None:
            for note in annotations["annotations"]:
                note["anchors"] = [quote(eid, q, "raw_text") for q in note["anchors"]]
            annotations["annotations"] = _table(annotations["annotations"])
        for note in source["unselected_regions"]:
            note["grounding"] = grounding(note["grounding"])
        source["unselected_regions"] = _table(source["unselected_regions"])
    value["evidence"] = _table(value["evidence"])
    value["grounding_table"] = _table(groundings)
    value["quote_table"] = _table(quote_rows)
    value["context_format"] = FORMAT
    value["serialization_legend"] = (
        "Tables: rows are positional against columns. For a key in shared_columns, the row cell is a zero-based "
        "index into that key's shared value list; otherwise it is the literal value. missing identifies absent "
        "cells, distinct from null. Evidence quotes and annotation anchors are zero-based quote_table row indices; "
        "the table preserves episode, raw segment ID, exact text/start/end. Annotation text is raw_text; evidence "
        "quote text is text. grounding is a zero-based grounding_table row index. Grounding source_ref resolves "
        "episode_id/raw_sha256/normalized_sha256 in sources; raw ID ranges are [start_id,end_id], timestamp regions "
        "are [start,end,merged_ids]. All conditions, exceptions, tradeoffs, notes, quotations and unknown values "
        "remain verbatim. Shared representations are not extra observations or independent evidence.")
    if expand(value) != context:
        raise ValueError("Compact profile context failed lossless round-trip validation.")
    return value


def expand(context):
    value = copy.deepcopy(context)
    if value.pop("context_format") != FORMAT:
        raise ValueError("Unknown compact profile context.")
    value.pop("serialization_legend")
    quotes, groundings = _records(value.pop("quote_table")), _records(value.pop("grounding_table"))
    sources = {s["source"]["episode_id"]: s["source"] for s in value["sources"]}

    def quote(index, eid, text_key):
        row = copy.deepcopy(quotes[index])
        if row.pop("episode_id") != eid:
            raise ValueError("Quote reference crosses episodes.")
        if text_key != "text":
            row[text_key] = row.pop("text")
        return row

    def grounding(index):
        row = copy.deepcopy(groundings[index])
        eid = row.pop("source_ref")
        row.update(episode_id=eid, source_raw_sha256=sources[eid]["raw_sha256"],
                   source_normalized_sha256=sources[eid]["normalized_sha256"])
        row["source_segment_id_ranges"] = [{"start_id": r[0], "end_id": r[1]} for r in row["source_segment_id_ranges"]]
        row["timestamp_regions"] = [{"start": r[0], "end": r[1], "merged_ids": r[2]} for r in row["timestamp_regions"]]
        return row

    value["evidence"] = _records(value["evidence"])
    for row in value["evidence"]:
        row["quotes"] = [quote(i, row["episode_id"], "text") for i in row["quotes"]]
        row["grounding"] = grounding(row["grounding"])
    for source in value["sources"]:
        eid = source["source"]["episode_id"]
        annotations = source["source"].get("reviewed_annotations")
        if annotations is not None:
            annotations["annotations"] = _records(annotations["annotations"])
            for note in annotations["annotations"]:
                note["anchors"] = [quote(i, eid, "raw_text") for i in note["anchors"]]
        source["unselected_regions"] = _records(source["unselected_regions"])
        for note in source["unselected_regions"]:
            note["grounding"] = grounding(note["grounding"])
    return value
