# Reasoning Mentor Architecture

This document defines the planned principle-plus-case Reasoning Mentor architecture. It does not describe an implemented production system yet.

## POC Responsibilities

```text
legacy_segment_poc
  -> Phase 1 transcription, normalization, dictionary annotations,
     timestamps, source IDs, and evidence mapping

semantic_analysis_poc
  -> offline semantic extraction:
     Level 1 Episode Semantic Map
     Level 2 Reasoning Records / Cases
     future Level 3 Episode-Level Provisional Principles
     future Level 4 Cross-Episode Decision Principles

legacy_mentor_rag_poc
  -> deterministic case-only RAG baseline:
     query -> retrieve Level 2 records -> domain filtering ->
     context assembly -> LLM answer -> citations

reasoning_mentor_poc
  -> planned principle-plus-case reasoning:
     query analysis -> retrieve Level 4 principles ->
     retrieve supporting/conflicting/similar Level 2 cases ->
     online applicability check -> conditional answer -> citations
```

## Level 1 — Episode Semantic Map

Purpose:

- understand the full episode
- identify topic threads and relationships between transcript regions
- connect interrupted and resumed discussions
- identify Q&A that continues or expands earlier reasoning
- route relevant transcript evidence into Level 2 extraction

Level 1 is semantic navigation, not final knowledge.

## Level 2 — Reasoning Records / Cases

Purpose:

- return to normalized transcript evidence
- extract claims, observations, reasoning steps, assumptions, conditions, risks, uncertainty, exceptions, contradictions, preferred actions, decision preferences, and speaker behavior
- preserve episode IDs, timestamps, source segment IDs, compact evidence references, and provenance

Level 2 is the primary historical case and evidence layer.

## Level 3 — Episode-Level Provisional Principles

Purpose:

- examine Level 2 records from one episode
- identify episode-local recurring decision patterns
- produce provisional, evidence-linked principles

Supported strategies:

- `principle_extraction`: extract local principles when the episode contains repeated decision patterns
- `light_principle_grouping`: group related records without forcing an episode-level thesis
- `bypass`: skip Level 3 when Level 2 cases are already the useful output

Level 3 principles are provisional. They must not be described as confirmed long-term speaker principles.

## Level 4 — Cross-Episode Decision Principles

Purpose:

- compare Level 2 cases and Level 3 provisional principles across episodes
- identify recurring decision preferences
- preserve conditions, exceptions, conflicts, counterexamples, evolution, independent episode coverage, and confidence
- maintain provenance back to Level 2 records

Level 4 is a provisional and revisable speaker decision model.

## Authority Relationship

```text
Principles determine how to think.
Cases determine whether the reasoning has evidence.
The new scenario determines whether the principle applies.
The online LLM performs the applicability check and synthesis.
```

A principle is an abstraction derived from its source records. It must not be counted as an additional independent piece of evidence.

## Planned Mentor Comparison

Legacy baseline:

```text
query
  -> retrieve Level 2 cases
  -> LLM synthesis
```

New reasoning path:

```text
query
  -> retrieve Level 4 principles
  -> retrieve Level 2 supporting/conflicting/similar cases
  -> applicability check
  -> LLM conditional synthesis
```

Future evaluation should compare:

- direct case copying versus new synthesis
- condition awareness
- exception handling
- missing-information detection
- conflict handling
- citation traceability
- incorrect generalization of speaker principles
- answer stability

This comparison has not been run yet.
