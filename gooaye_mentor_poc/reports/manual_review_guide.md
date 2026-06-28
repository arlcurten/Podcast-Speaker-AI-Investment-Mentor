# EP674 Manual Review Guide

No human listening judgment has been completed yet. This guide describes how to perform the review using existing artifacts.

## Inputs

- Review package: `reports/EP674_manual_review_package.md`
- Review CSV: `data/evaluation/manual_review.csv`
- Review windows JSON: `data/evaluation/EP674/review_windows.json`
- Review clips: `data/evaluation/EP674/review_clips/`
- Source audio: `data/audio/EP674/episode.mp3`
- Raw transcript: `data/transcripts/EP674/large-v3-turbo/transcript.json`
- Merged transcript: `data/transcripts/EP674/large-v3-turbo/merged_transcript.json`

## Review Windows

The current package covers:

- `opening_general`: early conversational content after intro/ad lead-in.
- `middle_analysis`: middle-region market or product analysis candidate.
- `english_terms_candidate`: keyword-heavy section with English/company/financial terms.
- `late_qa_candidate`: later QA candidate; verify by listening.

Do not reselect windows unless the user asks. The current task is to listen and record issues.

## Playback

From the Local POC root:

```bash
ffplay data/evaluation/EP674/review_clips/opening_general_180_420.mp3
ffplay data/evaluation/EP674/review_clips/middle_analysis_1431_1731.mp3
ffplay data/evaluation/EP674/review_clips/english_terms_candidate_1896_2136.mp3
ffplay data/evaluation/EP674/review_clips/late_qa_candidate_2480_2720.mp3
```

If a clip needs source context:

```bash
ffplay -ss 180.00 -to 420.00 data/audio/EP674/episode.mp3
```

## What To Record

Do not try to correct the whole transcript word-for-word. Prioritize errors that affect investment meaning, entity identity, financial logic, or timestamp usability.

For each issue, add or edit a row in `data/evaluation/manual_review.csv`:

- `expected_text`: what you hear in the audio, only for the relevant phrase.
- `transcribed_text`: the ASR text being judged.
- `error_category`: one of the categories below.
- `severity`: `minor`, `moderate`, or `critical`.
- `semantic_impact`: one of the values below.
- `notes`: short explanation and uncertainty.

## Error Categories

- Chinese substitution
- English company-name error
- Ticker error
- Financial terminology error
- Number error
- Negation error
- Missing words
- Hallucination
- Repetition
- Segmentation
- Timestamp drift
- Other

## Severity

Minor:

- punctuation issue
- harmless homophone
- slight sentence boundary issue
- company name remains obvious from context

Moderate:

- entity name is wrong but recoverable from context
- small amount of missing content
- financial term is wrong but does not change the overall conclusion
- timestamp drift exists but the source is still easy to locate

Critical:

- company A is transcribed as company B
- ticker is wrong
- number or percentage is wrong
- negation is lost
- bullish/bearish or trading meaning is reversed
- causal reasoning is changed
- hallucinated statement appears

## Semantic Impact

Use:

- `none`
- `low`
- `changes_entity`
- `changes_number`
- `changes_sentiment`
- `changes_reasoning`
- `unknown`

Until this review is actually completed, keep transcript quality as Conditional/Pending.
