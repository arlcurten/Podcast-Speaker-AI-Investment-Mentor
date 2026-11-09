# Speaker Profile

Independent offline extraction from explicitly selected, hash-bound transcripts.
Artifacts use `profile.v1`; they do not grant source acceptance, publication,
serving eligibility or automatic inclusion in a global profile.

```bash
python -m speaker_profile plan --config "$PROFILE_CONFIG"
python -m speaker_profile extract --config "$PROFILE_CONFIG" --through evidence
python -m speaker_profile extract --config "$PROFILE_CONFIG" --through synthesis
python -m speaker_profile validate --config "$PROFILE_CONFIG"
python -m speaker_profile schema --through evidence
```

The private JSON config uses `profile_config.v1`, `run_id`, private `output_root`,
explicit `model`, `reasoning_effort`, `timeout_seconds`, `max_input_chars`,
`max_calls`, `retry_count`, `reserved_episode_ids`, and `episodes`.
Each episode specifies `episode_id`, `raw`, `draft`, `source_review`, and their
`*_sha256` pins. An absent review has a null path and hash. Optional
`prior_findings` must be empty; source context belongs in the pinned review.
Optional `catalog: {path, sha256}` binds selection metadata without model input.
Reserved IDs cannot appear in the extraction selection. No sources are discovered
implicitly and knowledge-layer outputs are not extraction inputs.

Evidence is saved under `<output_root>/<run_id>/<episode_id>/evidence.json`;
synthesis is `<output_root>/<run_id>/synthesis.json`. Requests, responses and
bounded error diagnostics stay private. Plan/validate never call a model.
Existing artifacts and call records are immutable. Matching stages resume without
inference; changed config, code, schemas or sources require a new run version.
Call budgets and zero/one repair retry limits apply across resumes of a run.
An interrupted request without a result stops for inspection, not automatic retry.

For synthesis recovery, a new run can set `context_format: compact_profile.v1`
and `reuse_evidence` with a pinned original run `manifest: {path, sha256}` plus
`artifacts: {episode_id: {path, sha256}}` for every selected episode. Original
sources, evidence, requests and results are revalidated; evidence is referenced,
not regenerated or relabeled under the new implementation. The new run writes
only synthesis artifacts. `plan` reports synthesis `input_chars` and `input_fits`.
Compact tables preserve every value and verify exact round-trip reconstruction;
they are not a summary or evidence filter. Character limits are local settings,
not a statement about provider context limits. Input overflow does not repeat an
identical repair attempt; use a new, explicitly reviewed recovery configuration.

To repair a persisted validation failure without repeating the initial synthesis,
set `reuse_failed_synthesis` in a **new run** alongside `reuse_evidence`:

```json
{
  "reuse_failed_synthesis": {
    "manifest": {"path": "<prior-run>/run_manifest.json", "sha256": "<sha256>"},
    "request": {"path": "<prior-run>/calls/cross_episode_synthesis_1.request.json", "sha256": "<sha256>"},
    "result": {"path": "<prior-run>/calls/cross_episode_synthesis_1.result.json", "sha256": "<sha256>"}
  }
}
```

Select the attempt with an actual response and `error.phase: validation`, not a
later input-limit or provider failure. Its manifest, source selection, evidence
lineage, request context and response binding must match the reused evidence.
The first new call contains `validation_repair.previous_response`, the original
`error`, and a `revalidated_error` from current validators. Originals stay intact;
the new manifest and synthesis artifact retain the pinned recovery lineage.
This does not accept or publish any evidence or weaken validation.

Run `plan` before extraction. For a fresh recovery it reports `base_input_chars`,
`repair_input_chars` (added overhead), and total `input_chars`, including the prompt
and transport envelope, plus `seeded_validation_repair` and `input_fits`. Set
`max_input_chars` at least to this measured total; nothing is truncated. The local
limit does not guarantee provider capacity. `max_calls: 1, retry_count: 0` allows
one seeded repair call; `max_calls: 2, retry_count: 1` permits one further attempt
using that call's failure. Budgets persist across resumes; input overflow is
terminal for that run. Plan/validate and provenance checks never invoke a model.
