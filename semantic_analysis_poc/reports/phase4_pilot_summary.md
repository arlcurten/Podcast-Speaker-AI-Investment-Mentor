# Phase 4 Semantic Extraction Pilot Summary

Scope: small pilot using available Phase 1 transcripts only. No full corpus processing was started.

- Available Phase 1 episodes discovered: EP450, EP57, EP670, EP674
- Episodes processed: EP450, EP57, EP670, EP674

## Results

| Episode | Status | Structure | Level 3 | Records | Tokens | Latency | Cost | Warnings |
|---|---|---|---|---:|---:|---:|---:|---|
| EP450 | success | `qa_heavy` | `light_consolidation` | 8 | 81887 | 194.0s | $0.01841 | EP450-reasoning-005 has no explicit risk/uncertainty fields; EP450-reasoning-006 has no explicit risk/uncertainty fields |
| EP57 | success | `qa_heavy` | `bypass` | 12 | 64625 | 245.1s | $0.01265 | 1 validation warnings need review; EP57-reasoning-003 has no explicit risk/uncertainty fields; EP57-reasoning-004 has no explicit risk/uncertainty fields; EP57-reasoning-006 has no explicit risk/uncertainty fields; EP57-reasoning-008 has no explicit risk/uncertainty fields; EP57-reasoning-011 has no explicit risk/uncertainty fields; EP57-reasoning-012 has no explicit risk/uncertainty fields |
| EP670 | success | `mixed` | `light_consolidation` | 10 | 83841 | 270.7s | $0.01114 | No major automated review warnings |
| EP674 | success | `conversational` | `light_consolidation` | 8 | 79462 | 368.4s | $0.01434 | No major automated review warnings |

- Total pilot cost: `$0.05654`

## MVP Readiness

- Enough episode diversity is available to begin evaluating a minimal Mentor Agent MVP.
- Before a real 3-20 episode pilot, add more Phase 1 normalized transcripts.
- Keep evidence compact with `merged_ids`, `source_segment_id_ranges`, and isolated `source_segment_ids`.
