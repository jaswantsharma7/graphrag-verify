import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Keep development requests on the same origin as the interface.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
    },
  },
});
