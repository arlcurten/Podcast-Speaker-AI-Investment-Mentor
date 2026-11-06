# Formal Transcription Foundation

Active first slice of the rebuild. Reuses reviewed worker, download, audio
inspection and merge algorithms from archived Phase 1, with namespaced imports
and integrity protections. No legacy imports, LLM calls, semantic extraction
or automated corpus scheduler.

## Flow

```text
private episode metadata -> exact episode resolution -> plan
-> audio download/reuse -> faster-whisper -> immutable raw JSON
-> structural validation -> completed checkpoint
-> content review -> normalized reading view + raw source mapping
-> extraction eligibility (later knowledge pipeline consumes this)
```

`catalog-fetch` records immutable RSS snapshots without downloading audio.
Ambiguous numbering fails by default; `--quarantine-ambiguous` preserves bad
metadata separately and excludes it from runnable episodes. Legacy JSON/JSONL
episode manifests remain readable. The current deployment is a sequential local
worker; a remote GPU is optional, never provisioned by this package.

## Setup and Commands

Python 3.10+ on Linux/WSL, `pip install -r transcription/requirements.txt`.
Actual GPU work also requires root `requirements-runpod.txt`, ffmpeg, ffprobe
and CUDA. Missing CUDA is not silently changed to CPU.

The root `.env` may provide `PODCAST_DATA_ROOT`, `PIPELINE_DATA_ROOT` and
`PRIVATE_DOCS_ROOT`; explicit environment values win. Model/API credentials are
not needed or loaded by this package. Data paths must be external absolute
paths, not the public repository or symlinks into it.

```bash
python3 -m transcription --help
python3 -m transcription catalog-fetch --rss-url "$PODCAST_RSS_URL" --output "$CATALOG_OUTPUT"
python3 -m transcription batch-plan --config transcription/config/batch.example.yaml --dry-run
python3 -m transcription batch-run --config "$BATCH_CONFIG" --episode EP57 --max-episodes 1
python3 -m transcription batch-status --config "$BATCH_CONFIG"
python3 -m transcription batch-resume --config "$BATCH_CONFIG"
python3 -m transcription batch-resume --config "$BATCH_CONFIG" --retry-failed
python3 -m transcription batch-validate --config "$BATCH_CONFIG"
python3 -m transcription validate-transcript --input "$RAW_TRANSCRIPT" --episode EP57
python3 -m transcription review-packet --input "$RAW_TRANSCRIPT" --output "$REVIEW_PACKET" --audio "$EXISTING_AUDIO" --samples 8
python3 -m transcription validate-transcript --input "$RAW_TRANSCRIPT" --review "$QUALITY_REVIEW" --require-ready
python3 -m transcription prepare --input "$RAW_TRANSCRIPT" --output "$NORMALIZED_OUTPUT" --review "$QUALITY_REVIEW"
```

Real commands above are opt-in, not instructions to run automatically.
`batch-plan` and any `--dry-run` do not download, initialize models or write
outputs. Supported overrides include `--episode-file`, `--device`,
`--compute-type`, `--audio-root`, `--transcript-root`, `--output-root`.
Only listed episodes run; numeric IDs never fall back to substring matching.

## Versions and Resume

Default paths, relative to configured roots:

```text
PODCAST_DATA_ROOT/audio/<episode>/episode.mp3
PODCAST_DATA_ROOT/transcripts/raw/<batch_id>/<episode>/raw_transcript.json
PODCAST_DATA_ROOT/transcripts/raw/<batch_id>/<episode>/raw_run_metadata.json
PIPELINE_DATA_ROOT/transcription/batches/<batch_id>/
    batch_manifest.json, checkpoint.json, summary.json, failures.json
```

Use a new batch ID and transcript root for changed model/settings/source
versions. Old POC checkpoints are deliberately incompatible. Completed outputs
are validated before skipping, including with `--retry-failed`. Resume recovers
a published transcript whose metadata/checkpoint was interrupted without a
model call. Invalid existing output is preserved and blocks that job; it is
never silently overwritten. Failed jobs need explicit `--retry-failed`.

Atomic checkpoints use temporary files, fsync and rename. A local file lock
prevents simultaneous workers for the same batch. There is no distributed
locking. A crash during transcription restarts that episode, not the batch;
there is no within-audio model checkpoint.

