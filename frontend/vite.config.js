import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { fileURLToPath } from 'url'

// https://vite.dev/config/
const proxyTarget = process.env.VITE_PROXY_API_TARGET || 'http://127.0.0.1:7348'
const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  base: './',
  plugins: [
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        ws: true,
      },
      '/design': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
})
