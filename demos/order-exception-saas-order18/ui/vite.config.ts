import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const localNoAuth = loadEnv(mode, '.', 'VITE_OE02_').VITE_OE02_LOCAL_NO_AUTH === 'true';
  return {
    base: '/trial-order18/',
    plugins: localNoAuth ? [{
      name: 'local-saved-run-readback',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const pathname = new URL(req.url || '/', 'http://localhost').pathname;
          if ((pathname === '/trial-order18/api' || pathname.startsWith('/trial-order18/api/')) &&
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
      port: 19706,
      strictPort: true,
      proxy: {
        '/trial/auth': {
          target: loadEnv(mode, '.', 'OE02_').OE02_TRIAL_KEYCLOAK_URL || 'http://127.0.0.1:19580',
          changeOrigin: false,
        },
        '/trial-order18/api': {
          target: loadEnv(mode, '.', 'OE02_').OE02_TRIAL_BACKEND_URL || 'http://127.0.0.1:19705',
          // Preserve the browser's Host so the isolated API can compare it with
          // Origin on synthetic-role writes without relaxing its CSRF check.
          changeOrigin: false,
          rewrite: (path) => path.replace(/^\/trial-order18/, ''),
        },
      },
    },
    build: { outDir: 'dist' },
  };
});
