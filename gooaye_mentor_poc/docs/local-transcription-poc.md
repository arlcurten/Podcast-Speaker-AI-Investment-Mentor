# Transcription POC

## Verified EP674 Audio

Verified from `data/audio/EP674/download_metadata.json` and `data/audio/EP674/audio_metadata.json`:

- Episode: EP674
- Title: `EP674 | 🦋`
- MP3 size: `52,478,280` bytes
- Duration: `3180.277551` seconds
- Codec: MP3
- Sample rate: 44100 Hz
- Channels: 2
- SHA-256: `21d2c45de471b1a94af05e7a8766b3aad3ce56e7329a639635c210035445791e`

## large-v3-turbo Full Episode

Verified from `data/transcripts/EP674/large-v3-turbo/run_metadata.json`:

- Backend: faster-whisper
- Model: `large-v3-turbo`
- Device: CUDA
- Compute type: float16
- Runtime: `451.807040401` seconds
- Realtime factor: `0.14206528617570963`
- Approximate speed: about 7x realtime
- Segment count: `2493`
- Output character count: `20,260`
- Status: success

This proves the local pipeline can complete a full episode with `large-v3-turbo`.

## Observed But Unreliable

Observed limitations:

- The GPU may have had other workload during the run.
- The runtime is useful feasibility evidence but is not a clean baseline.
- Existing `peak_vram_mb` values from the original run are marked unreliable because PyTorch CUDA counters do not capture all CTranslate2 allocation behavior.
- A manual spot check reportedly saw about `3454 MiB / 4096 MiB`, but this is not a formal peak measurement and may include other GPU processes.

## Controlled Desktop Benchmark

The clean same-clip benchmark has now completed under a controlled desktop baseline. This is not a perfect zero-background lab run, but the GPU had low power draw, no visible process, and stable VRAM baseline.

Fixed clip:

- Source: `gooaye_mentor_poc/data/audio/EP674/episode.mp3`
- Clip: `gooaye_mentor_poc/data/benchmarks/EP674/benchmark_clip.mp3`
- Requested start/end: `1200.00s` / `2100.00s`
- Requested duration: `900.00s`
- ffprobe duration: `900.022857s`
- SHA-256: `46311b2f8199b803e46f9e2cb296cae336c0b28c3ad73f76f7b6102488372ace`

Baseline:

- `nvidia-smi` showed no visible process.
- VRAM min/max/median: `794 / 817 / 794.5 MiB`
- Utilization min/max/median: `26 / 31 / 29%`
- Power min/max/median: `6.70 / 7.84 / 7.76 W`
- P-states observed: `P5`, `P8`

large-v3-turbo runs:

| Run | Runtime | RTF | Peak total VRAM | Segments | Chars | Transcript hash |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| run-01 | 88.793s | 0.099 | 3280 MiB | 611 | 5889 | `a35a3bfa1c341d6a382554ec2a1405cc029e8b624cfc5de880867ba8385c1607` |
| run-02 | 96.810s | 0.108 | 3300 MiB | 610 | 5713 | `a0334749955c809feb7949bbb1f988298ec6726b53e48e2a161453d2b9592d08` |
| run-03 | 93.708s | 0.104 | 3334 MiB | 610 | 5713 | `a0334749955c809feb7949bbb1f988298ec6726b53e48e2a161453d2b9592d08` |

Stability:

- Mean runtime: `93.104s`
- Median runtime: `93.708s`
- Standard deviation: `4.043s`
- Runtime variation: `9.029%`
- Classification: usable with minor desktop interference
- Transcript hashes: run-02 and run-03 match; run-01 differs.

large-v3 same-clip result:

- Status: success
- Device: CUDA
- Compute type: `int8_float16`
- Runtime: `242.694s`
- RTF: `0.270`
- Peak total-device VRAM: `3571 MiB`
- Segments: `537`
- Characters: `5941`
- Transcript hash: `40256b7f12c568be2aacd2d8e9e3dd82de0b0234c319e4b31ceb25d9ac810572`

Interpretation:

