import express from 'express';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformer } from '@openfga/syntax-transformer';
import { z } from 'zod';
import { Tuple, seed, sameTuple, explainTrace, users, objects } from './domain';
const fs = await import('node:fs');
const envFile = fileURLToPath(new URL('.env', import.meta.url));
if (fs.existsSync(envFile)) process.loadEnvFile(envFile);
const app = express(); app.use(express.json({ limit: '16kb' }));
app.use('/api', (req, res, next) => {
  if (req.headers.origin && !['http://localhost:5176', 'http://127.0.0.1:5176'].includes(req.headers.origin)) { res.status(403).json({ error: 'Local origin required' }); return; }
  next();
});
const base = process.env.FGA_API_URL || 'http://127.0.0.1:8080';
let mode: 'mock' | 'live' = 'mock'; let local = structuredClone(seed);
let storeId: string | undefined, modelId: string | undefined;
async function fga(path: string, body: unknown) {
  const response = await fetch(`${base}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`OpenFGA ${response.status}: ${await response.text()}`);
  return response.json();
}
async function readTuples(): Promise<Tuple[]> {
  if (mode === 'mock') return structuredClone(local);
  const tuples: Tuple[] = []; let token = '';
  do { const result = await fga(`/stores/${storeId}/read`, { page_size: 100, continuation_token: token }); tuples.push(...(result.tuples || []).map((entry: { key: Tuple }) => entry.key)); token = result.continuation_token || ''; } while (token);
  return tuples;
}
app.get('/api/state', async (_req, res) => { res.json({ mode, tuples: await readTuples(), storeId, modelId }); });
app.post('/api/mode', async (req, res) => {
  const target = z.enum(['mock', 'live']).parse(req.body.mode);
  if (target === 'live' && !storeId) {
    const store = await fga('/stores', { name: 'Tech Playground sharing experiment' });
    const model = transformer.transformDSLToJSONObject(await readFile(new URL('./model.fga', import.meta.url), 'utf8'));
    const result = await fga(`/stores/${store.id}/authorization-models`, model);
    await fga(`/stores/${store.id}/write`, { authorization_model_id: result.authorization_model_id, writes: { tuple_keys: seed } });
    storeId = store.id; modelId = result.authorization_model_id;
  }
  mode = target; res.json({ mode, tuples: await readTuples(), storeId, modelId });
});
app.post('/api/tuples', async (req, res) => {
  const operation = z.enum(['add', 'delete']).parse(req.body.operation);
  const tuple = Tuple.parse(req.body.tuple); const tuples = await readTuples();
  const exists = tuples.some(t => sameTuple(t, tuple));
  if ((operation === 'add' && exists) || (operation === 'delete' && !exists)) { res.status(409).json({ error: exists ? 'Tuple already exists' : 'Tuple no longer exists' }); return; }
  if (mode === 'live') await fga(`/stores/${storeId}/write`, { authorization_model_id: modelId, [operation === 'add' ? 'writes' : 'deletes']: { tuple_keys: [tuple] } });
  else local = operation === 'add' ? [...local, tuple] : local.filter(t => !sameTuple(t, tuple));
  res.json({ mode, tuples: await readTuples(), storeId, modelId });
});
app.post('/api/check', async (req, res) => {
  const query = z.object({ user: z.enum(users), relation: z.enum(['viewer', 'editor', 'owner']), object: z.enum(objects.filter(o => !o.startsWith('team:')) as ['folder:a', 'folder:b', 'document:1', 'document:2']) }).parse(req.body);
  const tuples = await readTuples(); const trace = explainTrace(tuples, query.user, query.relation, query.object);
  const path = trace?.path ?? null;
  const decision = mode === 'live' ? await fga(`/stores/${storeId}/check`, { authorization_model_id: modelId, tuple_key: query, consistency: 'HIGHER_CONSISTENCY' }) : { allowed: !!path };
  const roles: Record<string, string> = { 'user:alice': 'editor', 'user:bob': 'viewer', 'user:carol': 'none' };
  const role = roles[query.user]; const rbacAllowed = query.relation === 'viewer' ? role !== 'none' : query.relation === 'editor' && role === 'editor';
  res.json({ ...decision, mode, query, path, pathTuples: trace?.tuples ?? [], explanationSource: 'Local educational traversal of current tuples; not an OpenFGA server trace', rbac: { role, allowed: rbacAllowed, note: 'Deliberately simple global roles. No per-resource sharing, membership, or inheritance.' } });
});
app.use(express.static(fileURLToPath(new URL('./dist', import.meta.url))));
app.use((error: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => { res.status(error instanceof z.ZodError ? 400 : 502).json({ error: error.message }); });
app.listen(5176, '127.0.0.1', () => console.log('Sharing Playground: http://localhost:5176 · MOCK until explicitly connected to OpenFGA'));
