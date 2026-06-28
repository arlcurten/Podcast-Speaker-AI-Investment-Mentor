# Podcast Speaker AI Investment Mentor

This project explores how to build an AI Investment Mentor from historical Gooaye 股癌 podcast episodes.

The goal is not automated trading or order placement. The long-term goal is to preserve and model how the speaker reasons about markets, industries, companies, risk, uncertainty, exceptions, position changes, and behavior across cases.

## Quick Overview

Current status:

- The active implementation is a Local POC in `gooaye_mentor_poc/`.
- EP674 has been ingested, downloaded, validated, transcribed, normalized, merged, and prepared for human review.
- The project is still in transcript validation and representation design.
- RAG, vector database, full Mentor Agent, fine-tuning, and cloud batch processing are not implemented yet.

Start here:

- `AGENTS.md`: project-wide agent rules.
- `gooaye_mentor_poc/README.md`: Local POC overview and commands.
- `docs/terminology.tsv`: shared terminology table for preserved terms, aliases, and corrections.
- `docs/future-improvements.md`: deferred optimization ideas.

## Project Flow

```text
[done]    RSS / Apple lookup
[done]    Episode manifest
[done]    EP674 MP3 audio + metadata
[done]    Raw ASR transcript
[done]    Traditional Chinese normalization
[done]    Deterministic merged utterances
[current] Human review of transcript representation and topic boundaries
[poc]     Minimal topic / classification / routing review
[todo]    20-episode pilot
[todo]    Topic and reasoning-case extraction
[todo]    Behavioral pattern modeling
[todo]    Retrieval-backed Mentor MVP
```

Every derived layer should remain traceable to episode, timestamp, and source segment IDs. Raw artifacts should not be overwritten by normalized, merged, corrected, or classified outputs.

Near-term TODO:

- Finish reviewing `gooaye_mentor_poc/reports/EP674_human_review.md`.
- Add confirmed terminology corrections to `docs/terminology.tsv`.
- Keep classification and segmentation improvements in `docs/future-improvements.md` until they are worth implementing.
- Decide whether transcript quality is good enough for a broader pilot.

## Repository Hierarchy

```text
Podcast-Speaker-AI-Investment-Mentor/
├── AGENTS.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── cloud-processing-plan.md
│   ├── future-improvements.md
│   └── terminology.tsv
└── gooaye_mentor_poc/
    ├── AGENTS.md
    ├── README.md
    ├── main.py
    ├── modules/
    ├── docs/
    ├── config/
    ├── data/
    └── reports/
```

## How To Use This Project

For current POC work:

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 main.py validate-merge --episode EP674 --configuration large-v3-turbo
python3 main.py normalize --episode EP674 --configuration large-v3-turbo
python3 main.py topic-review-poc --episode EP674 --configuration large-v3-turbo
```

For human review, open:

```text
gooaye_mentor_poc/reports/EP674_human_review.md
```

## More Detail

- Architecture: `docs/architecture.md`
- Data model: `docs/data-model.md`
- Cloud plan: `docs/cloud-processing-plan.md`
- Future improvements: `docs/future-improvements.md`
- Terminology: `docs/terminology.tsv`
- POC details: `gooaye_mentor_poc/docs/local-transcription-poc.md`
- POC troubleshooting: `gooaye_mentor_poc/docs/troubleshooting.md`
