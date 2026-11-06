# Investment Mentor

Local orchestration and evidence search, with OpenRouter structured reasoning.
The LLM is remotely hosted; keeping files local does not make model inference
local. Selected context is transmitted to the configured provider.

```text
question + compact principle index
-> structured scenario/search plan
-> local case search + principle-linked support/counterevidence
-> applicability (conflict / exception / context difference)
-> final route -> conditional answer -> citation/guard validation
```

Routes: `principle_assisted`, `case_only`, `insufficient_evidence`. There is no
`principle_guided` route. L2 cases are evidence authority; L4 is a revisable
framework, never an independent evidence item or permanent speaker policy.

## Setup and Use

Install root `requirements.txt`. Configure private paths and local secrets in
the ignored root `.env` or environment. Set `MENTOR_RELEASE` to a reviewed
`knowledge_release.v1` descriptor produced by `python3 -m knowledge publish`.
Set `OPENROUTER_API_KEY`; optionally override `MENTOR_MODEL_ID`. Never use a
public sample or bypass review to manufacture a live demo.

```bash
python3 -m mentor_agent --help
python3 -m mentor_agent --no-llm validate-data
python3 -m mentor_agent retrieve --query "What conditions should change a position decision?"
python3 -m mentor_agent ask --query "Evidence is incomplete; should I wait or use a small position?"
python3 -m mentor_agent ask --query "Does the historical framework apply here?" --json --output "$PRIVATE_TRACE"
python3 -m mentor_agent serve --port 8765
```

`--config` is a global option for a private copy of `config/default.yaml`.
Normal CLI output is concise; JSON/output mode includes retrieval, assessment,
guards and model metadata. Full diagnostics must remain private. No command
commits or pushes. `retrieve` never calls a model. The HTTP API binds only
`127.0.0.1`; `/health`, `/docs` and `POST /ask` are available locally. No public
deployment/authentication is provided. One request runs at a time; competing
requests get 429. Access logs and persistent question history are disabled.

The example Qwen model remains configurable; current provider availability and
quality need a small live smoke before relying on it. API responses must support
strict JSON schema; unsupported providers fail safely instead of silently
dropping structured output. Token/latency/cost metadata is recorded when given;
cost remains null otherwise. No model fallback silently changes the backend.

## Evidence and Limits

The loader revalidates source reviews, source/artifact hashes and deterministic
qualification. A release descriptor pins accepted case/principle IDs. Public
fixtures are never auto-discovered. Changing/revoking review invalidates use.
New knowledge does not silently alter a running service. Publish a new reviewed
descriptor, change `MENTOR_RELEASE` and restart; retain the old descriptor and
its referenced sources/stages for rollback. Responses include `release_sha256`;
each citation also pins its L2 artifact hash, distinguishing revised knowledge
that uses the same episode/record ID. This is version traceability, not proof
that the new interpretation is more accurate.

Retrieval uses local BM25, term-frequency cosine, metadata matches and Chinese
concept aliases. This is not neural embeddings or the archived LSA experiment.
The structured model plan can expand search terms from the principle/domain
index, but the first implementation has no unbounded autonomous tool loop.
Only a small configured case set reaches the model. If a principle's supporting
and opposing evidence cannot fit, reject the principle rather than drop its
counterexamples. Duplicate controls do not conflate different contexts/episodes.

Potential-conflict/exception roles are candidate signals, not contradiction
proof. Applicability compares actual conditions and may reject the principle.
Unresolved conflict reduces confidence and can downgrade the route. A major
conflict plus missing information prevents directional synthesis. Both sides
retain source citations, including the non-directional fallback.

Historical claims cite episode, record ID, source hash and separate timestamp
regions. Model interpretation, missing scenario information and unavailable
current facts are separate response fields. No current prices/news/tools or
personalized trading instructions are supported. Citation existence and rule
guards do **not** prove semantic entailment; subjective answer quality requires
source-grounded evaluation. A fluent answer is not validation.

No live corpus has been approved merely by implementing this runtime. Until
transcript and semantic acceptance are complete, use synthetic deterministic
tests and the archived baseline for comparison, not unreviewed production data.

```bash
python3 -m pytest -q mentor_agent/tests
python3 -m compileall -q mentor_agent
```

No neural embeddings/vector database, market tools, portfolio/user profile,
persistent memory, UI, multi-agent orchestration, trading or cloud deployment.
