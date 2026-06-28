# Gooaye Mentor Local POC Report

This report is generated from local artifacts only. Items not present on disk are marked as not executed or not measured.

## Environment
- OS: Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.43
- CPU: 12th Gen Intel(R) Core(TM) i7-12700H
- CPU cores: logical=20, physical=10
- RAM: total=7.590 GB, available at inspection=6.610 GB
- GPU / VRAM: [{'name': 'NVIDIA GeForce RTX 3050 Laptop GPU', 'vram_mb': '4096', 'driver_version': '581.95'}]
- Disk: total=1006.850 GB, available at inspection=943.430 GB
- Python: 3.12.13 | packaged by conda-forge | (main, Mar  5 2026, 16:50:00) [GCC 14.3.0]
- ffmpeg: ffmpeg version 8.0.1-3ubuntu2 Copyright (c) 2000-2025 the FFmpeg developers
- ffprobe: ffprobe version 8.0.1-3ubuntu2 Copyright (c) 2007-2025 the FFmpeg developers
- Backend: faster-whisper CUDA

## Data ingestion
- Requested RSS URL: https://feeds.soundon.fm/podcasts/4f2a74ec-cc7a-4284-be4b-74b882da701c
- Requested RSS result: HTTP 404 during local execution; not treated as successful ingestion.
- Active RSS URL used for manifest: https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml
- Resolved RSS URL: https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml
- Discovery method: apple_lookup_fallback
- Apple collection ID: 1500839292
- Channel title: Gooaye 股癌
- Fetched at: 2026-06-28T05:53:22.702197+00:00
- RSS success: yes
- Manifest count: 674
- RSS snapshot: data/rss/rss_snapshot_20260628T055322Z.xml
- EP672 found in manifest: yes

### Downloaded episode: EP674
- Title: EP674 | 🦋
- Publication date: 2026-06-27T07:23:35+00:00
- MP3 downloaded: yes
- Size: 52478280 bytes
- RSS enclosure length: 1
- RSS length valid: False
- HTTP Content-Length: 52478280
- Actual file size: 52478280
- Duration: 3180.278 seconds
- SHA-256: 21d2c45de471b1a94af05e7a8766b3aad3ce56e7329a639635c210035445791e
- Content-Type valid: True
- RSS size matches actual: not measured
- Integrity validation priority: HTTP Content-Length, actual file size, ffprobe, SHA-256. RSS enclosure length is advisory only.

## Benchmark results

### large-v3-turbo
- Model: large-v3-turbo
- Benchmark scope: full episode
- Device: cuda
- Compute type: float16
- Audio duration: 3180.278 seconds
- Runtime: 451.807 seconds
- Realtime factor: 0.142
- Peak RAM: 1963.600 MB
- Peak VRAM: not measured MB
- Transcript output size: 20260 chars
- Status: success
- Failure reason: not measured

### large-v3-turbo_partial_60s
- Model: large-v3-turbo
- Benchmark scope: partial audio
- Device: cuda
- Compute type: float16
- Audio duration: 60.000 seconds
- Runtime: 18.174 seconds
- Realtime factor: 0.303
- Peak RAM: 1077.030 MB
- Peak VRAM: not measured MB
- Transcript output size: 176 chars
- Status: success
- Failure reason: not measured

### large-v3_partial_1200s
- Model: large-v3
- Benchmark scope: partial audio
- Device: cuda
- Compute type: int8_float16
- Audio duration: 1200.000 seconds
- Runtime: 54.595 seconds
- Realtime factor: 0.045
- Peak RAM: 2421.200 MB
- Peak VRAM: not measured MB
- Transcript output size: 199 chars
- Status: failed
- Failure reason: CUDA failed with error out of memory

### large-v3_partial_300s_int8_float16
- Model: large-v3
- Benchmark scope: partial audio
- Device: cuda
- Compute type: int8_float16
- Audio duration: 300.000 seconds
- Runtime: 105.919 seconds
- Realtime factor: 0.353
- Peak RAM: 2484.340 MB
- Peak VRAM: not measured MB
- Transcript output size: 1667 chars
- Status: failed
- Failure reason: CUDA failed with error out of memory

