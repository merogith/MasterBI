/* KPI Dashboard Maker — front end.
 *
 * Vanilla JS on purpose: the whole app is five screens over a six-endpoint API,
 * and a build step would cost more than it returns. Swapping this for React
 * later touches nothing server-side (ROADMAP M8).
 *
 * Value formatting mirrors kpi_maker/fmt.py exactly. A number must read the
 * same here, in the PDF and in the workbook — three formatters would guarantee
 * they eventually disagree. */

const API = '';
// Empty when the FastAPI server serves the app from the domain root. The
// static GitHub Pages build lives under /MasterBI/, so the shim sets this and
// the one hand-built download link below stays correct in both.
const FILES = window.KPI_FILES_BASE ?? '';
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STATUS_LABEL = {
  green: 'On track', amber: 'Watch', red: 'Off track',
  unscored: 'No target', unknown: 'No data',
};
const STATUS_GLYPH = { green: '●', amber: '▲', red: '■', unscored: '◇', unknown: '○' };
const SEVERITY_LABEL = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', positive: 'Strength',
};
const CURRENCY_SYMBOL = { USD: '$', EUR: '€', GBP: '£', TRY: '₺', JPY: '¥', SEK: 'kr', CAD: 'C$', AUD: 'A$', INR: '₹', AED: 'AED ' };

const state = {
  survey: null,
  answers: {},
  step: 0,
  steps: [],
  runId: null,
  summary: null,
  poll: null,
  kpiFilter: 'all',
  kpiSearch: '',
};

/* ---------------------------------------------------------------- utils */

function fmtValue(value, unit, currency = 'USD') {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const v = Number(value);
  if (unit === 'pct')  return (v * 100).toFixed(1) + '%';
  if (unit === 'currency') {
    const sym = CURRENCY_SYMBOL[currency] ?? '';
    for (const [t, s] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) {
      if (Math.abs(v) >= t) return sym + (v / t).toFixed(1) + s;
    }
    return sym + Math.round(v).toLocaleString();
  }
  if (unit === 'months') return v.toFixed(1) + ' mo';
  if (unit === 'days')   return v.toFixed(1) + ' d';
  if (unit === 'hours')  return v.toFixed(1) + ' h';
  if (unit === 'count' || unit === 'score') return Math.round(v).toLocaleString();
  return v.toFixed(2);
}

const fmtBytes = (n) =>
  n > 1e6 ? (n / 1e6).toFixed(1) + ' MB'
  : n > 1e3 ? Math.round(n / 1e3) + ' KB'
  : n + ' B';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Acronyms that must not be sentence-cased into "Mrr" / "Cac".
const ACRONYMS = new Set(['mrr', 'arr', 'kpi', 'cac', 'ltv', 'nrr', 'grr', 'sql',
                          'mql', 'smb', 'roi', 'b2b', 'b2c', 'ebitda', 'rnd', 'ga']);
const titleCase = (s) => String(s ?? '').replace(/_/g, ' ')
  .replace(/\b[\w&]+/g, (w) =>
    ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1));

