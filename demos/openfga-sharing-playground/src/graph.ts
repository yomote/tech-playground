import { Tuple } from '../domain';
export const tupleId = (tuple: Tuple) => `${tuple.user}|${tuple.relation}|${tuple.object}`;
export const nodePrincipal = (node: string) => node === 'team:x' ? 'team:x#member' : node;
export function relationsFor(source: string, target: string): Tuple['relation'][] {
  return (['member', 'owner', 'editor', 'viewer', 'parent'] as const).filter(relation => Tuple.safeParse({ user: nodePrincipal(source), relation, object: target }).success);
}
export const entities = [
  { id: 'user:alice', name: 'Alice', kind: 'user', x: 0, y: 30 },
  { id: 'user:bob', name: 'Bob', kind: 'user', x: 0, y: 230 },
  { id: 'user:carol', name: 'Carol', kind: 'user', x: 0, y: 430 },
  { id: 'team:x', name: 'Team X', kind: 'team', x: 225, y: 230 },
  { id: 'folder:a', name: 'Folder A', kind: 'folder', x: 455, y: 90 },
  { id: 'folder:b', name: 'Folder B', kind: 'folder', x: 455, y: 400 },
  { id: 'document:1', name: 'Document 1', kind: 'document', x: 695, y: 90 },
  { id: 'document:2', name: 'Document 2', kind: 'document', x: 695, y: 400 },
];
export const entityName = (id: string) => entities.find(entity => entity.id === id.replace('#member', ''))?.name ?? id;
