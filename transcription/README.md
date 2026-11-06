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

RSS discovery remains available in the archive; this first active slice consumes
an existing private JSON/JSONL episode manifest. RunPod performs transcription
only. Normalization stays local. No GPU smoke is claimed yet.

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
python3 -m transcription batch-plan --config transcription/config/batch.example.yaml --dry-run
python3 -m transcription batch-run --config "$BATCH_CONFIG" --episode EP57 --max-episodes 1
python3 -m transcription batch-status --config "$BATCH_CONFIG"
python3 -m transcription batch-resume --config "$BATCH_CONFIG"
python3 -m transcription batch-resume --config "$BATCH_CONFIG" --retry-failed
python3 -m transcription batch-validate --config "$BATCH_CONFIG"
python3 -m transcription validate-transcript --input "$RAW_TRANSCRIPT" --episode EP57
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

`keep` (example default) and `reuse_existing` retain audio. `delete_on_success`
deletes only after validation and durable job completion. Failures retain audio.
The old shell sync helper remains a **legacy** smoke tool, not the formal v2
publisher; do not use its output layout blindly. Cross-machine formal sync is
still pending. No batch or sync command performs Git operations.

## Quality Gate

Structural checks cover episode identity, nonempty text, unique IDs,
finite/ordered timestamps and duration bounds. They do not measure word error
rate or certify names, numbers, negation, attribution or omissions.

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
