import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

// Output lands inside the Python package because that is what ships: FastAPI
// serves it, and the one-file executable in 1.3 bundles it. Neither the
// launcher nor the exe may require Node, so the build happens here and the
// result travels as data.
/* Two targets, one source.
 *
 * `vite build` produces what FastAPI and the packaged executable serve.
 * `vite build --mode pages` produces the GitHub Pages demo, which lives under a
 * repository sub-path.
 *
 * The base has to be absolute in both. A relative base makes
 * `./assets/index.js` resolve against `/runs/` when the user reloads on
 * `/runs/abc123`, and the bundle 404s — which is why the sub-path is baked in
 * rather than avoided. `PAGES_BASE` lets a fork with a different repository
 * name build its own without editing this file.
 */
export default defineConfig(({ mode }) => {
  const pages = mode === 'pages';
  return {
    plugins: [preact()],
    base: pages ? (process.env['PAGES_BASE'] ?? '/MasterBI/') : '/',
    build: {
      outDir: pages ? '../web/dist-pages' : '../kpi_maker/ui_dist',
      emptyOutDir: true,
      sourcemap: true,
    },
  };
});
