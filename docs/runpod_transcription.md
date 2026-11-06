# Temporary GPU Transcription Worker

RunPod executes only audio acquisition and faster-whisper. Local code owns
quality acceptance, normalization, knowledge extraction and Mentor serving.
The private workspace remains the source of truth. No API keys are needed on
the worker. This document does not provision a Pod or authorize a corpus run.

## Environment

Start with one 24 GB CUDA GPU (L4/A5000/3090/4090 class), not a multi-GPU node.
This is capacity guidance, not a measured speed/cost ranking. Choose using
availability and the displayed hourly price; record that price privately.
Use Linux x86_64, Python 3.12 and a CUDA-12-capable NVIDIA driver. The worker
pins the resolved Python dependency set, including faster-whisper/CTranslate2,
and installs cuBLAS 12/cuDNN 9 Python wheels.
See [upstream runtime requirements](https://github.com/SYSTRAN/faster-whisper#gpu).
System ffmpeg/ffprobe remain required by our audio-inspection wrapper.

Use a volume with at least 35 GB initially free, preferably 60 GB or more;
models/environments and the 25 GB safety reserve need room even though audio
is sequential. Put outputs on persistent storage, not the container layer.
On Linux the shared budget uses a stable filesystem root, so creating cache or
output subdirectories does not reset or invalidate it. On WSL the actual Windows
backing-drive mount must still be configured explicitly.
Stopping/deleting has different consequences for container, volume and network
storage; consult [RunPod storage lifetimes](https://docs.runpod.io/pods/storage/types).

Clone the public repository at the reviewed rebuild commit. If local commits
are not yet on the remote, transfer a clean Git bundle rather than accidentally
running the archived main-branch worker. Never copy `.env` or a home directory.
No private Git credentials are required: transfer only the selected private
episode metadata and batch config using SSH/scp/rsync.

Set these to directories on the worker volume, outside the source checkout:

```bash
export WORKER_ROOT="$(realpath "$VOLUME_MOUNT")/transcription-worker"
export WORKER_VENV="$WORKER_ROOT/venv"
export HF_HOME="$WORKER_ROOT/model-cache"
export PODCAST_DATA_ROOT="$WORKER_ROOT/shared_data"
export PIPELINE_DATA_ROOT="$WORKER_ROOT/pipeline_data"
bash setup_runpod.sh
bash scripts/transcription_worker.sh doctor
```

`VOLUME_MOUNT` is your actual mounted volume; do not use a guessed location.
Install ffmpeg and Python venv support with the image's package manager first
if setup reports they are missing. Setup does not change drivers, download a
model, fetch podcast audio, or call any LLM. CUDA library paths are configured
by `scripts/transcription_worker.sh` before Python starts.

Setup shares `PIPELINE_DATA_ROOT/storage_budget.json` with the worker; it checks
all installation/cache/output filesystems before installation and again after.
It reserves 10 GB of installation headroom in addition to the 25 GB free-space
minimum and never resets an existing growth budget. This is admission control,
not a hard filesystem quota on pip or other processes. When validating setup on
WSL, explicitly set `WORKER_HOST_ROOT` to the confirmed Windows backing-drive
mount, matching private batch `storage.host_root`. Linux Pods normally leave it
unset. A nonempty `WORKER_VENV` that is not a virtual environment is rejected.

The isolated Python 3.12 installation, dependency imports, CUDA libraries and
compute-type diagnostics have passed locally without loading Whisper. That
does not prove a clean Pod image works or measure cloud transcription speed.

## Single-Episode Smoke

Copy `transcription/config/runpod.example.yaml` to a private `$BATCH_CONFIG`.
Transfer the existing private catalog to the configured manifest path. Use one
regression episode, a new batch ID and its matching new transcript directory.
The default is turbo/float16 with a pinned model revision; full large-v3 is a
separate quality experiment, not a proven accuracy upgrade. Changing settings
requires a new batch/output version. Keep `local_files_only: true` during runs.

```bash
bash scripts/transcription_worker.sh batch-plan --config "$BATCH_CONFIG" --dry-run
bash scripts/transcription_worker.sh cache-model --config "$BATCH_CONFIG"
bash scripts/transcription_worker.sh cache-model --config "$BATCH_CONFIG" --execute
bash scripts/transcription_worker.sh batch-run --config "$BATCH_CONFIG" --max-episodes 1
bash scripts/transcription_worker.sh batch-validate --config "$BATCH_CONFIG"
bash scripts/transcription_worker.sh batch-resume --config "$BATCH_CONFIG" --max-episodes 1
```

Inspect exact episode identity, then validate output, completed checkpoint and
`skipped_completed_count: 1` on resume. No second episode should run. Planning
and model-cache dry-run do not download or initialize models. Model caching is
explicit with a 6 GB admission reserve; the upstream downloader is not an OS
quota. Record GPU, package/model revision, timings and peak device memory from
raw metadata. Billing estimates remain null without a supplied hourly rate.

## Verified Transfer

After the batch stops, export all completed transcripts and allowlisted batch
metadata. Failed jobs' checkpoint/failure records are included, but partial
transcripts, audio, credentials, caches and arbitrary extra files are not.

```bash
python3 -m transcription batch-export --config "$BATCH_CONFIG" --output "$BUNDLE"
python3 -m transcription batch-export --config "$BATCH_CONFIG" --output "$BUNDLE" --execute
```

Copy only this bundle using SSH with host-key checking (no token in the command):

```bash
rsync -av --partial -e "ssh -p $POD_SSH_PORT" "$POD_SSH_TARGET:$BUNDLE/" "$LOCAL_STAGING/"
bash scripts/sync_transcription_outputs.sh --source "$LOCAL_STAGING" --dest-root "$LOCAL_PRIVATE_ROOT" --storage-config "$LOCAL_STORAGE_CONFIG"
bash scripts/sync_transcription_outputs.sh --source "$LOCAL_STAGING" --dest-root "$LOCAL_PRIVATE_ROOT" --storage-config "$LOCAL_STORAGE_CONFIG" --execute
```

The local storage config is a private batch YAML with the existing shared
`storage.budget_file`; on WSL include the verified Windows backing-drive mount.
The helper defaults to dry-run. It verifies SHA-256/size, JSON, episode identity,
segments/timestamps, source metadata, config consistency and completion stages.
It publishes atomically at:

```text
pipeline_data/transcription/imports/<batch_id>/
    bundle_manifest.json
    batch/{batch_manifest,checkpoint,summary,failures,validation_summary}.json
    transcripts/<episode>/{raw_transcript,raw_run_metadata}.json
```

`validation_summary.json` is optional. Identical imports are revalidated and
reused; any different existing bundle is rejected regardless of modification
time. Canonical older transcripts are never overwritten. Source worker paths
inside checkpoint snapshots are audit history, not local executable paths.
Resume processing on the worker before exporting a new snapshot under a new
bundle version. Importing is not content-quality acceptance.

The importer performs no Git operation. Record useful exports in the existing
private artifact manifest. Keep test imports noncanonical. Normalization can
read the imported raw file directly after source-bound review; no copy back to
the public repository or old canonical location is needed.

## Pilot and Shutdown

Only after a successful single-episode smoke and content review, privately
select 5-10 episodes from verified metadata. Use a new batch ID, one job at a
time and `delete_on_success` for batch-owned downloaded audio. Failed jobs keep
audio; `--retry-failed` is explicit. Completed jobs are never retranscribed on
resume. Old user-provided audio is never automatically deleted.

Before shutdown: export, transfer, import, verify local checksums, then confirm
no output exists only on ephemeral storage. Only then delete temporary worker
audio or terminate the Pod through its console. Volume/network storage can
continue billing after compute stops. Check the console; the scripts cannot
confirm shutdown or billing. Never terminate a Pod before securing outputs.

Local deterministic tests and a local GPU run do not certify a clean RunPod
image. Rental, clean-image setup and GPU smoke remain explicit user-run checks.
