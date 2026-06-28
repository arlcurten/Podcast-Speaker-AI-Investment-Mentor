# Data Source Notes

## RSS Role

RSS is the public XML catalog for a podcast. A podcast episode normally appears as an RSS `<item>`. The `<enclosure url>` points to the actual MP3 file.

RSS itself does not store audio. In this project:

- SoundOn is the hosting provider that serves the feed and MP3 files.
- Apple Podcasts is a directory/player and can expose the current feed URL through lookup.
- The Apple Podcasts show page is not an RSS URL.

## Episode Manifest

The ingestion script parses RSS and writes source artifacts:

- `data/source/rss_snapshot_*.xml`
- `data/source/episodes.jsonl`
- `data/source/rss_ingestion_metadata.json`

The manifest is the stable internal episode index for later download, transcription, and evaluation steps. Later scripts should not repeatedly parse XML if `data/source/episodes.jsonl` already captures the feed snapshot being processed.

Verified manifest fields include podcast title, episode title, episode number, GUID, publication date, duration, description, audio URL, audio MIME type, RSS enclosure length, ingestion timestamp, requested RSS URL, resolved RSS URL, discovery method, and Apple collection ID.

The manifest is a snapshot of one feed fetch. It is not a guarantee that the feed URL or episode URLs will remain unchanged forever.

## Feed Migration Issue

Observed during this POC:

- Old requested feed:

```text
https://feeds.soundon.fm/podcasts/4f2a74ec-cc7a-4284-be4b-74b882da701c
```

- Result: HTTP 404 during local execution.
- Active feed resolved through Apple Podcasts lookup:

```text
https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml
```

Possible causes include hosting migration, feed recreation, provider-side UUID changes, or Apple directory updates.

Recommended handling:

1. Treat the configured URL as requested input, not permanent truth.
2. If it fails, run the existing lookup fallback using Apple collection ID `1500839292`.
3. Verify `channel_title` is `Gooaye 股癌`.
4. Save requested URL, resolved URL, discovery method, fetch timestamp, channel title, and lookup ID.
5. Update configuration when appropriate, but keep the old feed URL in provenance.

## RSS Enclosure Length Issue

EP674 verified case:

- RSS enclosure length: `1`
- HTTP Content-Length: `52,478,280`
- actual file size: `52,478,280`
- SHA-256: `21d2c45de471b1a94af05e7a8766b3aad3ce56e7329a639635c210035445791e`

The RSS enclosure length is therefore an invalid placeholder for this episode. Do not use it as the sole expected size.

Correct handling:

1. Store `rss_enclosure_length`.
2. Mark `rss_length_valid = false` when unreasonable.
3. Use HTTP Content-Length and actual file size as the size check.
4. Use ffprobe for media parse/decode validation.
5. Use SHA-256 as the local reproducibility fingerprint.

## Validation Roles

- HTTP status and headers show whether the server actually returned an audio response.
- HTTP Content-Length is the best available server-provided size for the current download.
- Actual file size verifies what was written locally.
- ffprobe verifies that the local file can be parsed as media and reports format, duration, codec, sample rate, channels, and bitrate.
- SHA-256 is a local reproducibility fingerprint. SoundOn does not provide an official checksum for comparison in this POC.
