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

from .common import atomic_write_json, private_path, safe_id, utc_now


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
