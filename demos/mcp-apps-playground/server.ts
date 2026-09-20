import express from 'express';
import { fileURLToPath } from 'node:url';
import { createMcpExpressApp } from '@modelcontextprotocol/express';
import { NodeStreamableHTTPServerTransport } from '@modelcontextprotocol/node';
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import { createServer } from './mcp';
if (process.argv.includes('--stdio')) {
  await createServer().connect(new StdioServerTransport());
} else {
  const app = createMcpExpressApp({ host: '127.0.0.1' });
  app.all('/mcp', async (req, res) => {
    const server = createServer();
    const transport = new NodeStreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on('close', () => { void transport.close(); void server.close(); });
    try { await server.connect(transport); await transport.handleRequest(req, res, req.body); }
    catch (error) { console.error(error); if (!res.headersSent) res.status(500).json({ error: 'MCP request failed' }); }
  });
  app.use(express.static(fileURLToPath(new URL('./dist/host', import.meta.url))));
  app.listen(5174, '127.0.0.1', () => console.log('Experiment Dashboard: http://localhost:5174 · MCP: http://localhost:5174/mcp'));
}
