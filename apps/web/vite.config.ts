import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  build: {
    // NOT the default "assets": the app has a real /assets page (the asset
    // register), and a bundle directory of the same name shadows it in nginx —
    // /assets was 301'd into the folder and 404'd instead of rendering the SPA.
    assetsDir: 'bundles',
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      // The refresh token is an httpOnly cookie scoped to /api/v1/auth/.
      // Proxying in dev keeps the SPA same-origin with the API, so the cookie
      // is sent without any SameSite relaxation that would weaken production.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
    // Many tests here drive whole pages through several steps in jsdom. With
    // four workers sharing the CPU the slowest took ~2-3 s and occasionally
    // spiked past 4 s, so the 5 s default failed different tests on different
    // runs. Typing is kept cheap (see fillIn in src/test/helpers.tsx); this
    // is the margin for scheduling spikes. A genuine hang still fails.
    testTimeout: 15_000,
  },
} as never)
