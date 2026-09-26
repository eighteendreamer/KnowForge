import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 后端没有 CORSMiddleware，门户依赖同源代理访问 /v1/knowledge 与 /v1/portal；生产环境同样由网关同域转发。
    proxy: {
      '/v1': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/tests/setup.ts'],
    restoreMocks: true,
    testTimeout: 20_000,
  },
})
