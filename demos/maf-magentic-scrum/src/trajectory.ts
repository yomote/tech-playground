export type Event = { seq: number; kind: string; agent: string; message: string; round: number; replans: number; nextAgent?: string; ledger?: Record<string, { answer?: string | boolean; reason?: string }>; [key: string]: unknown };
export type Run = { id: string; mode: string; status: string; pending: string | null; current: string; task: string; round: number; replans: number; events: Event[] };
export const specialists = ['Planner', 'Developer', 'Reviewer', 'Tester'];
export function agentName(value?: string) { return ['Verifier', ...specialists, 'Manager'].find(name => value?.toLowerCase().includes(name.toLowerCase())) ?? 'Manager'; }
export function decisionTarget(event: Event) { const name = event.nextAgent ?? event.ledger?.next_speaker?.answer; return typeof name === 'string' && name ? agentName(name) : null; }
export function trajectory(events: Event[]) {
  const visits: Record<string, number> = Object.fromEntries(specialists.map(name => [name, 0]));
  let active = 'Manager'; let decision: Event | undefined;
  const tools = new Set<string>();
  for (const event of events) {
    if (event.kind === 'decision') { decision = event; const target = decisionTarget(event); if (target && target !== 'Manager') { active = target; visits[target] = (visits[target] || 0) + 1; } else active = 'Manager'; }
    else if (['plan', 'replan', 'stall', 'approval', 'final', 'error'].includes(event.kind)) active = 'Manager';
    else if (['tool', 'agent'].includes(event.kind)) active = agentName(event.agent);
    if (event.kind === 'tool') tools.add(agentName(event.agent));
  }
  return { active, visits, decision, tools, latest: events.at(-1), toolCount: events.filter(e => e.kind === 'tool').length };
}
