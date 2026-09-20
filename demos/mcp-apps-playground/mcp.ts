import { readFile } from 'node:fs/promises';
import { McpServer } from '@modelcontextprotocol/server';
import { registerAppTool, registerAppResource, RESOURCE_MIME_TYPE } from '@modelcontextprotocol/ext-apps/server';
import { z } from 'zod';
import { ExperimentStore, Settings } from './experiments';
import { formFor, type Experiment } from './contract';
export const resourceUri = 'ui://experiments/dashboard.html';
export const store = new ExperimentStore();
export function createServer() {
  const server = new McpServer({ name: 'experiment-dashboard', version: '0.1.0' });
  const result = (operation: () => Experiment) => {
    try { const experiment = operation(); const data = { experiment, form: formFor(experiment.kind) }; return { content: [{ type: 'text' as const, text: JSON.stringify(data) }], structuredContent: data }; }
    catch (error) { return { isError: true, content: [{ type: 'text' as const, text: String(error) }] }; }
  };
  registerAppTool(server, 'create_experiment', {
    description: 'Open a prompt comparison form or structured extraction form according to kind. Experiments use deterministic mock data.',
    inputSchema: Settings, _meta: { ui: { resourceUri } },
  }, async args => result(() => store.create(args)));
  registerAppTool(server, 'update_experiment', {
    description: 'Update settings using an explicit experimentId; clears previous results.',
    inputSchema: Settings.extend({ experimentId: z.string().uuid() }), _meta: { ui: { resourceUri } },
  }, async ({ experimentId, ...settings }) => result(() => store.update(experimentId, settings)));
  for (const name of ['run_experiment', 'get_result'] as const) {
    registerAppTool(server, name, {
      description: name === 'run_experiment' ? 'Run deterministic mock computation for the handle.' : 'Read settings and last result by handle.',
      inputSchema: z.object({ experimentId: z.string().uuid() }), _meta: { ui: { resourceUri } },
    }, async ({ experimentId }) => result(() => name === 'run_experiment' ? store.run(experimentId) : store.get(experimentId)));
  }
  registerAppResource(server, 'Experiment Dashboard', resourceUri, { mimeType: RESOURCE_MIME_TYPE }, async () => ({ contents: [{ uri: resourceUri, mimeType: RESOURCE_MIME_TYPE, text: await readFile(new URL('./dist/ui/app.html', import.meta.url), 'utf8'), _meta: { ui: { prefersBorder: true } } }] }));
  return server;
}
