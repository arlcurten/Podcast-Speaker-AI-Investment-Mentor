# Mentor Flow

The current runtime combines local evidence retrieval with a configured remote
model. Local files do not imply local model inference: selected context is sent
to the provider. See [runtime setup](../mentor_agent/README.md).

```mermaid
flowchart TD
    question["Question"] --> analysis["Scenario and search analysis"]
    analysis --> evidence["Cases and candidate principles"]
    evidence --> applicability["Compare conditions, exceptions and conflicting evidence"]
    applicability --> route{"Route"}
    route --> assisted["principle_assisted"]
    route --> cases["case_only"]
    route --> insufficient["insufficient_evidence"]
    assisted --> answer["Conditional synthesis and citations"]
    cases --> answer
    insufficient --> limits["Evidence limits and missing information"]
    answer --> guards["Citation and response validation"]
```

Principles organize reasoning; source cases support historical attribution.
Citation checks validate links, not the truth of an interpretation. The system
is for learning and discussion, not impersonation or automated trading.
Behavioral-profile extraction and integration are separate development steps.
