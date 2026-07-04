# EP674 Phase 3 Model Comparison

Scope: one episode, same normalized transcript, terminology table, prompts, JSON schema response format, temperature, and output requirements.

## Summary

| Model | Status | Structure | Level 3 | Threads | Records | Tokens | Latency | Cost | Notes |
|---|---:|---|---|---:|---:|---:|---:|---:|---|
| `qwen/qwen3-235b-a22b-2507` | success | `qa_heavy` | `bypass` | 8 | 8 | 114036 | 481.4s | $0.02554 | validation warnings: 1 |
| `google/gemini-2.5-flash-lite` | failed | - | - | - | - | 47354 | 27.1s | $0.00834 | Level 1 invalid JSON / output truncation |
| `deepseek/deepseek-v3.2` | success | `mixed` | `light_consolidation` | 5 | 9 | 102055 | 354.4s | $0.02878 | validation warnings: 1 |

- Comparison-row cost: `$0.06266`.
- OpenRouter account usage after all calls was checked separately by `preflight-auth` and showed `$0.09132129` usage. That includes earlier failed Qwen attempts during schema/prompt adjustment.

## Codex Automated Review Findings

### qwen/qwen3-235b-a22b-2507

- Passed all three levels after compact evidence prompt was added.
- Best topic-thread coverage among successful runs: it kept market correction, Apple pricing, investor psychology, and multiple Q&A/personal/business-philosophy threads.
- Level 3 selected `bypass`, which is defensible for a Q&A-heavy/conversational episode because Level 2 records are mostly independent.
- Risk: it may preserve too many low-investment Q&A/personal threads as semantic threads, so retrieval could need later filtering or tags.
- No obvious schema failure in final successful run; one Level 3 validation warning remains and should be inspected before pilot hardening.

Thread titles:
- Personal and Investment Performance Review (H1 2024)
- Market Correction Analysis: Causes and Technical Signals
- Apple's Price Hike and Consumer Markup Dynamics
- Investor Psychology: Greed, Fear, and the 'Rebalance' Narrative
- Q&A: Listener Life and Career Dilemmas
- Q&A: Investor Behavior and Market Sentiment
- Q&A: Personal Values, Family, and Spending
- Q&A: Business Philosophy on Partnerships and Complexity

### google/gemini-2.5-flash-lite

- Failed at Level 1 JSON parsing.
- The raw failed output expanded long `source_segment_ids` lists and hit the 12000 completion-token cap.
- This is a structured-output reliability / instruction-following issue for the current prompt/schema, not a semantic-quality result.
- Do not use this model for the pilot without prompt/schema changes that make evidence references more compact or schema-enforced differently.

### deepseek/deepseek-v3.2

- Passed all three levels.
- Faster than Qwen in this run and produced valid structured output.
- It chose `mixed` and `light_consolidation`, which is also defensible because EP674 contains market commentary plus Q&A/personal/business topics.
- It produced fewer Level 1 threads than Qwen and appears to compress or omit some Q&A/personal segments. This may be better for investment-focused extraction but risks missing speaker-behavior evidence.
- Good candidate when cost/latency and valid JSON reliability matter more than broad conversational coverage.

Thread titles:
- Mid-Year Review: Personal & Investment Performance
- Analysis of Recent Market Correction & Outlook
- Apple's Price Hike & Consumer Market Analysis
- Portfolio Strategy & Cross-Market Allocation
- Business Philosophy: Avoiding Complex Partnerships

## Major Differences

- Qwen is broader and more Q&A-aware; DeepSeek is more compact and investment/business focused.
- Qwen selected `bypass`; DeepSeek selected `light_consolidation`. Both are acceptable for this episode, but the user should decide whether Phase 2 should prefer preserving independent records or lightly consolidating related investment/business threads.
- Gemini did not reach semantic comparison because Level 1 was malformed.
- No model was judged for transcript ASR correctness in this comparison; this only compares semantic extraction behavior from the same Phase 1 transcript.

## Recommendation

- Best semantic coverage: `qwen/qwen3-235b-a22b-2507`.
- Best cost/quality balance for the 3-20 episode pilot: `qwen/qwen3-235b-a22b-2507`, with DeepSeek kept as backup.
- Best structured-output reliability in this small run: tie between Qwen final compact run and DeepSeek; Gemini failed.
- Recommended pilot model: `qwen/qwen3-235b-a22b-2507`, because it preserved more of the conversational/Q&A structure that matters for speaker behavior and future Mentor reasoning. Use the compact evidence prompt and keep the current schema.

## Prompt / Schema Issues Before Pilot

- Evidence references must stay compact. Long `source_segment_ids` enumeration caused Qwen and Gemini failures before the prompt was tightened.
- Consider representing evidence as `merged_ids` plus `source_segment_id_ranges` in the next schema revision instead of large raw ID arrays.
- Keep the current instruction that Level 3 can be `bypass`; forcing synthesis would over-compress this episode.
- Add a small validation check that warns when an output uses too many raw source IDs in one evidence item.

## Output Paths

- `qwen/qwen3-235b-a22b-2507`: `data/model_comparison/EP674/qwen__qwen3-235b-a22b-2507`
- `google/gemini-2.5-flash-lite`: `data/model_comparison/EP674/google__gemini-2.5-flash-lite`
- `deepseek/deepseek-v3.2`: `data/model_comparison/EP674/deepseek__deepseek-v3.2`

- Canonical Phase 2 output: `data/outputs/EP674/`
- Canonical Phase 2 review: `reports/EP674_phase2_human_review.md`
