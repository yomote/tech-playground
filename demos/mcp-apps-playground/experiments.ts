import { randomUUID } from 'node:crypto';
import { Settings, type Experiment } from './contract';
export { Settings, type Experiment } from './contract';
// Application state, explicitly keyed by handle. No transport/session state.
export class ExperimentStore {
  private records = new Map<string, Experiment>();
  create(settings: Settings): Experiment {
    if (this.records.size >= 1000) throw new Error('Demo capacity reached; restart the server to clear experiments.');
    const record: Experiment = { ...Settings.parse(settings), experimentId: randomUUID(), mock: true, result: null };
    this.records.set(record.experimentId, record); return structuredClone(record);
  }
  get(id: string): Experiment { const value = this.records.get(id); if (!value) throw new Error('Unknown experimentId (server restarts clear the in-memory store)'); return structuredClone(value); }
  update(id: string, settings: Settings): Experiment { const record = { ...this.get(id), ...Settings.parse(settings), result: null }; this.records.set(id, record); return structuredClone(record); }
  run(id: string): Experiment {
    const record = this.get(id);
    let score = { GPT: .82, Claude: .86, Local: .68 }[record.model] - (record.dataset === 'Sample B' ? .12 : 0);
    record.result = { success: Math.round(record.runs * score), latencySeconds: record.model === 'Local' ? .6 : 1.8, score: Number(score.toFixed(2)) };
    const config = record.configuration;
    if (record.kind === 'prompt-comparison') {
      const hash = (text: string) => Array.from(text).reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) % 997, 0);
      record.result.comparisons = [config.promptA, config.promptB].map((prompt, i) => ({
        variant: i ? 'B · candidate' : 'A · baseline', preview: prompt,
        score: Number(Math.max(0, Math.min(1, score + (hash(prompt + config.rubric) / 997 - .5) * (.1 + config.temperature * .1))).toFixed(2)),
      }));
    } else {
      const entries = new Map(config.document.split('\n').map(line => { const colon = line.indexOf(':'); return colon < 0 ? ['', ''] : [line.slice(0, colon).trim(), line.slice(colon + 1).trim()]; }));
      const fields = [...new Set(config.fields.split(',').map(field => field.trim()).filter(Boolean))];
      if (!fields.length) throw new Error('At least one output field is required.');
      const extracted = Object.fromEntries(fields.map(field => [field, entries.get(field) || null]));
      const missing = fields.filter(field => extracted[field] === null);
      score = (fields.length - missing.length) / fields.length;
      record.result = { ...record.result, score: Number(score.toFixed(2)), success: Math.round(record.runs * score), extracted, reviewFields: config.missingPolicy === 'Flag for review' ? missing : [] };
    }
    this.records.set(id, record); return structuredClone(record);
  }
}
