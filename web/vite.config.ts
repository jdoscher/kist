import react from '@vitejs/plugin-react'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import { buildVersion, injectBuild, precacheList } from './src/swBuild.ts'

/** Writes this build's version and file list into dist/sw.js (the service
 * worker, public/sw.js). See src/swBuild.ts. */
function kistServiceWorker(): Plugin {
  let outDir = 'dist'
  return {
    name: 'kist-service-worker',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    writeBundle(_options, bundle) {
      const indexHtml = readFileSync(join(outDir, 'index.html'), 'utf8')
      const assets = precacheList(Object.keys(bundle), indexHtml)
      // root files (icons, theme-init.js) aren't content-hashed by name:
      // their bytes go into the version too
      const extra = assets
        .filter((a) => a !== '/' && !a.startsWith('/assets/'))
        .map((a) => {
          try {
            return readFileSync(join(outDir, a.split('?')[0])).toString('base64')
          } catch {
            return ''
          }
        })
        .join('\n')
      const swPath = join(outDir, 'sw.js')
      const sw = readFileSync(swPath, 'utf8')
      writeFileSync(swPath, injectBuild(sw, { version: buildVersion(assets, indexHtml, extra), assets }))
    },
  }
}

export default defineConfig({
  plugins: [react(), kistServiceWorker()],
  build: {
    rollupOptions: {
      output: {
        // libraries change less often than the app: their own chunks stay
        // cached across Kist updates (the app's screens past the library
        // are split with React.lazy, see App.tsx)
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react'
          if (id.includes('@tanstack')) return 'tanstack'
          return undefined
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/mcp': 'http://localhost:8080',
    },
  },
})
