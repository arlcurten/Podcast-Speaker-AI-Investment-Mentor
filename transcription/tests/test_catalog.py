import pytest

from transcription.catalog import parse_feed, fetch_catalog
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
