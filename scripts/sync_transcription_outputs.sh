#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=1
SOURCE=""
DEST=""
SOURCE_ROOT=""
DEST_ROOT=""
BATCH_ID=""

usage() {
  cat <<'USAGE'
Usage:
  Preferred smoke-sync mode:
    scripts/sync_transcription_outputs.sh \
      --source-root <runpod-private-root> \
      --dest-root <local-private-repo-root> \
      --batch-id <batch-id> [--execute]

  Legacy batch-dir mode:
    scripts/sync_transcription_outputs.sh --source <batch-dir> --dest <private-repo-root> [--execute]

Default is dry-run. Smoke-sync mode copies:
  - pipeline_data/legacy_segment/batches/<batch-id>/
  - shared_data/transcripts/raw/<episode-id>/

into:
  pipeline_data/legacy_segment/runpod_smoke/<batch-id>/

It does not sync audio, commit Git changes, or push.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --source-root) SOURCE_ROOT="$2"; shift 2 ;;
    --dest-root) DEST_ROOT="$2"; shift 2 ;;
    --batch-id) BATCH_ID="$2"; shift 2 ;;
    --execute) DRY_RUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -n "$SOURCE_ROOT" || -n "$DEST_ROOT" || -n "$BATCH_ID" ]]; then
  if [[ -z "$SOURCE_ROOT" || -z "$DEST_ROOT" || -z "$BATCH_ID" ]]; then
    usage >&2
    exit 2
  fi
  if [[ ! -d "$SOURCE_ROOT" ]]; then
    echo "Source root not found: $SOURCE_ROOT" >&2
    exit 3
  fi
  if [[ ! -d "$DEST_ROOT" ]]; then
    echo "Destination private repo root not found: $DEST_ROOT" >&2
    exit 4
  fi

  SOURCE_BATCH="$SOURCE_ROOT/pipeline_data/legacy_segment/batches/$BATCH_ID"
  SOURCE_TRANSCRIPTS="$SOURCE_ROOT/shared_data/transcripts/raw"
  TARGET_ROOT="$DEST_ROOT/pipeline_data/legacy_segment/runpod_smoke/$BATCH_ID"
  TARGET_BATCH="$TARGET_ROOT/batch"
  TARGET_TRANSCRIPTS="$TARGET_ROOT/transcripts/raw"

  if [[ ! -d "$SOURCE_BATCH" ]]; then
    echo "Source batch directory not found: $SOURCE_BATCH" >&2
    exit 5
  fi

  mapfile -t EPISODES < <(python3 - "$SOURCE_BATCH/checkpoint.json" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit("checkpoint.json not found")
payload = json.loads(path.read_text(encoding="utf-8"))
for episode_id in sorted(payload.get("jobs", {})):
    print(episode_id)
PY
)

  echo "Source root: $SOURCE_ROOT"
  echo "Target root: $TARGET_ROOT"
  echo "Batch ID: $BATCH_ID"
  echo "Episodes: ${EPISODES[*]:-(none)}"
  echo "Audio sync: disabled"

  RSYNC_FLAGS="-avc --checksum --update"
  if [[ "$DRY_RUN" == "1" ]]; then
    RSYNC_FLAGS="-avnc --checksum --update"
  else
    mkdir -p "$TARGET_BATCH" "$TARGET_TRANSCRIPTS"
  fi

  rsync $RSYNC_FLAGS "$SOURCE_BATCH/" "$TARGET_BATCH/"
  for EPISODE_ID in "${EPISODES[@]}"; do
    SRC_EP="$SOURCE_TRANSCRIPTS/$EPISODE_ID"
    if [[ ! -d "$SRC_EP" ]]; then
      echo "Missing transcript directory for $EPISODE_ID: $SRC_EP" >&2
      exit 6
    fi
    rsync $RSYNC_FLAGS "$SRC_EP/" "$TARGET_TRANSCRIPTS/$EPISODE_ID/"
  done

  VALIDATE_ROOT="$SOURCE_ROOT"
  [[ "$DRY_RUN" == "0" ]] && VALIDATE_ROOT="$TARGET_ROOT"
  python3 - "$DRY_RUN" "$SOURCE_BATCH" "$SOURCE_TRANSCRIPTS" "$TARGET_ROOT" <<'PY'
import json
import sys
from pathlib import Path

dry_run = sys.argv[1] == "1"
source_batch = Path(sys.argv[2])
source_transcripts = Path(sys.argv[3])
target_root = Path(sys.argv[4])
base_batch = source_batch if dry_run else target_root / "batch"
base_transcripts = source_transcripts if dry_run else target_root / "transcripts" / "raw"

checkpoint = json.loads((base_batch / "checkpoint.json").read_text(encoding="utf-8"))
errors = []
for episode_id in sorted(checkpoint.get("jobs", {})):
    transcript = base_transcripts / episode_id / "raw_transcript.json"
    if not transcript.exists():
        errors.append(f"{episode_id}: raw_transcript.json missing")
        continue
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    meta_episode = payload.get("metadata", {}).get("episode_id")
    source_episode = payload.get("metadata", {}).get("source_episode_metadata", {}).get("episode_id")
    if meta_episode and meta_episode != episode_id:
        errors.append(f"{episode_id}: metadata episode_id mismatch: {meta_episode}")
    if source_episode and source_episode != episode_id:
        errors.append(f"{episode_id}: source episode_id mismatch: {source_episode}")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append(f"{episode_id}: no transcript segments")
        continue
    seen = set()
    prev_end = -1.0
    for segment in segments:
        sid = segment.get("segment_id")
        if sid in seen:
            errors.append(f"{episode_id}: duplicate segment_id {sid}")
        seen.add(sid)
        start = float(segment["start"])
        end = float(segment["end"])
        if start < 0 or end < start:
            errors.append(f"{episode_id}: invalid timestamp in segment {sid}")
        if start < prev_end - 5:
            errors.append(f"{episode_id}: timestamps appear unsorted near segment {sid}")
        prev_end = max(prev_end, end)
if errors:
    raise SystemExit("\\n".join(errors))
print("Transcript and batch JSON validation passed")
PY

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "Dry-run complete. Re-run with --execute to copy."
  else
    echo "Sync complete. No Git commit or push was performed."
  fi
  exit 0
fi

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
  rsync -avnc --checksum --update "$SOURCE/" "$TARGET/"
  echo "Dry-run complete. Re-run with --execute to copy."
  exit 0
fi

mkdir -p "$TARGET"
rsync -avc --checksum --update "$SOURCE/" "$TARGET/"

python3 - <<PY
import json
from pathlib import Path
target = Path("$TARGET")
for path in target.rglob("*.json"):
    json.loads(path.read_text(encoding="utf-8"))
print("JSON validation passed for", target)
PY

echo "Sync complete. No Git commit or push was performed."
