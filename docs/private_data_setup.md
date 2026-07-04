# Private Data Setup

This public repository should contain source code, schemas, prompts, public documentation, and small safe examples. Full or source-derived podcast artifacts should live in the sibling private repository:

```text
projects/
├── Podcast-Speaker-AI-Investment-Mentor/
└── consultant-ai-agent-private/
```

## Environment Variables

Create a local `.env` in the public repository or export these values in your shell:

```bash
PODCAST_DATA_ROOT=/absolute/path/to/consultant-ai-agent-private/shared_data
PIPELINE_DATA_ROOT=/absolute/path/to/consultant-ai-agent-private/pipeline_data
PRIVATE_DOCS_ROOT=/absolute/path/to/consultant-ai-agent-private/private_docs
```

Do not commit `.env`.

## What Belongs In Private Data

- audio
- raw ASR transcripts
- normalized full transcripts
- merged full transcripts
- full Level 1-4 semantic outputs
- model inputs and outputs
- model comparison traces
- private reports and review notes
- retrieval indexes and runtime outputs

## Public Repo Behavior

Code paths that require private artifacts should read from the environment variables above. If private data is missing, CLI commands should fail with a clear missing-data message instead of a long stack trace.

Commands that only inspect source code, show help, run syntax checks, or validate schemas should work without private data.

## Publication Policy

Do not copy private transcript text, full model outputs, audio URLs with sensitive tokens, internal review notes, or private experiment details into public fixtures, reports, logs, or commits. Use schema-compatible synthetic examples or explicitly reviewed redacted examples for public demos.
