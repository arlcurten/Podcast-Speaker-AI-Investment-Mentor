# AI Investment Mentor Agent

This is the formal AI Investment Mentor runtime. It consumes private Phase 2 semantic outputs and answers user questions with source-grounded, conditional reasoning.

## Architecture

```text
User query
→ Query / Scenario Analysis
→ Candidate Route Selection
→ Level 4 Principle Retrieval
→ Level 2 Case Retrieval
→ Applicability Assessment
→ Final Route Decision
→ Grounded Answer Generation
→ Citation / Guard Validation
```

## Evidence Authority

- Level 2 reasoning records are the evidence authority.
- Level 4 principles are revisable reasoning abstractions.
- Principles do not count as additional evidence.
- Online applicability determines whether a principle can be used.

Current routes:

- `principle_assisted`
- `case_only`
- `insufficient_evidence`

`principle_guided` is intentionally not enabled yet because the current Level 4 corpus is too small.

## Data Boundary

Production data is read from the private repository through `PIPELINE_DATA_ROOT`. Public `sample_outputs/` are never used as canonical runtime input.

```bash
export PIPELINE_DATA_ROOT=/absolute/path/to/AI-Investment-Mentor-private/pipeline_data
export OPENROUTER_API_KEY="..."
export MENTOR_MODEL_ID=qwen/qwen3-235b-a22b-2507
```

`MENTOR_MODEL_ID` is optional if `config/agent.yaml` provides a model ID.

## CLI

Deterministic checks without API calls:

```bash
python -m mentor_agent --no-llm validate-data
python -m mentor_agent --no-llm inspect-query --query "他在面對市場敘事和數據不一致時，會怎麼判斷？"
python -m mentor_agent --no-llm retrieve --query "他如何看待追高？"
python -m mentor_agent --no-llm ask --query "他如何看待追高？"
python -m mentor_agent --no-llm compare
```

Live answer generation:

```bash
python -m mentor_agent ask --query "如果現在某檔股票剛出現新聞利多，是否應該直接追高？"
```

Use `--json` on `ask`, `retrieve`, or `compare` for full diagnostics.

## Limitations

- Only EP674, EP670, EP450, and EP57 are configured.
- No live web, news, market data, portfolio context, persistent memory, UI, deployment, MCP, or multi-agent orchestration.
- Citation validation checks existence and retrieved-set membership; it is not full semantic entailment.
- Current-market questions are answered only as historical reasoning frameworks unless external tools are added later.

