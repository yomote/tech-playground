import { defineConfig } from 'vite';
import { generateIndex, root } from '../../tools/metadata';
export default defineConfig({ server: { port: 5173, strictPort: true, watch: { ignored: ['**/.venv/**', '**/runs/**', '**/demos/*/dist/**'] } }, plugins: [{
  name: 'demo-index',
  async buildStart() { await generateIndex(); },
  configureServer(server) {
    server.watcher.add(`${root}/demos`);
    let queue = Promise.resolve();
    server.watcher.on('all', (_event, file: string) => {
      if (/[\\/]demos[\\/][^\\/]+[\\/](demo\.yaml|README\.md)$/.test(file)) {
        queue = queue.then(async () => { await generateIndex(); server.ws.send({ type: 'full-reload' }); })
          .catch(error => server.config.logger.error(String(error)));
      }
    });
  },
}] });
