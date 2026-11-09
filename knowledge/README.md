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

### Bounded Corpus Jobs

`python3 -m knowledge.batch --config "$BATCH_CONFIG" --dry-run` plans explicit
private sources without inference. Remove `--dry-run` and optionally set
`--workers 2 --max-episodes 8` for a measured batch. Each episode has its own
source review, normalized version, L1-L3 stages and atomic status. A held or
failed episode does not discard other results. No command publishes a release
or starts serving. This runs subscription model calls, not a free validation.

For an already running candidate batch, `python3 -m knowledge.prefetch
--config "$BATCH_CONFIG" --run-id "$TRIAL_ID" --episode "$EPISODE_ID"
--workers 1 --dry-run` plans a bounded look-ahead trial (repeat `--episode`).
Choose unstarted episodes well away from the main queue head. Remove `--dry-run`
to execute up to 16 episodes with 1-4 additional workers. Account for all other
running model services before choosing concurrency; token allowance does not
establish a concurrency limit. The trial uses the original per-episode stage
store and locks, leaves the main queue/status untouched, and writes a separate
private trial report. The main queue later validates/reuses these stages without
new model calls. A scheduler collision cannot generate the same stage twice;
the current scheduler defers a busy job until resume. Already-running schedulers
from older code do not hot-reload this handling: keep trials far from their queue
head and inspect any busy-job failure before retry. A failed trial preserves
partial stages; the main queue's normal bounded execution policy still applies.
Trials never restart automatically or grant review/publication acceptance.
`--minimum-queue-gap` requires that many unstarted, unselected episodes before
each trial episode and rechecks on admission; it reduces collision risk with old
live schedulers but is not a reservation or a substitute for per-episode locks.

For sustained throughput, use `python3 -m knowledge.prefetch --config
"$BATCH_CONFIG" --run-id "$CONTROLLER_ID" --workers 4 --minimum-queue-gap 128
--continue-pending`. The controller waits for an existing look-ahead trial, then
automatically schedules further tail chunks. Sixteen episodes is a chunk size,
not a total work limit. A controller lock prevents multiple controllers; do not
start separate manual trials alongside it. Completed artifacts are reused and
isolated content failures are retained for review while other episodes continue.
Interrupted trial episodes are preserved for explicit inspection. Transient
provider failures get a backoff; repeated provider failures stop with a visible
`needs_attention` checkpoint. Completion means the safe tail is drained, not that
the main queue, semantic review, or serving release is finished. State is under
`controllers/<run-id>/status.json` in the private batch directory. Resume with the
same controller command/config; this does not restart it after machine shutdown.

Add `--continuous-refill` with a **new controller ID** to refill each free worker
slot without waiting for a whole chunk. It keeps the same stage store, episode
locks, candidate holds and 1-4-worker limit. Immutable per-episode admissions and
ordered completion receipts live under the controller directory. A restart
never automatically retries admitted work; an admission without a completion
receipt requires inspection. The main-queue gap excludes previously reserved
tail work. Three consecutive provider-failed episode outcomes stop admission;
a new successful provider response (including a content-validation rejection)
resets the streak, but cached reuse, revalidation and skipped jobs do not. The
lifetime total is reported separately and does not trip this guard. Once tripped,
the guard remains latched while active calls finish, including across restart.
The older chunk mode retains its cumulative provider-failure guard.

For a controller started with drain-aware code, `python3 -m knowledge.prefetch
--config "$BATCH_CONFIG" --run-id "$CONTROLLER_ID" --request-drain` creates a
durable admission-stop marker. Active work finishes normally; chunk mode checks
the marker between chunks. Wait for state `drained` and release of the controller
lock before starting a new controller ID. The marker is sticky, not a process
signal. An already-running older controller cannot hot-reload drain support:
wait for its natural exit or a separately authorized cooperative handoff; do not
assume editing source or writing the marker drains that process. No controller
completion grants semantic acceptance or serving readiness.

Continuous refill optionally accepts `--extra-workers 1` (up to 4) with
`--burst-until "$UTC_DEADLINE"`, an explicit timezone-aware UTC ISO 8601
timestamp. The existing `--workers` remains the 1-4-worker baseline. One
controller owns the combined pool, capped at baseline plus extra workers; no
second controller or lock bypass is needed. At the deadline it admits only up
to the baseline budget: already admitted work finishes normally, and no jobs
are cancelled. An already-expired deadline starts at baseline only. Expiry is
latched in the checkpoint, including across resume and backwards clock changes.
The deadline and extra budget are immutable controller identity fields; changing
either requires a new controller ID. These options do not hot-reload a running
controller or schedule a future launch. Account for other model services before
explicitly enabling a burst; the bounded standalone/chunk interfaces are unchanged.

