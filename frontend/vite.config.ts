/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      // wake/openWakeWordDetector.ts imports the narrow 'onnxruntime-web/wasm'
      // subpath (CPU-only, single ~13.4 MB wasm variant), never the bare
      // 'onnxruntime-web' specifier -- which resolves to ort.bundle.min.mjs
      // (~406 KB of JS, ~5.6x the /wasm entry's ~73 KB) because it also
      // registers the WebGL backend this app never uses. Verified by
      // measuring both files in node_modules/onnxruntime-web/dist directly.
      // This alias is a no-op today (nothing imports the bare specifier) --
      // it exists so a future `import ... from 'onnxruntime-web'` gets
      // silently redirected to the already-minimal entry point instead of
      // silently regrowing the bundle. Deliberately NOT touching
      // ort.env.wasm.wasmPaths / vendoring the .wasm binary into public/ --
      // see openWakeWordDetector.ts's loadRealSessions() comment for why an
      // earlier attempt at that duplicated the ~13 MB binary in dist.
      { find: /^onnxruntime-web$/, replacement: 'onnxruntime-web/wasm' },
    ],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