function statusChip(status) {
  const s = status || 'unknown';
  return `<span class="chip ${s}"><span class="chip-glyph" aria-hidden="true">${STATUS_GLYPH[s] || '○'}</span>${STATUS_LABEL[s] || s}</span>`;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' }, ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

/* --------------------------------------------------------------- router */

function show(view) {
  $$('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
  if (view === 'samples') loadSamples();
  if (view === 'survey')  loadSurvey();
}

document.addEventListener('click', (e) => {
  const nav = e.target.closest('[data-nav]');
  if (nav) { e.preventDefault(); show(nav.dataset.nav); }
});

/* ---------------------------------------------------------------- theme */

function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  $('#btn-theme').textContent = mode === 'dark' ? 'Light' : 'Dark';
  try { localStorage.setItem('kpiUiTheme', mode); } catch (_) {}
}
$('#btn-theme').addEventListener('click', () => {
  setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
});
setTheme(
  (() => { try { return localStorage.getItem('kpiUiTheme'); } catch (_) { return null; } })()
  || (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
);

/* -------------------------------------------------------------- samples */

let samplesLoaded = false;
async function loadSamples() {
  if (samplesLoaded) return;
  const grid = $('#sample-grid');
  grid.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const samples = await api('/api/samples');
    if (!samples.length) { grid.innerHTML = '<p class="empty">No samples found.</p>'; return; }
    grid.innerHTML = samples.map((s) => `
      <article class="sample-card">
        <div>
          <h2>${esc(s.title)}</h2>
          <div class="sample-tagline">${esc(s.tagline)}</div>
        </div>
        <p class="sample-story">${esc(s.story)}</p>
        <div class="sample-stats">
          <div><div class="stat-k">Revenue</div><div class="stat-v">${fmtValue(s.revenue, 'currency', s.currency)}</div></div>
          <div><div class="stat-k">Customers</div><div class="stat-v">${Number(s.customers).toLocaleString()}</div></div>
          <div><div class="stat-k">Headcount</div><div class="stat-v">${s.headcount}</div></div>
          <div><div class="stat-k">Stage</div><div class="stat-v">${titleCase(s.stage)}</div></div>
        </div>
        <div class="tag-row">${(s.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>
        <p class="watch-for"><strong>Watch for:</strong> ${esc(s.watch_for || '')}</p>
        <button class="primary" data-sample="${esc(s.id)}">Generate this pack</button>
      </article>`).join('');
    samplesLoaded = true;
  } catch (err) {
    grid.innerHTML = `<p class="empty">Could not load samples: ${esc(err.message)}</p>`;
  }
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-sample]');
  if (btn) startRun({ mode: 'sample', sample_id: btn.dataset.sample });
});

$('#btn-surprise').addEventListener('click', () => {
  startRun({ mode: 'surprise', seed: Math.floor(Math.random() * 1e7) });
});

/* --------------------------------------------------------------- survey */

async function loadSurvey() {
  if (state.survey) return;
  try {
    state.survey = await api('/api/survey');
    buildSurveySteps();
  } catch (err) {
    toast('Could not load the survey: ' + err.message, true);
  }
}

function buildSurveySteps() {
  const { questions, groups, optional_group: optionalGroup } = state.survey;
  state.steps = groups.map((g) => ({
    title: g,
    questions: questions.filter((q) => q.group === g),
    // The deep-dive block is skippable wholesale. Marking it lets the nav
    // offer "skip this section" rather than demanding 5 more answers.
    optional: g === optionalGroup,
  }));
  state.steps.push({ title: 'Name and review', questions: [], review: true });

  $('#survey-steps').innerHTML = state.steps.map((step, i) => `
    <fieldset class="survey-step${i === 0 ? ' active' : ''}" data-step="${i}">
      <legend class="survey-group-title">${esc(step.title)}</legend>
      ${step.optional ? `<p class="step-note">
          Optional. Every answer here replaces an assumption with a fact — skip
          the section and we use sector benchmarks instead, clearly footnoted.
        </p>` : ''}
      ${step.review ? reviewStepHtml() : step.questions.map(questionHtml).join('')}
    </fieldset>`).join('');

  state.step = 0;
  renderStep();
}

function questionHtml(q) {
  return `
    <div class="question" data-qid="${esc(q.id)}">
      <div class="question-text">${esc(q.text)}</div>
      <div class="question-help">${esc(q.help)}</div>
      ${q.unlocks ? `<div class="question-unlocks">Unlocks: ${esc(q.unlocks)}</div>` : ''}
      <div class="options">
        ${q.options.map((o) => {
          const disabled = o.disabled ? ' disabled' : '';
          const cls = [
            'option',
            o.value === '__unknown__' ? 'unknown' : '',
            o.disabled ? 'disabled' : '',
          ].filter(Boolean).join(' ');
          return `
          <label class="${cls}">
            <input type="radio" name="${esc(q.id)}" value="${esc(o.value)}"${disabled}>
            <span>${esc(o.label)}</span>
            ${o.disabled ? '<span class="option-soon">Soon</span>' : ''}
          </label>`;
        }).join('')}
      </div>
    </div>`;
}

