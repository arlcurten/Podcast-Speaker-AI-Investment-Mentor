# Speaker Profile

Independent offline extraction from explicitly selected, hash-bound transcripts.
Artifacts use `profile.v1`; they do not grant source acceptance, publication,
serving eligibility or automatic inclusion in a global profile.

## Reviewed Native Teaching Context

An explicitly authored, source-bound review can authorize selected native synthesis
hypotheses for provisional private teaching. It does not approve a global persona,
change episode source holds, or enable the API backend. Prepare is read-only;
publication requires per-item source and semantic findings plus pinned review reports.

```bash
python -m speaker_profile.release prepare --input "$PROFILE_CANDIDATE" --sha256 "$CANDIDATE_SHA256" --hypothesis "$HYPOTHESIS_ID"
python -m speaker_profile.release schema
python -m speaker_profile.release publish --input "$SCOPED_REVIEW" --sha256 "$REVIEW_SHA256" --output "$NEW_RELEASE"
python -m speaker_profile.release validate --input "$NEW_RELEASE" --sha256 "$RELEASE_SHA256"
python -m speaker_profile.release read --input "$NEW_RELEASE" --sha256 "$RELEASE_SHA256"
```

Use configured private roots for all files. Each read replays pinned sources and
returns support, counterevidence, inference bases and incident comparison context.
Rare observations and unresolved differences remain visible; abstractions are not
extra evidence. Tests verify integrity, not the truth of reviewer judgments.
An existing release is immutable; changed source, review or implementation requires
an explicit new version or its historical code for replay.

## Corpus Inventory

Use `speaker_profile.corpus_inventory inspect|freeze|validate|closure` with an
explicit private selection. New selections use `profile_corpus_selection.v2`;
new frozen indexes use `profile_corpus_inventory.v2`. The selection declares all
expected episode/synthesis IDs, reserved IDs and each input's available/missing/
failed/held state. All evidence items remain indexed; availability is not approval.

An available episode accepts `{kind: completed, manifest: REF, artifact: REF}`
or `{kind: recovered|editorial|native, artifact: REF}`, where every `REF` is a
private `{path, sha256}`. Native kind requires an adopted
`profile_native_evidence_candidate.v1`, not a packet, submission or backend result.
All authoritative lineage is preflighted before raw/normalized source loaders
run. Native lineage follows the submission's packet and config, not duplicated
artifact headers; reserved episodes in that config cannot be concealed by a
different header. Preflight reads pinned artifacts, including frozen packets
with embedded raw text: it does not guarantee that no reserved-source bytes
have been read before rejection.

Native entries retain `original_manifest: null`, explicit submission/packet/config
and source-file references, unmeasured model/usage metadata and import-only call
accounting. Discovery reads their checked raw references directly and preserves
native authorship, source holds, notes and omissions. Native evidence cannot act
as a synthesis parent under an invented backend manifest. Existing completed,
recovered and editorial artifacts retain their original bytes and provenance;
known syntheses still require the exact selected evidence versions.

Old selection/inventory schemas and discovery configs are rejected with new-version
guidance, not automatically migrated. Preserve their files/reports and use code
`8c91e87` for the pre-native-inventory contracts. Create an explicit new selection,
freeze a new index, then create a new discovery config/run. This zero-inference
revalidation includes source and provenance checks, not merely trusting old index
headers. Neither structural replay nor inventory completeness certifies semantics.

## Optional Native Synthesis Index

`speaker_profile.inventory_extension` creates a separate v3 index from a checked
v2 inventory and explicitly selected native synthesis candidates. It preserves
exact evidence versions, all hypotheses, comparisons and pending comparisons;
principles do not add evidence. Existing inventories and consumers are unchanged.

```bash
python -m speaker_profile.inventory_extension inspect --input "$EXTENSION_SELECTION" --sha256 "$SELECTION_SHA256"
python -m speaker_profile.inventory_extension freeze --input "$EXTENSION_SELECTION" --sha256 "$SELECTION_SHA256" --output "$NEW_INDEX"
python -m speaker_profile.inventory_extension validate --input "$NEW_INDEX" --sha256 "$INDEX_SHA256" --selection "$EXTENSION_SELECTION" --selection-sha256 "$SELECTION_SHA256"
```

