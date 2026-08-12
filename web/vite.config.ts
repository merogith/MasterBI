import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

// Output lands inside the Python package because that is what ships: FastAPI
// serves it, and the one-file executable in 1.3 bundles it. Neither the
// launcher nor the exe may require Node, so the build happens here and the
// result travels as data.
export default defineConfig({
  plugins: [preact()],
  // Absolute, because the app has deep routes: a relative base makes
  // `./assets/index.js` resolve against `/runs/` when the user reloads on
  // `/runs/abc123`, and the bundle 404s. The GitHub Pages build is served from
  // a sub-path and needs its own base — that is 1.2's second build target, not
  // a value this one can also satisfy.
  base: '/',
  build: {
    outDir: '../kpi_maker/ui_dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    // `styles.css` still lives in the legacy `ui/` directory and is imported
    // from there rather than copied. One stylesheet, one source of truth,
    // until 1.1c generates the tokens from the engine.
    fs: { allow: ['..'] },
  },
});
