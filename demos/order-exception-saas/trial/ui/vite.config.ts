import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const localNoAuth = loadEnv(mode, '.', 'VITE_OE02_').VITE_OE02_LOCAL_NO_AUTH === 'true';
  return {
    base: '/trial/',
    plugins: localNoAuth ? [{
      name: 'local-saved-run-readback',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const pathname = new URL(req.url || '/', 'http://localhost').pathname;
          if ((pathname === '/trial/api' || pathname.startsWith('/trial/api/')) &&
              !['GET', 'HEAD', 'OPTIONS'].includes(req.method || '')) {
            res.statusCode = 405;
            res.setHeader('Allow', 'GET, HEAD, OPTIONS');
            res.setHeader('Content-Type', 'application/json; charset=utf-8');
            res.end('{"error":"local_readback_only"}');
            return;
          }
          next();
        });
      },
    }] : [],
    server: {
      host: '127.0.0.1',
      port: 5346,
      strictPort: true,
      proxy: {
        '/trial/api': {
          target: loadEnv(mode, '.', 'OE02_').OE02_TRIAL_BACKEND_URL || 'http://127.0.0.1:19601',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/trial/, ''),
        },
      },
    },
    build: { outDir: 'dist' },
  };
});
