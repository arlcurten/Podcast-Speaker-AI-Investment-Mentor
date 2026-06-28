# Project Roadmap

## 1. Local POC

Goal: prove that RSS ingestion, audio acquisition, audio validation, and one-episode transcription can run locally.

Current status: in progress, with EP674 artifacts generated.

## 2. Manual Transcript Review

Goal: determine whether the transcript is accurate enough for downstream classification and reasoning extraction.

Review should focus on:

- company names
- tickers
- financial terms
- numbers and percentages
- negation
- bullish/bearish direction
- reasoning causality
- timestamp usability

## 3. 20-Episode RunPod Pilot

Goal: validate throughput, failure handling, cost assumptions, and transcript quality across a small sample before full batch processing.

Gate: complete basic manual review first.

## 4. Full Transcription Batch

Goal: process the full historical corpus with one episode as one independent job.

Each completed episode should checkpoint transcripts, metadata, logs, and audio hashes to persistent storage.

## 5. Topic And Reasoning Extraction

Goal: transform reviewed transcript layers into topic segments and reasoning cases while preserving source timestamps and IDs.

This phase should not overwrite raw ASR or merged utterance layers.

## 6. Mentor MVP

Goal: build a retrieval-backed assistant that can answer with source-grounded historical patterns.

The MVP must distinguish historical commentary from current advice.

## 7. Behavioral / Personality Modeling

Goal: model cross-case behavior such as risk posture, uncertainty handling, position adjustment logic, and conditions for changing views.

The model should represent uncertainty and exceptions rather than hard-coding a fixed persona.
