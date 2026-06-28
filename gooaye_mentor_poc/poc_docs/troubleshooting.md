# Troubleshooting

Use this as the first stop for known POC failure modes.

## RSS URL Returns 404

Symptom: the configured SoundOn RSS URL returns HTTP 404.

Possible causes:

- feed migration
- SoundOn UUID changed
- hosting provider changed
- old feed was removed

How to diagnose:

- Check HTTP status and error body.
- Confirm whether the Apple Podcasts collection still exists.
- Verify the discovered feed channel title.

Recommended handling:

1. Do not immediately conclude the podcast no longer exists.
2. Use Apple Podcasts collection ID `1500839292` or the existing lookup fallback to find the current feed.
3. Verify channel title is `Gooaye 股癌`.
4. Save requested URL, resolved URL, discovery method, fetch timestamp, and channel title.
5. Update configuration when appropriate, but keep old feed provenance.

## RSS Enclosure Length Is 1 Or Unreasonable

Symptom: RSS says the MP3 length is `1` or another impossible size.

Possible causes:

- hosting provider emitted a placeholder
- RSS metadata is stale or malformed
- feed generator does not publish reliable enclosure length

How to diagnose:

- Compare RSS enclosure length with HTTP Content-Length.
- Compare HTTP Content-Length with actual file size.
- Run ffprobe.

Recommended handling:

- Set `rss_length_valid = false`.
- Use HTTP Content-Length and actual file size for validation.
- Use ffprobe to validate media parse/decode.
- Do not fail the download solely because RSS length is wrong.

## MP3 Download Incomplete

Symptom: the final MP3 is missing, remains as `.part`, or does not match the expected size.

Possible causes:

- `.part` file remains.
- actual file size differs from HTTP Content-Length.
- ffprobe fails.
- SHA-256 changes unexpectedly.

How to diagnose:

- Check HTTP status.
- Check `Content-Length`.
- Compare actual file size.
- Inspect retry logs.
- Check whether Range/resume is supported if implementing resume.
- Run ffprobe.

Recommended handling:

- Keep partial files separate from final `episode.mp3`.
- Resume only if supported and implemented safely.
- Otherwise restart the download.
- Do not rename to final path until validation succeeds.

## ffprobe Fails

Symptom: ffprobe cannot parse or decode the downloaded audio.

Possible causes:

- downloaded HTML or JSON error page instead of MP3
- incomplete file
- wrong Content-Type
- damaged MP3
- ffprobe not installed or not in PATH

How to diagnose:

1. Inspect the first bytes or MIME type.
2. Check HTTP status and response headers.
3. Compare file size with HTTP Content-Length.
4. Confirm ffprobe is installed.

Recommended handling:

- Re-download if the file is incomplete or not audio.
- Fix PATH or install ffmpeg/ffprobe if the tool is missing.

## CUDA OOM

Symptom: faster-whisper/CTranslate2 fails with CUDA out-of-memory.

Known local constraint:

- RTX 3050 Laptop GPU has 4 GB VRAM.
- `large-v3-turbo` completed EP674.
- `large-v3` CUDA partial tests failed with out-of-memory.

How to diagnose:

- Check `nvidia-smi`.
- Check model, compute type, and clip length.
- Check whether another process already uses VRAM.

Recommended handling:

- Run `nvidia-smi` before any GPU work.
- Close games or other GPU processes.
- Use short clips for comparison.
- Prefer `large-v3-turbo` locally.
- Try lower-risk compute settings only with user approval.
- Move `large-v3` work to a 24 GB cloud GPU if quality comparison is needed.
- Do not repeatedly run high-risk OOM settings.

## GPU Benchmark Under Contention

Symptom:

- another process is using GPU memory or compute
- runtime varies widely
- total VRAM cannot be attributed to one process

Possible causes:

- game or desktop workload is active
- another ML process is running
- total-device VRAM includes unrelated allocations

Recommended handling:

