import { readFile, appendFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { parse } from 'yaml';
import { ChangeSchema } from 'devops-agent/core';

// v0.1.1 policy message: only this exact advisory can coexist with a passing
// automated-evidence job. The original release assessment is never rewritten.
export const mockWarning = 'MOCK agent output was used; real review and test planning are still required';

export function evaluateReadiness(run, catalog, cliExitCode, expected = {}) {
  const parsed = ChangeSchema.safeParse(run?.change);
  if (!parsed.success) return { passed: false, releaseStatus: 'unknown', errors: ['Invalid or absent Change report'], tests: [] };
  const change = parsed.data;
  const assessment = change.releaseAssessment;
  const errors = [];
  const plan = change.testPlan?.tests ?? [];
  if (run.lastError) errors.push(`Agent error: ${run.lastError}`);
  if (run.skippedStages?.length) errors.push('A lifecycle stage was skipped');
  if (expected.source && change.sourceRevision !== expected.source) errors.push('Source revision mismatch');
  if (expected.target && change.targetRevision !== expected.target) errors.push('Target revision mismatch');
  if (!run.review) errors.push('Structured review is absent');
  if (!plan.length) errors.push('Validation plan is empty');
  const mandatory = catalog.filter(test => test.required && test.always);
  if (!mandatory.length) errors.push('No required baseline tests are configured');
  for (const registered of mandatory) {
    const planned = plan.find(test => test.id === registered.id);
    if (!planned?.required || planned.command !== registered.command || planned.cwd !== (registered.cwd ?? '.')) {
      errors.push(`Required baseline omitted or altered: ${registered.id}`);
    }
  }
  const tests = plan.map(test => {
    const evidence = change.evidence.filter(item => item.type === 'test' && item.testId === test.id
      && item.sourceRevision === change.sourceRevision && item.targetRevision === change.targetRevision
      && item.command === test.command && item.cwd === test.cwd).at(-1);
    const passed = Boolean(test.command) && evidence?.status === 'passed' && evidence.exitCode === 0
      && !evidence.timedOut && !evidence.outputLimitExceeded;
    if (test.required && !passed) errors.push(`Required evidence missing, failed or manual: ${test.id}`);
    return { id: test.id, required: test.required, status: passed ? 'passed' : (evidence?.status ?? 'missing') };
  });
  if (!assessment) errors.push('Release assessment is absent');
  else {
    errors.push(...assessment.blockers);
    const mockUsed = Object.values(run.executors ?? {}).includes('mock');
    const isMockAdvisory = assessment.status === 'needs-review' && mockUsed && cliExitCode === 3
      && assessment.warnings.length === 1 && assessment.warnings[0] === mockWarning;
    const isReady = assessment.status === 'ready' && !mockUsed && cliExitCode === 0 && !assessment.warnings.length;
    if (!isMockAdvisory && !isReady) errors.push('Assessment is not ready or the sole known mock advisory');
  }
  return { passed: errors.length === 0, releaseStatus: assessment?.status ?? 'unknown', errors, tests };
}

async function main() {
  const [reportPath, rawExitCode] = process.argv.slice(2);
  if (!reportPath || !/^\d+$/.test(rawExitCode ?? '')) throw new Error('Usage: node .devops-agent/ci-readiness.mjs REPORT CLI_EXIT_CODE');
  const run = JSON.parse(await readFile(reportPath, 'utf8'));
  const config = parse(await readFile('.devops-agent/config.yaml', 'utf8'));
  const outcome = evaluateReadiness(run, config.testing.commands, Number(rawExitCode), {
    source: process.env.EXPECTED_SOURCE, target: process.env.EXPECTED_TARGET,
  });
  await writeFile('.artifacts/ci-outcome.json', JSON.stringify(outcome, null, 2) + '\n');
  const escape = value => String(value).replace(/[<>|\r\n]/g, ' ');
  const summary = [
    '## DevOps Agent evidence',
    `Automated checks: **${outcome.passed ? 'passed' : 'failed'}**. Release assessment: **${outcome.releaseStatus}**.`,
    '',
    'The readiness job verifies automated evidence. A green job is not release approval.',
    ...(Object.values(run.executors ?? {}).includes('mock') ? ['**MOCK review/planning: no semantic AI review was performed. Real review remains outstanding.**'] : []),
    ...(process.env.BASELINE_ONLY === 'true' ? ['Initial/history-unavailable baseline: HEAD compared with itself; the full required suite ran, but no change diff was reviewed.'] : []),
    '', '| Check | Required | Evidence |', '| --- | --- | --- |',
    ...outcome.tests.map(test => `| ${escape(test.id)} | ${test.required} | ${escape(test.status)} |`),
    '', ...outcome.errors.map(error => `- ${escape(error)}`), '',
  ].join('\n');
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, summary);
  process.stdout.write(summary);
  process.exitCode = outcome.passed ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main().catch(error => { process.stderr.write(String(error) + '\n'); process.exitCode = 1; });
}
