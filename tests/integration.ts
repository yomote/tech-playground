/** Run against local demo servers. Real OpenFGA must be listening on 8080. */
import assert from 'node:assert/strict';
import { setTimeout } from 'node:timers/promises';
async function request<T = Record<string, unknown>>(port: number, path: string, body?: unknown): Promise<T> {
  const result = await fetch(`http://127.0.0.1:${port}${path}`, body === undefined ? { signal: AbortSignal.timeout(10000) } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
  });
  const json = await result.json(); assert.ok(result.ok, JSON.stringify(json)); return json;
}
const query = { user: 'user:bob', relation: 'editor', object: 'document:1' };
const tuple = { user: 'user:bob', relation: 'member', object: 'team:x' };
for (const mode of ['mock', 'live']) {
  await request(5176, '/api/mode', { mode });
  assert.equal((await request(5176, '/api/check', query)).allowed, true);
  await request(5176, '/api/tuples', { operation: 'delete', tuple });
  try { assert.equal((await request(5176, '/api/check', query)).allowed, false); }
  finally { await request(5176, '/api/tuples', { operation: 'add', tuple }); }
  assert.equal((await request(5176, '/api/check', query)).allowed, true);
  assert.equal((await request(5176, '/api/check', { user: 'user:alice', relation: 'viewer', object: 'document:1' })).allowed, true);
  assert.equal((await request(5176, '/api/check', { user: 'user:carol', relation: 'editor', object: 'document:1' })).allowed, false);
  console.log(`OpenFGA ${mode}: inheritance, grant/revoke, owner implication, denied check PASS`);
}
await request(5176, '/api/mode', { mode: 'mock' });
type Run = { id: string; status: string; round: number; replans: number; events: { kind: string; message: string }[] };
let run = await request<Run>(5175, '/api/runs', { mode: 'mock', scenario: 'test-failure', approval: false });
for (let attempt = 0; attempt < 100 && run.status === 'running'; attempt++) {
  await setTimeout(100); run = await request<Run>(5175, `/api/runs/${run.id}`);
}
assert.equal(run.status, 'completed'); assert.equal(run.replans, 1); assert.ok(run.events.some(e => e.kind === 'final'));
console.log(`MAF HTTP mock: test failure → replan → PASS (${run.round} rounds)`);
