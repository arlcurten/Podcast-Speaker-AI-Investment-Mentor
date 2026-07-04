# Legacy Mentor RAG POC

This is the legacy deterministic case-only RAG baseline.

It retrieves Level 2 reasoning records and asks an LLM to synthesize an evidence-grounded answer. It does not contain the new Level 3 / Level 4 principle model or online applicability reasoning. It is retained for regression testing and comparison with the new `../reasoning_mentor_poc/`.

Do not add future principle extraction, cross-episode decision-principle modeling, online applicability assessment, current-market tools, or production Agent features here. Keep this baseline stable unless a compatibility or correctness bug is found.

It starts from semantic knowledge produced by `../semantic_analysis_poc/` and demonstrates:

```text
user question
  -> retrieve relevant reasoning records
  -> assemble compact evidence context
  -> call an OpenRouter chat model
  -> return a concise source-grounded mentor-style answer
```

The current data sources are EP674, EP670, EP450, and EP57.

## Architecture

```text
Phase 1 normalized transcripts
        ↓
Phase 2 semantic maps and reasoning records
        ↓
Mentor knowledge loader
        ↓
BM25 + metadata + local dense scoring
        ↓
domain / investment relevance gate
        ↓
evidence context assembly
        ↓
OpenRouter Mentor response
        ↓
episode / timestamp / record citations
```

## What It Uses

- Level 1 Episode Semantic Map
- Level 2 Reasoning Records
- Level 3 optional consolidation when present as Phase 2 context
- compact evidence references with episode, timestamp, and record IDs

The agent does not rerun transcription or semantic extraction.

## How Retrieval Works

The first version uses lightweight hybrid retrieval:

```text
BM25 lexical score
+ field-weighted metadata/tag matches
+ small Chinese query expansion table
+ local TF-IDF + LSA/SVD dense similarity over compact reasoning records
+ deterministic domain / investment-relevance gate
```

It searches:

- reasoning record titles
- claims and observations
- reasoning steps
- conditions, risks, exceptions, and uncertainty
- decisions and speaker behavior
- tags and evidence quotes

No vector database is used yet. The default retrieval mode is `hybrid_dense` with the domain gate enabled.

Optional modes:

```bash
python3 main.py ask "他在操作上最重視哪些條件？" --mode lexical --debug
python3 main.py ask "他在操作上最重視哪些條件？" --mode hybrid_dense --debug
python3 main.py ask "他在操作上最重視哪些條件？" --mode hybrid_rerank --debug
python3 main.py eval-retrieval
python3 main.py eval-domain-gate
```

`hybrid_rerank` uses the configured OpenRouter chat model only on top candidates. It is useful for diagnosis, but `hybrid_dense` is the current default because it improves relevance without extra API latency.

## Model Setup

Credentials are read from the repository-root `.env` or the shell environment:

```bash
OPENROUTER_API_KEY=...
MENTOR_MODEL_ID=qwen/qwen3-235b-a22b-2507
```

`MENTOR_MODEL_ID` overrides `config/provider.yaml`.

## Run

Ask one question:

```bash
cd legacy_mentor_rag_poc
python3 main.py ask "這種市場環境適合追高嗎？"
```

With debug metadata:

```bash
python3 main.py ask "面對不確定市場時，他會怎麼控制風險？" --debug
```

Run the small evaluation set:

```bash
python3 main.py eval
```

Run the cross-episode evaluation set:

```bash
python3 main.py eval-cross
```

Reports are private evaluation artifacts and should be written under `${PIPELINE_DATA_ROOT}/legacy_mentor_rag/evaluations/` or kept local/ignored.

Build the deterministic domain annotation index:

```bash
python3 main.py build-domain-index
```

Compare domain gate behavior without paid API calls:

```bash
python3 main.py eval-domain-gate
```

## Demo Flow

1. Ask a question.
2. Show retrieved reasoning records and scores.
3. Show a grounded answer.
4. Show episode/timestamp citations.
5. Show insufficient-evidence behavior.
6. For cross-episode questions, check that episode/time contexts remain separate.

Recommended demo questions:

```bash
python3 main.py ask "他在操作上重視哪些條件？" --debug
python3 main.py ask "面對不確定市場時，他會怎麼控制風險？" --debug
python3 main.py ask "他在不同時期如何看待追高？" --debug
python3 main.py ask "有哪些支持同一原則的不同案例？" --debug
python3 main.py ask "現在可以買 Apple 嗎？" --debug
python3 main.py ask "他有沒有談到房地產投資策略？" --debug
```

## Limitations

- Only four episodes are loaded.
- No live market data or web search.
- No portfolio integration.
- No trading advice or execution.
- No vector database or formal cross-episode behavioral model yet.
- If no evidence is retrieved, the agent should say evidence is insufficient rather than answer from general model knowledge.
- Generated full transcripts, Phase 2 JSON outputs, API response JSON, and temporary indexes are local artifacts, not production storage.

Additional episodes can be added by placing their Phase 2 outputs under `${PIPELINE_DATA_ROOT}/semantic_analysis/outputs/<EPXXX>/` and adding them to `config/data.yaml`. Larger reasoning-system changes should happen in `../reasoning_mentor_poc/`, not in this legacy baseline.
