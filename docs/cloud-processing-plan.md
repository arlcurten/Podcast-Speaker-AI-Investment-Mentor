# Cloud Processing Plan

This is a planning note, not an executed cloud benchmark.

## Proposed Progression

```text
Local RTX 3050
    ↓
Local POC + manual review
    ↓
20-episode RunPod Pilot
    ↓
RunPod or Vast.ai full processing
    ↓
AWS/GCP only as fallback or formal verification
```

## Provider Notes

- RunPod: preferred first pilot target because it is accessible and GPU-focused.
- Vast.ai: useful for cost optimization, but instance stability and reproducibility may vary more.
- AWS/GCP: stable and operationally mature, but likely more expensive for this exploratory workload.
- TPU is not recommended because faster-whisper/CTranslate2 primarily targets CUDA for this workflow.

## Recommended GPU Class

Use CUDA GPUs with about 24 GB VRAM for safer `large-v3` work:

- NVIDIA L4 24 GB
- A10G 24 GB
- RTX 3090 24 GB
- RTX 4090 24 GB
- RTX A5000 24 GB

## Job Shape

- One episode should be one independent job.
- Each completed episode should checkpoint immediately.
- Do not rely on a long uninterrupted instance lifecycle.
- Store transcript and metadata in persistent storage, not only ephemeral instance disk.
- Keep audio SHA-256, run metadata, package versions, and logs per episode.

## Storage

Planning estimate:

- Worker disk: 50-100 GB minimum for pilot jobs, temporary audio, and logs.
- Larger staging or full-corpus runs may need 100-200 GB depending on audio retention and intermediate files.
- Persist outputs to object storage or another durable location.

## Pilot Gate

Do not launch a 20-episode pilot only because the pipeline runs. First complete basic manual transcript review for EP674 or a small local sample.

Pilot should answer:

- Is `large-v3-turbo` quality good enough for downstream extraction?
- Does `large-v3` materially reduce important financial/entity errors?
- What is clean throughput on a cloud GPU?
- What failure modes appear across more than one episode?
- Are checkpoint and retry mechanics sufficient?

All cost and time estimates before the pilot should be labeled as planning estimates, not measured results.
