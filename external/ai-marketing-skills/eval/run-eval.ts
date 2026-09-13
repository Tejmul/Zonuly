#!/usr/bin/env npx tsx

/**
 * Universal AI Eval Runner
 *
 * Evaluates any AI-powered API against test scenarios with pass/fail criteria.
 * Works with chat agents (multi-turn), content generators, classifiers, and summarizers.
 *
 * Usage:
 *   npx tsx .claude/skills/eval/run-eval.ts
 *   npx tsx .claude/skills/eval/run-eval.ts --config path/to/config.json
 *   npx tsx .claude/skills/eval/run-eval.ts --verbose
 *   npx tsx .claude/skills/eval/run-eval.ts --baseline
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { resolve } from 'path';

// --- Types ---

interface Criterion {
  type: string;
  value?: string | number;
  field?: string;
  description: string;
}

interface Scenario {
  name: string;
  messages?: string[];       // For chat type (multi-turn)
  input?: string | object;   // For single-turn types
  criteria: Criterion[];
  tenant?: string;
}

interface EvalConfig {
  name: string;
  type: 'chat' | 'content' | 'classifier' | 'summarizer' | 'custom';
  endpoint: string;
  method?: string;
  headers?: Record<string, string>;
  request_template: Record<string, any>;
  response_field: string;
  session_id_field?: string;
  defaults?: Record<string, any>;
  threshold: number;
  good_behaviors?: string[];
  bad_behaviors?: string[];
  scenarios: Scenario[];
}

interface ScenarioResult {
  name: string;
  criteria_results: { criterion: Criterion; passed: boolean; actual?: string }[];
  passed: number;
  total: number;
  conversation?: { role: string; text: string }[];
  response_times?: number[];
  error?: string;
}

// --- Args ---

const args = process.argv.slice(2);
const configPath = args.includes('--config')
  ? args[args.indexOf('--config') + 1]
  : 'eval.config.json';
const verbose = args.includes('--verbose');
const saveBaseline = args.includes('--baseline');

// --- Load config ---

const fullConfigPath = resolve(process.cwd(), configPath);
if (!existsSync(fullConfigPath)) {
  console.error(`Config not found: ${fullConfigPath}`);
  console.error('Create one with /eval or copy from .claude/skills/eval/eval.config.example.json');
  process.exit(1);
}

const config: EvalConfig = JSON.parse(readFileSync(fullConfigPath, 'utf-8'));
console.log(`\n${'='.join ? '=' : '='}${'='.repeat(79)}`);
console.log(`EVAL: ${config.name}`);
console.log(`Type: ${config.type} | Scenarios: ${config.scenarios.length} | Threshold: ${config.threshold}%`);
console.log(`Endpoint: ${config.endpoint}`);
console.log('='.repeat(80));

// --- Helpers ---

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function countSentences(text: string): number {
  return text.split(/[.!?]+/).filter((s) => s.trim().length > 0).length;
}

function interpolate(template: any, vars: Record<string, any>): any {
  if (typeof template === 'string') {
    return template.replace(/\{\{(\w+)\}\}/g, (_, key) => {
      const val = vars[key];
      return val !== undefined ? (typeof val === 'string' ? val : JSON.stringify(val)) : '';
    });
  }
  if (Array.isArray(template)) {
    return template.map((item) => interpolate(item, vars));
  }
  if (typeof template === 'object' && template !== null) {
    const result: Record<string, any> = {};
    for (const [k, v] of Object.entries(template)) {
      result[k] = interpolate(v, vars);
    }
    return result;
  }
  return template;
}

function evaluateCriterion(
  criterion: Criterion,
  fullOutput: string,
  responseTimeMs?: number,
  input?: string
): boolean {
  const lower = fullOutput.toLowerCase();
  const val = criterion.value;

  switch (criterion.type) {
    case 'contains':
      return lower.includes(String(val).toLowerCase());

    case 'not_contains':
      return !lower.includes(String(val).toLowerCase());

    case 'regex':
      return new RegExp(String(val), 'i').test(fullOutput);

    case 'max_length':
      return fullOutput.length <= Number(val);

    case 'min_length':
      return fullOutput.length >= Number(val);

    case 'max_sentences':
      return countSentences(fullOutput) <= Number(val);

    case 'response_time':
      return (responseTimeMs || 0) <= Number(val);

    case 'json_valid':
      try { JSON.parse(fullOutput); return true; } catch { return false; }

    case 'json_field_equals':
      try {
        const parsed = JSON.parse(fullOutput);
        return parsed[criterion.field || ''] === val;
      } catch { return false; }

    case 'no_hallucination':
      // Basic check: output shouldn't contain proper nouns not in the input
      // This is a heuristic, not perfect
      if (!input) return true;
      const outputNames = fullOutput.match(/[A-Z][a-z]+(?:\s[A-Z][a-z]+)*/g) || [];
      const inputLower = input.toLowerCase();
      const hallucinated = outputNames.filter(
        (name) => !inputLower.includes(name.toLowerCase()) && name.length > 3
      );
      return hallucinated.length <= 2; // Allow some tolerance

    case 'preserves_names':
      if (!input) return true;
      const names = input.match(/[A-Z][a-z]+(?:\s[A-Z][a-z]+)+/g) || [];
      return names.length === 0 || names.some((n) => fullOutput.includes(n));

    case 'preserves_numbers':
      if (!input) return true;
      const numbers = input.match(/\d[\d,]+(?:\.\d+)?/g) || [];
      if (numbers.length === 0) return true;
      return numbers.some((n) => fullOutput.includes(n));

    default:
      console.warn(`Unknown criterion type: ${criterion.type}`);
      return true;
  }
}

