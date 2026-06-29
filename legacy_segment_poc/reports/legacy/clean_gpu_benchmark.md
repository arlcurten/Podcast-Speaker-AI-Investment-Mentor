# Clean GPU Benchmark

Status: completed

## Environment

- GPU: NVIDIA GeForce RTX 3050 Laptop GPU
- Driver: 581.95
- CUDA: 13.0
- Total VRAM: 4096 MiB
- Python: 3.12.13
- faster-whisper: 1.2.1
- CTranslate2: 4.8.0
- Measurement scope: total-device VRAM from `nvidia-smi`; Windows/WSL attribution may be incomplete.

## Baseline

- Classification: controlled desktop baseline
- VRAM min/max/median: 794.000 / 817.000 / 794.500 MiB
- Utilization min/max/median: 26.000 / 31.000 / 29.000 %
- Power min/max/median: 6.700 / 7.840 / 7.760 W
- Temperature min/max/median: 56.000 / 57.000 / 57.000 C
- P-states observed: P5, P8
- Visible processes: none reported by `nvidia-smi` / `pmon`

## Fixed Benchmark Clip

- Source: `data/audio/EP674/episode.mp3`
- Clip: `data/legacy/benchmarks/EP674/benchmark_clip.mp3`
- Requested start/end: 1200.000s / 2100.000s
- ffprobe duration: 900.022857s
- SHA-256: `46311b2f8199b803e46f9e2cb296cae336c0b28c3ad73f76f7b6102488372ace`

## large-v3-turbo Clean Runs

### run-01
- Status: success
- Runtime: 88.793s
- RTF: 0.099
- Model load: 4.149s
- Baseline / peak VRAM: 794.000 / 3280.000 MiB
- Segments / chars: 611 / 5889
- Transcript hash: `a35a3bfa1c341d6a382554ec2a1405cc029e8b624cfc5de880867ba8385c1607`

### run-02
- Status: success
- Runtime: 96.810s
- RTF: 0.108
- Model load: 4.139s
- Baseline / peak VRAM: 750.000 / 3300.000 MiB
- Segments / chars: 610 / 5713
- Transcript hash: `a0334749955c809feb7949bbb1f988298ec6726b53e48e2a161453d2b9592d08`

### run-03
- Status: success
- Runtime: 93.708s
- RTF: 0.104
- Model load: 3.896s
- Baseline / peak VRAM: 780.000 / 3334.000 MiB
- Segments / chars: 610 / 5713
- Transcript hash: `a0334749955c809feb7949bbb1f988298ec6726b53e48e2a161453d2b9592d08`

## Stability

- Mean runtime: 93.104s
- Median runtime: 93.708s
- Stddev runtime: 4.043s
- Runtime variation: 9.029%
- Classification: usable with minor desktop interference
- Transcript hashes identical: no

## large-v3 Same Clip
- Status: success
- Failure stage: not measured
- Failure reason: not measured
- Runtime: 242.694s
- RTF: 0.270
- Baseline / peak VRAM: 823.000 / 3571.000 MiB
- Segments / chars: 537 / 5941
- Transcript hash: `40256b7f12c568be2aacd2d8e9e3dd82de0b0234c319e4b31ceb25d9ac810572`

## Quality

No human transcript quality judgment is included in this benchmark. Transcript quality remains pending manual review.

## Limitations

- This is a controlled desktop baseline, not a perfect zero-background lab run.
- Windows desktop / WSL GPU utilization attribution may be incomplete.
- Transcript quality is not judged by this benchmark.
