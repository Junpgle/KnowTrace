import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The browser talks to Vite on :5173; Vite proxies API requests to FastAPI.
// No CORS changes or public network exposure are required for local development.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
