import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// Built assets are served by FastAPI under /static (StaticFiles mount), and
// index.html is returned at "/". So asset URLs must be absolute /static/...,
// and the build lands directly in heaviside/api/static/.
export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  build: {
    outDir: fileURLToPath(new URL('../api/static', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: {
      // `npm run dev` proxies API calls to the running FastAPI server.
      '^/(jobs|design|crossref|cre|catalog|manufacturers|topologies|health)':
        'http://127.0.0.1:8773',
    },
  },
})