function reviewStepHtml() {
  return `
    <div class="question">
      <div class="question-text">What should we call the company?</div>
      <div class="question-help">Optional — we'll invent a name if you leave this blank.</div>
      <input class="name-input" id="company-name" placeholder="e.g. Northwind Analytics" maxlength="60">
    </div>
    <div class="question">
      <div class="question-text">Your answers</div>
      <div class="question-help">
        Anything marked <em>assumed</em> will be filled from sector benchmarks and
        footnoted in the report appendix rather than presented as fact.
      </div>
      <div class="review-list" id="review-list"></div>
    </div>`;
}

$('#survey-form').addEventListener('change', (e) => {
  if (e.target.type !== 'radio') return;
  state.answers[e.target.name] = e.target.value;
  const group = e.target.closest('.options');
  $$('.option', group).forEach((o) => o.classList.toggle('selected', $('input', o).checked));
});

function renderStep() {
  $$('.survey-step').forEach((el, i) => el.classList.toggle('active', i === state.step));
  const total = state.steps.length;
  const step = state.steps[state.step];
  $('#survey-progress').style.width = `${((state.step) / (total - 1)) * 100}%`;
  $('#survey-step-label').textContent = `Step ${state.step + 1} of ${total} · ${step.title}`;
  $('#survey-back').style.visibility = state.step === 0 ? 'hidden' : 'visible';
  $('#survey-next').textContent = step.review ? 'Generate my pack' : 'Next';
  $('#survey-skip').hidden = !!step.review;
  $('#survey-skip').textContent = step.optional
    ? 'Skip this section' : 'Skip — use averages';
  if (step.review) renderReview();
}

function renderReview() {
  const rows = state.survey.questions.map((q) => {
    const value = state.answers[q.id];
    const answered = value && value !== '__unknown__';
    if (!answered && q.optional) {
      // An unanswered optional question is not an assumption the user made —
      // it is one we are making. Say which.
      return `<div class="review-row">
          <span class="k">${esc(q.text)}</span>
          <span class="v assumed">sector benchmark</span>
        </div>`;
    }
    const effective = answered ? value : q.default;
    const option = q.options.find((o) => o.value === effective);
    return `<div class="review-row">
        <span class="k">${esc(q.text)}</span>
        <span class="v ${answered ? '' : 'assumed'}">${esc(option ? option.label : effective)}${answered ? '' : ' · assumed'}</span>
      </div>`;
  }).join('');
  $('#review-list').innerHTML = rows;
}

$('#survey-back').addEventListener('click', () => {
  if (state.step > 0) { state.step--; renderStep(); }
});

$('#survey-skip').addEventListener('click', () => {
  // "Use averages" is a real answer, not a skip: it records __unknown__ so the
  // provenance shows the field was defaulted rather than silently guessed.
  state.steps[state.step].questions.forEach((q) => {
    if (!state.answers[q.id]) state.answers[q.id] = '__unknown__';
  });
  advance();
});

$('#survey-next').addEventListener('click', advance);

