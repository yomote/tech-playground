import { z } from 'zod';

export const DemoStatus = z.enum(['idea', 'exploring', 'working', 'completed', 'paused', 'abandoned']);
export type DemoStatus = z.infer<typeof DemoStatus>;
const date = z.iso.date();
const httpUrl = z.url().refine(value => ['http:', 'https:'].includes(new URL(value).protocol), 'HTTP(S) URL required');
export const Demo = z.object({
  id: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
  title: z.string().trim().min(1), status: DemoStatus,
  summary: z.string().trim().min(1), goal: z.string().trim().min(1),
  questions: z.array(z.string()), tags: z.array(z.string().min(1)), stack: z.array(z.string().min(1)),
  findings: z.array(z.string()),
  references: z.array(z.union([httpUrl, z.object({ title: z.string().min(1), url: httpUrl })])),
  createdAt: date, updatedAt: date,
  app: z.object({ url: httpUrl }).optional(),
}).strict().refine(demo => demo.updatedAt >= demo.createdAt, 'updatedAt must not precede createdAt');
export type Demo = z.infer<typeof Demo>;
export type DemoEntry = Demo & { readme: string };

export function searchDemos(demos: DemoEntry[], query: string, filters: { status?: string; tag?: string; stack?: string } = {}) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return demos.filter(d => {
    const haystack = [d.title, d.summary, d.goal, ...d.questions, ...d.tags, ...d.stack, ...d.findings].join(' ').toLocaleLowerCase();
    return terms.every(term => haystack.includes(term)) && (!filters.status || d.status === filters.status)
      && (!filters.tag || d.tags.includes(filters.tag)) && (!filters.stack || d.stack.includes(filters.stack));
  });
}
