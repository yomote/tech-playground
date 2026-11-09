import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5341,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5340',
        changeOrigin: true,
        headers: { Origin: 'http://127.0.0.1:5340' },
      },
    },
  },
  build: { outDir: 'dist' },
});
