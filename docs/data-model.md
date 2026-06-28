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

7. Future topic segments
   - Semantic grouping by subject.
   - Should reference merged utterance IDs or raw segment IDs.

8. Future decision/reasoning cases
   - Extracted investment reasoning examples.
   - Must link back to source timestamps and text.

9. Future behavioral patterns
   - Cross-case patterns such as risk attitude, changing views, and uncertainty handling.
   - Should remain auditable to source cases.

10. Future speaker model
   - Higher-level representation used by the mentor system.
   - Should not erase uncertainty or source evidence.

## Core Rules

- Derived artifacts do not overwrite raw artifacts.
- Every upper layer should be traceable to source IDs and timestamps.
- Preserve audio SHA-256 for reproducibility.
- Preserve model, device, compute type, parameters, package versions, and execution metadata.
- Prefer project-relative paths for artifacts inside the repository.
- If an absolute path is useful, store it separately from the project-relative path.
- If text normalization is introduced later, retain original text and store normalized text separately.
- If LLM extraction is introduced later, store prompts, model, version, and confidence/uncertainty notes.
- Do not treat transcript text as verified until manual review is complete.

## Project-Wide Path Rule

Use project-relative paths for artifacts whenever possible. Historical absolute runtime paths may be preserved as provenance, but they should not be the only locator.

## Current EP674 Layers

Verified:

- RSS snapshots exist.
- Episode manifest exists.
- EP674 MP3 and audio metadata exist.
- EP674 raw `large-v3-turbo` transcript exists.
- EP674 deterministic merged transcript exists.
- EP674 segment audit and manual review package exist.

Pending:

- Manual quality judgments.
- Semantic topic segmentation.
- Reasoning-case extraction.
- Behavioral pattern extraction.
