import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// Same-origin HTTP + WebSocket access avoids permissive CORS settings.
const target = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
const proxy = Object.fromEntries(['/replay', '/health', '/history', '/observations', '/feed', '/ws'].map(path => [path, { target, ws: path === '/ws' }]))
export default defineConfig({
  plugins: [vue()],
  server: { proxy },
  preview: { proxy },
  test: { environment: 'jsdom', restoreMocks: true, clearMocks: true },
})
