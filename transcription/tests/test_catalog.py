import pytest

from transcription.catalog import parse_feed, fetch_catalog, compare_catalogs
from transcription.common import atomic_write_json, find_episode, load_manifest


def feed(items):
    return ('<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Synthetic podcast</title>' + items + '</channel></rss>').encode()


def item(number, *, explicit=None, guid=None):
    return f'<item><title>EP{number} synthetic</title><itunes:episode>{explicit or number}</itunes:episode><guid>{guid or number}</guid><pubDate>Thu, 05 Nov 2026 00:00:00 +0000</pubDate><enclosure url="https://example.invalid/{number}.mp3" type="audio/mpeg" length="100"/></item>'


def test_catalog_number_matching_and_load(tmp_path):
    catalog = parse_feed(feed(item(579) + item(57) + item(670)))
    path = tmp_path / "catalog.json"
    atomic_write_json(path, catalog)
    rows = load_manifest(path)
    assert find_episode(rows, "EP57")["audio_url"].endswith("/57.mp3")
    assert find_episode(rows, "EP670")["episode_number"] == "670"
    assert rows[0]["publication_date"] == "2026-11-05T00:00:00+00:00"


def test_ambiguous_feed_fails_instead_of_wrong_download():
    with pytest.raises(ValueError, match="duplicate"):
        parse_feed(feed(item(57) + item(57, guid="different")))
    with pytest.raises(ValueError, match="disagree"):
        parse_feed(feed(item(579, explicit=57)))
    assert len(parse_feed(feed(item(57) + item(57)))["episodes"]) == 1


@pytest.mark.parametrize("content", [b"not xml", b"<rss/>", b'<!DOCTYPE rss [<!ENTITY e "unsafe">]><rss/>', feed("")])
def test_invalid_feed_rejected(content):
    with pytest.raises(ValueError):
        parse_feed(content)


def test_catalog_never_overwrites(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("existing")
    with pytest.raises(ValueError, match="immutable"):
        fetch_catalog("https://example.invalid/rss", path)
    assert path.read_text() == "existing"


def test_quarantine_keeps_other_episodes_and_excludes_all_ambiguous_duplicates():
    result = parse_feed(feed(item(57) + item(670) + item(670, guid="duplicate") + item(579, explicit=58)), quarantine_ambiguous=True)
    assert [r["episode_id"] for r in result["episodes"]] == ["EP57"]
    assert len(result["quarantined_items"]) == 3
    with pytest.raises(ValueError, match="not found"):
        find_episode(result["episodes"], "EP670")


def test_catalog_diff_is_non_destructive_and_does_not_mean_new_publication(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("No network for catalog diff"))
    old, new, report = (tmp_path / name for name in ("old.json", "new.json", "delta.json"))
    initial = parse_feed(feed(item(57) + item(450) + item(670)))
    current = parse_feed(feed(item(57) + item(670) + item(674)))
    current["episodes"][1]["audio_url"] = "https://example.invalid/changed.mp3"
    atomic_write_json(old, initial); atomic_write_json(new, current)
    before_bytes = old.read_bytes(), new.read_bytes()
    result = compare_catalogs(old, new, report)
    assert result["added_to_catalog"] == ["EP674"]
    assert result["missing_from_catalog"] == ["EP450"]
    assert result["unchanged_count"] == 1
    assert result["changed"][0]["episode_id"] == "EP670"
    assert result["changed"][0]["audio_identity_requires_review"]
    assert result["downloads_started"] == result["artifacts_deleted"] == 0
    assert (old.read_bytes(), new.read_bytes()) == before_bytes
    with pytest.raises(FileExistsError): compare_catalogs(old, new, report)


@pytest.mark.parametrize("fault", ["duplicate_id", "duplicate_guid", "renumbered_guid", "number_mismatch"])
def test_catalog_diff_rejects_ambiguous_identities(tmp_path, fault):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    initial = parse_feed(feed(item(57) + item(670)))
    current = parse_feed(feed(item(57) + item(674)))
    if fault == "duplicate_id": current["episodes"].append(current["episodes"][0])
    elif fault == "duplicate_guid": current["episodes"][1]["guid"] = current["episodes"][0]["guid"]
    elif fault == "renumbered_guid": current["episodes"][1]["guid"] = initial["episodes"][1]["guid"]
    else: current["episodes"][0]["episode_number"] = "579"
    atomic_write_json(old, initial); atomic_write_json(new, current)
    with pytest.raises(ValueError): compare_catalogs(old, new)
