# /eval — AI Output Evaluation Skill for Claude Code

Automated evaluation suite for any AI-powered feature. Works with chat agents, content generators, classifiers, summarizers, and any system that takes input and produces AI output.

## What it does

1. Asks you what "good" looks like for your AI feature
2. Generates test scenarios and pass/fail criteria
3. Runs the scenarios against your API
4. Scores results and detects regressions
5. Helps you fix failures

## Install

Copy the `eval/` directory into your project's `.claude/skills/`:

```bash
cp -r eval/ your-project/.claude/skills/eval/
```

Or clone and symlink:

```bash
git clone https://github.com/nichetools/ai-marketing-skills.git
ln -s ai-marketing-skills/eval your-project/.claude/skills/eval
```

## Quick start

1. In Claude Code, type `/eval`
2. Answer the questions about what your AI does and what good/bad looks like
3. The skill generates `eval.config.json` with test scenarios
4. It runs the scenarios and shows you the results
5. Fix failures, re-run, repeat

## Running evals manually

```bash
# Run all scenarios
npx tsx .claude/skills/eval/run-eval.ts

# Use a specific config
npx tsx .claude/skills/eval/run-eval.ts --config my-eval.config.json

# See full AI outputs
npx tsx .claude/skills/eval/run-eval.ts --verbose

# Save current results as baseline
npx tsx .claude/skills/eval/run-eval.ts --baseline
```

## Supported AI types

| Type | How it works |
|------|-------------|
| **Chat agent** | Sends multi-turn messages, evaluates full conversation |
| **Content generator** | Sends single input, evaluates output quality |
| **Classifier** | Sends inputs with known labels, checks accuracy |
| **Summarizer** | Sends documents, checks summary quality |
| **Custom** | Any input/output API you define |

## Criterion types

| Type | What it checks |
|------|---------------|
| `contains` | Output includes a string (case-insensitive) |
| `not_contains` | Output does NOT include a string |
| `regex` | Output matches a regex pattern |
| `max_length` | Output is under N characters |
| `min_length` | Output is at least N characters |
| `max_sentences` | Output is under N sentences |
| `response_time` | API responds within N milliseconds |
| `json_valid` | Output is valid JSON |
| `json_field_equals` | A JSON field equals an expected value |
| `no_hallucination` | Output doesn't contain claims not in the input |
| `preserves_names` | Key names from input appear in output |
| `preserves_numbers` | Key numbers from input appear in output |

## Config format

```json
{
  "name": "My AI Feature Eval",
  "type": "chat",
  "endpoint": "https://my-api.com/chat",
  "method": "POST",
  "headers": { "Content-Type": "application/json" },
  "request_template": {
    "message": "{{message}}",
    "history": "{{history}}"
  },
  "response_field": "response",
  "threshold": 80,
  "scenarios": [
    {
      "name": "basic_greeting",
      "messages": ["Hello"],
      "criteria": [
        { "type": "contains", "value": "help", "description": "Offers help" }
      ]
    }
  ]
}
```

See `eval.config.example.json` for a full example.

## Regression detection

Save a baseline after your first passing run:

```bash
npx tsx .claude/skills/eval/run-eval.ts --baseline
```

Future runs automatically compare against the baseline and flag:
- Score drops
- Individual scenarios that got worse
- New failures that didn't exist before

## When to run evals

- After every prompt or model change
- Before deploying to production
- Weekly to catch quality drift from content/data changes
- When onboarding a new AI feature

## Adding custom criteria

Edit `run-eval.ts` and add a case to the `evaluateCriterion` function:

```typescript
case 'my_custom_check':
  // Your logic here
  return someCondition;
```

Then use it in your config:

```json
{ "type": "my_custom_check", "value": "whatever", "description": "My check" }
```

## Philosophy

> "Don't ship prompts without evals. It's the AI equivalent of shipping code without tests."

Manual testing is important for tone and feel. But automated evals catch regressions, enforce quality standards, and give you a score to track over time. Use both.