Use an explicit private `profile_inventory_extension_selection.v1` selection
with `base_inventory`, `native_syntheses`, expected synthesis IDs, reserved episode
IDs and positive `limits` (`max_nodes`, `max_depth`, `max_metadata_bytes`). Limits
fail visibly rather than truncate data. `closure` uses the same validation flags
plus repeated `--support` evidence IDs to retain relevant counterevidence and
comparison context. This is a candidate index, not a serving release. Existing
discovery does not automatically consume v3; new consumers must opt in explicitly.
Closure is seed-relative and non-transitive: a returned hypothesis does not imply
that every comparison involving all of its other evidence has been expanded.

### Synthesis Over An Extended Inventory

`speaker_profile.native_synthesis_extension` explicitly consumes a checked v3
inventory. Select a private evidence workset and author from its returned packet:

```bash
python -m speaker_profile.native_synthesis_extension schema --kind config
python -m speaker_profile.native_synthesis_extension prepare --input "$SYNTHESIS_CONFIG" --sha256 "$CONFIG_SHA256"
python -m speaker_profile.native_synthesis_extension schema --kind submission
python -m speaker_profile.native_synthesis_extension preview --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis_extension apply --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis_extension validate --input "$NEW_CANDIDATE" --sha256 "$CANDIDATE_SHA256"
```

Preparation is read-only and includes primary source rows, separate analyst notes,
and incident inherited support, counters and pending comparisons. Keep its full
output private. Bind the returned packet hash in the submission and distinguish
inspected evidence from pending evidence. Outside-workset items remain in the
pinned inventory; they are not implicitly read or discarded. Inherited hypotheses
are context, not additional observations. Only `apply` writes a new candidate.
This format does not yet feed publication or recursive indexing. No operation
calls a model or certifies semantic fidelity; originating agent usage is unknown.
Byte limits reject oversized packets without truncation, not bound replay RAM.

### Cross-Version Comparisons

`speaker_profile.native_synthesis_reconciliation` compares explicitly pinned native
and extension candidates over their shared checked inventory. It preserves source
versions, counterevidence and pending comparisons; additional raw context uses
separate citations, not extra evidence. Comparison-only submissions are supported.

```bash
python -m speaker_profile.native_synthesis_reconciliation schema --kind config
python -m speaker_profile.native_synthesis_reconciliation prepare --input "$CONFIG" --sha256 "$CONFIG_SHA256"
python -m speaker_profile.native_synthesis_reconciliation schema --kind submission
python -m speaker_profile.native_synthesis_reconciliation preview --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis_reconciliation apply --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis_reconciliation validate --input "$NEW_CANDIDATE" --sha256 "$CANDIDATE_SHA256"
```

Keep configurations and full command output private. No command runs inference or
publishes a profile. Recursive reconciliation and automatic serving are unsupported.

## Corpus Discovery

For an explicitly frozen private Profile inventory, use bounded discovery to
organize evidence into candidate behavioral families:

```bash
python -m speaker_profile.corpus_discovery plan --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256"
python -m speaker_profile.corpus_discovery run --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256" --max-packets 1
python -m speaker_profile.corpus_discovery validate --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256"
```

