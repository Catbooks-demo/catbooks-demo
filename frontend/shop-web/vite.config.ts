import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// 포트는 ax-backend/CLAUDE.md 포트 표 기준. .env는 레포 루트 것을 읽는다
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  envDir: '..',
  server: { port: 3000, strictPort: true },
  preview: { port: 3000, strictPort: true },
})