// --- Run scenarios ---

async function runChatScenario(scenario: Scenario): Promise<ScenarioResult> {
  const messages = scenario.messages || [];
  const conversation: { role: string; text: string }[] = [];
  const responseTimes: number[] = [];
  let sessionId: string | null = null;
  const history: { role: string; text: string }[] = [];

  for (const userMessage of messages) {
    const vars: Record<string, any> = {
      message: userMessage,
      history: history,
      session_id: sessionId || '',
      ...(config.defaults || {}),
      ...(scenario.tenant ? { tenant: scenario.tenant } : {}),
    };

    const body = interpolate(config.request_template, vars);
    // Ensure history is passed as array, not string
    if (body.history === '[]' || body.history === '') {
      body.history = history.map((h) => ({ role: h.role, text: h.text }));
    }
    if (sessionId && config.session_id_field) {
      body[config.session_id_field] = sessionId;
    }

    const start = Date.now();
    try {
      const res = await fetch(config.endpoint, {
        method: config.method || 'POST',
        headers: { 'Content-Type': 'application/json', ...(config.headers || {}) },
        body: JSON.stringify(body),
      });

      const elapsed = Date.now() - start;
      responseTimes.push(elapsed);

      if (!res.ok) {
        return {
          name: scenario.name,
          criteria_results: [],
          passed: 0,
          total: scenario.criteria.length,
          error: `API returned ${res.status}`,
        };
      }

      const data = await res.json();
      const responseText = data[config.response_field] || '';
      if (config.session_id_field && data[config.session_id_field]) {
        sessionId = data[config.session_id_field];
      }

      conversation.push({ role: 'user', text: userMessage });
      conversation.push({ role: 'agent', text: responseText });
      history.push({ role: 'user', text: userMessage });
      history.push({ role: 'model', text: responseText });
    } catch (err: any) {
      return {
        name: scenario.name,
        criteria_results: [],
        passed: 0,
        total: scenario.criteria.length,
        error: err.message,
      };
    }

    await sleep(1000);
  }

  // Evaluate criteria against full conversation
  const fullOutput = conversation
    .filter((c) => c.role === 'agent')
    .map((c) => c.text)
    .join('\n');

  const avgResponseTime =
    responseTimes.length > 0
      ? responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length
      : undefined;

  const criteriaResults = scenario.criteria.map((criterion) => ({
    criterion,
    passed: evaluateCriterion(criterion, fullOutput, avgResponseTime),
  }));

  return {
    name: scenario.name,
    criteria_results: criteriaResults,
    passed: criteriaResults.filter((r) => r.passed).length,
    total: criteriaResults.length,
    conversation,
    response_times: responseTimes,
  };
}

async function runSingleTurnScenario(scenario: Scenario): Promise<ScenarioResult> {
  const input = typeof scenario.input === 'string' ? scenario.input : JSON.stringify(scenario.input);

  const vars: Record<string, any> = {
    input,
    message: input,
    ...(config.defaults || {}),
    ...(scenario.tenant ? { tenant: scenario.tenant } : {}),
  };

  const body = interpolate(config.request_template, vars);

  const start = Date.now();
  try {
    const res = await fetch(config.endpoint, {
      method: config.method || 'POST',
      headers: { 'Content-Type': 'application/json', ...(config.headers || {}) },
      body: JSON.stringify(body),
    });

    const elapsed = Date.now() - start;

    if (!res.ok) {
      return {
        name: scenario.name,
        criteria_results: [],
        passed: 0,
        total: scenario.criteria.length,
        error: `API returned ${res.status}`,
      };
    }

    const data = await res.json();
    const responseText =
      typeof data === 'string' ? data : data[config.response_field] || JSON.stringify(data);

    const criteriaResults = scenario.criteria.map((criterion) => ({
      criterion,
      passed: evaluateCriterion(criterion, responseText, elapsed, input),
    }));

    return {
      name: scenario.name,
      criteria_results: criteriaResults,
      passed: criteriaResults.filter((r) => r.passed).length,
      total: criteriaResults.length,
      conversation: [
        { role: 'input', text: input },
        { role: 'output', text: responseText },
      ],
      response_times: [elapsed],
    };
  } catch (err: any) {
    return {
      name: scenario.name,
      criteria_results: [],
      passed: 0,
      total: scenario.criteria.length,
      error: err.message,
    };
  }
}

// --- Main ---

