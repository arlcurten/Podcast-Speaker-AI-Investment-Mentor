#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${WORKER_VENV:+$WORKER_VENV/bin/}python3"
LIBS="$("$PYTHON" - <<'PY'
from pathlib import Path
import nvidia.cublas
import nvidia.cudnn
print(':'.join(str(Path(module.__path__[0]) / 'lib') for module in (nvidia.cublas, nvidia.cudnn)))
PY
)"
export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$PYTHON" -m transcription "$@"
