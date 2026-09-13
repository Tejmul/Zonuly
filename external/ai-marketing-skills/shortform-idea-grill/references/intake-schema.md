# Intake schema

Use this structure internally or save it as `intake.json`:

```json
{
  "status": "ready|needs_intake",
  "primary_outcome": "ordered business or audience outcome",
  "target_viewer": "specific role and relevant moment",
  "active_work": ["current build, experiment, or operating lesson"],
  "owned_proof": [
    {
      "claim": "defensible claim",
      "artifact": "result, screen, data, story, or demonstration",
      "evidence_status": "verified|available|needs_verification"
    }
  ],
  "claim_boundaries": ["unsupported or future-looking claim"],
  "cta_map": [
    {"keyword": "KEYWORD", "destination": "offer or URL", "topic_fit": "when to use it"}
  ],
  "channels": ["instagram"],
  "production_constraints": ["count, time, screens, or filming format"],
  "next_question": "single blocking question when status is needs_intake",
  "recommended_answer": "best current recommendation"
}
```

Blocking fields are `primary_outcome`, `target_viewer`, `owned_proof`, `claim_boundaries`, and `cta_map`.
