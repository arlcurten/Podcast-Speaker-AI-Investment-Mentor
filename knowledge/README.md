# Offline Knowledge Construction

Working, versioned L1-L4 extraction from **accepted** private transcript versions.
No imports from the archive. This is not transcription or the online Mentor.

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
navigation, not final claims. L3 supports `principle_extraction`,
`light_principle_grouping`, `bypass`. No episode must produce a principle.

Principles preserve trade-offs, accepted costs, conditions, switch conditions,
exceptions, counterexamples, contradictions, distinctive behavior and
falsification criteria. Single-record L3 becomes a hypothesis, not a recurring
principle. L4 requires at least two unique supporting episodes/contexts; L3
hypotheses cannot enter `derived_from`. Their original cases can participate
only alongside genuinely independent evidence.

Raw case counts, unique episodes/threads, overlapping regions and conservative
independent-context components are computed. Same-thread or overlapping records
within an episode count as one context. Different dates alone do not prove
different market regimes. Principles are never additional evidence items.

Confidence is multidimensional. Computable coverage constrains model proposals;
`overall=high` is unavailable in this version. Specificity and semantic
consistency still require review, not a claim of deterministic truth. Generic,
single-case, major-conflict or weak patterns cannot enter online reasoning.
L3 is always episode-local and not independently runtime-eligible. Eligible L4
is at most `principle_assisted`, never `principle_guided`.

## Publication Review

Every generated artifact is initially `semantic_review_status: pending`.
Publication requires a private `knowledge_review.v1` with:

```text
decision: accept
reviewer, reviewed_at, method
artifact_sha256: {relative L2/L4 artifact path: exact SHA-256}
accepted_case_ids: explicitly reviewed global case IDs
accepted_principle_ids: explicitly reviewed eligible L4 IDs, possibly empty
unresolved_critical_issues: []
```

Review must actually compare claims/conditions/attribution with source evidence.
Schema/citation checks do not establish semantic entailment. The release is a
small private descriptor, not another corpus copy. It pins source config,
review/artifact hashes and selected IDs; selected principles require both their
support and counterevidence in the release. Old flat-confidence outputs are not
silently upgraded. Archived data stays untouched; regenerate a new version only
after source acceptance.

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
automatic semantic approval are implemented. Full-corpus scheduling is separate.
