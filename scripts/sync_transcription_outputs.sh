#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=1
SOURCE=""
DEST=""

usage() {
  cat <<'USAGE'
Usage:
  scripts/sync_transcription_outputs.sh --source <batch-dir> --dest <private-repo-root> [--execute]

Default is dry-run. The script copies batch outputs into the private repository
without committing or pushing. It verifies JSON readability after sync.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --execute) DRY_RUN=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$SOURCE" || -z "$DEST" ]]; then
  usage >&2
  exit 2
fi

if [[ ! -d "$SOURCE" ]]; then
  echo "Source batch directory not found: $SOURCE" >&2
  exit 3
fi

if [[ ! -d "$DEST" ]]; then
  echo "Destination private repo root not found: $DEST" >&2
  exit 4
fi

TARGET="$DEST/pipeline_data/legacy_segment/batches/$(basename "$SOURCE")"
echo "Source: $SOURCE"
echo "Target: $TARGET"

if [[ "$DRY_RUN" == "1" ]]; then
  rsync -avnc --checksum "$SOURCE/" "$TARGET/"
  echo "Dry-run complete. Re-run with --execute to copy."
  exit 0
fi

mkdir -p "$TARGET"
rsync -avc --checksum "$SOURCE/" "$TARGET/"

python3 - <<PY
import json
from pathlib import Path
target = Path("$TARGET")
for path in target.rglob("*.json"):
    json.loads(path.read_text(encoding="utf-8"))
print("JSON validation passed for", target)
PY

echo "Sync complete. No Git commit or push was performed."
