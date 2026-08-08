import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // .env único do projeto vive na raiz do repo (../.env a partir de
  // frontend/), não em frontend/.env — ver ../.env.example.
  envDir: '..',
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
  },
});
