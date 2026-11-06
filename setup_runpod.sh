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
: "${PIPELINE_DATA_ROOT:?Set PIPELINE_DATA_ROOT to an external worker-volume data path}"

check_storage() {
python3 - "$WORKER_VENV" "$HF_HOME" "$PIPELINE_DATA_ROOT" "${WORKER_HOST_ROOT:-}" "$1" "${WORKER_VOLUME_ROOT:-}" "${WORKER_VOLUME_CAPACITY_BYTES:-}" <<'PY'
import json
import sys
from transcription.environment import setup_storage_check
try:
    result = setup_storage_check(*sys.argv[1:4], host_root=sys.argv[4] or None,
                                 reserve_bytes=int(sys.argv[5]), shared_volume_root=sys.argv[6] or None,
                                 volume_capacity_bytes=int(sys.argv[7]) if sys.argv[7] else None)
except (ValueError, RuntimeError, OSError) as exc:
    raise SystemExit(f"Worker setup storage check failed: {exc}") from None
print(json.dumps(result))
PY
}

check_storage 10000000000

python3 -m venv "$WORKER_VENV"
"$WORKER_VENV/bin/python" -m pip install --no-cache-dir -r requirements-runpod.txt
check_storage 0
"$WORKER_VENV/bin/python" -m pip check
bash scripts/transcription_worker.sh doctor

echo "RunPod transcription dependencies installed."
echo "Next: configure private data paths, batch-plan, cache-model, then one-episode smoke."
