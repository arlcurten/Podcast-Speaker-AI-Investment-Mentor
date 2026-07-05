# Data Model

The long-term project should preserve evidence and derivation boundaries. Each layer should remain traceable to the layer below it.

## Layers

1. RSS XML snapshot
   - Raw feed response saved at a fetch timestamp.
   - Evidence for episode metadata and enclosure URLs at that point in time.

2. Episode manifest
   - JSONL/CSV index derived from RSS.
   - Used by download and processing scripts.
   - Stores feed provenance and episode metadata.

3. Local MP3
   - Downloaded selected episode audio.
   - Should be stored with final file size and SHA-256.

4. Audio metadata
   - ffprobe-derived format, codec, duration, bitrate, sample rate, and channels.
   - Confirms media parse/decode viability.

5. Raw ASR transcript
   - Direct model output with raw segments and timestamps.
   - Must not be overwritten by cleanup or LLM correction.

6. Deterministic merged utterances
   - Readability layer derived from raw ASR segments.
   - Preserves source segment IDs and timestamps.
   - Does not change text and is not topic segmentation.

7. Traditional Chinese normalized transcript
   - Derived text layer that retains raw text and adds normalized `zh-TW` text.
   - Preserves timestamps and source segment IDs.
   - Uses deterministic normalization and glossary replacement, not free-form LLM rewriting.

8. Phase 2 input package
   - A compact copy of the Phase 1 evidence needed for semantic extraction.
   - Includes raw transcript, normalized merged transcript, merge integrity metadata, episode/audio metadata, and terminology reference.

9. Episode Semantic Map
   - Level 1 semantic overview of major episode themes and threads.
   - Must reference source evidence.

10. Topic-thread Reasoning Records
   - Level 2 reasoning records.
   - Threads may be long, overlapping, or non-contiguous.
   - Must preserve conditions, assumptions, risks, exceptions, counterexamples, opinion changes, decisions, and speaker behavior.

11. Episode-Level Provisional Principles
   - Level 3 layer derived from one episode's Level 2 records.
   - Captures episode-local recurring decision patterns and preferences.
   - Supports `principle_extraction`, `light_principle_grouping`, and `bypass`.
   - Must remain provisional and evidence-linked.

12. Cross-Episode Decision Principles
   - Level 4 layer comparing Level 2 cases and Level 3 provisional principles across episodes.
   - Preserves conditions, exceptions, conflicts, counterexamples, evolution, independent episode coverage, and confidence.
   - Must link back to Level 2 source records.

13. Future behavioral patterns
   - Cross-case patterns such as risk attitude, changing views, and uncertainty handling.
   - Should remain auditable to source cases.

14. Future speaker model
   - Higher-level representation used by the mentor system.
   - Should not erase uncertainty or source evidence.

## Core Rules

- Derived artifacts do not overwrite raw artifacts.
- Every upper layer should be traceable to source IDs and timestamps.
- Preserve audio SHA-256 for reproducibility.
- Preserve model, device, compute type, parameters, package versions, and execution metadata.
- Prefer project-relative paths for artifacts inside the repository.
- If an absolute path is useful, store it separately from the project-relative path.
- Text normalization must retain original text and store normalized text separately.
- LLM extraction must store prompts, model, provider, version, schema version, token/cost metadata when available, and confidence/uncertainty notes.
- Terminology corrections must be annotations or parallel fields, not destructive transcript rewrites.
- Level 1 must record episode structure and route evidence to Level 2 extraction.
- Level 3 principles must be marked provisional and scoped to their episode.
- Level 4 principles must preserve source case coverage and counterexamples.
- A principle is an abstraction derived from records. It must not be counted as an additional independent piece of evidence.
- Principle confidence is multi-dimensional and deterministically constrained from provenance where possible.
- Level 3 `overall` confidence is limited to low/medium because it is episode-local.
- Runtime eligibility and runtime mode are separate from extraction confidence.
- Do not treat transcript text as verified until manual review is complete.

## Project-Wide Path Rule

Use project-relative paths for artifacts whenever possible. Historical absolute runtime paths may be preserved as provenance, but they should not be the only locator.

## Current EP674 Layers

Verified:

- RSS snapshots exist.
- Episode manifest exists.
- EP674 MP3 and audio metadata exist.
- EP674 raw `large-v3-turbo` transcript exists.
- EP674 deterministic merged transcript exists and has passed raw-to-merged integrity validation.
- EP674 Traditional Chinese normalized raw and merged transcript layers exist.
- EP674 minimal deterministic topic segmentation, classification, and routing POC exists as legacy review/reference only.
- EP674 segment audit and manual review package exist.

Current higher-level status:

- Phase 2 Level 1/Level 2 extraction has pilot outputs for EP674, EP670, EP450, and EP57.
- Offline Level 3/Level 4 principle extraction POC has generated local outputs from those Level 2 records.
- `legacy_mentor_rag_poc/` can retrieve Level 2 records and generate case-grounded answers.
- `mentor_agent/` consumes private Level 2 records and Level 4 principles for first-version online principle-assisted or case-only answering.

## Phase 2 Inputs

Complete Phase 2 inputs and generated outputs live in the private repository and are accessed through `PIPELINE_DATA_ROOT`. Public samples are synthetic or curated derivatives only, not canonical pipeline input.

It intentionally does not copy MP3 files, benchmark artifacts, old deterministic topic outputs, or cloud/runtime scratch data.
