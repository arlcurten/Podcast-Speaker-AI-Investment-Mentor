# Principle Confidence Audit

## Previous Implementation

- `confidence` was a flat `low|medium|high` string emitted by the LLM.
- Deterministic code validated only the enum value and supporting-record references.
- It did not override high confidence, compute thread independence, or compute runtime eligibility.

## New Implementation

- `confidence` is a deterministic multi-dimensional object after post-processing.
- Level 3 `overall` is constrained to `low` or `medium`; `high` is not allowed for episode-local principles.
- Level 4 requires at least two supporting episodes.
- Single-record Level 3 outputs become `case_derived_hypothesis` and are not eligible for Level 4.
- Runtime eligibility is separate from extraction confidence.

## Level 3 Summary

### EP674

- Strategy: `light_principle_grouping`
- Principle count: `3`
- Overall confidence: `{'medium': 1, 'low': 2}`
- Principle types: `{'conditional_pattern': 1, 'case_derived_hypothesis': 2}`
- Runtime modes: `{'principle_assisted': 1, 'not_eligible': 2}`

### EP670

- Strategy: `principle_extraction`
- Principle count: `3`
- Overall confidence: `{'medium': 2, 'low': 1}`
- Principle types: `{'conditional_pattern': 2, 'case_derived_hypothesis': 1}`
- Runtime modes: `{'principle_assisted': 2, 'not_eligible': 1}`

### EP450

- Strategy: `light_principle_grouping`
- Principle count: `6`
- Overall confidence: `{'medium': 1, 'low': 5}`
- Principle types: `{'conditional_pattern': 1, 'case_derived_hypothesis': 5}`
- Runtime modes: `{'principle_assisted': 1, 'not_eligible': 5}`

### EP57

- Strategy: `bypass`
- Principle count: `0`
- Overall confidence: `{}`
- Principle types: `{}`
- Runtime modes: `{}`

## Level 4 Summary

- Candidate count: `1`
- Rejected candidate count: `1`
- Validation ok: `True`
- Confidence: `{'medium': 1}`
- Runtime modes: `{'principle_assisted': 1}`
- Codex review: `{'needs_revision': 1}`