API callers may additionally provide `burst_allowed`, a fast, nonblocking callback,
and `burst_guard_sha256`, the lowercase SHA-256 of their guard implementation/config
manifest. Both are required together; changing the guard identity requires a new
controller ID. The caller must verify that manifest against the actual guard code
and settings. Only the literal result `True` permits extra admissions; any other
result or exception limits admissions to baseline workers without cancelling jobs.
The callback is checked before admissions and while waiting at `poll_seconds`
intervals, so recovered resources can restore extra slots before the deadline.
Expiry remains permanent for that controller. Status records the admission budget,
`burst_admission_reason` and exception type when applicable. Monitor freshness,
resource thresholds and host-specific paths belong to the caller, not this API.

For **candidate construction**, set `extraction_mode: candidate`. The same
pipeline validates the raw/draft mapping, then extracts L1-L3 without requiring
whole-episode acceptance or repeating a preliminary model review. Each L2 case
has a source assessment and separate, anchored supplementary notes. Local doubt
can limit one claim/case without blocking unrelated material. Known source
reviews are passed as pinned `source_review` / `source_review_sha256` references,
including held reviews; their notes are retained, not silently marked resolved.
Use an empty `prior_findings` list in this mode; existing findings must instead
be preserved in a pinned review. Do not omit known findings to bypass accounting.

In candidate L2 accounting, `unextracted_regions` records source-anchored omission
notes, not a disjoint sentence partition. One merged region may contain both a
usable claim and an omitted ambiguous statement; different notes may reference
that same region. Keep all reasons and expose `partial_extraction_notes`, counting
unique regions rather than extra evidence. Missing/foreign IDs, within-note
duplicates and unresolved case/issue references still fail. Reviewed-mode
partition validation remains unchanged. Region accounting is not claim completeness.

Candidate artifacts use `knowledge_candidate.v1`, not `knowledge.v2`.
They cannot be published or loaded by Mentor, even with a forged acceptance
selection or changed config mode. `usable_with_limits` is a model proposal,
not source certification. Unresolved supporting cases constrain principles to
low confidence and prevent L3 eligibility; all candidate principles remain
ineligible online. Sources and prior artifacts stay immutable. Acceptance and
promotion require separate source-grounded review; there is no automatic promotion.

The private JSON `knowledge_batch.v1` config declares `batch_id`, `output_root`,
`model`, `reasoning_effort`, optional `timeout_seconds` / `max_input_chars`, and
`episodes`. Each entry specifies `episode_id`, `raw`, `draft`, `raw_sha256`,
`draft_sha256`, and `prior_findings`. Inputs must be private and hash-pinned.
The default `reviewed` mode retains the existing whole-episode source gate.
Known critical findings must be included, not dropped to obtain acceptance.

A new candidate batch may give each episode `reuse_artifacts`: a contiguous
`level1` through `level3` prefix of `{path, sha256}` references. Preflight verifies
the exact source, model/prompt and upstream bindings before generation. This can
select an explicitly revised L2 without copying old artifacts; its old L3 cannot
be reused against the changed L2. Full-prefix reuse makes no inference calls.
Full-context model source review is a text-based judgment, not audio listening
or ASR-accuracy certification. Structural checks alone never grant acceptance.

Completed stages resume without new generation; changed sources/settings need
a new batch version. Holds remain holds. Failed or interrupted jobs require
explicit `--retry-failed`; immutable review/call records may require inspection
or a new version before retry. Source review and semantic publication review
are separate gates. L4 remains a separately configured, bounded cross-episode
consolidation; this command does not claim full-corpus L4 scalability.

After the selected jobs finish, `--consolidate-config "$L4_CONFIG" --run-id
candidate_cross_v1` creates a separately runnable L4 config referencing their
immutable L1-L3 files. It makes no model calls and rejects unfinished inputs.
Run `python3 -m knowledge run --config "$L4_CONFIG"` to consolidate that explicit
bounded corpus. Input-size limits still apply; a small batch is not global L4
coverage of every episode in a larger queue.

