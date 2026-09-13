# Ideas JSON schema

Save an array as `ideas.json`. Each record follows this shape:

```json
{
  "idea_id": "stable-kebab-case-id",
  "topic": "concise topic",
  "hook": "five-second overlay",
  "virality_score": 9.2,
  "three_second_hook_score": 9.5,
  "payoff_confidence": 8.8,
  "yap_bullets": ["context or stakes", "mechanism or tactic", "result or application"],
  "complete_payoff": "proof, source link, and claim boundary",
  "proof_status": "verified|available|needs_verification|thesis",
  "cta_keyword": "KEYWORD",
  "cta_destination": "offer or URL",
  "source_basis": [
    {"label": "real supporting artifact", "url": "https://example.com/source"}
  ],
  "claim_caveat": "what must not be overstated"
}
```

The scoring script adds `priority_score` and `rank`.
