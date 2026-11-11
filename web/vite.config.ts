import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// В разработке API — на сервере FastAPI (python -m orchestra serve), в сборке — тот же источник.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8710', changeOrigin: false } },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    assetsInlineLimit: 0, // шрифты отдельными файлами: CSP без data: для font-src
  },
});
