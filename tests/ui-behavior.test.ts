import test from 'node:test';
import assert from 'node:assert/strict';
import { explainTrace, seed, type Tuple } from '../demos/openfga-sharing-playground/domain';
import { nodePrincipal, relationsFor, tupleId } from '../demos/openfga-sharing-playground/src/graph';
import { decisionTarget, trajectory, type Event } from '../demos/maf-magentic-scrum/src/trajectory';
import { formFor, Settings } from '../demos/mcp-apps-playground/contract';
import { ExperimentStore } from '../demos/mcp-apps-playground/experiments';

test('authorization graph highlights the exact group and inheritance edges and reacts to revocation', () => {
  const trace = explainTrace(seed, 'user:bob', 'editor', 'document:1');
  assert.ok(trace);
  assert.deepEqual(trace.tuples.map(tupleId), [
    'user:bob|member|team:x',
    'team:x#member|editor|folder:a',
    'folder:a|parent|document:1',
  ]);
  for (const removed of trace.tuples) {
    assert.equal(explainTrace(seed.filter(tuple => tupleId(tuple) !== tupleId(removed)), 'user:bob', 'editor', 'document:1'), null);
  }
});

test('implied permissions have explanatory steps without creating nonexistent graph edges', () => {
  const trace = explainTrace(seed, 'user:alice', 'viewer', 'document:1');
  assert.ok(trace);
  assert.deepEqual(trace.tuples.map(tupleId), ['user:alice|owner|folder:a', 'folder:a|parent|document:1']);
  assert.ok(trace.path.some(step => step.includes('owner implies editor')));
  assert.ok(trace.path.some(step => step.includes('editor implies viewer')));
  assert.equal(explainTrace(seed, 'user:bob', 'owner', 'document:1'), null);
});

test('a cyclic branch terminates without hiding a separate valid authorization path', () => {
  const tuples: Tuple[] = [
    { user: 'folder:b', relation: 'parent', object: 'folder:a' },
    { user: 'folder:a', relation: 'parent', object: 'folder:b' },
    { user: 'folder:a', relation: 'parent', object: 'document:1' },
    { user: 'user:carol', relation: 'viewer', object: 'folder:b' },
  ];
  assert.equal(explainTrace(tuples, 'user:bob', 'viewer', 'document:1'), null);
  assert.deepEqual(explainTrace(tuples, 'user:carol', 'viewer', 'document:1')?.tuples.map(tupleId), [
    'user:carol|viewer|folder:b', 'folder:b|parent|folder:a', 'folder:a|parent|document:1',
  ]);
});

test('graph connections map team nodes to membership principals and enforce model direction', () => {
  assert.equal(nodePrincipal('team:x'), 'team:x#member');
  assert.deepEqual(relationsFor('user:bob', 'team:x'), ['member']);
  assert.deepEqual(relationsFor('team:x', 'document:1'), ['editor', 'viewer']);
  assert.deepEqual(relationsFor('user:alice', 'folder:a'), ['owner', 'editor', 'viewer']);
  assert.deepEqual(relationsFor('folder:a', 'document:1'), ['parent']);
  assert.deepEqual(relationsFor('folder:a', 'folder:b'), ['parent']);
  for (const [source, target] of [['document:1', 'folder:a'], ['team:x', 'user:bob'], ['folder:a', 'folder:a'], ['unknown', 'document:1']]) {
    assert.deepEqual(relationsFor(source, target), [], `${source} → ${target} must be rejected`);
  }
});

const event = (seq: number, values: Partial<Event>): Event => ({ seq, kind: 'decision', agent: 'Manager', message: '', round: 1, replans: 0, ...values });

test('agent graph understands both scripted selections and real Magentic next_speaker ledgers', () => {
  const scripted = event(1, { nextAgent: 'Planner' });
  const sdk = event(2, { ledger: { next_speaker: { answer: 'Developer', reason: 'Implement the approved plan' } }, round: 2 });
  assert.equal(decisionTarget(scripted), 'Planner');
  assert.equal(decisionTarget(sdk), 'Developer');
  assert.equal(decisionTarget(event(3, { ledger: { next_speaker: { answer: false } } })), null);
  const state = trajectory([scripted, sdk]);
  assert.equal(state.active, 'Developer');
  assert.equal(state.visits.Planner, 1);
  assert.equal(state.visits.Developer, 1);
  assert.equal(state.visits.Reviewer, 0);
  assert.equal(state.decision, sdk);
});

test('replay derives active agents, tool owners, rounds, and replans from the selected prefix', () => {
  const events = [
    event(1, { nextAgent: 'Developer' }),
    event(2, { kind: 'tool', agent: 'Developer', message: 'write_solution' }),
    event(3, { nextAgent: 'Tester', round: 2 }),
    event(4, { kind: 'tool', agent: 'Tester', message: 'run_tests', round: 2 }),
    event(5, { kind: 'stall', round: 2 }),
    event(6, { kind: 'replan', round: 2, replans: 1 }),
    event(7, { nextAgent: 'Developer', round: 3, replans: 1 }),
    event(8, { kind: 'tool', agent: 'Verifier', message: 'run_tests', round: 3, replans: 1 }),
    event(9, { kind: 'final', round: 3, replans: 1 }),
  ];
  const beforeFailure = trajectory(events.slice(0, 4));
  assert.equal(beforeFailure.active, 'Tester');
  assert.equal(beforeFailure.toolCount, 2);
  assert.deepEqual([...beforeFailure.tools], ['Developer', 'Tester']);
  assert.equal(beforeFailure.latest?.replans, 0);
  const replanning = trajectory(events.slice(0, 6));
  assert.equal(replanning.active, 'Manager');
  assert.equal(replanning.latest?.replans, 1);
  const repair = trajectory(events.slice(0, 7));
  assert.equal(repair.active, 'Developer');
  assert.equal(repair.visits.Developer, 2);
  assert.equal(repair.latest?.round, 3);
  const verification = trajectory(events.slice(0, 8));
  assert.equal(verification.active, 'Verifier');
  assert.deepEqual([...verification.tools], ['Developer', 'Tester', 'Verifier']);
  assert.equal(verification.toolCount, 3);
  assert.equal(trajectory(events).active, 'Manager');
  assert.equal(trajectory([]).active, 'Manager');
  assert.equal(trajectory([]).toolCount, 0);
});

