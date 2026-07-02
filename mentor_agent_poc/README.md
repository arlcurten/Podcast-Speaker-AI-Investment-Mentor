# Mentor Agent POC

This is a minimal demo workspace for the Podcast Speaker AI Investment Mentor Agent.

It starts from semantic knowledge produced by `../semantic_analysis_poc/` and demonstrates:

```text
user question
  -> retrieve relevant reasoning records
  -> assemble compact evidence context
  -> call an OpenRouter chat model
  -> return a concise source-grounded mentor-style answer
```

The current data source is EP674 only.

## What It Uses

- Level 1 Episode Semantic Map
- Level 2 Reasoning Records
- Level 3 optional consolidation
- compact evidence references with episode, timestamp, and record IDs

The agent does not rerun transcription or semantic extraction.

## How Retrieval Works

The first version uses lightweight hybrid retrieval:

```text
BM25 lexical score
+ field-weighted metadata/tag matches
+ small Chinese query expansion table
```

It searches:

- reasoning record titles
- claims and observations
- reasoning steps
- conditions, risks, exceptions, and uncertainty
- decisions and speaker behavior
- tags and evidence quotes

No vector database is used yet.

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
cd mentor_agent_poc
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

Reports are written under `reports/`.

## Demo Flow

1. Ask a question.
2. Show retrieved reasoning records and scores.
3. Show a grounded answer.
4. Show episode/timestamp citations.
5. Show insufficient-evidence behavior.

Recommended demo questions:

```bash
python3 main.py ask "他在操作上重視哪些條件？" --debug
python3 main.py ask "面對不確定市場時，他會怎麼控制風險？" --debug
python3 main.py ask "現在可以買 Apple 嗎？" --debug
python3 main.py ask "他有沒有談到房地產投資策略？" --debug
```

## Limitations

- EP674 only.
- No live market data or web search.
- No portfolio integration.
- No trading advice or execution.
- No vector database or cross-episode behavioral model yet.
- If no evidence is retrieved, the agent should say evidence is insufficient rather than answer from general model knowledge.

Additional episodes can be added by placing their Phase 2 outputs under `semantic_analysis_poc/data/outputs/<EPXXX>/` and adding them to `config/data.yaml`.
