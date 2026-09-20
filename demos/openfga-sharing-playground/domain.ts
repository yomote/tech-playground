import { z } from 'zod';
export const users = ['user:alice', 'user:bob', 'user:carol'] as const;
export const objects = ['team:x', 'folder:a', 'folder:b', 'document:1', 'document:2'] as const;
export const Tuple = z.object({ user: z.enum([...users, 'team:x#member', 'folder:a', 'folder:b']), relation: z.enum(['owner', 'editor', 'viewer', 'member', 'parent']), object: z.enum(objects) }).superRefine((tuple, context) => {
  const valid = tuple.object.startsWith('team:') ? tuple.relation === 'member' && tuple.user.startsWith('user:')
    : tuple.relation === 'parent' ? tuple.user.startsWith('folder:') && tuple.user !== tuple.object
    : ['owner', 'editor', 'viewer'].includes(tuple.relation) && (tuple.user.startsWith('user:') || (tuple.relation !== 'owner' && tuple.user === 'team:x#member'));
  if (!valid) context.addIssue({ code: 'custom', message: 'This tuple does not match the authorization model' });
});
export type Tuple = z.infer<typeof Tuple>;
export const seed: Tuple[] = [
  { user: 'user:alice', relation: 'owner', object: 'folder:a' },
  { user: 'user:bob', relation: 'member', object: 'team:x' },
  { user: 'team:x#member', relation: 'editor', object: 'folder:a' },
  { user: 'folder:a', relation: 'parent', object: 'document:1' },
  { user: 'folder:b', relation: 'parent', object: 'document:2' },
  { user: 'user:carol', relation: 'viewer', object: 'folder:b' },
];
export const sameTuple = (a: Tuple, b: Tuple) => a.user === b.user && a.relation === b.relation && a.object === b.object;
export type Explanation = { path: string[]; tuples: Tuple[] };
/** Educational path finder for this model only, not an OpenFGA decision trace. */
export function explainTrace(tuples: Tuple[], user: string, relation: string, object: string, visited = new Set<string>()): Explanation | null {
  const key = `${user}|${relation}|${object}`;
  if (visited.has(key)) return null;
  const next = new Set(visited).add(key);
  for (const tuple of tuples.filter(t => t.object === object && t.relation === relation)) {
    if (tuple.user === user) return { path: [`${user} — ${relation} → ${object}`], tuples: [tuple] };
    if (tuple.user.includes('#')) {
      const [team, membership] = tuple.user.split('#');
      const trace = explainTrace(tuples, user, membership, team, next);
      if (trace) return { path: [...trace.path, `${team}#${membership} — ${relation} → ${object}`], tuples: [...trace.tuples, tuple] };
    }
  }
  const implied = relation === 'viewer' ? ['editor'] : relation === 'editor' ? ['owner'] : [];
  for (const sub of implied) { const trace = explainTrace(tuples, user, sub, object, next); if (trace) return { ...trace, path: [...trace.path, `${sub} implies ${relation} on ${object}`] }; }
  if (['viewer', 'editor'].includes(relation)) {
    for (const parent of tuples.filter(t => t.object === object && t.relation === 'parent')) {
      const trace = explainTrace(tuples, user, relation, parent.user, next);
      if (trace) return { path: [...trace.path, `${object} inherits ${relation} from ${parent.user}`], tuples: [...trace.tuples, parent] };
    }
  }
  return null;
}
export function explain(tuples: Tuple[], user: string, relation: string, object: string): string[] | null {
  return explainTrace(tuples, user, relation, object)?.path ?? null;
}
