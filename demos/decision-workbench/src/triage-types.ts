import type { AdapterUsage, Model } from './types';

export type TriageModelId = 'jev' | 'llm-adapter';
export type TriageMode = 'batch' | 'sequential' | 'compare' | 'sweep';
export type QuestionCount = 1 | 4 | 8 | 16;
export type TriageQuestion = { id: string; title: string; instructions: string; criteria: Record<string, string>; expectedOptionId: string | null; basis: 'text' | 'judgment'; rationale: string };
export type TriageConfig = { models: Model[]; preset: { id: string; title: string; text: string; questions: TriageQuestion[] } };
export type TriageAnswer = { questionId: string; status: 'ok' | 'error'; selectedOptionId: string | null; scores: { optionId: string; score: number; rawScore: null }[]; confidence?: number; matchesExpected: boolean | null; error?: string };
export type TriagePhase = { model: TriageModelId; modelName: string; providerModel?: string; requestedModel?: string; strategy: 'batch' | 'sequential'; questionCount: number; status: 'ok' | 'partial' | 'error'; elapsedMs: number; requestCount: number; completedCalls: number; answers: TriageAnswer[]; calls: { questionIds: string[]; elapsedMs: number; status: 'ok' | 'error'; error?: string; usage?: AdapterUsage }[]; error?: string };
export type TriageRun = { id: string; kind: 'triage'; status: 'running' | 'completed' | 'failed'; createdAt: string; finishedAt?: string; text: string; questions: TriageQuestion[]; questionCount: QuestionCount; models: TriageModelId[]; mode: TriageMode; results: TriagePhase[]; progress: { completedCalls: number; totalCalls: number; current: string }; error?: string };

export function snapshotQuestions(text: string, questions: TriageQuestion[], preset?: TriageConfig['preset']): TriageQuestion[] {
  const unchanged = !!preset && text === preset.text && questions.length === preset.questions.length && questions.every((question, index) => {
    const original = preset.questions[index];
    return original.id === question.id && original.title === question.title && original.instructions === question.instructions && JSON.stringify(original.criteria) === JSON.stringify(question.criteria);
  });
  return questions.map((question, index) => {
    const original = preset?.questions[index];
    return { ...question, criteria: { ...question.criteria }, expectedOptionId: unchanged && original ? original.expectedOptionId : null, basis: unchanged && original ? original.basis : 'judgment', rationale: unchanged && original ? original.rationale : '' };
  });
}
export function plannedCalls(mode: TriageMode, count: QuestionCount, models: number): number {
  return (mode === 'sweep' ? 4 : mode === 'compare' ? count + 1 : mode === 'sequential' ? count : 1) * models;
}