The private `profile_discovery_config.v2` pins `inventory: {path, sha256}`,
`run_id`, `output_root`, model/effort, timeout, input/response character limits,
`max_calls` (the run's cumulative admission budget), and `retry_count` (0 or 1).
`plan` and `validate` make no model calls. Repeat `run` to skip matching completed
packets; use `--retry-failed` only after inspecting a failed result. An admitted
request without a result requires inspection, not an automatic retry.

New discovery runs use `compact_profile_discovery.v2`: lossless `compact_profile.v1`
tables plus ordered, SHA-bound references to exact source annotation subsets.
The codec, reference helper, and formats are pinned in implementation metadata.
Source catalogs, inline extensions, issues and commentary authority are preserved.
Input limits measure the packed request, including its legend and schema;
validators expand it before applying
the unchanged evidence rules. Missing values remain distinct from explicit nulls.
Request/run v5 measures each complete candidate once after final sorting; the
outer greedy planner still checks every ownership-prefix candidate. Partial
packed sizes are not assumed monotone. Oversized candidates are fully constructed
before the packed-cap check: `max_input_chars` is not an unpacked RAM bound.
The one-episode materialization cache is unchanged. Changed planning pins require
new runs; request/run v5 receipts cannot resume older runs. The base serializer and
compact transport remain unchanged; native provenance is retained losslessly.

Rare, non-investment and ungrouped evidence remains accounted for. Hypotheses
provide navigation, not additional evidence. Source notes retain their original
authority. Discovery is packet-local grouping, **not** global reconciliation,
semantic approval, a final Speaker Profile, or serving authorization. Changed
discovery inputs or implementation require a new run version; preserve old ones.
An existing inventory remains reusable only when its own validation pins match.

## Standalone Native Synthesis

`speaker_profile.native_synthesis` adopts genuinely agent-authored synthesis over
one checked inventory, including native-only or mixed evidence. It does not run a
backend, modify the pipeline, register a new inventory parent, or grant serving
approval. A candidate can be useful provisionally with explicitly pending work.

```bash
python -m speaker_profile.native_synthesis schema --kind config
python -m speaker_profile.native_synthesis schema --kind submission
python -m speaker_profile.native_synthesis prepare --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis apply --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_synthesis validate --input "$CANDIDATE" --sha256 "$CANDIDATE_SHA256"
```

Omitting the command defaults to read-only `prepare`; only `apply` writes a new
immutable private candidate. Python equivalents are `prepare(submission_ref,
output)`, `apply(submission_ref, output)`, and `load_checked(candidate_ref)`.
Existing outputs and protected input directories are rejected. Schema export is
the authoritative field list; all references are private `{path, sha256}` objects.

The `profile_native_synthesis_config.v1` config pins an inventory, an `actor`
(`identity`, `session_id`), and actual `reports` (`ref`, `authored_at`, `method`).
Its `reading` contains `inspected_evidence_ids`, `pending_evidence_ids`, `method`,
and `limitations`. These ID lists partition the inventory, not the entire corpus.
`comparison_coverage` lists only declared tasks: `comparison_id`, `evidence_ids`,
`status` (`inspected` or `pending`), and `method`. No Cartesian task set is required.

The `profile_native_synthesis_submission.v1` wrapper supplies the config ref,
matching actor, actual `authored_at`, `method`, `limitations`, and `content` in the
unchanged synthesis schema. `model_metadata` is exactly
`{"provider":"native_agent_candidate","model":null,"usage":null,"cost_usd":null}`.
There is no pre-authoring freeze assertion: authors may structure existing reports
later, recording that method honestly. A report about an older evidence version
is historical context, not evidence that its author inspected a newer version;
pin an actual amendment reread report and identify it in the relevant bindings.

Each `hypothesis_anchors` row has `hypothesis_id`, `source_anchors`, and configured
`report_refs`. Each anchor is `{evidence_id, segment_id, start, end, text}`, matching
an exact full raw row in that item's **primary** source region. Supplementary
commentary cannot substitute for primary hypothesis support, counters or bases.
Anchor every support, counter, and inference basis; these IDs must be declared
inspected. Distinct items can cite the same raw row without becoming independent
events. Text/timestamp binding does not certify that a claim follows from a quote.

`comparisons` is a general ledger, separate from strict statement/action links:
each row has `comparison_id`, `endpoints` (`evidence_id`, actual `evidence_kind`),
`relationship`, `reason`, `hypothesis_ids`, `source_anchors`, and `report_refs`.
For example, two `direct_statement` endpoints may have `relationship: unresolved`
and `hypothesis_ids: []`. Action/action pairs also work. Relationship values are
`agreement`, `apparent_mismatch`, `context_resolved_difference`, `unresolved`, or
`not_comparable`. Ledger IDs must exactly match inspected comparison tasks; pending
tasks have no ledger row. Linked hypotheses must contain the endpoints or bases.
Comparison/context anchors may separately cite a supplementary note's source:
add `scope: supplementary_context`, `authority: model_analyst_not_speaker`, and
`note_index` (zero-based in that item's `grounding.supplementary_context`). Such
anchors are checked against that exact note and remain analyst context, not
speaker evidence. Unmarked anchors always mean primary evidence.

`context_dispositions` can be empty. The bridge retains every prior hypothesis
reference and its indexed links/limitations, including recovered comparisons.
For each new hypothesis or inspected comparison it identifies incident links from
both support and counter sides, including inference bases. These are automatically
retained as pending context, not silently dropped or declared resolved. An authored
override names `claim_id` (`hypothesis:PROFILE:H1` or `comparison:<id>`),
`prior_hypothesis_key`, `disposition` (`retained` or `contextualized`), `reason`, and
exact `source_anchors` for all its `required_evidence_ids` (also inspected).
This is one incident pass, not transitive closure or proof of complete recall.

Every operation replays the inventory once, then checks pinned materials and all
submitted anchors without repeated source replay per claim. Raw anchor checks
parse one referenced episode at a time; inventory replay and synthesis derivation
still hold larger structures in memory. JSON inputs/candidates use the existing
32 MB native replay limit without truncation; it is not a hard RAM limit.
All items, source holds, notes and omissions remain reachable through exact
evidence refs; item accounting does not imply inspection. Code validates bindings,
accounting and kinds, not semantic fidelity or author identity authentication.
Import call count is zero; originating native reasoning usage remains unknown.
Changed implementation pins require a new candidate version and historical code
for old replay. Existing pipeline/native evidence pins are unchanged by this module.

## Episode Extraction

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

### Supplementary Context

Omitting `commentary_scope` preserves the item-only `profile.v1` grounding and
prompt. New run or batch configs may opt in with
`"commentary_scope": "episode_context.v1"`. Each supplementary note then resolves
its own nonempty, unique merged IDs against the same episode's pinned source,
even when fully disjoint from the item's primary anchors. Grounding adds a
separate `supplementary_context` list per item, in note order, with exact source
mapping, source warnings, reviewed annotations and
`authority: model_analyst_not_speaker`. Primary merged IDs, quotes, inference
basis checks, source-region accounting and synthesis counts are unchanged.
Notes do not cover unselected regions or become additional independent evidence.
All source holds and provisional policies remain in force.

The mode is pinned in config, request context/prompt and fingerprints. Config or
implementation changes still require new run/batch IDs; existing manifests are
not migrated. Explicit evidence reuse validates with the original run's scope,
not the new run's scope. This option does not recover failed historical responses
or rewrite their call records; that requires a separate provenance-bound recovery
operation. Code validation does not approve commentary semantics or source quality.

For an existing response rejected specifically by the old item-only commentary
rule, use explicit zero-call recovery instead of repeating extraction:

```bash
python -m speaker_profile.recover_evidence validate --config "$PROFILE_RECOVERY"
python -m speaker_profile.recover_evidence apply --config "$PROFILE_RECOVERY" --output "$RECOVERED_CANDIDATE"
python -m speaker_profile.recover_evidence validate --artifact "$RECOVERED_CANDIDATE" --sha256 "$RECOVERED_SHA256"
```

The private `profile_evidence_recovery_config.v1` contains `commentary_scope:
episode_context.v1` and `manifest`, `request`, `result` references, each with
`path` and `sha256`. Recovery verifies the original failed call and source mapping,
then separately grounds the unchanged response. The new
`profile_recovered_evidence.v1` preserves the original failure and historical
model usage; recovery itself makes zero calls. It does not overwrite later
repairs, automatically prefer one response version, grant acceptance, or create
a successful model receipt. Ordinary `reuse_evidence` does not accept this
distinct provenance contract.

To synthesize recovered candidates, use a **new run** with
`commentary_scope: episode_context.v1` and an explicit mapping:

```json
{
  "reuse_recovered_evidence": {
    "SYNTHETIC_EPISODE": {"path": "<private-recovery>/candidate.json", "sha256": "<sha256>"}
  }
}
```

This mapping may be combined with the existing `reuse_evidence` manifest/artifact
references for completed originals. Together they must cover every selected
episode exactly once; neither automatic preference nor new evidence extraction
is allowed. When mixing, completed references may select a subset of their
original run. Each selected source entry, reserved-ID set and catalog pin must
still match its original run. Recovery checks include the failed call, source
reviews and annotation dependencies; all content and historical provenance stay
unchanged. Recovered candidates are not converted into completed model artifacts.

`plan` marks these evidence inputs `referenced_recovered` and counts no new
evidence calls. The synthesis manifest, fingerprint and artifact bind both sets
of references; only synthesis can make a new call. Resume revalidates the pins,
and a changed selection requires a new run. A failed-synthesis repair must use
exactly the same completed and recovered references as its parent.

## Native-Agent Candidate Adoption

`speaker_profile.native_evidence` is a standalone, single-episode authoring and
import contract. It does not launch an agent, call a provider, create backend call
receipts, or automatically register candidates in extraction, inventory or synthesis.
Inventory adoption is a separate explicit `native` descriptor under the v2 contract.
An already available agent must genuinely read and author against the frozen
packet. Do not relabel an earlier response as having used a later packet.

Export the strict config and submission schemas:

```bash
python -m speaker_profile.native_evidence schema --kind config
python -m speaker_profile.native_evidence schema --kind submission
```

A private `profile_native_evidence_config.v1` contains:

- `packet_id`: a new safe identifier; `actor: {identity, session_id}`: the known
  assigned author and existing session, not a guessed model identity.
- `episode: {episode_id, raw, normalized, source_review}`: each file is an absolute
  private `{path, sha256}` reference; absent `source_review` is explicitly null.
- `reserved_episode_ids`: explicit unique IDs, checked before source loading.
- `commentary_scope: episode_context.v1`.
- `max_packet_bytes` and `max_submission_bytes`: positive byte limits, at most
  32,000,000 each. Complete packets are measured without truncation. These are
  file limits, not peak-RAM or provider-context guarantees; packet construction
  materializes one whole episode. Existing admission/reserve policies are unchanged.

```bash
python -m speaker_profile.native_evidence plan --input "$NATIVE_CONFIG" --sha256 "$CONFIG_SHA256" --output "$NEW_PACKET"
python -m speaker_profile.native_evidence freeze --input "$NATIVE_CONFIG" --sha256 "$CONFIG_SHA256" --output "$NEW_PACKET"
python -m speaker_profile.native_evidence validate --input "$NEW_PACKET" --sha256 "$PACKET_SHA256"
```

`plan` is read-only and reports full packet bytes and source counts. Only `freeze`
writes the new packet. It binds the config, raw/normalized/review dependencies,
annotation sidecars/supporting reviews, terminology, exact evidence prompt and
native authoring instructions, full raw/mapped context, schemas and implementation.
No knowledge-layer extraction artifacts are inputs. The packet remains private.

After freeze, the assigned agent writes a separate private
`profile_native_evidence_submission.v1`: `packet: {path, sha256}`, matching `actor`,
actual timezone-aware `authored_at`, `method`, `complete_episode_read: true`,
nonempty `limitations`, and `content` conforming to the packet's evidence schema.
The declaration is allowed only after the complete reading; record nonblind or
other method limits honestly. Hashes and timestamp ordering bind the declared
workflow, not independent proof of authorship, attention, audio fidelity or truth.
Required `model_metadata` is exactly:

```json
{"provider": "native_agent_candidate", "model": null, "usage": null, "cost_usd": null}
```

The provider value identifies this authoring path, not a completed API call.
Model/token/cost metadata is unknown, not zero. Preserve and pin the saved
submission before adoption:

```bash
python -m speaker_profile.native_evidence prepare --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_evidence apply --input "$SUBMISSION" --sha256 "$SUBMISSION_SHA256" --output "$NEW_CANDIDATE"
python -m speaker_profile.native_evidence validate --input "$NEW_CANDIDATE" --sha256 "$CANDIDATE_SHA256"
```

`prepare` is read-only; `apply` writes only a new immutable
`profile_native_evidence_candidate.v1`. Python equivalents are
`plan/freeze(config_ref, output)`, `load_packet(packet_ref)`,
`prepare/apply(submission_ref, output)` and `load_checked(candidate_ref)`.
The unchanged evidence validator checks exact quotes/timestamps, attribution and
inference constraints, same-episode context and full region accounting. Overlap
is recomputed; independent-event count stays null. These checks cannot certify
semantic fidelity or establish that reported actions actually happened.

Adoption records `operation: import_only`, `model_call_performed: false` and
`model_calls_this_execution: 0`. Those zeros describe import only, **not** the
originating agent's reasoning usage. All source holds, analyst-note authority and
unreviewed/non-serving policy survive. There are no successful backend receipts,
automatic publication or L1-L4 changes. Replay requires the exact pinned inputs
and implementation; code/source changes need a new explicitly authored version.
Synthetic tests exercise this contract, not real extraction quality.

## Corpus Evidence Queue

For independent per-episode evidence extraction, use a private `profile_batch.v1`
config with `batch_id`, `output_root`, the same source/model options above,
`excluded_episode_ids`, and `max_provider_failures`. The batch has no `run_id` or
global `max_calls`: each episode receives its own immutable attempt and at most
one configured repair. Explicitly exclude episodes already extracted by a pilot;
reserved evaluation episodes must remain outside the selected source list.

```bash
python -m speaker_profile.batch plan --config "$PROFILE_BATCH" --workers 4
python -m speaker_profile.batch run --config "$PROFILE_BATCH" --workers 4
python -m speaker_profile.batch status --config "$PROFILE_BATCH"
python -m speaker_profile.batch run --config "$PROFILE_BATCH" --workers 4 --retry-failed
python -m speaker_profile.batch validate --config "$PROFILE_BATCH"
```

Worker count is configurable; include other active model jobs when choosing it.
Plan and validation make no model calls. A normal resume reuses completed evidence
and continues pending episodes, while failed/interrupted attempts require explicit
`--retry-failed`. Retries preserve previous inputs, responses and failures in a new
attempt directory. Source/content failures do not discard other episodes.
`max_provider_failures` limits consecutive exhausted provider failures, not total
errors scattered across a corpus; newly created evidence resets that streak.
Repeated provider failures leave a visible `needs_attention` report. Inspect the
retained failures before explicitly retrying, rather than blindly resubmitting.

Config or implementation changes require a new batch ID. The batch saves an
immutable manifest, per-episode configs/checkpoints/call records, evidence and an
atomic summary. Status only reads checkpoints; validation checks artifact/source
bindings. Neither approves semantic quality. This queue does **not** perform
cross-episode synthesis, publish a global profile or make candidates serving-ready.

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
set `reuse_failed_synthesis` in a **new run** alongside `reuse_evidence`,
`reuse_recovered_evidence`, or their explicit combination:

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

## Source-Grounded Editorial Revisions

For narrow prose or unresolved-relationship corrections after source review,
keep the model artifact unchanged and create a separate private revision:

```bash
python -m speaker_profile.review apply --proposal "$PROFILE_REVISION" --output "$PROFILE_REVISED"
python -m speaker_profile.review validate --proposal "$PROFILE_REVISION" --output "$PROFILE_REVISED"
```

The `profile_synthesis_revision.v1` proposal pins its parent config/artifact and
specifies hypothesis-relative JSON pointers, exact old/new values, reviewer
reasons, existing evidence IDs and exact raw segment anchors. Only descriptive
fields and relationship downgrades to `unresolved` are editable. Validation
replays provenance and edits; it does not judge the new prose's semantic truth.
The immutable `profile_reviewed_revision.v1` result identifies amendments as
reviewer commentary, not a new model response or source evidence. Evidence,
source holds and serving policy remain unchanged. Neither command calls a model
or publishes a global profile.

Synthesis revisions follow the parent's explicit completed and recovered evidence
references. Recovered artifacts retain their distinct recovery provenance; they
are not copied into the synthesis run or relabeled as successful extraction.

For a `profile_recovered_synthesis.v1` parent, use the separate explicit
`profile_recovered_synthesis_revision.v1` proposal. It contains `parent_artifact:
{path, sha256}`, `reviewer`, `method`, `rationale`, and the same anchored `edits`
contract above; it does not contain `parent_config`.

```bash
python -m speaker_profile.review_recovered apply --proposal "$PROFILE_REVISION" --output "$PROFILE_REVISED"
python -m speaker_profile.review_recovered validate --artifact "$PROFILE_REVISED" --sha256 "$PROFILE_REVISED_SHA256"
```

The immutable `profile_reviewed_recovered_revision.v1` retains the original failed
call references, error, historical usage, recovery comparison ledger and source
holds. Recovery comparison limitations cannot be changed, removed or relocated,
including through whole-list edits. Validation replays the recovered parent and
all edits with current validators; historical implementation hashes remain lineage,
not a claim that historical code is executing. Changed replay results fail closed.
This separate format leaves completed-parent revision replay unchanged. Neither
format grants acceptance or certifies the semantic truth of reviewer prose.

Run `plan` before extraction. For a fresh recovery it reports `base_input_chars`,
`repair_input_chars` (added overhead), and total `input_chars`, including the prompt
and transport envelope, plus `seeded_validation_repair` and `input_fits`. Set
`max_input_chars` at least to this measured total; nothing is truncated. The local
limit does not guarantee provider capacity. `max_calls: 1, retry_count: 0` allows
one seeded repair call; `max_calls: 2, retry_count: 1` permits one further attempt
using that call's failure. Budgets persist across resumes; input overflow is
terminal for that run. Plan/validate and provenance checks never invoke a model.

## Parallel Semantic Discovery

For large, explicitly selected inventories, `speaker_profile.episode_discovery`
uses whole-episode transport batches and splits oversized batches without
discarding evidence or relationship context. This is not semantic segmentation.
Use a new `profile_episode_discovery_config.v1` with
`packet_strategy: whole_episode_adjacent_midpoint_split.v1`; other fields match
the existing discovery config. Old runs are not silently migrated.

```bash
python -m speaker_profile.episode_discovery run --config "$PROFILE_DISCOVERY_CONFIG" --sha256 "$PROFILE_DISCOVERY_SHA256" --workers 4 --progress
python -m speaker_profile.episode_discovery validate --config "$PROFILE_DISCOVERY_CONFIG" --sha256 "$PROFILE_DISCOVERY_SHA256" --workers 4
```

Completed packets are reused; failed packets require `--retry-failed` within
the configured retry budget. Provider failures stop new admissions and drain
in-flight calls. Interrupted requests require inspection. Incomplete runs exit
nonzero. Concurrency speeds provider calls, not source replay; outputs remain
private candidates requiring cross-packet reconciliation and semantic review.

For new runs, use `speaker_profile.role_aware_episode_discovery` with
`schema_version: profile_role_aware_episode_discovery_config.v1` and
`membership_policy: unique_family_role.v1`. Its CLI flags and packet strategy
are unchanged. One observation may support and limit the same family; retain
both roles and reasons without counting the observation twice. Identical
family/role duplicates remain invalid. Old receipts are not silently adopted.

## Importing Sealed Responses

Saved first-attempt responses require the separately versioned
`speaker_profile.import_aware_episode_discovery` adapter. Do not use the ordinary
role-aware adapter or the original `sealed_response_import` for production
recovery: they do not require recovery provenance on subsequent replay.

First declare the immutable source receipts without making model calls:

```bash
python -m speaker_profile.sealed_response_import_v2 declare \
  --source-config "$SOURCE_CONFIG" --source-sha256 "$SOURCE_CONFIG_SHA256" \
  --source-manifest-sha256 "$SOURCE_MANIFEST_SHA256" \
  --expected-responses "$EXPECTED_RESPONSES" --output "$ORIGIN_JSON"
```

Create a new private config and run ID with
`schema_version: profile_import_aware_episode_discovery_config.v1`, retaining
the episode packet strategy and role-aware membership policy. Its required
`import_origin: {path, sha256}` points to the declaration returned above.
Generation inputs must match the original run. Do not edit old configs or receipts.

```bash
python -m speaker_profile.sealed_response_import_v2 import \
  --config "$IMPORT_CONFIG" --sha256 "$IMPORT_CONFIG_SHA256" --progress
```

Only after successful import, resume using the import-aware adapter:

```bash
python -m speaker_profile.import_aware_episode_discovery run \
  --config "$IMPORT_CONFIG" --sha256 "$IMPORT_CONFIG_SHA256" --workers 4 --progress
python -m speaker_profile.import_aware_episode_discovery validate \
  --config "$IMPORT_CONFIG" --sha256 "$IMPORT_CONFIG_SHA256" --workers 4
```

The destination must not exist before import. Responses and original usage are
preserved exactly, with zero new provider calls during import. Interrupted or
unsealed source attempts and provider/source-validation failures are rejected.
Missing or inconsistent origin declarations, recovery receipts/seals, source
hashes, or imported target mappings block subsequent preparation and replay.

Imported requests count toward the target lifetime admission cap. Their copied
usage is historical, not new spending. `model_calls_this_execution` reports only
calls made during that execution; never subtract imported counts from that field.
Downstream parent specifications must name the import-aware adapter and its exact
versions. `project_completed(spec)` includes explicit origin hashes in its
projection. Recovery grants no source approval, semantic review, or publication.
