"""Reviewed episode-local notes, never replacements for transcript evidence."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from .common import private_path, sha256_file


KINDS = {"transcription_correction", "interpretive_caution", "uncertainty"}


def _fields(value, required: set[str], optional: set[str] | None = None) -> None:
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - (optional or set())):
        raise ValueError("Invalid annotation fields.")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reference(reference, owner: Path) -> tuple[Path, str]:
    _fields(reference, {"path", "sha256"})
    if not _text(reference["path"]) or not re.fullmatch(r"[a-f0-9]{64}", str(reference["sha256"])):
        raise ValueError("Annotation references require a path and SHA-256.")
    path = Path(reference["path"]).expanduser()
    return private_path(path if path.is_absolute() else owner.parent / path), reference["sha256"]


def _check_file(path: Path, expected: str) -> None:
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise ValueError("Missing or unreadable annotation/review reference.") from exc
    if actual != expected:
        raise ValueError("Annotation/review reference checksum mismatch.")


def load_annotations(review: dict, review_path: Path, raw: dict, raw_sha256: str) -> dict | None:
    """Resolve v2's reviewed dependency; v1 retains its original behavior.

    File hashes establish identity, not the truth of a review or human listening.
    Returned metadata omits private paths before entering model context.
    """
    if review.get("schema_version") == "transcript_review.v1":
        return None
    if review.get("schema_version") != "transcript_review.v2":
        raise ValueError("Unsupported annotation review version.")
    path, expected = _reference(review.get("annotations"), private_path(review_path))
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("Missing or unreadable annotation sidecar.") from exc
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("Annotation sidecar checksum mismatch.")
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid annotation JSON.") from exc
    _fields(data, {"schema_version", "episode_id", "transcript_sha256", "decision", "reviewer",
                   "reviewed_at", "method", "supporting_reviews", "annotations"})
    if (data["schema_version"] != "transcript_annotations.v1"
            or data["episode_id"] != raw["metadata"]["episode_id"]
            or data["transcript_sha256"] != raw_sha256):
        raise ValueError("Annotation episode/raw version mismatch.")
    if data["decision"] != "accept_annotations" or any(not _text(data[k]) for k in ("reviewer", "reviewed_at", "method")):
        raise ValueError("Annotations require a separate explicit review and its method/limitations.")
    references = data["supporting_reviews"]
    if not isinstance(references, list) or not references:
        raise ValueError("Annotations require hash-bound supporting reviews.")
    pinned = {}
    for reference in references:
        supporting, checksum = _reference(reference, path)
        if supporting in pinned or supporting == path or supporting == private_path(review_path):
            raise ValueError("Duplicate or circular supporting review reference.")
        _check_file(supporting, checksum)
        pinned[supporting] = checksum
    items = data["annotations"]
    if not isinstance(items, list) or not items:
        raise ValueError("Annotations must be a nonempty list.")
    originals = {s["segment_id"]: s for s in raw["segments"]}
    seen, corrections = set(), {}
    for item in items:
        _fields(item, {"annotation_id", "kind", "severity", "anchors", "note"}, {"correction"})
        identifier = item["annotation_id"]
        if not _text(identifier) or identifier in seen:
            raise ValueError("Annotation IDs must be nonempty and unique within the episode.")
        seen.add(identifier)
        if (not isinstance(item["kind"], str) or item["kind"] not in KINDS
                or not isinstance(item["severity"], str) or item["severity"] not in {"critical", "noncritical"}
                or not _text(item["note"])):
            raise ValueError("Invalid annotation kind, severity or note.")
        correction = item["kind"] == "transcription_correction"
        if correction and not _text(item.get("correction")):
            raise ValueError("Transcription corrections require nonempty correction text.")
        if not correction and "correction" in item:
            raise ValueError("Cautions and uncertainty cannot supply replacement text.")
        anchors = item["anchors"]
        if not isinstance(anchors, list) or not anchors:
            raise ValueError("Annotations require exact raw segment anchors.")
        ids = set()
        for anchor in anchors:
            _fields(anchor, {"segment_id", "raw_text", "start", "end"})
            sid = anchor["segment_id"]
            if type(sid) is not int or sid not in originals or sid in ids:
                raise ValueError("Unresolved or duplicate annotation anchor.")
            ids.add(sid)
            original = originals[sid]
            if (anchor["raw_text"] != original["text"]
                    or any(type(anchor[k]) not in (int, float) or not math.isfinite(anchor[k])
                           or anchor[k] != original[k] for k in ("start", "end"))):
                raise ValueError("Annotation anchors must match exact raw text and timestamps.")
            if correction:
                if sid in corrections and corrections[sid] != item["correction"]:
                    raise ValueError("Conflicting corrections overlap a raw segment; review them together.")
                corrections[sid] = item["correction"]
    for supporting, checksum in pinned.items():
        _check_file(supporting, checksum)
    _check_file(path, expected)
    return {"sha256": expected,
            "review": {**{k: data[k] for k in ("decision", "reviewer", "reviewed_at", "method")},
                       "supporting_review_sha256": list(pinned.values())},
            "annotations": items}
