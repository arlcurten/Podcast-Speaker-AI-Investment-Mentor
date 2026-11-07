# Offline Knowledge Construction

Working, versioned L1-L4 extraction from **accepted** private transcript versions.
No imports from the archive. This is not transcription or the online Mentor.

Active artifact contract: **`knowledge.v2`**. Structural validation accounts for
source coverage, context independence and upstream counterevidence. It does not
certify semantic completeness or replace the source-grounded publication review.

```text
raw + normalized + source-hash-bound content review
-> L1 episode navigation -> L2 historical cases
-> optional L3 episode-local hypotheses/patterns
-> L4 cross-episode candidates
-> source-grounded semantic review -> immutable release descriptor
```

## Run

Install root `requirements.txt`, install/sign in to Codex CLI locally, and set
`KNOWLEDGE_MODEL_ID` to a model available to that login. Copy
`config/example.yaml` privately, specifying actual raw/normalized/review paths
and a new run ID. Root `.env` can supply model and data-path variables; it is
never committed. Subscription usage is not an unlimited token pool or a
per-call dollar price. There is no assumed subscription-to-API credit conversion.

```bash
python3 -m knowledge plan --config "$KNOWLEDGE_CONFIG"
python3 -m knowledge run --config "$KNOWLEDGE_CONFIG" --through level2
python3 -m knowledge run --config "$KNOWLEDGE_CONFIG" --through level4
python3 -m knowledge validate --config "$KNOWLEDGE_CONFIG"
python3 -m knowledge schema --through level4
python3 -m knowledge publish --config "$KNOWLEDGE_CONFIG" --review "$KNOWLEDGE_REVIEW" --output "$RELEASE"
```

Plan/validation do not call a model. Plan still checks source readiness; an
unaccepted transcript is a real blocker, not an optional warning. Use synthetic
tests while real review is pending, never fabricate approval to unblock code.
Stages are immutable JSON under the configured private knowledge root/run ID.
Resume verifies source/schema/prompt/model/upstream fingerprints and skips valid
stages. Changed settings require a new run ID. Default retry count is zero;
at most one retry is configurable. A per-execution call cap bounds work.

## Incremental Updates

Keep the serving release fixed while preparing new knowledge. A private JSON or
YAML source list contains entries with `episode_id`, `raw`, `normalized`, and
`review`, just like `config.episodes`; every source must already be accepted.

```bash
python3 -m knowledge extend --config "$PREVIOUS_CONFIG" --sources "$NEW_SOURCES" --run-id corpus_v2 --output "$NEXT_CONFIG"
python3 -m knowledge plan --config "$NEXT_CONFIG"
python3 -m knowledge run --config "$NEXT_CONFIG"
python3 -m knowledge validate --config "$NEXT_CONFIG"
# After actual source-grounded semantic review:
python3 -m knowledge publish --config "$NEXT_CONFIG" --review "$NEW_REVIEW" --output "$NEW_RELEASE"
```

`extend` makes no model call. Unchanged episodes reference their original L1-L3
paths and exact hashes, without copying files or creating symlinks. New episodes
get new L1-L3; L4 is reconsolidated across the explicit selection. One new episode
normally requires four calls, excluding retries. All existing references are
checked before any call; incompatible prompts/models/schemas or changed/missing
artifacts fail closed. Completed update stages are skipped on resume.

Replacing an episode requires a new accepted source version and explicit
`--replace-existing`; originals and old releases remain intact. Repeating an
identical replacement is rejected. Changed extraction settings require a new
generation run without incompatible reuse references. A reference is a storage
dependency: retain the old run and source files when backing up a newer release.

Publication still requires a fresh hash-bound review of the selected corpus,
including conflicts with new evidence. A successful update never activates
itself. Select the new descriptor via `MENTOR_RELEASE` and restart the service;
rollback within the same supported schema selects the retained old descriptor.
Cross-schema rollback also needs the corresponding earlier code revision.
New artifacts capture their generation prompt so later prompt edits do not invalidate reviewed serving
releases. Earlier artifacts without that snapshot require the matching prompt
revision. Schema/provenance validation remains mandatory in either case.

