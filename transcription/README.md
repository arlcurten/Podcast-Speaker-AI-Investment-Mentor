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
Episodes stay sequential even with opt-in batched inference inside one episode.

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
python3 -m transcription catalog-diff --before "$OLD_CATALOG" --after "$NEW_CATALOG" --output "$PRIVATE_DELTA"
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
`--inference-mode sequential|batched` and `--inference-batch-size` configure
within-episode decoding, not simultaneous episode workers.
Only listed episodes run; numeric IDs never fall back to substring matching.

`catalog-diff` compares existing snapshots without network/model calls. It lists
added/missing IDs and metadata changes; GUID renumbering or duplicate identities
fail before work is scheduled. A feed membership change is not proof of a new
publication: RSS windows can omit old episodes. Missing entries never delete
data, changed audio metadata requires review, and new entries are not downloaded
automatically. Choose episodes explicitly after inspecting the private delta.

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

Rejected decoder output is quarantined under the batch directory as
`rejected/<episode>.attempt-<n>.rejected.json` when JSON serialization and disk
space allow. Checkpoint errors include a source-independent validation reason
and the saved payload checksum. Quarantine files are not completed transcripts
and are excluded from successful transfer bundles; preserve them separately
when investigating a worker failure. Retry attempts never overwrite them.

An explicitly inspected failure can use `quality.recover_padding_tail` offline
with the rejected file's expected episode and SHA-256. It only quarantines one
final segment starting in the last audio second, ending 1-30 seconds beyond the
audio, with `no_speech_prob >= 0.8`. A specifically inspected case may explicitly
set `maximum_tail_lead_seconds` up to two seconds; that override is recorded in
recovery metadata, not enabled by batch processing. Other errors still fail. Publish
the returned payload in a **new version** and retain the rejected source. The
excluded segment remains verbatim in recovery metadata; retained text, IDs and
timestamps do not change. This is not a batch default, silence certification or
content approval. The final audio region still requires review before extraction.

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

## Raw Preservation

Keep raw versions, rejected decoder outputs and paired provenance in private
storage. A `transcript_snapshot_selection.v1` JSON contains an explicit `files`
list: `path` relative to the supplied private root, `sha256`, `size_bytes`,
`role` (`raw`, `rejected`, `metadata`), optional `episode_id`, and boolean
`active`. Select at most one raw version per active episode; historical and
rejected versions remain backed up but are not review inputs.

```bash
python3 -m transcription snapshot-create --selection "$RAW_SELECTION" --root "$PRIVATE_ROOT" --output "$SNAPSHOT" --storage-config "$LOCAL_STORAGE_CONFIG"
# Add --execute after inspecting the dry-run.
python3 -m transcription snapshot-verify --snapshot "$SNAPSHOT"
python3 -m transcription snapshot-restore --snapshot "$SNAPSHOT" --output "$NEW_RESTORE_DIR" --storage-config "$LOCAL_STORAGE_CONFIG" --execute
python3 -m transcription snapshot-protect --snapshot "$SNAPSHOT" --root "$PRIVATE_ROOT" --execute
python3 -m transcription corpus-review --snapshot "$SNAPSHOT" --root "$PRIVATE_ROOT" --output "$NEW_TRIAGE_REPORT"
```

Storage config uses the existing batch `storage` section, including the real
host-drive check on WSL. The snapshot is a compressed ZIP, manifest and checksum
receipt, verified before publication. No audio, symlinks or non-JSON files are
accepted; files are explicitly selected, never a copy of the whole private
workspace. The current safety cap is 2 GB uncompressed per snapshot. Existing
snapshots and restore directories are never overwritten. Test a complete
restore into a new temporary directory before relying on a snapshot.

`snapshot-protect` first verifies source hashes, then makes raw/rejected files
read-only. This prevents ordinary accidental writes, not an owner deliberately
changing permissions or replacing a file. A same-device snapshot is not disaster
recovery; copy the verified snapshot and checksum receipt to an independently
confirmed private backup destination. Never publish it in the public repository.

