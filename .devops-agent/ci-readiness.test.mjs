import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateReadiness, mockWarning } from './ci-readiness.mjs';

const catalog = [{ id: 'build', command: 'pnpm build', required: true, always: true }];
const fixture = () => ({
  review: { summary: 'MOCK', risks: [], findings: [] }, executors: { review: 'mock', testPlanning: 'mock' }, skippedStages: [],
  change: {
    id: 'test', repository: { provider: 'local-git', name: 'test', root: '/test' },
    sourceRevision: 'head-sha', targetRevision: 'base-sha', files: [], status: 'ASSESSED', risks: [], findings: [],
    testPlan: { rationale: 'baseline', tests: [{ id: 'build', type: 'custom', command: 'pnpm build', cwd: '.', required: true, description: 'Build', reason: 'baseline' }] },
    evidence: [{ id: 'evidence', type: 'test', source: 'deterministic-process', testId: 'build', command: 'pnpm build', cwd: '.', status: 'passed', summary: 'done', exitCode: 0, sourceRevision: 'head-sha', targetRevision: 'base-sha' }],
    releaseAssessment: { status: 'needs-review', summary: 'Mock needs review', blockers: [], warnings: [mockWarning] },
  },
});

test('only known mock advisory permits automated success; release remains needs-review', () => {
  const result = evaluateReadiness(fixture(), catalog, 3, { source: 'head-sha', target: 'base-sha' });
  assert.equal(result.passed, true);
  assert.equal(result.releaseStatus, 'needs-review');
});

test('required manual validation and failed/stale evidence cannot become green', () => {
  for (const mutation of [
    run => { run.change.evidence = []; },
    run => { run.change.evidence[0].sourceRevision = 'old-sha'; },
    run => { run.change.evidence[0].command = 'pnpm ignored'; },
    run => { run.change.evidence[0].status = 'failed'; },
    run => { run.change.evidence[0].exitCode = 1; },
    run => { run.change.evidence[0].timedOut = true; },
    run => { run.change.testPlan.tests = []; },
    run => { run.change.testPlan.tests[0].required = false; },
    run => { run.change.testPlan.tests.push({ id: 'manual-ui', type: 'e2e', cwd: '.', description: 'Inspect UI', reason: 'UI changed', required: true }); },
  ]) {
    const run = fixture(); mutation(run);
    assert.equal(evaluateReadiness(run, catalog, 3).passed, false);
  }
});

test('unknown warnings, skipped stages, blockers, errors and mismatching CLI status fail', () => {
  for (const mutation of [
    run => { run.change.releaseAssessment.warnings.push('Human review of a finding is pending'); },
    run => { run.change.releaseAssessment.blockers.push('Manual approval required'); },
    run => { run.skippedStages.push('review'); },
    run => { run.lastError = 'failed'; },
    run => { delete run.review; },
  ]) {
    const run = fixture(); mutation(run);
    assert.equal(evaluateReadiness(run, catalog, 3).passed, false);
  }
  assert.equal(evaluateReadiness(fixture(), catalog, 0).passed, false);
  assert.equal(evaluateReadiness(fixture(), catalog, 3, { source: 'different-sha' }).passed, false);
  assert.equal(evaluateReadiness({ error: 'broken config' }, catalog, 1).passed, false);
});

test('real executor ready assessment can pass, but mock cannot claim ready', () => {
  const run = fixture();
  run.change.releaseAssessment = { status: 'ready', summary: 'Requirements satisfied', blockers: [], warnings: [] };
  assert.equal(evaluateReadiness(run, catalog, 0).passed, false);
  run.executors = { review: 'command', testPlanning: 'command' };
  assert.equal(evaluateReadiness(run, catalog, 0).passed, true);
});
