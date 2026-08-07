/* Hybrid bridge for the published site.
 *
 * One URL, two capabilities:
 *
 *   Nothing running locally -> serve the three sample companies from JSON that
 *   tools/build_pages.py rendered at build time. Read-only, instant, no setup.
 *
 *   Local server detected   -> proxy every call to it. All four modes work,
 *   runs are written to the user's own runs/ directory, and the Recent runs
 *   drawer lists them across restarts. The published page is the front end;
 *   their machine is the engine.
 *
 * A loopback address is a trustworthy origin, so an HTTPS page reaching
 * http://127.0.0.1 is not mixed content. Chrome's Private Network Access rules
 * additionally want a header on the preflight, which the server sends.
 *
 * ui/app.js is untouched by all of this: it calls the same six endpoints and
 * never learns which side answered. That is the point — one front end, not a
 * hosted copy and a local copy drifting apart.
 *
 * The probe never blocks startup. app.js loads immediately in demo mode and
 * the detection runs behind it; if a local server turns up, the switch is a
 * variable assignment, because app.js resolves both the API target and the
 * file base at call time. Blocking on the probe cost six seconds of dead page
 * for the common case of nobody running anything locally. */

(function () {
  'use strict';

  const PORTS = [8000, 8001, 8080];
  const PROBE_MS = 1200;
  const STORE_KEY = 'kpiBridgePort';

  const realFetch = window.fetch.bind(window);
  const BASE = new URL('.', window.location.href).href;
  const asset = (rel) => new URL(rel, BASE).href;

  let live = null;                     // e.g. 'http://127.0.0.1:8000'

  window.KPI_STATIC = true;
  window.KPI_FILES_BASE = BASE.replace(/\/$/, '');

  /* ------------------------------------------------------- static payloads */

  const json = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
  const fail = (detail, status = 501) => json({ detail }, status);

  const OFFLINE_NOTE =
    'This is the hosted showcase, which serves three pre-built companies. ' +
    'Generating a new one runs the Python pipeline, so it needs the app ' +
    'running on your own machine — press "Run locally" in the header for the ' +
    'two commands. This page picks it up automatically once it is running.';

  async function prebuilt(rel) {
    const res = await realFetch(asset(rel));
    if (!res.ok) return fail(`Missing pre-built data: ${rel}`, 404);
    try { return json(await res.json()); }
    catch (_) { return fail(`Corrupt pre-built data: ${rel}`, 500); }
  }

  function staticRoute(path, method, init) {
    if (path === '/api/samples') return prebuilt('data/samples.json');
    if (path === '/api/survey')  return prebuilt('data/survey.json');
    if (path === '/api/runs' && method === 'GET') return prebuilt('data/runs.json');

    if (path === '/api/runs' && method === 'POST') {
      let payload = {};
      try { payload = JSON.parse(init.body || '{}'); } catch (_) {}
      if (payload.mode !== 'sample') {
        return Promise.resolve(fail(
          (payload.mode === 'surprise'
            ? 'Surprise me builds a brand-new company. '
            : 'Building your own company runs the generator. ') + OFFLINE_NOTE));
      }
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
      return Promise.resolve(fail('Profiling a spreadsheet reads it with pandas. ' + OFFLINE_NOTE));
    }
    return Promise.resolve(fail(`No static stand-in for ${path}`, 404));
  }

  /* ----------------------------------------------------------- bridge mode */

  /* The local server returns root-relative artifact links (/files/<id>/...).
   * On this origin those would resolve to merogith.github.io and 404, so they
   * are rewritten to absolute loopback URLs on the way through. */
  function absolutise(value) {
    if (typeof value === 'string') {
      return value.startsWith('/files/') ? live + value : value;
    }
    if (Array.isArray(value)) return value.map(absolutise);
    if (value && typeof value === 'object') {
      const out = {};
      for (const k of Object.keys(value)) out[k] = absolutise(value[k]);
      return out;
    }
    return value;
  }

  async function proxy(path, init) {
    const res = await realFetch(live + path, init);
    const type = res.headers.get('Content-Type') || '';
    if (!type.includes('application/json')) return res;
    let body;
    try { body = await res.json(); }
    catch (_) { return res; }
    return new Response(JSON.stringify(absolutise(body)), {
      status: res.status, headers: { 'Content-Type': 'application/json' },
    });
  }

  /* ------------------------------------------------------------ the patch */

  window.fetch = function (input, init = {}) {
    const raw = typeof input === 'string' ? input : input?.url ?? '';
    if (!raw.startsWith('/api/') && !raw.startsWith('/files/')) {
      return realFetch(input, init);
    }
    if (live) return proxy(raw, init);
    if (raw.startsWith('/files/')) {
      return Promise.resolve(fail('That file only exists on a local run.', 404));
    }
    return staticRoute(raw.split('?')[0], (init.method || 'GET').toUpperCase(), init);
  };

  /* --------------------------------------------------------------- probing */

  async function probe(port) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), PROBE_MS);
    try {
      const res = await realFetch(`http://127.0.0.1:${port}/api/health`,
                                  { signal: ctrl.signal, cache: 'no-store' });
      if (!res.ok) return false;
      const body = await res.json();
      return body && body.status === 'ok';
    } catch (_) {
      return false;                    // refused connection is the normal case
    } finally {
      clearTimeout(timer);
    }
  }

  async function connect() {
    let remembered = null;
    try { remembered = localStorage.getItem(STORE_KEY); } catch (_) {}
    const order = remembered
      ? [Number(remembered), ...PORTS.filter((p) => p !== Number(remembered))]
      : PORTS;

    // Probed together, not in turn. Serially these cost ~2s each even against a
    // closed port, which is three times the wait for the usual answer of "no".
    const hits = await Promise.all(order.map(async (p) => (await probe(p)) ? p : null));
    const port = hits.find((p) => p !== null);

    if (port) {
      live = `http://127.0.0.1:${port}`;
      window.KPI_STATIC = false;
      window.KPI_FILES_BASE = live;
      try { localStorage.setItem(STORE_KEY, String(port)); } catch (_) {}
      return true;
    }
    live = null;
    window.KPI_STATIC = true;
    window.KPI_FILES_BASE = BASE.replace(/\/$/, '');
    return false;
  }

  /* ------------------------------------------------------------ status UI */

  const RUN_STEPS = [
    'git clone https://github.com/merogith/MasterBI.git',
    'cd MasterBI && pip install -r requirements.txt',
    'python -m kpi_maker serve',
  ];

  function injectStyles() {
    const css = document.createElement('style');
    css.textContent = `
      .bridge-pill{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line,#d8d8d2);
        background:var(--card,#fff);color:var(--ink,#1a1a18);border-radius:999px;padding:6px 13px;
        font:inherit;font-size:13px;cursor:pointer}
      .bridge-pill:hover{border-color:var(--ink,#1a1a18)}
      .bridge-dot{width:8px;height:8px;border-radius:50%;background:var(--muted,#8a8a82);flex:none}
      .bridge-pill.on .bridge-dot{background:var(--good,#2e7d5b);box-shadow:0 0 0 3px rgba(46,125,91,.18)}
      .bridge-pill.pending{border-color:var(--warn,#b8860b)}
      .bridge-pill.pending .bridge-dot{background:var(--warn,#b8860b);animation:bridge-blink 1.4s ease-in-out infinite}
      @keyframes bridge-blink{50%{opacity:.35}}
      .bridge-sheet{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;
        justify-content:center;z-index:60;padding:20px}
      .bridge-box{background:var(--card,#fff);color:var(--ink,#1a1a18);border:1px solid var(--line,#d8d8d2);
        border-radius:14px;max-width:620px;width:100%;padding:26px 28px;max-height:88vh;overflow:auto}
      .bridge-box h2{margin:0 0 6px;font-size:20px}
      .bridge-box p{margin:0 0 14px;color:var(--muted,#6b6b64);line-height:1.55;font-size:14px}
      .bridge-box ol{margin:0 0 16px;padding-left:20px}
      .bridge-box li{margin-bottom:9px;font-size:14px}
      .bridge-box code{display:block;background:var(--bg,#f7f7f4);border:1px solid var(--line,#e4e4de);
        border-radius:7px;padding:9px 11px;font-size:12.5px;overflow-x:auto;white-space:pre;margin-top:5px}
      .bridge-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}`;
    document.head.appendChild(css);
  }

  const LABEL = {
    on:      ['Running on your computer', 'bridge-pill on'],
    off:     ['Demo mode · Run locally',  'bridge-pill'],
    pending: ['Local app found · switch', 'bridge-pill pending'],
  };

  function renderPill(stateName) {
    let pill = document.getElementById('bridge-pill');
    if (!pill) {
      pill = document.createElement('button');
      pill.id = 'bridge-pill';
      pill.innerHTML = '<span class="bridge-dot"></span><span class="bridge-text"></span>';
      pill.addEventListener('click', onPillClick);
      const nav = document.querySelector('.topnav');
      if (!nav) return;
      nav.insertBefore(pill, nav.firstChild);
    }
    const [text, cls] = LABEL[stateName];
    pill.className = cls;
    pill.querySelector('.bridge-text').textContent = text;
    pill.title = {
      on: `Connected to ${live} — every mode works and runs are saved on your machine.`,
      off: 'Showing three pre-built companies. Click for the commands that unlock everything.',
      pending: 'Your local app is running. Click to switch to it — this reloads the page.',
    }[stateName];

    const notice = document.querySelector('.static-notice');
    if (notice) notice.hidden = stateName === 'on';
  }

  async function onPillClick() {
    const pill = document.getElementById('bridge-pill');
    pill.querySelector('.bridge-text').textContent = 'Looking…';
    const found = await connect();
    if (found) { location.reload(); return; }   // cleanest way to reset app state
    renderPill('off');
    openSheet();
  }

  function openSheet() {
    const sheet = document.createElement('div');
    sheet.className = 'bridge-sheet';
    sheet.innerHTML = `
      <div class="bridge-box" role="dialog" aria-modal="true" aria-label="Run locally">
        <h2>Unlock every mode</h2>
        <p>This page is showing three pre-built companies. Building your own,
           uploading a spreadsheet and Surprise me all run a Python pipeline,
           which a static host cannot do — so they run on your machine instead.
           Start the app locally and <strong>this same page</strong> connects to
           it automatically. Your runs are then saved in your own
           <code style="display:inline;padding:1px 5px">runs/</code> folder and
           stay there.</p>
        <ol>${RUN_STEPS.map((s) => `<li><code>${s}</code></li>`).join('')}</ol>
        <p>Needs Python 3.9 or newer. Then reload this page, or press
           <em>Check again</em>.</p>
        <div class="bridge-actions">
          <button class="ghost" data-close>Close</button>
          <button class="primary" data-retry>Check again</button>
        </div>
      </div>`;
    const close = () => sheet.remove();
    sheet.addEventListener('click', async (e) => {
      if (e.target === sheet || e.target.closest('[data-close]')) return close();
      if (e.target.closest('[data-retry]')) {
        const btn = e.target.closest('[data-retry]');
        btn.textContent = 'Checking…';
        if (await connect()) { location.reload(); return; }
        btn.textContent = 'Still not found — check again';
      }
    });
    document.body.appendChild(sheet);
  }

  /* -------------------------------------------------------------- startup */

  injectStyles();
  renderPill('off');

  // Load the app first; detection catches up behind it.
  const tag = document.createElement('script');
  tag.src = asset('app.js');
  document.body.appendChild(tag);

  connect().then((found) => {
    if (!found) { renderPill('off'); return; }

    // Upgrading silently is only safe while nothing is on screen from the demo
    // data. Once a pre-built run is open, its id exists only in this build —
    // routing the next call to the local server would 404 on a page that looks
    // fine. Offer the switch instead of performing it.
    let busy = false;
    try { busy = typeof state !== 'undefined' && state.runId !== null; } catch (_) {}
    if (busy) {
      live = null;                       // stay on demo data until they choose
      window.KPI_STATIC = true;
      window.KPI_FILES_BASE = BASE.replace(/\/$/, '');
      renderPill('pending');
    } else {
      renderPill('on');
    }
  });
})();
