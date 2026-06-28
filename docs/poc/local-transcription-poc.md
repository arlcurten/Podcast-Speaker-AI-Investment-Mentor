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

## Pending Measurement

- Clean fixed-clip `large-v3-turbo` benchmark with idle GPU.
- Same-clip `large-v3` comparison if GPU headroom permits.
- Manual transcript quality review.

## Clean Benchmark Attempt

Prepared but not executed because the GPU was not stably near idle.

Fixed clip:

- Source: `gooaye_mentor_poc/data/audio/EP674/episode.mp3`
- Clip: `gooaye_mentor_poc/data/benchmarks/EP674/benchmark_clip.mp3`
- Requested start/end: `1200.00s` / `2100.00s`
- Requested duration: `900.00s`
- ffprobe duration: `900.022857s`
- SHA-256: `46311b2f8199b803e46f9e2cb296cae336c0b28c3ad73f76f7b6102488372ace`

GPU readiness observation:

- `nvidia-smi` showed no visible process.
- `nvidia-smi pmon` showed no visible process.
- GPU utilization still fluctuated around 25-32%, with one 56% sample.

Result:

- `large-v3-turbo` clean benchmark: not run.
- `large-v3` same-clip comparison: not run.
- Reason: clean-baseline requirement was not met.

See `gooaye_mentor_poc/reports/clean_gpu_benchmark.md` and `gooaye_mentor_poc/data/evaluation/clean_gpu_benchmark.json`.

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

1. Complete manual review of the four prepared review windows.
2. Record error category, severity, and semantic impact in `data/evaluation/manual_review.csv`.
3. When GPU is idle and user approves, rerun a clean fixed-clip turbo benchmark.
4. Run same-clip `large-v3` comparison only if the pre-check shows enough GPU headroom.
5. Decide whether transcript quality is sufficient for a 20-episode cloud pilot.
