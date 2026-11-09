# Knowledge Layers

Raw transcripts preserve source text, segment IDs and timestamps. Normalized
views improve readability while keeping source mappings and separate annotations.

| Layer | Purpose |
| --- | --- |
| L1: Episode map | Navigate topics, resumed discussions and question/answer relationships |
| L2: Cases | Preserve claims, reasoning, actions, conditions, risks, uncertainty and source evidence |
| L3: Episode principles | Form provisional episode-local patterns where evidence supports them |
| L4: Cross-episode principles | Compare patterns across episodes, retaining exceptions, disagreement and change |

```mermaid
flowchart LR
    source["Transcript evidence"] --> l1["L1: navigation"]
    source --> l2["L2: cases"]
    l1 --> l2
    l2 --> l3["L3: provisional episode patterns"]
    l2 --> l4["L4: cross-episode abstractions"]
    l3 --> l4
```

Principles are abstractions, not additional independent evidence. Generated
artifacts remain separate from accepted releases. Structural validation does
not establish semantic accuracy. Preserve previous versions when making changes.

See [knowledge usage and contracts](../knowledge/README.md) and
[private data setup](private_data_setup.md).
