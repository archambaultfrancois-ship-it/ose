import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../src/ose_ws/static', emptyOutDir: true, chunkSizeWarningLimit: 1100 },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8080', ws: true } } },
})
