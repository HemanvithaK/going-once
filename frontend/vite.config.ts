/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// One file configures the dev server, the production build and the tests.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  test: {
    environment: 'jsdom', // a fake browser, so components can render inside Node
    setupFiles: ['./src/test/setup.ts'],
  },
})