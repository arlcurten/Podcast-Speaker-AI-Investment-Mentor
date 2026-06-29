# GPU Benchmarks

Do not run more GPU work unless the user explicitly approves.

## Completed Clean Benchmark

Status: completed under a controlled desktop baseline.

A fixed benchmark clip has been prepared:

```text
data/legacy/benchmarks/EP674/benchmark_clip.mp3
```

Clip details:

- Start/end: 1200.00s / 2100.00s
- ffprobe duration: 900.022857s
- SHA-256: 46311b2f8199b803e46f9e2cb296cae336c0b28c3ad73f76f7b6102488372ace

Baseline:

- VRAM min/max/median: 794 / 817 / 794.5 MiB
- Utilization min/max/median: 26 / 31 / 29%
- Power min/max/median: 6.70 / 7.84 / 7.76 W
- Visible GPU processes: none

Results:

- Turbo clean run-01: 88.793s, RTF 0.099
- Turbo clean run-02: 96.810s, RTF 0.108
- Turbo clean run-03: 93.708s, RTF 0.104
- Runtime variation: 9.029%
- Stability: usable with minor desktop interference
- large-v3 same-clip: success, 242.694s, RTF 0.270, peak total-device VRAM 3571 MiB

See `reports/legacy/clean_gpu_benchmark.md`.

Manual transcript quality remains pending. These are performance/feasibility results only.

## Future Pre-check

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
nvidia-smi
python3 main.py inspect-env
```

Confirm there is no game or unrelated heavy GPU process. The transcription script records total-device VRAM with `nvidia-smi`; if other processes are present, VRAM is not process-specific.

## Historical Benchmark Command: large-v3-turbo clean rerun

Use the same fixed 15-minute EP674 clip to avoid a long full-episode rerun:

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 main.py benchmark-clip \
  --clip data/legacy/benchmarks/EP674/benchmark_clip.mp3 \
  --episode EP674 \
  --model large-v3-turbo \
  --output-dir data/legacy/benchmarks/EP674/large-v3-turbo-clean \
  --device cuda \
  --compute-type float16 \
  --audio-duration 900.022857 \
  --clip-start 1200.00 \
  --clip-end 2100.00
```

Expected output directory:

```text
data/legacy/benchmarks/EP674/large-v3-turbo-clean/
```

Record:

- runtime
- RTF
- peak RAM
- baseline VRAM
- peak total-device VRAM
- whether other GPU processes were present

## Historical Benchmark Command: large-v3 short-clip comparison

The local same-clip `large-v3` comparison completed successfully once. Do not keep re-running without a specific reason.

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 main.py benchmark-clip \
  --clip data/legacy/benchmarks/EP674/benchmark_clip.mp3 \
  --episode EP674 \
  --model large-v3 \
  --output-dir data/legacy/benchmarks/EP674/large-v3-clean \
  --device cuda \
  --compute-type int8_float16 \
  --audio-duration 900.022857 \
  --clip-start 1200.00 \
  --clip-end 2100.00
```

Regenerate summaries:

```bash
python3 main.py benchmark-report
```