For a **new candidate L4 config**, opt in to `context_format: compact_candidate.v1`
to retain every L2 case, assessment, omission reason and exact annotation/issue
note while deduplicating episode-scoped source issues. Case evidence references
source hashes and keeps raw IDs and timestamp regions; repeated raw anchor text
and annotation-review blobs stay in the full immutable artifacts, not the model
view. The context includes its reference legend. This is serialization, not
retrieval, filtering, summarization or source acceptance. Only L4 generation
and its fingerprint use the explicit format; L1-L3 contexts and fingerprints
remain unchanged, including pinned L3 reuse. Omit this setting for existing runs: default contexts and
fingerprints are unchanged. Measure the resulting context before choosing an
explicit input cap; compact serialization does not guarantee it fits the default.

Optional `retry_count: 1` permits one repair per stage using the actual validation
failure and retained proposal, never an invented source fix. Completed stages
are reused. If an unchanged candidate proposal rejected by an older validator
passes all current checks, resume revalidates it without inference. The original
failed call remains immutable and a separate recovery record links its checksum;
original token usage is not counted twice. This is not semantic approval.
`max_consecutive_failures: 3` stops admission of queued jobs after
successive new failures; in-flight calls finish and remaining jobs are deferred.
Inspect private reports before resuming failed jobs. This bounds failures, not
subscription availability or completion time.

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
L4 must account for every counterevidence link from support-overlapping L3
principles. Additional links are allowed only when their exact parent, record
and kind exist in the supplied L3 artifacts, with a reason and consistent
retention status. They do not add independent support. Missing required links,
invented links and duplicate dispositions still fail validation.
The loader also reconstructs OpenCC text and terminology annotations using the recorded
normalizer/dictionary version. Editing a normalized view cannot bypass raw-source
acceptance; upgrading those versions requires a new derived version.