`corpus-review` reuses ASR review signals plus repetition, long-segment and
recovery flags. It emits a private listening-priority queue, not an error-rate
estimate or passing review. Counts cover all segments; the queue retains at most
16 separated candidate regions per episode, prioritizing explicit recovery,
repetition and duration flags over probability flags alone.
Recovery annotations remain visible in listening
packets. `prepare` without `--review` can produce an unaccepted reading draft;
it remains `ready_for_extraction: false`. A genuinely accepted source review
requires a new normalized version before knowledge extraction. Raw is unchanged.

## Single-GPU Batching

The default remains `inference_mode: sequential`, `inference_batch_size: 1`.
An experimental candidate uses `batched` with size `4`; accepted sizes are 1-32,
not a promise that they fit every GPU. Only one episode/model is active at a
time. The worker does not prefetch an entire corpus or run parallel batches.

The batched decoder requires VAD enabled and previous-text conditioning disabled.
Timestamp tokens are explicitly enabled; optional word timestamps, source IDs
and ASR diagnostics remain available. Unlike sequential decoding, upstream
batched decoding does not use temperature fallback or the same silence/log-probability
rejection thresholds. See the pinned [upstream implementation](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py).
This is a candidate decoding change, not an automatic quality-preserving speedup.

Changing mode or size requires a **new batch ID and transcript root**; it cannot
reuse a sequential checkpoint. Existing sequential fingerprints remain compatible.
Mode, size, decoding caveats and performance metadata are recorded per run.
OOM or other errors do not silently change mode/size; preserve the failed job,
select a smaller size in a new private version and retry explicitly.

Compare two existing same-audio runs without model calls:

```bash
python3 -m transcription compare-transcripts --baseline "$BASELINE_RAW" --candidate "$CANDIDATE_RAW" --output "$PRIVATE_COMPARISON"
```

The command verifies episode/audio identity and raw structure, reports timings,
memory, timestamp spans, text/segment counts and settings differences. It withholds
the speedup ratio when model/hardware/package/decoding-control metadata differ
or are missing. Reports contain no transcript text and never approve extraction.
Run `review-packet` for both versions and listen to comparable time regions;
matching text or a high final timestamp is not a word-error-rate measurement.
Full comparison reports remain private. No Git or cloud actions are performed.

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

Shared cloud mounts need different accounting: set `storage.shared_volume_root`
and the actual `storage.volume_capacity_bytes`. This checks owned-volume bytes
and its allocation, rather than attributing changes across an entire shared
storage pool to one job. The separate `storage_volume_budget.v1` persists its
baseline and limits; old host budgets are not silently migrated. All worker
writes must stay inside that volume. This option is prohibited on WSL and does
not replace the Windows host-space safeguards. See the GPU setup guide for
matching installation and runtime settings.

On shared volumes, full usage scans happen at stage boundaries and before
output writes, not per download chunk or decoded segment. Downloads reserve
their capped maximum up front and report cumulative bytes against that
reservation; decoding holds segments in memory. Progress checks still read
live filesystem free space. This requires exclusive workspace ownership and
does not replace an OS quota against unrelated writers. WSL continues to check
actual host free space and growth on every progress check.
An orchestrator may set `TRANSCRIPTION_CODE_COMMIT` to the immutable deployed
40-character Git revision; raw metadata records it, or null when unknown.

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
It can prepare a pending-review view, but that is not accepted evidence.
The active `knowledge` consumer rejects unaccepted versions. New outputs never
overwrite old ones.

### Episode-Local Reviewed Annotations

`transcript_review.v1` keeps its original behavior. To require a separately
reviewed annotation sidecar, use `transcript_review.v2` with all the same source
review fields and one additional reference:

```json
"annotations": {"path": "SYN1_annotations.json", "sha256": "<exact sidecar SHA-256>"}
```