Codex uses [non-interactive structured output](https://learn.chatgpt.com/docs/non-interactive-mode),
an ephemeral read-only session, no tool execution and a bounded timeout.
Authentication stays local; the supplied transcript context is sent to the
model service. This is **not local model inference**. All production inputs,
failure responses, usage metadata and outputs stay private. Missing cost
metadata is null. Input overflow fails, never silently truncates an episode.

## Contracts and Evidence

`schemas.py` defines JSON Schema proposals. Cases cite supplied `merged_ids`;
code reconstructs raw ID ranges/singles and distinct timestamp regions. A
min/max timestamp range must not imply missing discussion was read. The loader
checks complete normalized-to-raw mapping, text preservation and version hashes.
It also reconstructs OpenCC text and terminology annotations using the recorded
normalizer/dictionary version. Editing a normalized view cannot bypass raw-source
acceptance; upgrading those versions requires a new derived version.

L2 is evidence authority. Host/listener/guest/unknown attribution is explicit;
listener/guest assertions cannot establish the host's principles. L1 guides
navigation, not final claims. L1 accounts for every merged segment in a thread
or an explained `excluded_regions` entry, without overlap between the two.
L2 cases must cite evidence contained in their declared threads. Every L1 thread
has a `thread_dispositions` entry with case IDs or an explicit omission reason;
remaining regions are accounted for as `unextracted_regions`. Exclusions produce
review warnings, not automatic semantic approval. Ads/logistics need not become
investment cases. A wrong map requires a revised L1 in a new run, not a fabricated
case/thread association. L3 supports `principle_extraction`,
`light_principle_grouping`, `bypass`. No episode must produce a principle.

Principles preserve trade-offs, accepted costs, conditions, switch conditions,
exceptions, counterexamples, contradictions, distinctive behavior and
falsification criteria. Single-record L3 becomes a hypothesis, not a recurring
principle. Multi-record hypotheses are also ineligible. L4 requires at least two
supporting episodes; online eligibility additionally requires at least two
independent contexts. Weak/unknown-independence candidates remain explicitly
ineligible for review. L3 hypotheses cannot enter `derived_from`. Their original
cases can participate only alongside genuinely independent evidence.

Raw case counts, unique episodes/threads, overlapping regions and conservative
independent-context components are computed. L2 `decision_context` distinguishes
historical events, hypothetical scenarios, general statements and unknowns;
event identity/time are nullable source claims, not invented global IDs.
L3/L4 `context_relationships` assess each supporting-case pair as same context,
independent or unknown, with a basis and explanation. Different episodes/dates
are not an independence basis. General/unknown statements cannot assert distinct
decisions. Same-thread/overlapping evidence and a shared explicit event identity
override claimed independence. Retellings and unknown pairs are conservatively
connected; connected groups count once. This may undercount uncertain contexts.
Pairwise accounting is for a principle's small evidence set, not all corpus pairs.

These are deterministic constraints on **proposed** semantic relationships,
not automated proof of event identity. Different wording for the same event can
still be mislabeled and must be checked in review. Calendar years are reported
only as publication coverage, not verified market regimes. Principles never
add evidence to these counts.

L4 must explicitly account for counterexamples/contradictions from every L3
whose support overlaps its own, including ineligible L3 and direct-case derivation.
`counterevidence_dispositions` records each upstream link as `retained`,
`context_excluded` with a scope justification, or `unresolved`. Retained/unresolved
links must appear in the corresponding L4 evidence list. Unresolved links lower
confidence and block online eligibility. Explained scope exclusions need review,
not automatic contradiction labeling. Link counts and unique record counts are
separate, never additional independent evidence. Known counterevidence is also
included in L4 input even when its L3 abstraction is ineligible.

Confidence is multidimensional. Computable coverage constrains model proposals;
`overall=high` is unavailable in this version. Specificity and semantic
consistency still require review, not a claim of deterministic truth. Generic,
single-case, major-conflict or weak patterns cannot enter online reasoning.
L3 is always episode-local and not independently runtime-eligible. Eligible L4
is at most `principle_assisted`, never `principle_guided`.

### Decision Process, Not Outcome Ranking

The [trajectory-informed memory paper](https://arxiv.org/abs/2603.10600)
studies guidance extracted from agent execution histories, evaluated on AppWorld,
not podcast interpretation. We borrow attention to decisions and corrections,
not its success-prioritized conflict resolution or inferred outcome labels.

Our L2 prompt uses existing fields to preserve the initial assumption/action,
new signal, stated explanation and revised action, with evidence for both ends.
Reported results stay attributed in `claims`; unstated results/causes stay in
`uncertainty`. Revision is not proof of successful recovery. L3/L4 must retain
decision-changing conditions and cannot rank a preference by narrated profit.
One event and its correction still count as one context, not repeated evidence.
No schema change, extra model pass or automatic learning loop is introduced.

Changed prompts require a new run ID; never rewrite artifacts or reuse their
acceptance as approval of a new extraction. Already reviewed releases keep their
pinned prompts. Synthetic tests check field/evidence transport and conservative
accounting, not whether a model faithfully extracts a real decision. Before
claiming a quality gain, compare the same accepted sources with the prior prompts,
review omitted revisions and unsupported causal/outcome claims, and test transfer
on held-out decisions. Retellings of the same event must not cross that split.

## Publication Review

Every generated artifact is initially `semantic_review_status: pending`.
Publication requires a private `knowledge_review.v1` with:

```text
decision: accept
reviewer, reviewed_at, method
artifact_sha256: {logical L2/L4 path, e.g. SYN1/level2.json: exact SHA-256}
accepted_case_ids: explicitly reviewed global case IDs
accepted_principle_ids: explicitly reviewed eligible L4 IDs, possibly empty
unresolved_critical_issues: []
```

Review must actually compare claims/conditions/attribution with source evidence,
inspect excluded regions and unextracted topics, verify event independence, and
review counterevidence scope exclusions. Complete ID accounting cannot detect a
case that cites the right region but misrepresents or omits its reasoning.
For revision cases, compare the original decision, new signal and correction
against all cited regions. Check that reported results remain attributed,
unknown results remain unknown, and profit/loss has not substituted for process
evidence. Do not mistake a source-grounded causal explanation for causal proof.
Schema/citation checks do not establish semantic entailment. The release is a
small private descriptor, not another corpus copy. It pins source config,
review/artifact hashes and selected IDs; selected principles require both their
support and counterevidence (including context-excluded sources for audit) in the
release. Old flat-confidence outputs are not silently upgraded. Archived data
stays untouched; regenerate a new version only
after source acceptance.

## Schema Migration

`knowledge.v1` artifacts lack these required coverage/independence/disposition
fields and are **not** silently upgraded or accepted by the v2 loader. Keep old
outputs, reviews and releases unchanged. For migration, create a private config
with a new `run_id`, the same accepted raw/normalized/review inputs when still
valid, and no `reuse_artifacts` pointing to v1. Run `plan`, regenerate L1-L4,
validate, then perform a fresh hash-bound semantic review before publishing a
new release. This does not require re-transcription. `knowledge_config.v1`,
review and release descriptor envelopes are unchanged; their hashes pin the new
v2 artifacts. Do not reuse an old acceptance as approval of regenerated content.

## Validation and Limits

```bash
python3 -m pytest -q knowledge/tests
python3 -m compileall -q knowledge
```

Tests synthesize source/reviews and mock inference. Passing them does not mean
the four real episodes are approved or the extraction beats the old baseline.
The bounded Codex transport has also been exercised on synthetic inputs only.
The provider's schema subset omits `uniqueItems`; full local validation still
rejects duplicate IDs. A known disabled-tool-host notice is allowlisted, but
real error events fail closed even if a result file exists.
No neural embeddings, vector database, web tools, self-improving principles or
automatic semantic approval are implemented. Updates are explicit batches, not
an autonomous scheduler. L4 still receives the selected case corpus under the
configured context cap; it fails on overflow rather than truncating. Large-corpus
partitioned consolidation requires a separate measured design and validation.