`keep` and `reuse_existing` retain audio. `delete_on_success` (example default)
deletes only batch-owned audio after validation and durable job completion.
Existing user audio is not deleted; replaced audio fails the cleanup hash check.
Completed-job resume also recovers interrupted cleanup. Failures retain audio.
`batch-export` publishes a checksum-bound bundle; `batch-import` verifies it and
publishes under private `pipeline_data/transcription/imports/<batch_id>` without
overwriting older raw data. Both default to dry-run. The shell sync wrapper
calls this formal importer; the old helper is archived under `legacy/scripts/`.
See [GPU setup and transfer](../docs/runpod_transcription.md). No batch or sync
command performs Git operations.

## Local Storage Safety

The worker handles one episode at a time. Example defaults use cached models
only, a 500 MB per-download limit, a 2 GB audio working set and 25 GB free-space
reserve. Streaming checks stop a download before exceeding the byte limit.
Low-space failures stop admitting further jobs; failed audio is not silently
deleted to make space. Existing transcripts/checkpoints are preserved.

WSL's reported ext4 capacity may exceed the actual host disk. Configure
`storage.host_root` in private config to the confirmed Windows backing-drive
mount. WSL execution fails closed without it; Linux/tmpfs paths do not qualify.
Both host and output filesystem space are checked. See
[Microsoft's WSL storage explanation](https://learn.microsoft.com/en-us/windows/wsl/disk-space).

A shared private `storage.budget_file` persists the initial host free space.
The default 60 GB growth cap is a conservative limit on **net host free-space
decline**, including other applications. Restarting a batch cannot reset or
increase an existing budget. This is application-level admission control, not
an OS quota or protection against unrelated processes exhausting the disk.
Deleting WSL files need not immediately shrink the host VHD; host checks remain
authoritative. The pipeline never resizes or compacts the VHD.

## Quality Gate

Structural checks cover episode identity, nonempty text, unique IDs,
finite/ordered timestamps and duration bounds. They do not measure word error
rate or certify names, numbers, negation, attribution or omissions.

`review-packet` writes a small private listening queue with source IDs,
timestamps, context and transcript/terminology hashes. It combines distributed
coverage with ASR metric and terminology signals; `--segment-id` can include
a known concern. `--samples` (1-32) and `--context-seconds` (0-60) bound the
queue, not the amount of episode content certified. Short recordings may yield
fewer samples. This is quality sampling, not semantic segmentation.

The command performs no model calls, decoding or audio listening. Optional
`--audio` hashes an existing file; without a hash in the original transcript,
the association is explicitly unverified. Packets always remain
`pending_audio_review` and cannot serve as acceptance reviews. CLI stdout
contains metadata only; private packets contain source excerpts.

Python checks structure and chooses candidates. An Agent/reviewer inspects
meaning, conditions and attribution; ambiguous words require source-audio
verification. There is no automatic Agent judge here. ASR thresholds are
triage heuristics, not calibrated error probabilities; high-confidence output
can still be wrong. Candidate counts are segment hits, not confirmed errors
or independent incidents. Sample review is not a whole-episode accuracy claim.

Without review, validation reports `review_required` and
`ready_for_extraction: false`. `--require-ready` exits nonzero until accepted.
Keep this compact review privately; never manufacture approval:

```text
schema_version: transcript_review.v1
episode_id: source episode
transcript_sha256: exact input hash
decision: accept | retranscribe | uncertain
reviewer: actual reviewer identifier
reviewed_at: actual review timestamp
method: audio/text checks performed and their limitations
sampled_segment_ids: reviewed raw segment IDs
unresolved_critical_issues: unresolved issues (empty to accept)
```

A review for different bytes/episode is rejected. The tool checks review
structure/identity, not the truth of the reviewer's assertions. Changed sources
need re-review. Coverage warnings need inspection, not automatic retranscription.

`prepare` preserves raw IDs and text in a merged reading view, applies OpenCC,
and retains terminology corrections as annotations, not replacement claims.
Matching checks both raw and normalized text, retaining dictionary status,
context notes and the matching view. Even human-reviewed dictionary entries
are suggestions requiring verification in the new context, not global edits.
New worker outputs retain `avg_logprob`, `no_speech_prob` and
`compression_ratio` when available; missing/nonfinite metrics are `null`.
`transcription_normalized.v1` metadata carries input/review hashes and readiness.
It can prepare a pending-review view, but that is not accepted evidence. No
active extraction consumer exists yet. New outputs never overwrite old ones.

## Validation

```bash
python3 -m compileall -q transcription
python3 -m pytest -q transcription/tests
```

Tests use synthetic temporary files and mocked download/Whisper calls; no private
corpus, CUDA, network or API keys. Real GPU/audio-quality validation and the
extraction comparison remain separate tasks.
