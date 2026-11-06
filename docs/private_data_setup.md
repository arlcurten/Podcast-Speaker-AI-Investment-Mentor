# Private Data Setup

This public repository should contain source code, schemas, prompts, public documentation, and curated public derivatives only. The private sibling repository is the canonical source of truth for complete or source-derived podcast artifacts:

```text
projects/
├── Podcast-Speaker-AI-Investment-Mentor/
└── AI-Investment-Mentor-private/
```

## Environment Variables

Create a local `.env` in the public repository or export these values in your shell:

```bash
PODCAST_DATA_ROOT=/absolute/path/to/AI-Investment-Mentor-private/shared_data
PIPELINE_DATA_ROOT=/absolute/path/to/AI-Investment-Mentor-private/pipeline_data
PRIVATE_DOCS_ROOT=/absolute/path/to/AI-Investment-Mentor-private/private_docs
```

Do not commit `.env`.

## Rebuild Paths

The active `transcription/` package loads the three data-root variables from
the root `.env` without overwriting existing environment values. It rejects
public-tree sources/destinations, including symlinks into the public tree.
Batch defaults use `PIPELINE_DATA_ROOT/transcription/batches/<batch_id>` and
`PODCAST_DATA_ROOT/transcripts/raw/<batch_id>/<episode_id>` to keep new runs
separate from old canonical transcripts. `prepare --output` requires a new
external path. No source-tree rename changes existing private artifact paths.

Archived implementations still read their original private layouts. The
versioned formal transcript/checkpoint format is not silently substituted for
legacy artifacts. Source-bound quality reviews also belong in private storage.

`knowledge/` uses explicit private raw/normalized/review paths and a versioned
run directory under `PIPELINE_DATA_ROOT/knowledge`. Only reviewed artifacts can
be published as an immutable release descriptor. `MENTOR_RELEASE` selects that
descriptor for `mentor_agent/`; there is no automatic fallback to samples or
archived output. Root `.env` also supports `KNOWLEDGE_MODEL_ID`,
`MENTOR_MODEL_ID` and the local `OPENROUTER_API_KEY`. No API credential is needed
on the transcription GPU worker.

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
- internal documents, experiment notes, and private know-how

Public samples do not replace private data and must not be used as production pipeline input.

## What Can Be Public

- source code
- schemas
- prompts
- non-sensitive configs
- public documentation
- synthetic fixtures
- manually redacted real examples
- aggregate reports that do not expose long transcript text or full model responses

Public derivatives should be recorded in `docs/public_artifacts.md` and linked back to private manifest entries.

## Public Repo Behavior

Code paths that require private artifacts should read from the environment variables above. If private data is missing, CLI commands should fail with a clear missing-data message instead of a long stack trace.

Commands that only inspect source code, show help, run syntax checks, or validate schemas should work without private data.

## Publication Policy

Do not copy private transcript text, full model outputs, audio URLs with sensitive tokens, internal review notes, or private experiment details into public fixtures, reports, logs, or commits. Use schema-compatible synthetic examples or explicitly reviewed redacted examples for public demos.

Do not create `private/`, `private_data/`, `internal/`, or `full_outputs/` directories inside the public repo. If an artifact is uncertain, keep it private and document it as unresolved.

## Public Artifact Workflow

1. Produce or preserve the complete artifact in the private repo.
2. Register the canonical artifact in the private manifest.
3. Decide whether a public derivative has portfolio, README, interview, architecture, or testing value.
4. Create a synthetic, redacted, summarized, or aggregated derivative.
5. Check transcript exposure and sensitive content.
6. Place the derivative under `sample_outputs/` or `reports/public/`.
7. Update `docs/public_artifacts.md`.
8. Update the private manifest `public_derivatives` field.
