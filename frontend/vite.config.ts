import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/v1': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/tests/setup.ts'],
    restoreMocks: true,
    // 首次 router.push 会懒加载视图分块，机器繁忙时超过 5s 默认值，与断言逻辑无关。
    testTimeout: 20_000,
  },
})
