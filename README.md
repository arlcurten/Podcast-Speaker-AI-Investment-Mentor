# Podcast Speaker AI Investment Mentor

Build an evidence-grounded Mentor from historical podcast reasoning, not a
trading system or an unconditional imitation of the speaker.

## Current Work

Previous implementations are frozen under [`legacy/`](legacy/README.md).
Their original snapshot is tagged `archive/pre-rebuild-2026-11-05` (`1919bbc`).
They remain available for comparison, not as the new production pipeline.

The rebuild starts with working [`transcription/`](transcription/README.md):
private episode-manifest input, exact matching, single/batch faster-whisper
worker, checkpoint/resume, raw validation, normalization and a pre-extraction
quality gate. GPU execution has **not** been validated in this rebuild.

No new `knowledge/` or `mentor_agent/` placeholder is created yet. The archived
Mentor remains runnable while its replacement is developed in later stages.

## End-to-End Plan

1. **Transcript foundation:** acquire/locate audio, transcribe, normalize with
   source mapping, inspect content quality. Reuse qualified transcripts;
   re-transcribe only where justified. Structural validity is not ASR accuracy.
2. **Knowledge construction:** compare proposed Codex extraction with the
   archived baseline on the same input. Then produce versioned L1 navigation,
   L2 cases, optional provisional L3 and revisable cross-episode L4. Only
   quality-accepted source versions enter extraction.
3. **Usable Mentor and serving:** local evidence search and bounded verification
   with OpenRouter reasoning, conditional answers and citations. Deliver on a
   small validated corpus first; no full-corpus requirement.
4. **Expansion and continuing updates:** after the service is usable, add
   backlog/new episodes and implement incremental knowledge/index refresh with
   regression checks. This is separate from serving, not its prerequisite.

L2 cases remain the evidence authority; principles are abstractions, not
additional evidence. Preserve conditions, uncertainty, conflicts and historical
context. No live-data tools or current investment claims are implied.

## Commands

```bash
python3 -m pip install -r transcription/requirements.txt
python3 -m transcription --help
python3 -m transcription batch-plan --config transcription/config/batch.example.yaml --dry-run
python3 -m transcription validate-transcript --input "$RAW_TRANSCRIPT" --require-ready
python3 -m transcription prepare --input "$RAW_TRANSCRIPT" --output "$NORMALIZED_OUTPUT"
python3 -m pytest -q
```

The example plan requires a configured private episode manifest. A structural
check without a source-bound quality review reports
`ready_for_extraction: false`. `prepare` never rewrites raw or prior outputs.
Real download/transcription is explicit, never part of tests or dry-run.

## Repository Layout

```text
transcription/     active worker, preparation, quality gate and synthetic tests
legacy/           frozen transcription, extraction and two Mentor baselines
docs/             public policies, setup and labeled historical designs
sample_outputs/   curated demonstration artifacts, never production inputs
reports/public/   aggregate historical evaluation summaries
```

New code does not import the archive. Reviewed utilities are promoted into the
active namespace with their own tests. Full data is not moved with source code.

## Data Boundary

**Private repo = canonical source of truth. Public repo = curated showcase.**
Complete audio/transcripts, semantic outputs, reviews and runtime reports stay
outside this repository. Public files are code, schemas, prompts, templates,
docs and synthetic/redacted/aggregate artifacts only.

Configure `PODCAST_DATA_ROOT`, `PIPELINE_DATA_ROOT` and `PRIVATE_DOCS_ROOT` through
environment variables or the ignored root `.env`. No private URLs, credentials
or real local paths belong in tracked config. Public samples cannot be selected
as production sources or destinations.

- [Private data setup](docs/private_data_setup.md)
- [Public artifacts policy and index](docs/public_artifacts.md)
- [Transcription workflow and quality review](transcription/README.md)
- [RunPod reference and worker setup](docs/runpod_transcription.md)
- [Archived architecture](docs/reasoning_mentor_architecture.md)

Live web/market tools, portfolio integration, memory, full corpus, neural
embedding infrastructure, UI and deployment remain out of scope.
