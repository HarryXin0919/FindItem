import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// @vitejs/plugin-react was already a declared dependency but was never wired
// up, so JSX relied on Vite's default esbuild transform and Fast Refresh was
// off. This makes the declared plugin actually take effect.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', sourcemap: false },
})