async function main() {
  const results: ScenarioResult[] = [];

  for (let i = 0; i < config.scenarios.length; i++) {
    const scenario = config.scenarios[i];
    process.stdout.write(`[${i + 1}/${config.scenarios.length}] Running: ${scenario.name}... `);

    let result: ScenarioResult;
    if (config.type === 'chat') {
      result = await runChatScenario(scenario);
    } else {
      result = await runSingleTurnScenario(scenario);
    }

    results.push(result);

    if (result.error) {
      console.log(`ERROR: ${result.error}`);
    } else {
      const fails = result.total - result.passed;
      console.log(
        `${result.passed}/${result.total}${fails > 0 ? ` (${fails} FAIL)` : ' (PASS)'}`
      );
    }

    if (verbose && result.conversation) {
      for (const msg of result.conversation) {
        const label = msg.role === 'user' || msg.role === 'input' ? 'USER' : 'AGENT';
        console.log(`    ${label}: ${msg.text.slice(0, 200)}${msg.text.length > 200 ? '...' : ''}`);
      }
      console.log('');
    }
  }

  // --- Summary table ---
  console.log('\n' + '='.repeat(80));
  console.log('EVAL RESULTS');
  console.log('='.repeat(80));

  // Collect all unique criterion descriptions
  const allCriteria = new Set<string>();
  for (const r of results) {
    for (const cr of r.criteria_results) {
      allCriteria.add(cr.criterion.description.slice(0, 12));
    }
  }
  const criteriaHeaders = Array.from(allCriteria);

  // Header
  const nameWidth = 30;
  const colWidth = 7;
  let header = 'Scenario'.padEnd(nameWidth);
  for (const ch of criteriaHeaders) {
    header += ch.padEnd(colWidth);
  }
  header += 'Score';
  console.log(header);
  console.log('-'.repeat(header.length));

  // Rows
  let totalPassed = 0;
  let totalCriteria = 0;

  for (const r of results) {
    let row = r.name.slice(0, nameWidth - 2).padEnd(nameWidth);
    for (const ch of criteriaHeaders) {
      const cr = r.criteria_results.find(
        (c) => c.criterion.description.slice(0, 12) === ch
      );
      if (!cr) {
        row += '-'.padEnd(colWidth);
      } else {
        row += (cr.passed ? 'OK' : 'FAIL').padEnd(colWidth);
      }
    }
    row += `${r.passed}/${r.total}`;
    console.log(row);
    totalPassed += r.passed;
    totalCriteria += r.total;
  }

  console.log('-'.repeat(header.length));
  const pct = totalCriteria > 0 ? ((totalPassed / totalCriteria) * 100).toFixed(1) : '0';
  console.log(`TOTAL: ${totalPassed}/${totalCriteria} criteria passed (${pct}%)\n`);

  // --- Baseline comparison ---
  const resultsPath = resolve(process.cwd(), 'eval-results.json');
  const baselinePath = resolve(process.cwd(), 'eval-baseline.json');

  if (existsSync(baselinePath) && !saveBaseline) {
    try {
      const baseline = JSON.parse(readFileSync(baselinePath, 'utf-8'));
      const baselinePct = baseline.score_pct || 0;
      const delta = parseFloat(pct) - baselinePct;
      console.log(`Baseline: ${baselinePct}% | Current: ${pct}% | Delta: ${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`);

      if (delta < 0) {
        console.log('WARNING: Score DROPPED from baseline. Check for regressions.');
        // Find scenarios that got worse
        for (const r of results) {
          const baseScenario = baseline.results?.find((b: any) => b.name === r.name);
          if (baseScenario && r.passed < baseScenario.passed) {
            console.log(`  REGRESSION: ${r.name} (was ${baseScenario.passed}/${baseScenario.total}, now ${r.passed}/${r.total})`);
          }
        }
      }
      console.log('');
    } catch {
      // Baseline unreadable, skip
    }
  }

  // --- Save results ---
  const output = {
    name: config.name,
    timestamp: new Date().toISOString(),
    score_pct: parseFloat(pct),
    total_passed: totalPassed,
    total_criteria: totalCriteria,
    results: results.map((r) => ({
      name: r.name,
      passed: r.passed,
      total: r.total,
      error: r.error,
      criteria: r.criteria_results.map((cr) => ({
        description: cr.criterion.description,
        type: cr.criterion.type,
        passed: cr.passed,
      })),
      conversation: r.conversation,
    })),
  };

  writeFileSync(resultsPath, JSON.stringify(output, null, 2));
  console.log(`Results written to: ${resultsPath}`);

  if (saveBaseline) {
    writeFileSync(baselinePath, JSON.stringify(output, null, 2));
    console.log(`Baseline saved to: ${baselinePath}`);
  }

  // --- Threshold check ---
  if (parseFloat(pct) < config.threshold) {
    console.log(`\nFAIL: Score ${pct}% is below threshold ${config.threshold}%`);
    process.exit(1);
  } else {
    console.log(`\nPASS: Score ${pct}% meets threshold ${config.threshold}%`);
  }
}

main().catch((err) => {
  console.error('Eval runner failed:', err);
  process.exit(1);
});
