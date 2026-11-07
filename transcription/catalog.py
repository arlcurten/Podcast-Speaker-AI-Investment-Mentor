"""Immutable RSS episode catalogs for the formal transcription workflow."""
from __future__ import annotations

import email.utils
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .common import atomic_write_json, private_path, safe_id, utc_now, load_manifest, read_json, sha256_file


ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
MAX_FEED_BYTES = 8_000_000


def parse_feed(content: bytes, *, quarantine_ambiguous: bool = False) -> dict[str, Any]:
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("RSS feed exceeds the catalog byte limit.")
    # Only simple RSS documents are accepted; no DTD/entity expansion.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", content.replace(b"\x00", b""), re.I):
        raise ValueError("RSS DTD/entity declarations are not supported.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Invalid RSS XML.") from exc
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel is missing.")
    rows = []
    quarantined = []
    blocked = set()
    seen: dict[str, dict[str, Any]] = {}
    for item in channel.findall("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        guid = (item.findtext("guid") or "").strip()
        explicit = (item.findtext(f"{{{ITUNES}}}episode") or "").strip()
        match = re.search(r"\bEP\.?\s*(\d+)\b", title, re.I)
        number = str(int(explicit)) if explicit.isdigit() else (str(int(match.group(1))) if match else None)
        if explicit.isdigit() and match and int(explicit) != int(match.group(1)):
            if not quarantine_ambiguous:
                raise ValueError("RSS title and explicit episode number disagree.")
            quarantined.append({"episode_title": title, "guid": guid, "explicit_number": explicit,
                                "title_number": match.group(1), "reason": "episode_number_disagreement"})
            continue
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            continue
        url = enclosure.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("RSS audio enclosure must be an unauthenticated HTTP(S) URL.")
        if not title or not (guid or number):
            raise ValueError("RSS item requires title and a stable episode number or GUID.")
        eid = f"EP{number}" if number else "special_" + hashlib.sha256(guid.encode()).hexdigest()[:16]
        date = (item.findtext("pubDate") or "").strip()
        published = None
        if date:
            try:
                stamp = email.utils.parsedate_to_datetime(date)
                if stamp.tzinfo is not None:
                    published = stamp.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        duration = item.findtext(f"{{{ITUNES}}}duration")
        row = {
            "episode_id": safe_id(eid), "episode_number": number, "episode_title": title,
            "guid": guid or None, "publication_date": published,
            "publication_date_original": date or None, "duration": duration,
            "audio_url": url, "audio_mime_type": enclosure.get("type"),
            "audio_length_from_rss": int(enclosure.get("length", "")) if enclosure.get("length", "").isdigit() else None,
        }
        if eid in seen:
            if seen[eid] != row or eid in blocked:
                if not quarantine_ambiguous:
                    raise ValueError("Ambiguous duplicate episode ID in RSS; resolve metadata before transcription.")
                if eid not in blocked:
                    rows.remove(seen[eid])
                    quarantined.append({**seen[eid], "reason": "duplicate_episode_id"})
                blocked.add(eid)
                quarantined.append({**row, "reason": "duplicate_episode_id"})
            continue
        rows.append(row)
        seen[eid] = row
    if not rows:
        raise ValueError("RSS has no usable audio episodes.")
    return {"schema_version": "episode_catalog.v1", "podcast_title": channel.findtext("title"),
            "fetched_at": utc_now(), "feed_sha256": hashlib.sha256(content).hexdigest(),
            "episodes": rows, "quarantined_items": quarantined}


def fetch_catalog(url: str, output: Path, *, quarantine_ambiguous: bool = False) -> dict[str, Any]:
    output = private_path(output)
    if output.exists():
        raise ValueError("Catalog snapshots are immutable; choose a new output filename.")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Provide an HTTP(S) RSS feed without embedded credentials.")
    try:
        with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "podcast-mentor-transcription/1.0"}) as response:
            response.raise_for_status()
            parts = []
            size = 0
            for part in response.iter_content(64 * 1024):
                size += len(part)
                if size > MAX_FEED_BYTES:
                    raise ValueError("RSS feed exceeds the catalog byte limit.")
                parts.append(part)
            payload = parse_feed(b"".join(parts), quarantine_ambiguous=quarantine_ambiguous)
    except requests.RequestException as exc:
        raise RuntimeError("RSS fetch failed; verify feed URL and connectivity. No audio downloaded.") from exc
    payload["source_feed_url"] = url
    atomic_write_json(output, payload, overwrite=False)
    dates = [e["publication_date"] for e in payload["episodes"] if e["publication_date"]]
    return {"episode_count": len(payload["episodes"]), "earliest_publication": min(dates) if dates else None,
            "latest_publication": max(dates) if dates else None, "feed_sha256": payload["feed_sha256"],
            "quarantined_count": len(payload["quarantined_items"]),
            "audio_downloaded": False}


def compare_catalogs(before: Path, after: Path, output: Path | None = None) -> dict[str, Any]:
    """Membership/metadata delta only: never schedule downloads or delete sources."""
    before, after = private_path(before), private_path(after)
    fields = ("episode_title", "guid", "publication_date", "audio_url", "duration",
              "audio_length_from_rss", "audio_mime_type")
    identity = ("guid", "audio_url")

    def index(path):
        by_id, guids = {}, {}
        for row in load_manifest(path):
            eid = safe_id(row["episode_id"])
            if eid in by_id:
                raise ValueError("Duplicate episode ID in catalog snapshot; resolve ambiguity first.")
            number = str(row.get("episode_number") or "")
            if number.isdigit() and eid != "EP" + str(int(number)):
                raise ValueError("Episode ID and explicit number disagree in catalog snapshot.")
            guid = row.get("guid")
            if guid and guid in guids:
                raise ValueError("One GUID maps to multiple episode IDs; resolve identity before updating.")
            if guid:
                guids[guid] = eid
            by_id[eid] = row
        return by_id, guids

    old, old_guids = index(before)
    new, new_guids = index(after)
    if any(old_guids[g] != new_guids[g] for g in old_guids.keys() & new_guids.keys()):
        raise ValueError("GUID was renumbered across snapshots; resolve identity before updating.")
    changed = []
    for eid in sorted(old.keys() & new.keys()):
        differences = [key for key in fields if old[eid].get(key) != new[eid].get(key)]
        if differences:
            changed.append({"episode_id": eid, "changed_fields": differences,
                            "audio_identity_requires_review": bool(set(differences) & set(identity)),
                            "action": "review_metadata_change_not_automatic_retranscription"})
    latest = read_json(after) if after.suffix != ".jsonl" else None
    result = {"schema_version": "episode_catalog_diff.v1", "created_at": utc_now(),
              "before_sha256": sha256_file(before), "after_sha256": sha256_file(after),
              "added_to_catalog": sorted(new.keys() - old.keys()),
              "missing_from_catalog": sorted(old.keys() - new.keys()), "changed": changed,
              "unchanged_count": len(old.keys() & new.keys()) - len(changed),
              "quarantined_count": len(latest.get("quarantined_items", [])) if isinstance(latest, dict) else None,
              "downloads_started": 0, "artifacts_deleted": 0,
              "scope": "Membership delta, not proof of new publication. RSS windows may omit old episodes. Select work explicitly and preserve existing data."}
    if output is not None:
        atomic_write_json(private_path(output), result, overwrite=False)
    return result
