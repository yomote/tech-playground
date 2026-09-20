export type ModelId = 'llm-adapter' | 'modernbert' | 'gliclass';
export type Mode = 'classification' | 'criteria' | 'sufficiency';
export type Language = 'ja' | 'en';
export type Option = { id: string; label: string; description: string };
export type Task = { mode: Mode; language: Language; text: string; question: string; criteria: string; options: Option[]; expectedOptionId?: string | null; fixtureId?: string };
export type Fixture = Task & { id: string; title: string; basis: 'text' | 'knowledge'; rationale: string; expectedOptionId: string | null };
export type Model = { id: ModelId; name: string; available: boolean; detail: string };
export type Config = { models: Model[]; fixtures: Fixture[] };
export type AdapterUsage = { input_tokens?: number; output_tokens?: number; input_tokens_total?: number; output_tokens_total?: number; n_retries?: number; n_retries_malformed_structure?: number; latency?: number };
export type Result = { inputIndex: number; model: ModelId; modelName: string; status: 'ok' | 'invalid' | 'error'; selectedOptionId: string | null; scores: { optionId: string; score: number; rawScore: number | null }[]; scoreKind: string; prompt: string; rawAnswer?: string; loadMs: number; inferenceMs: number; warning?: string; error?: string; matchesExpected: boolean | null; provider?: string; providerModel?: string; requestedModel?: string; usage?: AdapterUsage };
export type Run = { id: string; status: 'running' | 'completed' | 'failed'; createdAt: string; inputs: Task[]; models: ModelId[]; results: Result[]; error?: string };

export function taskFrom(source: Task): Task {
  return { mode: source.mode, language: source.language, text: source.text, question: source.question, criteria: source.criteria, options: source.options.map(option => ({ ...option })) };
}
export function isPristine(task: Task, fixture: Fixture | undefined): boolean {
  return !!fixture && JSON.stringify(taskFrom(task)) === JSON.stringify(taskFrom(fixture));
}
export function scoreLabel(value: number): string { return Number.isFinite(value) ? value.toFixed(3) : '—'; }
export function elapsed(value: number): string { return Number.isFinite(value) ? value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s` : '—'; }
