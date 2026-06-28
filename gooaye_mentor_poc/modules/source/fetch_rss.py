#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email.utils
import html
import logging
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from modules.common import DATA, slugify_episode, utc_now, write_csv, write_json, write_jsonl


RSS_URL = "https://feeds.soundon.fm/podcasts/4f2a74ec-cc7a-4284-be4b-74b882da701c"
APPLE_LOOKUP_URL = "https://itunes.apple.com/lookup"
APPLE_COLLECTION_ID = "1500839292"
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def text(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    if found is not None and found.text:
        return html.unescape(found.text.strip())
    return None


def clean_html(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_pubdate(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def episode_number(title: str | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if not title:
        return None
    match = re.search(r"\bEP\.?\s*(\d+)\b", title, re.I)
    return match.group(1) if match else None


def parse_rss(xml_bytes: bytes, provenance: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel not found")
    podcast_title = text(channel, "title") or ""
    ingestion_timestamp = utc_now()
    rows: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        title = text(item, "title")
        guid = text(item, "guid")
        ep_num = episode_number(title, text(item, "itunes:episode"))
        stable = f"EP{ep_num}" if ep_num else slugify_episode(guid or title or str(len(rows)))
        row = {
            "podcast_title": podcast_title,
            "episode_title": title,
            "episode_number": ep_num,
            "episode_id": stable,
            "guid": guid,
            "publication_date": parse_pubdate(text(item, "pubDate")),
            "duration": text(item, "itunes:duration"),
            "description": clean_html(text(item, "description") or text(item, "content:encoded")),
            "audio_url": enclosure.get("url") if enclosure is not None else None,
            "audio_mime_type": enclosure.get("type") if enclosure is not None else None,
            "rss_enclosure_length": enclosure.get("length") if enclosure is not None else None,
            "audio_length_from_rss": enclosure.get("length") if enclosure is not None else None,
            "ingestion_timestamp": ingestion_timestamp,
            "requested_rss_url": provenance.get("requested_rss_url"),
            "resolved_rss_url": provenance.get("resolved_rss_url"),
            "discovery_method": provenance.get("discovery_method"),
            "apple_collection_id": provenance.get("apple_collection_id"),
        }
        rows.append(row)
    return podcast_title, rows


def apple_lookup_feed(collection_id: str) -> str:
    response = requests.get(APPLE_LOOKUP_URL, params={"id": collection_id}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results or not results[0].get("feedUrl"):
        raise ValueError(f"Apple lookup did not return feedUrl for collection ID {collection_id}")
    return str(results[0]["feedUrl"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rss-url", default=RSS_URL)
    parser.add_argument("--apple-fallback", action="store_true", help="If requested RSS fails, resolve current feed via Apple Podcasts lookup.")
    parser.add_argument("--apple-collection-id", default=APPLE_COLLECTION_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    requested_url = args.rss_url
    resolved_url = requested_url
    discovery_method = "direct"
    requested_error = None
    try:
        response = requests.get(resolved_url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        requested_error = str(exc)
        if not args.apple_fallback:
            print(f"Failed to download RSS: {exc}", file=sys.stderr)
            return 2
        try:
            resolved_url = apple_lookup_feed(args.apple_collection_id)
            discovery_method = "apple_lookup_fallback"
            response = requests.get(resolved_url, timeout=30)
            response.raise_for_status()
        except Exception as fallback_exc:
            print(f"Failed to download RSS and Apple fallback failed: {fallback_exc}", file=sys.stderr)
            return 2
    snapshot = DATA / "rss" / f"rss_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.xml"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(response.content)
    try:
        provenance = {
            "requested_rss_url": requested_url,
            "resolved_rss_url": resolved_url,
            "discovery_method": discovery_method,
            "apple_collection_id": args.apple_collection_id if discovery_method == "apple_lookup_fallback" else None,
        }
        podcast_title, rows = parse_rss(response.content, provenance)
    except Exception as exc:
        print(f"Failed to parse RSS: {exc}", file=sys.stderr)
        return 3
    fields = [
        "podcast_title",
        "episode_title",
        "episode_number",
        "episode_id",
        "guid",
        "publication_date",
        "duration",
        "description",
        "audio_url",
        "audio_mime_type",
        "rss_enclosure_length",
        "audio_length_from_rss",
        "ingestion_timestamp",
        "requested_rss_url",
        "resolved_rss_url",
        "discovery_method",
        "apple_collection_id",
    ]
    write_jsonl(DATA / "manifests" / "episodes.jsonl", rows)
    write_csv(DATA / "manifests" / "episodes.csv", rows, fields)
    write_json(DATA / "manifests" / "rss_ingestion_metadata.json", {
        "requested_rss_url": requested_url,
        "resolved_rss_url": resolved_url,
        "rss_url": resolved_url,
        "discovery_method": discovery_method,
        "requested_rss_error": requested_error,
        "apple_collection_id": args.apple_collection_id if discovery_method == "apple_lookup_fallback" else None,
        "fetched_at": utc_now(),
        "snapshot": str(snapshot.relative_to(DATA.parent)),
        "podcast_title": podcast_title,
        "channel_title": podcast_title,
        "episode_count": len(rows),
        "ingestion_timestamp": utc_now(),
    })
    print(f"RSS parsed: {len(rows)} episodes")
    print("Latest 10 episodes:")
    for row in rows[:10]:
        print(f"- {row.get('episode_id')}: {row.get('publication_date')} | {row.get('episode_title')}")
    found_672 = any(str(row.get("episode_number")) == "672" or row.get("episode_id") == "EP672" for row in rows)
    print(f"EP672 found: {found_672}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