- `large-v3-turbo` has a usable controlled desktop same-clip baseline on this local RTX 3050.
- `large-v3` can complete this 15-minute clip with `int8_float16`, but it is much slower and uses more VRAM.
- Previous larger/longer local `large-v3` tests still show full-episode risk on 4 GB VRAM.

Manual transcript quality review remains pending. Do not infer quality superiority from these benchmark numbers.

Future benchmark metadata should record:

- `nvidia-smi` pre-check
- baseline total-device VRAM
- sampled peak total-device VRAM
- sampling interval
- whether other GPU processes were present

## large-v3 Results

Verified from `data/evaluation/benchmark_comparison.csv`:

- `large-v3_partial_1200s` on CUDA `int8_float16`: failed with CUDA out of memory.
- `large-v3_partial_300s_int8_float16` on CUDA `int8_float16`: failed with CUDA out of memory.
- `large-v3_partial_60s_int8` on CPU: succeeded, runtime about `98.142s`, RTF about `1.636`.

Interpretation:

- Local 4 GB RTX 3050 is not a safe target for full-episode `large-v3`.
- CPU int8 is too slow for real processing and should remain a smoke-test path.
- Use `large-v3` comparisons on a fixed short clip only after the GPU is idle and the user approves.

## Segment Audit And Merged Layer

Verified from `data/evaluation/EP674_segment_audit.json`:

- Raw segment count: `2493`
- Faster-whisper segment shape: true
- Appears to be word timestamps: false
- Median raw segment duration: about `1.2s`
- Segments shorter than 1 second: `795`
- Timestamp overlap: `0`
- Non-monotonic timestamps: `0`
- Assessment: over-fragmented for SRT/Markdown readability

Verified from `data/evaluation/EP674_merge_summary.json`:

- Merged count: `132`
- Merged layer is deterministic.
- Text is not modified.
- This is not semantic topic segmentation.

The merge implementation now joins source segment text with a single space between non-empty stripped segment texts. Raw-to-merged integrity is verified by `gooaye_mentor_poc/main.py validate-merge`.

Verified from `gooaye_mentor_poc/data/evaluation/EP674_merge_integrity.json`:

- Merged start timestamp equals the first contributing raw segment start.
- Merged end timestamp equals the last contributing raw segment end.
- `source_segment_ids` match the contributing raw segment IDs in order.
- Merged text equals contributing raw source texts joined with the deterministic separator.

## Normalization And Topic POC

EP674 now has a separate Traditional Chinese normalization layer:

- Raw normalized transcript: `gooaye_mentor_poc/data/transcripts/EP674/large-v3-turbo/normalized_transcript_zh_tw.json`
- Merged normalized transcript: `gooaye_mentor_poc/data/transcripts/EP674/large-v3-turbo/normalized_merged_transcript_zh_tw.json`
- Method: OpenCC `s2twp` plus replacements from `docs/terminology.tsv`.
- Raw ASR transcript remains unchanged.

A minimal deterministic topic segmentation, classification, and routing POC exists for EP674:

- Topic segments: `gooaye_mentor_poc/data/topic_segments/EP674/topic_segments.json`
- Human review package: `gooaye_mentor_poc/reports/EP674_human_review.md`

This POC uses keyword and continuity heuristics with configurable thresholds in `gooaye_mentor_poc/config/topic_routing.yaml`. It is designed for human review and does not create a vector database, RAG index, or Mentor Agent.

## Transcript Quality

Pending manual review. Do not claim the transcript is suitable for knowledge extraction, classification, or RAG until sampled audio has been checked.

Review risk areas:

- company names
- tickers
- financial terms
- numbers and percentages
- negation
- bullish/bearish meaning
- timestamp drift
- hallucination or repetition

## Next Steps

1. Review the selected examples in `gooaye_mentor_poc/reports/EP674_human_review.md`.
2. Add confirmed terminology corrections to `docs/terminology.tsv`.
3. Treat topic/classification limitations as future optimization work unless they block the POC.
4. Decide whether transcript quality is sufficient for a broader pilot.
