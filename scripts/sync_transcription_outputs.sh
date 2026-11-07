#!/usr/bin/env bash
# Source is a downloaded formal bundle, not a legacy batch directory.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PYTHON:-python3}" -m transcription batch-import "$@"
