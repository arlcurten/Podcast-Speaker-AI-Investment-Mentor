# Archived Implementations

These are frozen comparison baselines, not the active development tree.
The exact pre-move version is tagged `archive/pre-rebuild-2026-11-05`
(commit `1919bbc`). Only relocation fixes have been applied to these copies.
Complete data and reports remain in the external private repository.

| Directory | Preserved responsibility |
| --- | --- |
| `legacy_segment_poc/` | Original acquisition, transcription, normalization, RunPod batch workflow |
| `semantic_analysis_poc/` | Qwen extraction and Level 1-4 knowledge baseline |
| `legacy_mentor_rag_poc/` | Case-only RAG baseline |
| `mentor_agent/` | First principle-assisted Mentor runtime |

From the public repository root:

```bash
python3 -m legacy.legacy_segment_poc --help
python3 legacy/semantic_analysis_poc/main.py --help
python3 legacy/legacy_mentor_rag_poc/main.py --help
python3 -m legacy.mentor_agent --help
python3 -m pytest -q legacy
```

Data-dependent commands retain the existing environment-configured private
paths. No private dataset paths are renamed by the source-tree migration.
Historical instructions/reports describe the old implementation; use the root
README for the active plan. New production modules must not import `legacy`.
Promote only reviewed, tested utilities into the active namespace.