- Mark the benchmark as not clean.
- Do not use it for cloud throughput extrapolation.
- Keep spot checks as informal context only.
- Re-run a fixed clip after GPU is idle.
- Record baseline VRAM and whether memory is total-device or process-specific.

## Peak VRAM Unreliable

Symptom: benchmark metadata has `peak_vram_mb` missing or marked unreliable.

Possible causes:

- original measurement used counters that did not fully capture CTranslate2 allocations
- `nvidia-smi` polling was not active
- another process shared the GPU

Recommended handling:

- Mark old values as unknown or unreliable.
- Preserve the measurement method and limitations.
- For future runs, poll `nvidia-smi`.
- Record sampling interval.
- State that total-device memory may include other processes.

## Transcript Has Too Many Tiny Segments

Symptom: SRT/Markdown transcript is hard to read because segments are very short.

Possible causes:

- word-level timestamps were mistaken for segments
- VAD or export settings produced very small segments
- faster-whisper raw segment granularity is naturally fine for this audio

How to diagnose:

- Check segment keys and shape.
- Compute duration distribution.
- Compute text length distribution.
- Check overlaps and non-monotonic timestamps.
- Check repeated adjacent text.

Recommended handling:

- Preserve raw segments.
- Create deterministic merged utterances as a derived layer.
- Do not overwrite raw output.
- Do not use an LLM to rewrite the merged layer.
- Keep future topic segmentation as a separate layer.

## Transcript Looks Fluent But May Be Wrong

Symptom: text reads plausibly but may not match the audio.

Risk areas:

- ticker
- company name
- number
- negation
- bullish/bearish meaning
- financial term

Recommended handling:

- Review four sampled window types: opening general, middle analysis, English/ticker-heavy section, and later QA.
- Record one row per issue in the manual review CSV.
- Use severity: minor, moderate, critical.
- Use semantic impact: none, low, changes_entity, changes_number, changes_sentiment, changes_reasoning, unknown.
- Do not rely only on WER.
- Until review is done, keep transcript quality as Conditional/Pending.

## CPU Transcription Is Too Slow

Symptom: CPU transcription cannot keep up with practical processing needs.

Observed:

- CPU int8 `large-v3` 60-second smoke test had RTF about `1.636`.

Recommended handling:

- Use CPU only for smoke tests.
- Do not run full local batches on CPU.
- Use local GPU with `large-v3-turbo` where safe.
- Use RunPod/Vast.ai or other 24 GB GPU instances for larger `large-v3` experiments.

## Background Game Using GPU

Symptom: `nvidia-smi` shows nontrivial GPU memory or utilization from unrelated work.

Possible causes:

- game is running
- browser or desktop acceleration is active
- another compute workload is active

How to diagnose:

- Run `nvidia-smi`.
- Look for utilization, memory use, and process list.

Recommended handling:

- Pause all new GPU transcription and benchmarks.
- Limit work to CPU-safe documentation, JSON/CSV/report inspection, py_compile, and static review.
- Resume GPU work only after the user explicitly confirms the GPU is idle.

## Repository Moved And Metadata Contains Stale Absolute Paths

Symptom: metadata or reports reference `/home/g9161/projects/gooaye_mentor_poc`, but the Local POC now lives at `/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc`.

Possible causes:

- earlier session ran from a different directory
- repository was moved under the full project root
- generated reports embedded absolute paths
- metadata stored an absolute runtime path as the only locator

How to diagnose:

```bash
rg -uu -n '/home/g9161/projects/gooaye_mentor_poc' .
```

Classify hits as generated metadata, generated report, documentation, script hardcoding, or historical provenance.

Recommended handling:

- Keep old absolute paths only as `original_*` historical provenance fields.
- Add project-relative locators such as `project_relative_path`.
- If an absolute runtime path is useful, store it separately as `runtime_absolute_path`.
- Update user-facing commands to run from the Local POC root using relative paths.
- Ensure scripts do not hardcode the home path.
- Regenerate or repair reports without modifying raw transcript segment text.