const settings = { model: 'GPT' as const, dataset: 'Sample A' as const, runs: 10 };

test('MCP tool input selects distinct editable form definitions with complete default values', () => {
  const store = new ExperimentStore();
  const comparison = store.create(settings);
  const extraction = store.create({ ...settings, kind: 'structured-extraction' });
  const comparisonForm = formFor(comparison.kind);
  const extractionForm = formFor(extraction.kind);
  assert.ok(comparisonForm.fields.some(field => field.key === 'promptA' && field.type === 'textarea'));
  assert.ok(comparisonForm.fields.some(field => field.key === 'promptB'));
  assert.ok(extractionForm.fields.some(field => field.key === 'document' && field.type === 'textarea'));
  assert.ok(extractionForm.fields.some(field => field.key === 'missingPolicy' && field.options?.includes('Flag for review')));
  assert.equal(extractionForm.fields.some(field => field.key === 'promptA'), false);
  assert.equal(comparisonForm.fields.some(field => field.key === 'document'), false);
  for (const experiment of [comparison, extraction]) {
    for (const field of formFor(experiment.kind).fields) assert.notEqual(experiment.configuration[field.key], undefined);
  }
});

test('prompt form edits change mock comparison output and clear the prior run', () => {
  const store = new ExperimentStore();
  const experiment = store.create({ ...settings, configuration: { promptA: 'Answer briefly.', promptB: 'Explain with examples.' } });
  const first = store.run(experiment.experimentId);
  assert.equal(first.mock, true);
  assert.equal(first.result?.comparisons?.length, 2);
  assert.deepEqual(store.run(experiment.experimentId).result, first.result, 'unchanged inputs must be deterministic');
  const updated = store.update(experiment.experimentId, { ...settings, configuration: { ...experiment.configuration, promptB: 'Return one concise sentence.', rubric: 'Conciseness', temperature: 2 } });
  assert.equal(updated.result, null);
  const second = store.run(experiment.experimentId);
  assert.equal(second.result?.comparisons?.[1].preview, 'Return one concise sentence.');
  assert.notDeepEqual(second.result?.comparisons?.map(row => row.score), first.result?.comparisons?.map(row => row.score));
  assert.equal(second.experimentId, experiment.experimentId);
});

test('extraction form handles deduplicated fields, missing values, and review policy', () => {
  const store = new ExperimentStore();
  const configuration = { document: 'vendor: Example Studio\nurl: https://example.test/invoice\namount: 42\nblank: \nunstructured text', fields: ' vendor, amount, url, purchase_order, vendor, blank ', missingPolicy: 'Flag for review' as const };
  const experiment = store.create({ ...settings, kind: 'structured-extraction', configuration });
  const result = store.run(experiment.experimentId).result;
  assert.deepEqual(result?.extracted, { vendor: 'Example Studio', amount: '42', url: 'https://example.test/invoice', purchase_order: null, blank: null });
  assert.deepEqual(result?.reviewFields, ['purchase_order', 'blank']);
  assert.equal(result?.score, 0.6);
  assert.equal(result?.success, 6);
  assert.equal(result?.comparisons, undefined);
  store.update(experiment.experimentId, { ...settings, kind: 'structured-extraction', configuration: { ...configuration, missingPolicy: 'Return null' } });
  const withoutReview = store.run(experiment.experimentId).result;
  assert.deepEqual(withoutReview?.extracted, result?.extracted);
  assert.deepEqual(withoutReview?.reviewFields, []);
});

test('invalid form values and unknown handles are rejected without modifying a valid experiment', () => {
  const store = new ExperimentStore();
  const experiment = store.create(settings);
  for (const invalid of [
    { ...settings, kind: 'unknown' },
    { ...settings, runs: 101 },
    { ...settings, configuration: { temperature: -1 } },
    { ...settings, configuration: { temperature: 2.1 } },
    { ...settings, configuration: { fields: '' } },
    { ...settings, configuration: { promptA: 'x'.repeat(4001) } },
    { ...settings, configuration: { missingPolicy: 'Guess a value' } },
  ]) assert.equal(Settings.safeParse(invalid).success, false);
  assert.throws(() => store.update(experiment.experimentId, { ...settings, configuration: { temperature: 9 } }));
  assert.deepEqual(store.get(experiment.experimentId), experiment);
  assert.throws(() => store.run('missing-handle'), /Unknown experimentId/);
  const externalCopy = store.get(experiment.experimentId);
  externalCopy.configuration.promptA = 'Unexpected outside edit';
  assert.deepEqual(store.get(experiment.experimentId), experiment, 'callers cannot mutate stored configuration through returned objects');
});