No extra CLI flag or knowledge configuration field is needed. The reference is
required for v2 and cannot be dropped to bypass a note. Its path is relative to
the review file, or an absolute private path. Supporting-review paths below are
relative to the sidecar, or absolute private paths. All resolved paths must stay
outside the public repository, including through symlinks. Every referenced file
must exist and match its SHA-256 when preparing, extracting or loading a release.

The following schema example is synthetic, with no private transcript source.
Replace the placeholders with actual hashes, review metadata and exact raw
anchors in private storage; this example is not an acceptance review:

```json
{
  "schema_version": "transcript_annotations.v1",
  "episode_id": "SYN1",
  "transcript_sha256": "<exact raw file SHA-256>",
  "decision": "accept_annotations",
  "reviewer": "<actual reviewer or reviewing system>",
  "reviewed_at": "<actual review timestamp>",
  "method": "<checks performed, evidence type, and limitations>",
  "supporting_reviews": [
    {"path": "evidence/focused_review.json", "sha256": "<exact file SHA-256>"}
  ],
  "annotations": [
    {
      "annotation_id": "SYN1:A01",
      "kind": "interpretive_caution",
      "severity": "critical",
      "anchors": [
        {"segment_id": 12, "raw_text": "<complete exact raw segment text>", "start": 10.0, "end": 12.0}
      ],
      "note": "<spoken inconsistency, numerical ambiguity, attribution or scope caution>"
    }
  ]
}
```

The sidecar accepts exactly these fields. `annotations`, `supporting_reviews`
and each `anchors` list are nonempty. Annotation IDs are unique within the
episode; each note's anchor IDs are unique. Anchors must match complete raw text
and original segment timestamps exactly, not edited text or inferred word timing.
Record actual audio interval coverage and audio identity/binding limitations in
the supporting review artifact. A hash proves file identity, not listening,
audio fidelity, review truth or independent corroboration.

Kinds and their contracts:

- `transcription_correction`: add a required nonempty `correction` string for a
  separately reviewed ASR/terminology reading. It is context, never a text edit.
  Combine corrections on an overlapping raw segment into one reviewed reading;
  incompatible correction strings on overlapping anchors are rejected.
- `interpretive_caution`: the transcript may accurately preserve a spoken slip,
  inconsistent arithmetic, listener premise or attribution hazard. Explain it in
  `note`; a `correction` field is prohibited. Do not invent intended wording.
- `uncertainty`: preserve unresolved wording or meaning in `note`; a `correction`
  field is prohibited. Model agreement or repetition of an ambiguous homophone
  alone is not resolution and must not be promoted into a confirmed correction.

Severity is `critical` or `noncritical`. A critical `uncertainty` blocks readiness
even if the source review claims acceptance with an empty critical-issue list.
A separately qualified critical caution is not automatically blocking, but it
does not approve the source either: the source review must still accept with no
unresolved critical issues. Do not relabel unresolved critical semantics as a
caution to pass. Reviewers remain responsible for these semantic distinctions.
Use `method` and `note` to state model-assisted evidence and its limitations
honestly; no field or validation result implies human listening.

Raw and normalized text, mapping and dictionary suggestions are unchanged.
Normalized metadata additionally pins `annotations_sha256`; complete notes stay
in their sidecar. A changed sidecar or supporting review needs a new sidecar,
source review, normalized version and affected knowledge run, with fresh review.
Retain earlier versions. Source acceptance and annotation review are separate;
pending normalization remains possible but does not become accepted evidence.
There is no scoped exclusion, automatic correction, or quality-gate bypass.
Unanchored missing-speech concerns remain in the source review's unresolved
critical issues, not fabricated segment annotations.

## Validation

```bash
python3 -m compileall -q transcription
python3 -m pytest -q transcription/tests
```

Tests use synthetic temporary files and mocked download/Whisper calls; no private
corpus, CUDA, network or API keys. Real GPU/audio-quality validation and the
extraction comparison remain separate tasks.
