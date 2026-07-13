# RunPod Batch Transcription

RunPod is intended only as a temporary single-GPU faster-whisper worker.
Local machines remain responsible for orchestration, normalization, semantic extraction through OpenRouter, validation, and reports.

Do not run Level 1-4 semantic extraction on the RunPod worker.

## Suggested GPU

Use a CUDA GPU with enough VRAM for the selected faster-whisper model. For `large-v3-turbo`, a 24 GB class GPU is a conservative pilot target. Smaller GPUs may work for some models/configs but must be validated with a one-episode smoke test.

## Data Boundary

Complete audio, raw transcripts, batch checkpoints, reports, and failures belong in the private repository or a RunPod mounted volume.

The public repository contains code, config examples, tests, and documentation only.

## Setup

1. Create a RunPod pod with a persistent volume if the pilot will process more than one episode.
2. Clone this public repository.
3. Provide private data by one of these modes:
   - Mode A: write outputs to a mounted RunPod volume and copy them back to the local private repo with `rsync` or `scp`.
   - Mode B: configure temporary private repo access yourself, such as an SSH deploy key or GitHub CLI login.
4. Do not write credentials into code, configs, checkpoints, logs, or command examples.
5. Install dependencies:

```bash
./setup_runpod.sh
```

6. Verify GPU and ffmpeg:

```bash
nvidia-smi
ffmpeg -version
```

## Environment

Set paths for the worker volume or mounted private data:

```bash
export PODCAST_DATA_ROOT=/path/to/private/shared_data
export PIPELINE_DATA_ROOT=/path/to/private/pipeline_data
```

Use local absolute paths only in your shell environment, not in tracked config.

## Dry Run

Dry-run planning does not download audio and does not initialize faster-whisper:

```bash
cd legacy_segment_poc
python3 main.py batch-plan --config config/transcription_batch.example.yaml --dry-run --max-episodes 1
```

## One-Episode Smoke Test

After manually confirming the config points to writable private or mounted-volume paths:

```bash
cd legacy_segment_poc
python3 main.py batch-run \
  --config config/transcription_batch.example.yaml \
  --episode EP674 \
  --device cuda \
  --compute-type float16
```

## 5-10 Episode Pilot

Edit a private copy of `config/transcription_batch.example.yaml` with the selected pilot episodes, then run:

```bash
cd legacy_segment_poc
python3 main.py batch-run --config /path/to/private/transcription_batch.yaml
```

Check status:

```bash
python3 main.py batch-status --config /path/to/private/transcription_batch.yaml
```

Resume or retry failed jobs:

```bash
python3 main.py batch-resume --config /path/to/private/transcription_batch.yaml
python3 main.py batch-run --config /path/to/private/transcription_batch.yaml --retry-failed
```

## Output Sync

Default sync is dry-run:

```bash
scripts/sync_transcription_outputs.sh \
  --source-root /runpod/volume \
  --dest-root /local/path/to/AI-Investment-Mentor-private \
  --batch-id <batch_id> \
  --dry-run
```

Execute copy:

```bash
scripts/sync_transcription_outputs.sh \
  --source-root /runpod/volume \
  --dest-root /local/path/to/AI-Investment-Mentor-private \
  --batch-id <batch_id> \
  --execute
```

The sync helper copies batch metadata and raw transcript directories into:

```text
pipeline_data/legacy_segment/runpod_smoke/<batch_id>/
```

It does not sync audio, does not overwrite newer local files, verifies JSON and transcript timestamps, and does not commit or push.

## Audio Policy

Default policy is `delete_on_success`. Audio is deleted only after:

1. transcription completes
2. output JSON is written
3. output validation passes
4. checkpoint is committed

Failed jobs keep audio for retry.

## Shutdown

After output sync and validation, stop or terminate the pod to avoid ongoing charges. This repository does not track RunPod hourly rates; cost fields remain `null` unless supplied by the user.
