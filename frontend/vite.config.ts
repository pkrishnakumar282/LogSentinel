import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const gateway = env.VITE_GATEWAY_ORIGIN ?? 'http://127.0.0.1:8000';
  return {
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: gateway, changeOrigin: true },
      '/health': { target: gateway, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    css: true,
    clearMocks: true,
  },
  };
});
