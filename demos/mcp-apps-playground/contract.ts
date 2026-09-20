import { z } from 'zod';

export const Configuration = z.object({
  promptA: z.string().max(4000).default('Answer the question briefly.'),
  promptB: z.string().max(4000).default('Explain your answer step by step, then give a concise conclusion.'),
  temperature: z.number().min(0).max(2).default(0.7),
  rubric: z.enum(['Correctness', 'Clarity', 'Conciseness']).default('Correctness'),
  document: z.string().max(8000).default('vendor: Northwind Studio\ninvoice_id: INV-2048\namount: 12800\ncurrency: JPY\ndue_date: 2026-10-15'),
  fields: z.string().min(1).max(500).default('vendor, invoice_id, amount, currency, due_date'),
  missingPolicy: z.enum(['Return null', 'Flag for review']).default('Flag for review'),
});
export const Settings = z.object({
  model: z.enum(['GPT', 'Claude', 'Local']), dataset: z.enum(['Sample A', 'Sample B']), runs: z.number().int().min(1).max(100),
  kind: z.enum(['prompt-comparison', 'structured-extraction']).default('prompt-comparison'),
  configuration: Configuration.default(() => Configuration.parse({})),
});
export type Settings = z.input<typeof Settings>;
export type Field = { key: keyof z.output<typeof Configuration>; label: string; help: string; type: 'text' | 'textarea' | 'number' | 'select'; options?: string[]; min?: number; max?: number; step?: number };
export type FormDefinition = { title: string; description: string; fields: Field[] };
export function formFor(kind: z.output<typeof Settings>['kind']): FormDefinition {
  return kind === 'prompt-comparison' ? {
    title: 'Prompt comparison lab', description: '2つのプロンプトと評価基準を設定して、比較結果の表示とMCPの往復を観察します。',
    fields: [
      { key: 'promptA', label: 'Prompt A · baseline', help: '比較元の指示。文面を変えると疑似スコアも変わります。', type: 'textarea' },
      { key: 'promptB', label: 'Prompt B · candidate', help: '改善案の指示。実際のモデル推論は行いません。', type: 'textarea' },
      { key: 'temperature', label: 'Temperature', help: '疑似スコアの変動係数。0〜2。', type: 'number', min: 0, max: 2, step: 0.1 },
      { key: 'rubric', label: 'Evaluation rubric', help: '比較する疑似評価軸。', type: 'select', options: ['Correctness', 'Clarity', 'Conciseness'] },
    ],
  } : {
    title: 'Structured extraction studio', description: '文書、抽出するフィールド、欠損時の扱いを設定し、構造化された結果を確認します。',
    fields: [
      { key: 'document', label: 'Source document', help: 'Mockは「key: value」の行だけを読み取ります。自由文を理解するLLMではありません。', type: 'textarea' },
      { key: 'fields', label: 'Output fields', help: 'カンマ区切り。例: vendor, amount, due_date, purchase_order', type: 'text' },
      { key: 'missingPolicy', label: 'Missing field policy', help: '存在しないフィールドをnullで返すか、要確認として記録します。', type: 'select', options: ['Return null', 'Flag for review'] },
    ],
  };
}
export type Experiment = z.output<typeof Settings> & {
  experimentId: string; mock: true;
  result: { success: number; latencySeconds: number; score: number; comparisons?: { variant: string; score: number; preview: string }[]; extracted?: Record<string, string | null>; reviewFields?: string[] } | null;
};
export type ToolData = { experiment: Experiment; form: FormDefinition };
