import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Demo, DemoStatus, searchDemos } from '@playground/demo-schema';
import { loadDemos } from '../tools/metadata';
import { createDemo, templates } from '../tools/create-demo/index';
import { ExperimentStore } from '../demos/mcp-apps-playground/experiments';
import { explain, seed, Tuple } from '../demos/openfga-sharing-playground/domain';

test('metadata is valid, flat, and searchable across questions/findings', async () => {
  const entries = await loadDemos();
  const demos = entries.filter(demo => ['mcp-apps-playground', 'maf-magentic-scrum', 'openfga-sharing-playground'].includes(demo.id));
  assert.equal(demos.length, 3); assert.equal(DemoStatus.options.length, 6);
  assert.equal(searchDemos(demos, 'human approval')[0].id, 'maf-magentic-scrum');
  assert.equal(searchDemos(demos, 'experimentId', { stack: 'typescript' }).length, 1);
  assert.equal(searchDemos(demos, '', { status: 'exploring', tag: 'openfga' }).length, 1);
  const { readme, ...meta } = demos[0]; assert.ok(readme);
  assert.equal(Demo.safeParse({ ...meta, updatedAt: '2026-02-30' }).success, false);
  assert.equal(Demo.safeParse({ ...meta, app: { url: 'javascript:alert(1)' } }).success, false);
});

test('CLI scaffolds every template and refuses overwrite/path traversal', async () => {
  const base = await mkdtemp(path.join(tmpdir(), 'tech-playground-test-'));
  try {
    for (const template of templates) {
      const options = { name: `Test ${template}`, template, summary: 'Learn by running', tags: ['test'] };
      const target = await createDemo(options, base);
      assert.match(await readFile(path.join(target, 'README.md'), 'utf8'), /## Findings/);
      const before = await readFile(path.join(target, 'demo.yaml'), 'utf8');
      await assert.rejects(createDemo(options, base), /EEXIST/);
      assert.equal(await readFile(path.join(target, 'demo.yaml'), 'utf8'), before);
      if (template === 'node' || template === 'react-vite') assert.equal(JSON.parse(await readFile(path.join(target, 'package.json'), 'utf8')).name, `@playground/test-${template}`);
    }
    await assert.rejects(createDemo({ name: 'Bad', id: '../escape', template: 'blank', summary: 'x', tags: [] }, base));
  } finally {
    // mkdtemp returned this exact owned test directory; never use an arbitrary path.
    assert.ok(path.resolve(base).startsWith(path.resolve(tmpdir()) + path.sep));
    await rm(base, { recursive: true, force: true });
  }
});

test('experiment handles isolate settings, invalidate stale results, and reject invalid input', () => {
  const store = new ExperimentStore(); const settings = { model: 'GPT' as const, dataset: 'Sample A' as const, runs: 10 };
  const a = store.create(settings), b = store.create(settings);
  assert.notEqual(a.experimentId, b.experimentId); assert.equal(store.run(a.experimentId).result?.success, 8);
  assert.equal(store.get(b.experimentId).result, null);
  assert.equal(store.update(a.experimentId, { ...settings, runs: 20 }).result, null);
  assert.throws(() => store.get('unknown')); assert.throws(() => store.create({ ...settings, runs: 0 }));
});

test('sharing supports groups, inheritance, direct grants, revocation, and cycle termination', () => {
  assert.ok(explain(seed, 'user:bob', 'editor', 'document:1'));
  assert.equal(explain(seed.filter(t => t.relation !== 'member'), 'user:bob', 'editor', 'document:1'), null);
  assert.ok(explain(seed, 'user:alice', 'viewer', 'document:1'));
  assert.equal(explain(seed, 'user:carol', 'editor', 'document:2'), null);
  assert.ok(explain(seed, 'user:carol', 'viewer', 'document:2'));
  const cyclic = [...seed, Tuple.parse({ user: 'folder:a', relation: 'parent', object: 'folder:b' }), Tuple.parse({ user: 'folder:b', relation: 'parent', object: 'folder:a' })];
  assert.equal(explain(cyclic, 'user:nobody', 'viewer', 'document:2'), null);
  assert.equal(Tuple.safeParse({ user: 'team:x#member', relation: 'owner', object: 'document:1' }).success, false);
});