### large-v3_partial_60s_int8
- Model: large-v3
- Benchmark scope: partial audio
- Device: cpu
- Compute type: int8
- Audio duration: 60.000 seconds
- Runtime: 98.142 seconds
- Realtime factor: 1.636
- Peak RAM: 4520.050 MB
- Peak VRAM: not measured MB
- Transcript output size: 386 chars
- Status: success
- Failure reason: not measured

## Segment audit
- Raw segment count: 2493
- Faster-whisper segment shape: True
- Appears to be word timestamps: False
- Duration mean / median / p10 / p90 / max: 1.272 / 1.200 / 0.660 / 1.940 / 30.000 seconds
- Text length mean / median / p10 / p90: 8.127 / 8.000 / 5.000 / 12.000 chars
- <0.5s: 76; <1s: 795; <3 CJK chars: 46; punctuation/blank only: 0
- Adjacent duplicate text: 2
- Overlap / same timestamp / non-monotonic: 0 / 0 / 0
- Assessment: over-fragmented for SRT/Markdown readability

## Merged transcript layer
- Deterministic readability layer only; not semantic topic segmentation, no LLM, no text correction.
- Raw count: 2493; merged count: 132
- Raw mean/median duration: 1.272 / 1.200 seconds
- Merged mean/median duration: 24.022 / 24.430 seconds
- Raw mean text length: 8.127; merged mean text length: 153.485
- Output sizes: raw md=109961 bytes, merged md=61867 bytes; raw srt=150277, merged srt=63787

## Quality assessment
- Current human review package exists in `reports/EP674_human_review.md`.
- Review windows prepared: 4
- Transcript has not completed human quality validation.
- Chinese comprehensibility, company-name quality, ticker quality, financial terminology quality, timestamp quality, hallucination, repetition, and large-v3 vs turbo quality difference require human listening review. This script does not fabricate those judgments.
- Completed raw transcript available: EP674 large-v3-turbo full episode.
- Large-v3 GPU runs on this 4 GB RTX 3050 failed with CUDA out of memory in tested partial configurations; only a 60-second CPU int8 smoke test completed.
- Existing peak_vram_mb values from completed runs are not reliable because the first script version used PyTorch CUDA counters, which do not capture CTranslate2 allocations. During the turbo full run, a spot check with nvidia-smi showed about 3454 MB used out of 4096 MB. The transcription script has been updated to poll nvidia-smi for future runs.
- Turbo runtime may be affected by other GPU workload and should not be treated as a clean performance baseline.

## Content-filtering feasibility
- Generated segment sample: yes
- Middle-third market-analysis judgment requires manual review of sampled transcript and source audio.
- No LLM classifier was created in this POC.
- Do not proceed to a 672/674-episode full batch from current artifacts alone.

## Cloud extrapolation
- Known corpus assumption: 672 episodes, 500-620 audio hours, 20-35 GB MP3.
- Local full-corpus estimate from best measured RTF: 71.033 to 88.080 wall-clock hours, assuming serial processing and same audio/model mix.
- Single AWS A10G or GCP L4 estimate is not locally measured. A reasonable planning range for faster-whisper large-v3/turbo is hours to a few days depending on model, batching, VAD, I/O, and CPU decode overhead; validate with a 20-episode cloud pilot before full processing.
- Strategy recommendation should be based on measured quality: use turbo if semantic/ticker quality is sufficient; use large-v3 on difficult episodes or audit samples if it materially improves errors.
- Cloud work disk suggestion: 100-200 GB for pilot/full batch staging, logs, and temporary decoded audio.
- Object storage suggestion: 100 GB minimum for MP3, transcripts, metadata, and rerun headroom.
- Before a 20-episode cloud pilot, complete basic manual review of the prepared windows and rerun clean GPU benchmarks when local GPU is idle.
- Pending local GPU commands are documented in `reports/pending_gpu_benchmarks.md`; they have not been executed.

## Go / No-Go decision
- RSS ingestion: Go
- MP3 acquisition: Go
- Local transcription: Go
- Transcript quality: Conditional until manual review is completed.
- Content classification feasibility: Conditional
- 20-episode cloud pilot: Proceed after manual review and clean benchmark