function advance() {
  const step = state.steps[state.step];
  if (step.review) {
    startRun({
      mode: 'survey',
      answers: state.answers,
      company_name: $('#company-name').value.trim() || null,
      seed: Math.floor(Math.random() * 1e7),
    });
    return;
  }
  // Optional questions never block progress — the derived assumption stands.
  const missing = step.optional
    ? [] : step.questions.filter((q) => !state.answers[q.id]);
  if (missing.length) {
    toast(`Answer ${missing.length} more question${missing.length > 1 ? 's' : ''}, or use "Skip — use averages".`, true);
    const el = $(`.question[data-qid="${missing[0].id}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  state.step++;
  renderStep();
}

/* -------------------------------------------------------------- builder */

const dropzone = $('#dropzone');
const fileInput = $('#file-input');

dropzone.addEventListener('click', () => fileInput.click());
['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) uploadFile(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  const box = $('#upload-result');
  box.hidden = false;
  box.innerHTML = '<p class="empty">Profiling columns…</p>';
  const body = new FormData();
  body.append('file', file);
  try {
    const res = await fetch(API + '/api/upload', { method: 'POST', body });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    box.innerHTML = `
      <h2 class="section-title" style="margin-top:28px">${esc(data.filename)}</h2>
      <p class="section-sub">${data.rows.toLocaleString()} rows · ${data.columns.length} columns</p>
      <div class="table-wrap"><table>
        <thead><tr>
          <th>Column</th><th>Inferred type</th><th class="num">Non-null</th>
          <th class="num">Missing</th><th class="num">Unique</th><th>Sample values</th>
        </tr></thead>
        <tbody>${data.columns.map((c) => `
          <tr>
            <td><strong>${esc(c.name)}</strong></td>
            <td>${esc(c.inferred_type)}</td>
            <td class="num">${c.non_null.toLocaleString()}</td>
            <td class="num">${(c.null_pct * 100).toFixed(1)}%</td>
            <td class="num">${c.unique.toLocaleString()}</td>
            <td>${esc((c.sample || []).join(' · '))}</td>
          </tr>`).join('')}</tbody>
      </table></div>
      <p class="watch-for" style="margin-top:14px">${esc(data.note)}</p>`;
  } catch (err) {
    box.innerHTML = `<p class="empty">Upload failed: ${esc(err.message)}</p>`;
  }
}

/* ------------------------------------------------------------ run + poll */

async function startRun(payload) {
  show('running');
  $('#run-company').textContent = 'Starting…';
  $('#run-steps').innerHTML = '';
  try {
    const res = await api('/api/runs', { method: 'POST', body: JSON.stringify(payload) });
    state.runId = res.run_id;
    $('#run-company').textContent = res.company || 'Generating…';
    pollRun();
  } catch (err) {
    toast('Could not start the run: ' + err.message, true);
    show('home');
  }
}

const EXPECTED_STEPS = [
  'Validating profile',
  'Selecting KPIs from the library',
  'Generating data and reconciling',
  'Computing metrics and detecting findings',
  'Rendering dashboard, report, deck and workbook',
];

function pollRun() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    if (!state.runId) return;
    try {
      const run = await api(`/api/runs/${state.runId}`);
      const done = run.steps || [];
      $('#run-steps').innerHTML = EXPECTED_STEPS.map((label) => {
        const complete = done.includes(label);
        return `<li class="${complete ? '' : 'pending'}">${esc(label)}</li>`;
      }).join('');

      if (run.status === 'done') {
        clearInterval(state.poll);
        state.summary = run.summary;
        renderResults(run.summary);
        show('results');
      } else if (run.status === 'error') {
        clearInterval(state.poll);
        toast('Run failed: ' + (run.error || 'unknown error'), true);
        show('home');
      }
    } catch (err) {
      clearInterval(state.poll);
      toast('Lost contact with the server: ' + err.message, true);
      show('home');
    }
  }, 700);
}

$('#run-cancel').addEventListener('click', () => {
  clearInterval(state.poll);
  state.runId = null;
  show('home');
});

/* -------------------------------------------------------------- results */

function renderResults(s) {
  const cur = s.currency;
  $('#res-company').textContent = s.company;
  $('#res-meta').textContent = [
    s.business_model.toUpperCase(), s.customer_type, s.country, s.period,
    `objective: ${titleCase(s.objective)}`,
    `for the ${titleCase(s.audience).toLowerCase()}`,
    `profile confidence ${Math.round(s.confidence * 100)}%`,
  ].filter(Boolean).join(' · ');

  const dash = s.artifacts.find((a) => a.name === 'dashboard.html');
  $('#res-open-dashboard').href = dash ? dash.url : '#';
  $('#res-frame').src = dash ? dash.url : 'about:blank';

  $('#res-warnings').innerHTML = (s.warnings || [])
    .map((w) => `<div class="warn-banner">${esc(w)}</div>`).join('');

  // Tiles
  $('#res-tiles').innerHTML = (s.tiles || []).slice(0, 6).map((k) => {
    let sub = '';
    if (k.prior_year !== null && k.prior_year !== undefined && k.current !== null) {
      const delta = k.current - k.prior_year;
      const up = delta >= 0;
      const better = k.direction === 'higher_is_better' ? up : !up;
      const text = k.unit === 'pct'
        ? `${Math.abs(delta * 100).toFixed(1)} pts`
        : `${Math.abs(k.prior_year) ? Math.round(Math.abs(delta / k.prior_year) * 100) : 0}%`;
      sub = `<span style="color:var(--${better ? 'good' : 'critical'})">${up ? '▲' : '▼'} ${text}</span> vs last year`;
    }
    return `
      <article class="tile">
        <div class="tile-head">
          <span class="tile-name">${esc(k.name)}</span>${statusChip(k.status)}
        </div>
        <div class="tile-value">${fmtValue(k.current, k.unit, cur)}</div>
        <div class="tile-sub">${sub}</div>
      </article>`;
  }).join('');

  // Findings
  $('#res-findings').innerHTML = (s.findings || []).slice(0, 10).map((f) => `
    <li class="finding ${esc(f.severity)}">
      <div class="finding-head">
        <span class="chip ${f.severity === 'positive' ? 'green' : f.severity === 'medium' ? 'amber' : 'red'}">
          ${esc(SEVERITY_LABEL[f.severity] || f.severity)}
        </span>
        <h3>${esc(f.title)}</h3>
      </div>
      <p>${esc(f.statement)}</p>
      ${f.recommendation ? `<p class="rec"><strong>So what:</strong> ${esc(f.recommendation)}</p>` : ''}
    </li>`).join('') || '<p class="empty">No findings.</p>';

  renderScorecard();

  // Dropped KPIs — "why isn't X on my dashboard?" must always have an answer.
  const dropped = Object.entries(s.dropped || {});
  $('#res-dropped').innerHTML = dropped.length ? `
    <summary>${dropped.length} KPIs were considered but not selected — why?</summary>
    <ul>${dropped.map(([k, v]) => `<li><code>${esc(k)}</code> — ${esc(v)}</li>`).join('')}</ul>` : '';

  // Downloads
  $('#res-downloads').innerHTML = (s.artifacts || []).map((a) => `
    <a class="dl-card" href="${esc(a.url)}" download>
      <span class="dl-kind">${esc(a.kind)}</span>
      <span class="dl-label">${esc(a.label)}</span>
      <span class="dl-blurb">${esc(a.blurb)}</span>
      <span class="dl-size">${fmtBytes(a.size)}</span>
    </a>`).join('');

  loadTables();
  activateTab('overview');
}

function renderScorecard() {
  const s = state.summary;
  if (!s) return;
  const cur = s.currency;
  const term = state.kpiSearch.toLowerCase();

  const rows = (s.kpis || []).filter((k) => {
    if (state.kpiFilter !== 'all' && k.status !== state.kpiFilter) return false;
    if (term && !String(k.name).toLowerCase().includes(term)) return false;
    return true;
  });

  const byPerspective = {};
  rows.forEach((k) => { (byPerspective[k.perspective] ||= []).push(k); });

  const body = Object.entries(byPerspective).map(([perspective, items]) => `
    <tr class="group-row"><td colspan="7">${esc(titleCase(perspective))}</td></tr>
    ${items.sort((a, b) => (a.tier ?? 9) - (b.tier ?? 9)).map((k) => `
      <tr>
        <td><strong>${esc(k.name)}</strong> <span style="color:var(--muted);font-size:11px">${esc(k.timing)}</span></td>
        <td class="num">${fmtValue(k.current, k.unit, cur)}</td>
        <td class="num">${fmtValue(k.prior_year, k.unit, cur)}</td>
        <td class="num">${fmtValue(k.target, k.unit, cur)}</td>
        <td class="num">${fmtValue(k.benchmark_p50, k.unit, cur)}</td>
        <td>${statusChip(k.status)}</td>
        <td style="color:var(--muted)">${esc(titleCase(k.benchmark_position || '—'))}</td>
      </tr>`).join('')}`).join('');

  $('#res-scorecard').innerHTML = `
    <thead><tr>
      <th>KPI</th><th class="num">Current</th><th class="num">12mo ago</th>
      <th class="num">Target</th><th class="num">Cohort median</th>
      <th>Status</th><th>vs cohort</th>
    </tr></thead>
    <tbody>${body || '<tr><td colspan="7" class="empty">No KPIs match.</td></tr>'}</tbody>`;
}

$('#kpi-search').addEventListener('input', (e) => {
  state.kpiSearch = e.target.value; renderScorecard();
});
$('#kpi-filters').addEventListener('click', (e) => {
  const btn = e.target.closest('.chip-btn');
  if (!btn) return;
  state.kpiFilter = btn.dataset.status;
  $$('.chip-btn', $('#kpi-filters')).forEach((b) => b.classList.toggle('active', b === btn));
  renderScorecard();
});

/* ------------------------------------------------------------ data tab */

async function loadTables() {
  const rail = $('#data-tables');
  rail.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const tables = await api(`/api/runs/${state.runId}/tables`);
    if (!tables.length) { rail.innerHTML = '<p class="empty">No tables.</p>'; return; }
    rail.innerHTML = tables.map((t, i) => `
      <button class="rail-btn${i === 0 ? ' active' : ''}" data-table="${esc(t.name)}">
        ${esc(titleCase(t.name))}
        <span class="rail-rows">${t.rows.toLocaleString()} rows</span>
      </button>`).join('');
    loadTable(tables[0].name);
  } catch (err) {
    rail.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

$('#data-tables').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-table]');
  if (!btn) return;
  $$('.rail-btn').forEach((b) => b.classList.toggle('active', b === btn));
  loadTable(btn.dataset.table);
});

async function loadTable(name) {
  const head = $('#data-head');
  const table = $('#data-preview');
  head.innerHTML = '';
  table.innerHTML = '<tbody><tr><td class="empty">Loading…</td></tr></tbody>';
  try {
    const data = await api(`/api/runs/${state.runId}/table/${name}`);
    head.innerHTML = `
      <div>
        <strong>${esc(titleCase(data.table))}</strong>
        <span style="color:var(--muted)"> · ${data.rows.toLocaleString()} rows · ${data.columns.length} columns${data.truncated ? ` · showing first ${data.preview.length}` : ''}</span>
      </div>
      <a class="ghost" href="${FILES}/files/${state.runId}/data/${esc(data.table)}.csv" download>Download CSV</a>`;
    table.innerHTML = `
      <thead><tr>${data.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${data.preview.map((row) => `
        <tr>${data.columns.map((c) => {
          const v = row[c];
          const num = typeof v === 'number';
          return `<td class="${num ? 'num' : ''}">${esc(num ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2)) : v)}</td>`;
        }).join('')}</tr>`).join('')}</tbody>`;
  } catch (err) {
    table.innerHTML = `<tbody><tr><td class="empty">${esc(err.message)}</td></tr></tbody>`;
  }
}

/* ------------------------------------------------------------ tabs */

function activateTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === name));
}
$$('.tab').forEach((t) => t.addEventListener('click', () => activateTab(t.dataset.tab)));

/* ------------------------------------------------------------ history */

const drawer = $('#drawer');
const scrim = $('#drawer-scrim');

async function openDrawer() {
  drawer.hidden = false; scrim.hidden = false;
  const list = $('#drawer-list');
  list.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const runs = await api('/api/runs');
    list.innerHTML = runs.length ? runs.map((r) => `
      <div class="run-row">
        <div>
          <div class="r-name">${esc(r.company || 'Untitled')}</div>
          <div class="r-meta">${esc(r.status)} · ${esc(r.mode || '')}</div>
        </div>
        ${r.status === 'done'
          ? `<button class="ghost" data-open-run="${esc(r.run_id)}">Open</button>`
          : ''}
      </div>`).join('') : '<p class="empty">No runs yet.</p>';
  } catch (err) {
    list.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

function closeDrawer() { drawer.hidden = true; scrim.hidden = true; }
$('#btn-history').addEventListener('click', openDrawer);
$('#drawer-close').addEventListener('click', closeDrawer);
scrim.addEventListener('click', closeDrawer);

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-open-run]');
  if (!btn) return;
  closeDrawer();
  try {
    const run = await api(`/api/runs/${btn.dataset.openRun}`);
    if (!run.summary) { toast('That run has no summary.', true); return; }
    state.runId = btn.dataset.openRun;
    state.summary = run.summary;
    renderResults(run.summary);
    show('results');
  } catch (err) {
    toast(err.message, true);
  }
});

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

show('home');
