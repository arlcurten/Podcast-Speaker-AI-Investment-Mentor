#!/usr/bin/env bash
set -euo pipefail

python3 --version

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required. Install it with apt before running transcription." >&2
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo "nvidia-smi not found. CUDA transcription will fail until GPU drivers are visible." >&2
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-runpod.txt

echo "RunPod transcription dependencies installed."
echo "Set PODCAST_DATA_ROOT and PIPELINE_DATA_ROOT before running batch commands."