An optional [reviewed annotation sidecar](../transcription/README.md#episode-local-reviewed-annotations)
is discovered only through a `transcript_review.v2` hash-bound reference. The
episode config remains `raw`/`normalized`/`review`; no duplicate annotation setting
is needed. All v1 sources retain their existing contract. V2 verifies the sidecar,
its separate review, exact raw anchors and supporting-review file hashes before
any model call. Critical unresolved annotation uncertainty blocks reviewed-mode
extraction, not explicit quarantined candidate construction.
Spoken slips and inconsistent quantities can be reviewed cautions, not ASR fixes
or silently normalized facts. Model agreement alone is not ambiguity resolution;
review methods preserve model-assisted versus human evidence and their limits.

**Source versus commentary:** raw anchors remain verbatim and normalized text
remains the deterministic reading aid. Annotation `note` is reviewer commentary,
not speech or evidence. Keep it brief; describe an ambiguity, limitation or
distinction without supplying missing context, facts, motives or reasoning.
Candidate extraction-time clarifications use separate `supplementary_notes` with
kind, concise note and relevant merged IDs. Reviewed-mode prose clarifications
use `[Agent clarification]` in L2 `uncertainty`, never in speaker claims or quotes. L3/L4 must
not promote these clarifications into preferences or independent support.

L1/L2 receive `reviewed_annotations` separately from unchanged transcript text,
with deterministically mapped `merged_ids`. Source metadata and fingerprints pin
`annotations_sha256`. Deterministic evidence attaches every note overlapping a
case/thread's raw IDs, with the sidecar hash, review metadata and supporting-review
hashes, but no private reference paths. Complete note anchors may extend beyond
the cited case; they do not expand its evidence IDs or independent-context counts.
L3/L4 and released cases retain these notes through case evidence even if a model
omits them from prose. Their faithful interpretation still requires semantic
review. Notes do not authorize exclusions or change existing coverage rules.

Preparation and extraction recheck dependencies before publishing derivatives;
release loading revalidates the same references. Missing, changed or mismatched
sidecars/supporting reviews fail closed. Use new reviewed source versions and
`extend --replace-existing` for annotation revisions; affected episode stages
and L4 need regeneration and fresh semantic acceptance. Never edit old sidecars,
reviews or outputs in place. Prompt changes also require a new run; existing
reviewed releases retain their pinned prompts. There is no automatic migration.

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

### Selected Case Use

An episode-wide source hold need not discard independently readable cases.
`knowledge_case_review.v1` provides explicit **selected-case** source fitness and
semantic review, with exact source/L1/L2 hashes. Every case field, overlapping
source issue, omission and global limitation needs a source-grounded disposition.
An unrelated uncertain label can be excluded with a reason; unresolved ambiguity
that determines the selected claim cannot be approved. This is a reviewer
judgment, not an automatic promotion based on a model score or passing tests.

```bash
python3 -m knowledge case-review-packet --config "$CANDIDATE_CONFIG" \
  --case-id SYN1:R1 --output "$PRIVATE_REVIEW_PACKET"
# Inspect the packet and source, then author the hash-bound private review.
python3 -m knowledge publish-cases --config "$CANDIDATE_CONFIG" \
  --review "$PRIVATE_CASE_REVIEW" --output "$PRIVATE_CASE_RELEASE"
```

The resulting `knowledge_case_release.v1` contains no principles. It preserves
the original episode hold, analyst/source distinction and limitations; it permits
case-only reasoning, not whole-episode acceptance. Source/review changes invalidate
the release. Complete reviews and evidence remain private. This does not replace
full corpus review or cross-episode principle construction.

`knowledge.v1` artifacts lack these required coverage/independence/disposition
fields and are **not** silently upgraded or accepted by the v2 loader. Keep old
outputs, reviews and releases unchanged. For migration, create a private config
with a new `run_id`, the same accepted raw/normalized/review inputs when still
valid, and no `reuse_artifacts` pointing to v1. Run `plan`, regenerate L1-L4,
validate, then perform a fresh hash-bound semantic review before publishing a
new release. This does not require re-transcription. `knowledge_config.v1`,
review and release descriptor envelopes are unchanged; their hashes pin the new
v2 artifacts. Do not reuse an old acceptance as approval of regenerated content.

## Bounded Corpus Discovery

An explicitly frozen private inventory can be organized into candidate reasoning
families before cross-episode consolidation:

```bash
python -m knowledge.corpus_discovery plan --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256"
python -m knowledge.corpus_discovery run --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256" --max-packets 1
python -m knowledge.corpus_discovery validate --config "$DISCOVERY_CONFIG" --sha256 "$CONFIG_SHA256"
```

Use a private `knowledge_discovery_config.v1` with `inventory: {path, sha256}`,
`run_id`, `output_root`, model/effort, timeout, input/response character limits,
cumulative `max_calls` and `retry_count` (0 or 1). Planning and validation make
no model calls. Matching completed packets are reused; failed packets require
explicit `--retry-failed`, and interrupted requests require inspection.

Complete evidence and counter-context are retained; oversized bundles are not
silently truncated. Families are packet-local navigation proposals, not Level 4
principles or independent evidence. Global reconciliation, counter-review and
semantic acceptance remain separate work. Preserve old runs when creating new
versions after input or implementation changes.

For a small completed discovery run, `python -m knowledge.family_reconciliation`
provides the same `plan`, `run` and `validate` interface. Its private
`knowledge_reconciliation_config.v1` replaces `inventory` with
`parent: {config: {path, sha256}, artifact: {path, sha256}}` and requires an
explicit `max_pairs`. Check the plan's pair/call estimate before execution.
This source-backed pair verifier is quadratic, not a full-corpus scheduler:
it retains conditional differences, unresolved relations and ungrouped cases,
but does not transitively merge families or perform global counter-review.

For descriptor-page routing, use `python -m knowledge.family_routing
plan|run|validate --config "$ROUTING_CONFIG" --sha256 "$CONFIG_SHA256"`.
`knowledge_family_routing_config.v1` uses the same runtime and `parent` fields,
replacing `max_pairs` with required `max_response_entries` (positive integer)
and `audit_pairs` (an explicit list, possibly empty, of ordered versioned family
key pairs). Plan reports actual packet counts and call-budget fit. Choose the
explicit lifetime `max_calls` from that plan plus an approved retry reserve;
`--max-packets` only pauses an invocation. There is no default one-call limit.
Descriptor fields are preserved; every cross-packet pair receives a routing
disposition. Routing negatives are not source-verified unrelatedness.

The completed routing `discovery.json` pins the exact positive/uncertain,
known-counter and audit selection. Pass its `{path, sha256}` as `selection` in
`knowledge_reconciliation_config.v2`, retaining the verifier's `parent`, runtime
and `max_pairs` fields. Use the existing `knowledge.family_reconciliation` CLI.
Only selected pairs receive source preflight and verification. Parent/routing
receipts are replayed; altered selections or parents fail closed. Both stages
retain all item accounting and explicit mandatory future global/ungrouped
challenge references; neither implements that challenge or authorizes release.
Oversized descriptors/source closure remain incomplete, never truncated. Routing
still has quadratic descriptor work and selected source work can be large.
Changed pins require new runs; no historical receipt migration is provided.

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
