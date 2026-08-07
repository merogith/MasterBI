/* Static-site shim — GitHub Pages has no Python, so there is no API to call.
 *
 * Rather than fork app.js (two front ends would drift within a week), this
 * patches window.fetch and answers the same six endpoints from JSON that
 * tools/build_pages.py pre-rendered at build time. app.js is byte-identical
 * to the one the FastAPI server serves and does not know it is being lied to.
 *
 * Loaded before app.js. On the real server this file is absent, so nothing
 * here runs and fetch is untouched.
 *
 * What genuinely cannot work here: modes that need Python at click time —
 * survey runs, surprise runs, spreadsheet upload. Those get a clear message
 * instead of a network error, because "Failed to fetch" teaches nobody
 * anything. */

(function () {
  'use strict';

  window.KPI_STATIC = true;

  // Everything is relative to the directory index.html sits in, so the site
  // works at merogith.github.io/MasterBI/ and at localhost alike.
  const BASE = new URL('.', window.location.href).href;
  const asset = (rel) => new URL(rel, BASE).href;

  // app.js builds one download link by hand rather than reading it from the
  // summary payload; this is the prefix it uses.
  window.KPI_FILES_BASE = BASE.replace(/\/$/, '');

  const LIVE_APP_NOTE =
    'This is the static showcase — it serves three pre-built companies and ' +
    'nothing else. Generating a new company runs the Python pipeline, which ' +
    'GitHub Pages cannot host. Run it locally, or use the live app.';

  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });

  const fail = (detail, status = 501) => json({ detail }, status);

  const realFetch = window.fetch.bind(window);

  /* Fetch a pre-built payload. Pages returns its 404 page as HTML, so a
   * missing file surfaces as a JSON parse error — catch it and say what is
   * actually wrong. */
  async function prebuilt(rel) {
    const res = await realFetch(asset(rel));
    if (!res.ok) return fail(`Missing pre-built data: ${rel}`, 404);
    try {
      return json(await res.json());
    } catch (_) {
      return fail(`Corrupt pre-built data: ${rel}`, 500);
    }
  }

  window.fetch = function (input, init = {}) {
    const raw = typeof input === 'string' ? input : input?.url ?? '';
    const method = (init.method || 'GET').toUpperCase();

    // Anything that is not one of our endpoints (the dashboard iframe, the
    // download anchors) goes straight through untouched.
    if (!raw.startsWith('/api/')) return realFetch(input, init);

    const path = raw.split('?')[0];

    if (path === '/api/samples') return prebuilt('data/samples.json');
    if (path === '/api/survey')  return prebuilt('data/survey.json');

    if (path === '/api/runs' && method === 'GET') return prebuilt('data/runs.json');

    if (path === '/api/runs' && method === 'POST') {
      let payload = {};
      try { payload = JSON.parse(init.body || '{}'); } catch (_) {}
      if (payload.mode !== 'sample') {
        return Promise.resolve(fail(
          (payload.mode === 'surprise'
            ? 'Surprise me needs to generate a brand-new company. '
            : 'Building your own company needs to run the generator. ') +
          LIVE_APP_NOTE));
      }
      // Sample runs were rendered at build time; the run id *is* the sample id.
      return prebuilt('data/runs.json').then(async (res) => {
        const runs = await res.clone().json().catch(() => []);
        const hit = Array.isArray(runs)
          ? runs.find((r) => r.run_id === payload.sample_id) : null;
        if (!hit) return fail(`Unknown sample '${payload.sample_id}'`, 404);
        return json({ run_id: hit.run_id, status: 'done', company: hit.company });
      });
    }

    const table = path.match(/^\/api\/runs\/([^/]+)\/table\/([^/]+)$/);
    if (table) return prebuilt(`data/runs/${table[1]}/table/${table[2]}.json`);

    const tables = path.match(/^\/api\/runs\/([^/]+)\/tables$/);
    if (tables) return prebuilt(`data/runs/${tables[1]}/tables.json`);

    const run = path.match(/^\/api\/runs\/([^/]+)$/);
    if (run) return prebuilt(`data/runs/${run[1]}/summary.json`);

    if (path === '/api/upload') {
      return Promise.resolve(fail(
        'Profiling a spreadsheet reads it with pandas on the server. ' + LIVE_APP_NOTE));
    }

    return Promise.resolve(fail(`No static stand-in for ${path}`, 404));
  };
})();
