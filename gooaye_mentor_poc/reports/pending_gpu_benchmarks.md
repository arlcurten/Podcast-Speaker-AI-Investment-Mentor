# Pending GPU Benchmarks

Do not run these until the GPU is idle and the user explicitly approves.

## Latest Readiness Attempt

Status: blocked on non-idle GPU utilization.

A fixed benchmark clip has been prepared:

```text
data/benchmarks/EP674/benchmark_clip.mp3
```

Clip details:

- Start/end: 1200.00s / 2100.00s
- ffprobe duration: 900.022857s
- SHA-256: 46311b2f8199b803e46f9e2cb296cae336c0b28c3ad73f76f7b6102488372ace

The GPU process list was empty, but utilization remained around 25-32% after repeated sampling, with one 56% sample. To keep the result clean, no benchmark was started.

See `reports/clean_gpu_benchmark.md`.

## Pre-check

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
nvidia-smi
python3 scripts/inspect_environment.py
```

Confirm there is no game or unrelated heavy GPU process. The transcription script records total-device VRAM with `nvidia-smi`; if other processes are present, VRAM is not process-specific.

## Benchmark A: large-v3-turbo clean rerun

Use the same fixed 15-minute EP674 clip to avoid a long full-episode rerun:

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 scripts/run_benchmark_clip.py \
  --clip data/benchmarks/EP674/benchmark_clip.mp3 \
  --episode EP674 \
  --model large-v3-turbo \
  --output-dir data/benchmarks/EP674/large-v3-turbo-clean \
  --device cuda \
  --compute-type float16 \
  --audio-duration 900.022857 \
  --clip-start 1200.00 \
  --clip-end 2100.00
```

Expected output directory:

```text
data/benchmarks/EP674/large-v3-turbo-clean/
```

Record:

- runtime
- RTF
- peak RAM
- baseline VRAM
- peak total-device VRAM
- whether other GPU processes were present

## Benchmark B: large-v3 short-clip comparison

Use the exact same 15-minute clip. Start with the least risky 4 GB setting:

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 scripts/run_benchmark_clip.py \
  --clip data/benchmarks/EP674/benchmark_clip.mp3 \
  --episode EP674 \
  --model large-v3 \
  --output-dir data/benchmarks/EP674/large-v3-clean \
  --device cuda \
  --compute-type int8_float16 \
  --audio-duration 900.022857 \
  --clip-start 1200.00 \
  --clip-end 2100.00
```

If this still OOMs, do not keep trying aggressive GPU settings. Save the failure metadata and run a CPU smoke test only if needed:

```bash
python3 scripts/transcribe_episode.py \
  --episode EP674 \
  --config large-v3 \
  --max-seconds 120 \
  --device cpu \
  --compute-type int8
```

After either benchmark, regenerate summaries:

```bash
python3 scripts/build_clean_gpu_benchmark_report.py
```
