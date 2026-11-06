#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 for the pinned worker environment"'

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required. Install it with apt before running transcription." >&2
  exit 1
fi

command -v ffprobe >/dev/null || { echo "ffprobe is required" >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo "GPU driver not visible; select a GPU Pod" >&2; exit 1; }
nvidia-smi
: "${WORKER_VENV:?Set WORKER_VENV to a worker-volume virtual environment path}"
: "${HF_HOME:?Set HF_HOME to an external worker-volume model cache path}"
python3 - "$WORKER_VENV" "$HF_HOME" <<'PY'
import shutil
import sys
from pathlib import Path
from transcription.common import private_path
from transcription.storage import existing_parent
for value in sys.argv[1:]:
    path = private_path(value)
    if shutil.disk_usage(existing_parent(path)).free < 35_000_000_000:
        raise SystemExit("Need 35 GB free before environment/model-cache preparation")
PY

python3 -m venv "$WORKER_VENV"
"$WORKER_VENV/bin/python" -m pip install --no-cache-dir -r requirements-runpod.txt
"$WORKER_VENV/bin/python" -m pip check
bash scripts/transcription_worker.sh doctor

echo "RunPod transcription dependencies installed."
echo "Next: configure private data paths, batch-plan, cache-model, then one-episode smoke."
