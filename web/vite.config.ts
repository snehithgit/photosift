import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '0.0.0.0', port: 3000, strictPort: true, allowedHosts: true, proxy: { '/api': process.env.PHOTOSIFT_API ?? 'http://127.0.0.1:8000' } },
  preview: { host: '0.0.0.0', port: 3000, strictPort: true },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
});
