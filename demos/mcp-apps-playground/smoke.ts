import assert from 'node:assert/strict';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { RESOURCE_MIME_TYPE } from '@modelcontextprotocol/ext-apps/server';
import type { Experiment } from './experiments';
const clients: Client[] = [];
async function connect() { const client = new Client({ name: 'smoke', version: '0.1.0' }); clients.push(client); await client.connect(new StreamableHTTPClientTransport(new URL('http://127.0.0.1:5174/mcp'))); return client; }
try {
  const first = await connect();
  const tools = await first.listTools(); assert.equal(tools.tools.length, 4);
  const created = await first.callTool({ name: 'create_experiment', arguments: { model: 'GPT', dataset: 'Sample A', runs: 10 } });
  const { experiment } = created.structuredContent as { experiment: Experiment };
  const resource = await first.readResource({ uri: 'ui://experiments/dashboard.html' });
  assert.equal(resource.contents[0].mimeType, RESOURCE_MIME_TYPE);
  assert.ok('text' in resource.contents[0] && resource.contents[0].text.includes('Experiment Dashboard'));
  const second = await connect();
  const output = await second.callTool({ name: 'run_experiment', arguments: { experimentId: experiment.experimentId } });
  assert.equal((output.structuredContent as { experiment: Experiment }).experiment.result?.success, 8);
  const read = await first.callTool({ name: 'get_result', arguments: { experimentId: experiment.experimentId } });
  assert.equal((read.structuredContent as { experiment: Experiment }).experiment.result?.score, .82);
  console.log('Real MCP HTTP: tools, UI resource, explicit handle across connections, result PASS');
} finally { await Promise.all(clients.map(client => client.close())); }
