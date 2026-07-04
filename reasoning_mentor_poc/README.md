# Reasoning Mentor POC

This is the minimal skeleton for the next principle-plus-case Mentor architecture.

It is not implemented yet. The current working answer demo remains in `../legacy_mentor_rag_poc/`.

## Planned Flow

```text
User query
  -> query / scenario analysis
  -> retrieve Level 4 decision principles
  -> retrieve supporting, conflicting, and similar Level 2 cases
  -> online applicability check
  -> conditional synthesis
  -> source citations
```

The first implementation should reuse the current four pilot episodes and existing Level 2 records from `../semantic_analysis_poc/`. It should not copy the entire legacy RAG codebase.

## What This Adds Over The Legacy Baseline

Legacy baseline:

```text
query -> retrieve Level 2 cases -> LLM answer
```

Planned reasoning path:

```text
query -> retrieve principles -> retrieve cases -> check applicability -> conditional answer
```

The new path should avoid treating historical commentary as current advice. It should cite source cases and clearly state missing information, exceptions, and conflicts.

Future online routing should consume the Level 4 fields produced by `../semantic_analysis_poc/`, especially:

- multi-dimensional `confidence`
- `principle_type`
- `generalization_scope`
- `decision_tradeoff`
- `distinctive_behavior`
- `genericness_status`
- `provenance_stats`
- `eligible_for_online_reasoning`
- `runtime_mode_recommendation`

Do not treat a principle as evidence by itself. The online system must still retrieve and cite the underlying Level 2 records.

## Current Contents

```text
reasoning_mentor_poc/
├── README.md
├── main.py
├── config/
├── modules/
│   └── contracts.py
└── prompts/
```

## Commands

Show the current status:

```bash
cd reasoning_mentor_poc
python3 main.py status
```

Future commands should be added only when there is real implementation behind them.

## Current Non-Goals

Do not implement these in this skeleton task:

- Level 3 LLM extraction
- Level 4 cross-episode consolidation
- principle retrieval
- online applicability LLM calls
- current-data tools
- neural embeddings or vector databases
- UI, deployment, MCP, multi-agent orchestration, or portfolio integration
