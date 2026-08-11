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
// Empty when the FastAPI server serves this from the domain root. The hosted
// build sets it — to its own subdirectory, or to the loopback address once it
// detects a local server — so the one hand-built download link below stays
// correct in all three cases. Read at call time, not at load: the hosted build
// probes for a local server in the background and may set it after this file
// has already been parsed.
const filesBase = () => window.KPI_FILES_BASE ?? '';
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
  tableNames: [],
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
          // `approximate` is not `disabled`. An option that runs on the
          // cross-sector pack is a real choice with a caveat, and offering it
          // with the caveat attached beats the old "Soon" pill, which showed
          // people two sectors they could look at and not pick.
          const disabled = o.disabled ? ' disabled' : '';
          const cls = [
            'option',
            o.value === '__unknown__' ? 'unknown' : '',
            o.disabled ? 'disabled' : '',
            o.approximate ? 'approximate' : '',
          ].filter(Boolean).join(' ');
          const badge = o.disabled
            ? '<span class="option-soon">Soon</span>'
            : (o.note ? `<span class="option-note">${esc(o.note)}</span>` : '');
          return `
          <label class="${cls}">
            <input type="radio" name="${esc(q.id)}" value="${esc(o.value)}"${disabled}>
            <span>${esc(o.label)}</span>
            ${badge}
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
    const res = await fetch(API + '/api/ingest/profile', { method: 'POST', body });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    // `/api/ingest/profile` returns strictly more than the route this used to
    // call: a semantic type per column rather than a four-way dtype guess,
    // the inferences the reader had to make (encoding, delimiter, a title
    // block above the real header), the problems it found, and which fact
    // table the file could become. Showing all of it is the difference
    // between a column dump and an answer.
    const prof = data.profile || {};
    const cols = prof.columns || [];
    const notes = (data.read && data.read.notes) || [];
    // Ordered by how many fields matched *confidently*, not by the server's
    // order. `detect_shape` ranks by ratio, so a five-field shape with one
    // match outranks an eight-field shape with two — which puts `crm_deals`
    // above `pnl_export` for a literal P&L export. Fixing that ranking is part
    // of the mapping work; showing the strongest candidate first costs nothing
    // and is true either way.
    const confidentCount = (sh) =>
      (sh.matches || []).filter((m) => m.status === 'confident').length;
    const shapes = (data.shapes || []).slice()
      .sort((a, b) => confidentCount(b) - confidentCount(a));
    const problems = prof.problems || [];

    box.innerHTML = `
      <h2 class="section-title" style="margin-top:28px">${esc(data.filename)}</h2>
      <p class="section-sub">${(prof.rows || 0).toLocaleString()} rows · ${cols.length} columns</p>

      ${notes.length ? `<div class="notice"><strong>How this file was read.</strong>
        <ul>${notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div>` : ''}

      ${shapes.length ? `<div class="notice"><strong>What this could become.</strong>
        <ul>${shapes.map((sh) => {
          const m = sh.matches || [];
          const sure = m.filter((x) => x.status === 'confident').length;
          const missing = (sh.missing_required || []).length;
          return `<li><strong>${esc(sh.shape || '')}</strong> → <code>${esc(sh.target_table || '')}</code>
            — ${sure} of ${m.length} field${m.length === 1 ? '' : 's'} matched confidently${
              missing ? `, ${missing} required field${missing === 1 ? '' : 's'} missing` : ''}
            ${sh.usable ? '' : ' <em>(not usable as-is)</em>'}</li>`;
        }).join('')}</ul>
        <p class="watch-for">A low match count means the column names did not
           resemble that shape — not that your data is wrong. Correcting a
           mapping by hand is part of the flow being built next.</p></div>` : ''}

      ${problems.length ? `<div class="notice"><strong>Worth a look.</strong>
        <ul>${problems.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}

      <div class="table-wrap"><table>
        <thead><tr>
          <th>Column</th><th>Reads as</th><th class="num">Non-null</th>
          <th class="num">Missing</th><th class="num">Unique</th><th>Sample values</th>
        </tr></thead>
        <tbody>${cols.map((c) => `
          <tr>
            <td><strong>${esc(c.name)}</strong>${(c.problems || []).length
              ? `<br><span class="watch-for">${esc(c.problems.join(' · '))}</span>` : ''}</td>
            <td>${esc(c.semantic || c.dtype || '')}</td>
            <td class="num">${(c.non_null || 0).toLocaleString()}</td>
            <td class="num">${((c.null_pct || 0) * 100).toFixed(1)}%</td>
            <td class="num">${(c.unique || 0).toLocaleString()}</td>
            <td>${esc((c.samples || []).join(' · '))}</td>
          </tr>`).join('')}</tbody>
      </table></div>

      <div class="surprise-row" style="margin-top:24px">
        <div>
          <h3>Ready to use this file?</h3>
          <p>Answer the profile questions first — a run needs to know who the
             company is before it can read its numbers. Then adopt this file in
             the Studio's Source panel.</p>
        </div>
        <button class="primary" data-nav="survey">Build the profile</button>
      </div>`;
  } catch (err) {
    box.innerHTML = `<p class="empty">Upload failed: ${esc(err.message)}</p>`;
  }
}

/* ------------------------------------------------------------ run + poll */

function resetRunProgress() {
  $('#run-steps').innerHTML = '';
  $('#run-bar-fill').style.width = '0%';
  $('#run-bar').setAttribute('aria-valuenow', '0');
  $('#run-stage').textContent = 'Queued';
  $('#run-count').textContent = '';
  $('#run-note').hidden = true;
  $('#run-cancel').disabled = false;
}

async function startRun(payload) {
  show('running');
  $('#run-company').textContent = 'Starting…';
  resetRunProgress();
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

/* The client used to hold its own list of five step labels and tick them off
   by string equality against what the server echoed. That is how the two drifted:
   the engine has seventeen stages and the screen knew about five of them, none
   of which were reported until the whole run had already finished. Render what
   the server sends and nothing else. */
function renderProgress(progress) {
  const stages = (progress && progress.stages) || [];
  const total = (progress && progress.total) || 0;
  const done = (progress && progress.done) || 0;
  const current = progress && progress.current;

  const pct = total ? Math.round((done / total) * 100) : 0;
  $('#run-bar-fill').style.width = `${pct}%`;
  $('#run-bar').setAttribute('aria-valuenow', String(pct));

  if (current) $('#run-stage').textContent = current.label;

  const eta = progress ? progress.eta_seconds : null;
  const left = eta === null || eta === undefined || eta < 0.5
    ? 'almost done'
    : `about ${eta < 1 ? 1 : Math.round(eta)}s left`;
  $('#run-count').textContent = total ? `${done} of ${total} · ${left}` : '';

  $('#run-steps').innerHTML = stages.map((s) => {
    const cls = s.state === 'running' ? 'running' : s.state === 'reused' ? 'reused' : '';
    const tail = s.state === 'reused' ? ' <span class="run-count">reused</span>' : '';
    return `<li class="${cls}">${esc(s.label)}${tail}</li>`;
  }).join('');
}

/* Backoff with a ceiling. A fixed 700ms interval polled a wedged run forever,
   and a run that is a minute in does not need four requests a second. */
const POLL_FIRST_MS = 400;
const POLL_MAX_MS = 3000;

function pollRun() {
  stopPolling();
  let wait = POLL_FIRST_MS;

  const tick = async () => {
    if (!state.runId) return;
    try {
      const run = await api(`/api/runs/${state.runId}`);
      renderProgress(run.progress);

      if (run.status === 'done') {
        stopPolling();
        state.summary = run.summary;
        renderResults(run.summary);
        show('results');
        return;
      }
      if (run.status === 'cancelled') {
        stopPolling();
        state.runId = null;
        toast('Run cancelled. Finished stages were kept, so starting again is quicker.');
        show('home');
        return;
      }
      if (run.status === 'error') {
        stopPolling();
        toast('Run failed: ' + (run.error || 'unknown error'), true);
        show('home');
        return;
      }
      wait = Math.min(Math.round(wait * 1.25), POLL_MAX_MS);
      state.poll = setTimeout(tick, wait);
    } catch (err) {
      stopPolling();
      toast('Lost contact with the server: ' + err.message, true);
      show('home');
    }
  };
  state.poll = setTimeout(tick, POLL_FIRST_MS);
}

function stopPolling() {
  if (state.poll) clearTimeout(state.poll);
  state.poll = null;
}

$('#run-cancel').addEventListener('click', async () => {
  const runId = state.runId;
  if (!runId) { show('home'); return; }
  // Say what is actually happening rather than closing the screen on a request
  // that had not been sent yet: the engine finishes the stage it is inside.
  $('#run-note').hidden = false;
  $('#run-cancel').disabled = true;
  try {
    await api(`/api/runs/${runId}/cancel`, { method: 'POST' });
  } catch (err) {
    toast('Could not cancel: ' + err.message, true);
    $('#run-cancel').disabled = false;
    $('#run-note').hidden = true;
  }
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
    // The Studio's calculated-column editor needs the table list too, and this
    // is the one place it is already fetched.
    state.tableNames = tables.map((t) => t.name);
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
      <a class="ghost" href="${filesBase()}/files/${state.runId}/data/${esc(data.table)}.csv" download>Download CSV</a>`;
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

/* ==========================================================================
   Studio — edit the RunSpec, see what it costs, re-run only that.

   The rail mirrors the stage graph on the server. Every control writes one
   field of the spec and PATCHes it; the server answers with which stages that
   invalidated, and the action bar reports it before the user commits to
   waiting. Nothing here knows what a stage *does* — that stays server-side, so
   adding a stage does not mean teaching the UI about it.
   ========================================================================== */

const studio = {
  spec: null,
  original: null,
  catalog: [],
  functions: [],
  stage: 'source',
  dirty: [],
  patchTimer: null,
};

const ARTIFACT_LABEL = {
  dashboard: 'Interactive dashboard', workbook: 'Data workbook',
  report_pdf: 'Executive report (PDF)', deck_pptx: 'Board deck (PPTX)',
  doc_docx: 'Editable report (DOCX)', charts_png: 'Chart images',
  csv_bundle: 'Fact table CSVs', facts_csv: 'KPI facts table',
  json_dumps: 'Profile, KPI set, findings',
};
const ARTIFACT_COST = {
  charts_png: '~2.1s', report_pdf: '~1.0s', dashboard: '~0.3s', doc_docx: '~0.4s',
  workbook: '~0.4s', deck_pptx: '~0.2s', csv_bundle: '~0.0s', facts_csv: '~0.0s',
  json_dumps: '~0.0s',
};
const DETECTOR_LABEL = {
  status_breaches: 'RAG breaches vs target',
  benchmark_gaps: 'Position against the cohort',
  trend_breaks: 'Trend reversals',
  segment_outliers: 'Outlying segments',
  operating_leverage: 'Cost growth vs revenue growth',
  arr_bridge: 'What moved ARR',
  channel_efficiency: 'Marketing channel efficiency',
  runway: 'Cash runway',
};

async function openStudio() {
  if (!state.runId) { toast('Open a run first.', true); return; }
  show('studio');
  $('#studio-company').textContent = state.summary?.company || 'Studio';
  try {
    const [spec, options, catalog, functions, ai] = await Promise.all([
      api(`/api/runs/${state.runId}/spec`),
      api('/api/catalog/options'),
      api('/api/catalog/kpis'),
      api('/api/catalog/functions'),
      // Never fatal. The AI layer is optional by design, so a studio that
      // cannot reach this endpoint shows the panel as unavailable rather than
      // refusing to open.
      api('/api/ai/status').catch(() => ({ available: false,
        reason: 'the AI status endpoint is not available' })),
    ]);
    studio.ops = options.ops || [];
    studio.opLabels = Object.fromEntries(studio.ops.map((o) => [o.name, o.label]));
    studio.spec = spec;
    studio.original = JSON.parse(JSON.stringify(spec));
    studio.options = options;
    studio.catalog = catalog.kpis;
    studio.userIds = catalog.user_ids || [];
    studio.functions = functions.functions;
    studio.ai = ai;
    studio.aiEstimate = null;
    renderStudio();
    refreshPlan();
    loadLineage();
    loadQuality();
  } catch (err) {
    toast('Could not open the Studio: ' + err.message, true);
    show('results');
  }
}

$('#res-adjust').addEventListener('click', openStudio);

/* What the current recipe actually did, and what the data is missing. Both are
 * fetched after the panels render: neither should delay the Studio opening. */
async function loadLineage() {
  try {
    const report = await api(`/api/runs/${state.runId}/lineage`);
    studio.lineage = report.steps || [];
    if (studio.stage === 'clean') panel('clean').innerHTML = cleanPanel();
  } catch (_) { studio.lineage = []; }
}

async function loadQuality() {
  try {
    studio.quality = await api(`/api/runs/${state.runId}/quality`);
    if (studio.stage === 'source') panel('source').innerHTML = sourcePanel();
  } catch (_) { studio.quality = null; }
}

$('#studio-rail').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-stage]');
  if (!btn) return;
  studio.stage = btn.dataset.stage;
  $$('#studio-rail .rail-btn').forEach((b) => b.classList.toggle('active', b === btn));
  $$('.studio-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === studio.stage));
});

/* ------------------------------------------------------------- rendering */

function renderStudio() {
  panel('source').innerHTML = sourcePanel();
  panel('clean').innerHTML = cleanPanel();
  panel('model').innerHTML = modelPanel();
  panel('kpis').innerHTML = kpiPanel();
  panel('analysis').innerHTML = analysisPanel();
  panel('design').innerHTML = designPanel();
  refreshBrandPreview();
  panel('outputs').innerHTML = outputsPanel();
  panel('ai').innerHTML = aiPanel();
}

const panel = (name) => $(`.studio-panel[data-panel="${name}"]`);

function section(title, hint, body) {
  return `<div class="studio-section">
    <h2>${esc(title)}</h2>
    <p class="hint">${hint}</p>
    ${body}</div>`;
}

/* 1.0 is "as shipped" for both sliders, so the label says so rather than
   making the user infer it from a bare number. */
const fmtKnob = (v) => (v === undefined || v === null || v === 1
  ? 'default' : Number(v).toFixed(2).replace(/0$/, ''));

function sourcePanel() {
  const g = studio.spec.source.generator;
  const q = studio.quality;
  const isUpload = studio.spec.source.kind === 'upload';

  const coverage = q ? `
    <div class="coverage">
      <div class="coverage-head">
        <strong>${q.kpis_available}</strong> KPIs computable from what you have${
          q.kpis_blocked ? `, <strong>${q.kpis_blocked}</strong> blocked by missing data` : ''}
      </div>
      ${q.tables_missing.length ? `
        <table class="kpi-table"><thead><tr>
          <th>Missing table</th><th>Would unlock</th><th>Supply as</th><th></th>
        </tr></thead><tbody>
        ${q.tables_missing.map((m) => {
          const filled = (studio.spec.source.fill_gaps || []).includes(m.table);
          return `<tr>
            <td><span class="k-name">${esc(titleCase(m.table))}</span>
                <div class="k-id">${esc(m.unlocks || '')}</div></td>
            <td class="num">${m.unlocks_kpis} KPI${m.unlocks_kpis === 1 ? '' : 's'}</td>
            <td>${esc(m.supply_by || '—')}</td>
            <td><div class="k-state">
              <button data-fill="${esc(m.table)}" class="${filled ? 'on' : ''}">
                ${filled ? 'Modelling it' : 'Model it'}</button>
            </div></td></tr>`;
        }).join('')}
        </tbody></table>
        <p class="hint" style="margin-top:10px">
          Modelling a missing table generates plausible data in its place. Every
          KPI computed from it is marked <em>modelled</em> in the dashboard, the
          workbook and the report — it is never presented as measured.
        </p>` : '<p class="hint">Every fact table is present.</p>'}
      ${q.gate_warnings?.length ? `<div class="warn-banner">${
        q.gate_warnings.map(esc).join('<br>')}</div>` : ''}
      ${q.blocking?.length ? `<div class="warn-banner">${
        q.blocking.map(esc).join('<br>')}</div>` : ''}
    </div>` : '';

  const generator = `
    <div class="field-row">
      <label class="field"><span>Random seed</span>
        <input type="number" data-spec="source.generator.seed" value="${g.seed ?? ''}"
               placeholder="${studio.spec.profile.seed}"></label>
      <label class="field"><span>Months of history</span>
        <input type="number" min="13" max="120" data-spec="source.generator.history_months"
               value="${g.history_months ?? ''}" placeholder="${studio.spec.profile.history_months}"></label>
    </div>
    <div class="field-row">
      <label class="field"><span>Seasonality</span>
        <input type="range" min="0" max="3" step="0.25"
               data-spec-number="source.generator.seasonality_amplitude"
               value="${g.seasonality_amplitude ?? 1}">
        <span class="range-value">${fmtKnob(g.seasonality_amplitude)}</span></label>
      <label class="field"><span>Volatility</span>
        <input type="range" min="0" max="3" step="0.25"
               data-spec-number="source.generator.volatility"
               value="${g.volatility ?? 1}">
        <span class="range-value">${fmtKnob(g.volatility)}</span></label>
      <label class="toggle">
        <input type="checkbox" data-spec-bool="source.generator.inject_anomalies"
               ${g.inject_anomalies === false ? '' : 'checked'}>
        <span>Plant deliberate events
          <span class="toggle-sub">churn spike, CAC inflation, margin compression</span></span>
      </label>
    </div>
    <p class="hint">Seasonality scales the swing around the annual average, so
       0 is a flat year and 2 is twice the usual peak-to-trough. Volatility
       widens the month-to-month noise without moving the trend. Turning off
       planted events gives a clean baseline to compare a scenario against —
       the findings list will be much shorter, because there is less wrong.</p>
    <p class="hint">Any of these regenerates the company and everything after
       it — the most expensive edit in the Studio.</p>`;

  return section('Where the numbers come from',
    isUpload
      ? `Your own files. They enter the same fact tables generated data does, so
         everything downstream treats them identically — which is why a KPI can
         be computed from your upload and one from a modelled table can sit
         beside it, correctly labelled.`
      : `Synthetic data, generated from the profile. The same seed always
         produces the same company — reproducibility is a feature, not a
         coincidence.`,
    `<div class="upload-row">
       <label class="dropzone compact" id="studio-drop">
         <input type="file" id="studio-file" accept=".csv,.tsv,.xlsx,.xls" hidden>
         <span class="drop-title">${isUpload ? 'Add another file' : 'Upload your own data'}</span>
         <span class="drop-sub">CSV, TSV or Excel — we read it, profile it and suggest the fixes</span>
       </label>
     </div>
     <div id="studio-upload-result"></div>
     ${coverage}
     ${isUpload ? '' : generator}`);
}

function cleanPanel() {
  const steps = studio.spec.cleaning.steps || [];
  const cards = steps.length ? steps.map((st, i) => {
    const line = (studio.lineage || [])[i];
    const meta = line
      ? `${line.rows_before.toLocaleString()} → ${line.rows_after.toLocaleString()} rows` +
        (line.cells_changed ? ` · ${line.cells_changed.toLocaleString()} cells changed` : '')
      : 'applies on the next run';
    return `
      <div class="op-card${st.enabled === false ? ' off' : ''}">
        <div class="op-num">${i + 1}</div>
        <div class="op-body">
          <div class="op-title">${esc((studio.opLabels || {})[st.op] || st.op)}
            <span class="op-target">${esc(st.table || 'every table')}</span></div>
          <div class="op-line">${esc(line ? line.sentence : JSON.stringify(st.params))}</div>
          <div class="op-meta">${esc(meta)}</div>
        </div>
        <div class="op-actions">
          <button data-op-move="${i}" data-dir="-1" ${i === 0 ? 'disabled' : ''} title="Move up">↑</button>
          <button data-op-move="${i}" data-dir="1" ${i === steps.length - 1 ? 'disabled' : ''} title="Move down">↓</button>
          <button data-op-toggle="${i}">${st.enabled === false ? 'Enable' : 'Disable'}</button>
          <button data-op-drop="${i}">Remove</button>
        </div>
      </div>`;
  }).join('') : '<p class="hint">No cleaning steps. Synthetic data arrives reconciled; an upload usually needs a few.</p>';

  const suggestions = (studio.suggestions || []).filter(
    (sg) => !steps.some((st) => st.op === sg.op && JSON.stringify(st.params) === JSON.stringify(sg.params)));

  return section('Cleaning and transformation',
    `Ordered — each step sees the output of the one before it. Every step
     records what it did, and those sentences become the methodology appendix,
     which is what makes a cleaned dataset defensible rather than merely tidy.`,
    `${suggestions.length ? `
      <div class="suggest-box">
        <div class="suggest-head">Suggested from your data — nothing is applied until you accept it</div>
        ${suggestions.map((sg, i) => `
          <div class="suggest-row">
            <span>${esc(sg.reason)}</span>
            <button class="ghost" data-accept-op="${i}">Accept</button>
          </div>`).join('')}
      </div>` : ''}
     <div class="op-list">${cards}</div>
     <div class="field-row" style="margin-top:16px">
       <label class="field"><span>Add a step</span>
         <select id="op-pick">
           <option value="">Choose an operation…</option>
           ${(studio.ops || []).map((o) =>
             `<option value="${esc(o.name)}">${esc(o.label)}</option>`).join('')}
         </select></label>
       <label class="field"><span>Table</span>
         <select id="op-table">
           <option value="">Every table</option>
           ${(state.tableNames || []).map((t) =>
             `<option value="${esc(t)}">${esc(titleCase(t))}</option>`).join('')}
         </select></label>
       <label class="field" style="flex:1;min-width:220px"><span>Parameters (JSON)</span>
         <input id="op-params" placeholder='{"column": "revenue", "to": "number"}'></label>
       <button class="ghost" id="op-add" style="align-self:flex-end">Add step</button>
     </div>
     <div class="fx-status" id="op-status"></div>
     <div id="op-help" class="hint" style="margin-top:8px"></div>`);
}

function modelPanel() {
  const cols = studio.spec.model.calculated_columns || [];
  const rows = cols.length ? cols.map((c, i) => `
    <tr>
      <td><span class="k-name">${esc(c.name)}</span>
          <div class="k-id">${esc(c.table)}</div></td>
      <td><code style="font-size:12px">${esc(c.expression)}</code></td>
      <td><div class="k-state"><button data-drop-col="${i}">Remove</button></div></td>
    </tr>`).join('') : '<tr><td colspan="3" class="empty">No calculated columns yet.</td></tr>';

  return section('Calculated columns',
    `Add a field to a fact table with a formula, evaluated one row at a time —
     <code>final_acv - initial_acv</code> on customers. KPIs can then aggregate
     over what you build here.`,
    `<table class="kpi-table"><thead><tr>
        <th>Column</th><th>Formula</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
     <div class="field-row" style="margin-top:16px">
       <label class="field"><span>Table</span>
         <select id="col-table">${(state.tableNames || []).map((t) =>
           `<option value="${esc(t)}">${esc(titleCase(t))}</option>`).join('')}</select></label>
       <label class="field"><span>Column name</span>
         <input id="col-name" placeholder="acv_delta" maxlength="40"></label>
       <label class="field" style="flex:1;min-width:260px"><span>Formula</span>
         <input id="col-expr" placeholder="final_acv - initial_acv"></label>
       <button class="ghost" id="col-add" style="align-self:flex-end">Add column</button>
     </div>
     <div class="fx-status" id="col-status"></div>`);
}

function kpiPanel() {
  const m = studio.spec.metrics;
  const pinned = new Set(m.pinned || []);
  const excluded = new Set(m.excluded || []);
  const overrides = m.overrides || {};
  const custom = m.custom || [];

  const rows = [...custom.map((c) => ({ ...c, _custom: true })), ...studio.catalog]
    .filter((k, i, all) => all.findIndex((o) => o.id === k.id) === i)
    .map((k) => {
      const ov = overrides[k.id] || {};
      const isUser = k._custom || k.origin === 'user';
      return `
      <tr class="${excluded.has(k.id) ? 'excluded' : ''}">
        <td>
          <span class="k-name">${esc(k.name)}</span>${isUser ? '<span class="tag-user">yours</span>' : ''}
          <div class="k-id">${esc(k.id)} · ${esc(k.unit)} · ${esc((k.timing || '').replace('_', ' '))}</div>
        </td>
        <td><input type="number" step="any" data-target="${esc(k.id)}"
                   value="${ov.target ?? ''}" placeholder="auto"></td>
        <td><div class="k-state">
          <button data-pin="${esc(k.id)}" class="${pinned.has(k.id) ? 'on' : ''}">Pin</button>
          <button data-exclude="${esc(k.id)}" class="${excluded.has(k.id) ? 'off' : ''}">Exclude</button>
        </div></td>
      </tr>`;
    }).join('');

  return section('Which KPIs, and your own',
    `Pin one to force it onto the scorecard, exclude one to drop it, or set a
     target that beats the benchmark median. The Balanced Scorecard coverage
     warnings still fire — you can overrule the selection engine, and it will
     tell you what that cost.`,
    `<div style="margin-bottom:14px"><button class="primary" id="kpi-add">+ Add a KPI</button></div>
     <table class="kpi-table">
       <thead><tr><th>KPI</th><th>Target</th><th>State</th></tr></thead>
       <tbody>${rows}</tbody></table>`);
}

function analysisPanel() {
  const a = studio.spec.analysis;
  const disabled = new Set(a.disabled || []);
  const boxes = (studio.options.detectors || []).map((d) => `
    <label class="toggle">
      <input type="checkbox" data-detector="${esc(d)}" ${disabled.has(d) ? '' : 'checked'}>
      <span>${esc(DETECTOR_LABEL[d] || titleCase(d))}
        <span class="toggle-sub">${esc(d)}</span></span>
    </label>`).join('');

  return section('What to look for',
    `Deterministic detectors — each one produces findings with the numbers
     already computed. Turning some off is a real editing choice: a bank cares
     about runway and concentration, and twenty findings bury the two that matter.`,
    `<div class="toggle-grid">${boxes}</div>
     <div class="field-row" style="margin-top:18px">
       <label class="field"><span>Most findings to keep</span>
         <input type="number" min="1" max="60" data-spec="analysis.max_findings"
                value="${a.max_findings ?? ''}" placeholder="all"></label>
     </div>`);
}

function designPanel() {
  const d = studio.spec.design;
  const brand = d.brand || (d.brand = {});
  const all = studio.options.sections || [];
  const order = d.sections || all.map((s) => s.id);
  const titleOf = Object.fromEntries(all.map((s) => [s.id, s.title]));
  const exhibits = studio.options.exhibits || [];
  const chosenEx = d.exhibits || exhibits;
  const widths = d.exhibit_widths || (d.exhibit_widths = {});

  const sectionRows = order.map((id, i) => `
    <li class="order-row">
      <span class="order-num">${i + 1}</span>
      <span class="order-name">${esc(titleOf[id] || id)}</span>
      <span class="order-actions">
        <button data-sec-move="${i}" data-dir="-1" ${i === 0 ? 'disabled' : ''} title="Move up">↑</button>
        <button data-sec-move="${i}" data-dir="1" ${i === order.length - 1 ? 'disabled' : ''} title="Move down">↓</button>
        <button data-sec-drop="${esc(id)}" title="Remove from the report">✕</button>
      </span>
    </li>`).join('');

  const dropped = all.filter((s) => !order.includes(s.id));
  const restore = dropped.length ? `
    <p class="hint">Not included:
      ${dropped.map((s) => `<button class="chip-btn" data-sec-add="${esc(s.id)}">+ ${esc(s.title)}</button>`).join(' ')}
    </p>` : '';

  const exhibitRows = exhibits.map((id) => {
    const on = chosenEx.includes(id);
    return `<label class="toggle">
      <input type="checkbox" data-exhibit="${esc(id)}" ${on ? 'checked' : ''}>
      <span>${esc(id.replace(/_/g, ' '))}</span>
      <select data-exhibit-width="${esc(id)}" ${on ? '' : 'disabled'}>
        ${['half', 'full'].map((w) =>
          `<option value="${w}" ${(widths[id] || '') === w ? 'selected' : ''}>${w}</option>`).join('')}
      </select>
    </label>`;
  }).join('');

  return section('How it looks',
    `Theme drives the dashboard and every exhibit. A brand colour is measured
     before it is used: it must stay readable against the page and stay
     distinguishable from the other two series, including for readers with
     colour vision deficiency.`,
    `<div class="field-row">
      <label class="field"><span>Theme</span>
        <select data-spec="design.theme">
          ${['light', 'dark', 'auto'].map((t) =>
            `<option value="${t}" ${d.theme === t ? 'selected' : ''}>${titleCase(t)}</option>`).join('')}
        </select></label>
      <label class="field"><span>Page size</span>
        <select data-spec="design.page_size">
          ${['A4', 'Letter'].map((x) =>
            `<option value="${x}" ${d.page_size === x ? 'selected' : ''}>${x}</option>`).join('')}
        </select></label>
    </div>

    <h3 class="studio-sub">Brand</h3>
    <div class="field-row">
      <label class="field"><span>Primary colour</span>
        <input type="text" id="brand-primary" placeholder="#2a78d6"
               value="${esc(brand.primary || '')}"></label>
      <label class="field"><span>Secondary (optional)</span>
        <input type="text" id="brand-accent" placeholder="leave blank"
               value="${esc(brand.accent || '')}"></label>
      <label class="field"><span>Logo (PNG or JPEG)</span>
        <input type="text" id="brand-logo" placeholder="path or uploaded filename"
               value="${esc(brand.logo_path || '')}"></label>
    </div>
    <div id="brand-preview" class="brand-preview"></div>

    <h3 class="studio-sub">Sections</h3>
    <p class="hint">Order and inclusion apply to the PDF, the editable report
      and the deck alike. Sections are renumbered as you move them.</p>
    <ol class="order-list">${sectionRows}</ol>
    ${restore}

    <h3 class="studio-sub">Exhibits</h3>
    <p class="hint">A chart with no data behind it is skipped whether or not it
      is ticked here.</p>
    <div class="toggle-grid">${exhibitRows}</div>`);
}

/* The brand preview is the point of the panel: an adjusted colour has to be
   visible as adjusted. A silent correction is how a user ends up believing
   their brand is on the page when something near it is. */
let brandTimer = null;

function queueBrandPreview() {
  clearTimeout(brandTimer);
  brandTimer = setTimeout(refreshBrandPreview, 250);
}

async function refreshBrandPreview() {
  const box = $('#brand-preview');
  if (!box) return;
  const brand = studio.spec.design.brand || {};
  if (!brand.primary && !brand.logo_path) { box.innerHTML = ''; return; }

  let r;
  try {
    r = await api('/api/design/preview', {
      method: 'POST',
      body: JSON.stringify({ primary: brand.primary || null,
                             accent: brand.accent || null,
                             logo_path: brand.logo_path || null }),
    });
  } catch (err) {
    box.innerHTML = `<p class="warn">${esc(String(err.message || err))}</p>`;
    return;
  }

  const p = r.palettes.light;
  const swatch = (hex, label) =>
    `<span class="swatch"><i style="background:${esc(hex)}"></i>
       <span>${esc(label)}<br><code>${esc(hex)}</code></span></span>`;

  const adjustments = p.adjustments.map((a) => `
    <div class="adjust-row">
      ${swatch(a.original, 'you asked for')} <span class="arrow">→</span>
      ${swatch(a.applied, 'used')}
      <span class="adjust-why"><strong>${esc(a.token.replace(/_/g, ' '))}</strong> —
        ${esc(a.reason)}<br><span class="hint">${esc(a.detail)}</span></span>
    </div>`).join('');

  const logo = r.logo;
  const logoHtml = !logo.path ? '' : (logo.ok
    ? `<div class="adjust-row"><img class="logo-preview" src="${logo.data_uri}" alt=""></div>`
    : `<p class="warn">${esc(logo.error)}</p>`);

  box.innerHTML = `
    <div class="swatch-row">
      ${p.series.map((c, i) => swatch(c, `series ${i + 1}`)).join('')}
      ${swatch(p.tokens.heading_accent || p.tokens.series_1, 'headings')}
    </div>
    <p class="hint">
      ${p.tokens.series_1} sits at ${p.against_surface}:1 against the page
      (${r.thresholds.graphical}:1 needed to see a line), headings at
      ${p.heading_ratio}:1 (${r.thresholds.text}:1 for AA text).
      Closest pair ΔE ${p.min_delta_e} against a floor of ${r.thresholds.separation},
      measured under normal, deuteranopic and protanopic vision.</p>
    ${p.notes.map((n) => `<p class="warn">${esc(n)}</p>`).join('')}
    ${adjustments}
    ${logoHtml}`;
}

function outputsPanel() {
  const chosen = new Set(studio.spec.outputs.artifacts || studio.options.artifacts);
  const boxes = (studio.options.artifacts || []).map((a) => `
    <label class="toggle">
      <input type="checkbox" data-artifact="${esc(a)}" ${chosen.has(a) ? 'checked' : ''}>
      <span>${esc(ARTIFACT_LABEL[a] || a)}
        <span class="toggle-sub">${esc(ARTIFACT_COST[a] || '')}</span></span>
    </label>`).join('');

  return section('What to produce',
    `Rendering is around 80% of a run, and the chart image export alone is 37%.
     Asking for only what you need is the single biggest speed-up available —
     dashboard-only finishes in about a second rather than seven.`,
    `<div class="toggle-grid">${boxes}</div>`);
}

/* ------------------------------------------------------------------- ai */

const SECTION_LABEL = {
  cover: 'Cover', exec_summary: 'Executive summary', scorecard: 'Scorecard',
  diagnostic: 'Diagnostic', deep_dives: 'Deep dives', benchmarks: 'Benchmarks',
  risks: 'Risks', actions: 'Actions', appendix: 'Appendix',
};

function aiPanel() {
  const ai = studio.spec.ai || {};
  const status = studio.ai || {};
  const narratable = status.narratable_sections || [];
  const chosen = new Set(ai.narrate_sections || narratable);

  if (!status.available) {
    // Say what is missing and what to type. An "AI unavailable" badge with no
    // remedy is the same as no feature at all.
    return section('AI',
      `Off, and the pipeline does not need it — every artifact is produced
       without a model. ${esc(status.reason || 'Not configured.')}`,
      `<pre class="ai-setup">pip install -r requirements-ai.txt
export ANTHROPIC_API_KEY=…</pre>`);
  }

  const boxes = narratable.map((s) => `
    <label class="toggle">
      <input type="checkbox" data-narrate="${esc(s)}" ${chosen.has(s) ? 'checked' : ''}>
      <span>${esc(SECTION_LABEL[s] || s)}</span>
    </label>`).join('');

  const est = studio.aiEstimate;
  const estimateLine = est
    ? `Estimated at most <strong>${est.worst_case_tokens.toLocaleString()}</strong>
       tokens, about <strong>$${est.worst_case_cost_usd.toFixed(2)}</strong> for
       both requests. Output is assumed at the ceiling, so the real cost is
       usually well under this.`
    : 'Press Estimate to price both requests before spending anything on them.';

  return section('AI',
    `Off by default. The narrator writes the connective paragraph in each
     section from the computed KPI table — never the underlying data — and
     every figure it writes is checked against that table before it is
     printed. The planner proposes changes to this configuration, which you
     review one at a time. Neither can change a number.`,
    `<label class="toggle ai-master">
       <input type="checkbox" data-spec-bool="ai.enabled" ${ai.enabled ? 'checked' : ''}>
       <span>Write the narrative on the next run
         <span class="toggle-sub">${esc(ai.model || status.default_model)}</span></span>
     </label>

     <h3 class="studio-sub">Sections to narrate</h3>
     <div class="toggle-grid">${boxes}</div>

     <h3 class="studio-sub">Cost</h3>
     <p class="hint" id="ai-estimate">${estimateLine}</p>
     <button class="ghost" id="ai-estimate-btn">Estimate</button>

     <h3 class="studio-sub">Let the AI configure this run</h3>
     <p class="hint">Proposes changes to the KPIs, sections, exhibits,
       detectors and outputs — never to the profile. You accept or reject each
       one before anything is written.</p>
     <button class="ghost" id="ai-plan-btn">Suggest changes</button>`);
}

/* The proposed patch, held here between "suggest" and "apply". Nothing in it
   reaches the spec until the user presses Apply on the rows they ticked. */
let plan = { changes: [], chosen: new Set() };

async function requestPlan() {
  const btn = $('#ai-plan-btn');
  btn.disabled = true;
  btn.textContent = 'Thinking…';
  try {
    const result = await api(`/api/ai/plan/${state.runId}`, { method: 'POST' });
    plan.changes = result.changes || [];
    // Pre-tick the legal ones: the common case is accepting most of a good
    // patch, and starting from nothing ticked makes the reviewer do clerical
    // work before they can do the actual review.
    plan.chosen = new Set(plan.changes.map((c, i) => (c.ok ? i : -1))
      .filter((i) => i >= 0));
    openPlanModal(result.summary || '');
  } catch (err) {
    toast('Could not get a plan: ' + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Suggest changes';
  }
}

const showValue = (v) => (v === undefined || v === null
  ? '—' : (typeof v === 'string' ? v : JSON.stringify(v)));

function openPlanModal(summary) {
  $('#plan-summary').textContent = summary;
  $('#plan-changes').innerHTML = plan.changes.length
    ? plan.changes.map((c, i) => `
      <div class="plan-change ${c.ok ? '' : 'plan-refused'}">
        <label class="plan-pick">
          <input type="checkbox" data-plan="${i}"
            ${c.ok ? '' : 'disabled'} ${plan.chosen.has(i) ? 'checked' : ''}>
          <code>${esc(c.path)}</code>
        </label>
        <div class="plan-diff">
          <span class="plan-before${
            c.before === null || c.before === undefined ? ' plan-unset' : ''
          }">${esc(showValue(c.before))}</span>
          <span class="plan-arrow">→</span>
          <span class="plan-after">${esc(showValue(c.value))}</span>
        </div>
        <p class="plan-why">${esc(c.rationale || '')}</p>
        ${c.ok ? '' : `<p class="plan-reject">Refused — ${esc(c.rejected)}</p>`}
      </div>`).join('')
    : '<p class="hint">The planner proposed no changes.</p>';
  refreshPlanCount();
  $('#plan-scrim').hidden = false;
  $('#plan-modal').hidden = false;
}

function closePlanModal() {
  $('#plan-scrim').hidden = true;
  $('#plan-modal').hidden = true;
}

function refreshPlanCount() {
  const refused = plan.changes.filter((c) => !c.ok).length;
  $('#plan-count').textContent =
    `${plan.chosen.size} of ${plan.changes.length} selected`
    + (refused ? ` · ${refused} refused` : '');
  $('#plan-apply').disabled = plan.chosen.size === 0;
}

async function applyPlan() {
  const changes = [...plan.chosen].sort((a, b) => a - b)
    .map((i) => ({ path: plan.changes[i].path, value: plan.changes[i].value }));
  try {
    await api(`/api/ai/apply/${state.runId}`, {
      method: 'POST', body: JSON.stringify({ changes }),
    });
    // Re-read rather than patching the local copy: the server validated and
    // may have normalised, and the studio should show what is actually stored.
    studio.spec = await api(`/api/runs/${state.runId}/spec`);
    renderStudio();
    refreshBrandPreview();
    refreshPlan();
    closePlanModal();
    toast(`Applied ${changes.length} change${changes.length === 1 ? '' : 's'}.`);
  } catch (err) {
    toast('Could not apply: ' + err.message, true);
  }
}

async function estimateAi() {
  const btn = $('#ai-estimate-btn');
  btn.disabled = true;
  try {
    studio.aiEstimate = await api(`/api/ai/estimate/${state.runId}`,
      { method: 'POST' });
    panel('ai').innerHTML = aiPanel();
  } catch (err) {
    toast('Could not estimate: ' + err.message, true);
  } finally {
    if ($('#ai-estimate-btn')) $('#ai-estimate-btn').disabled = false;
  }
}

$('#plan-close').addEventListener('click', closePlanModal);
$('#plan-cancel').addEventListener('click', closePlanModal);
$('#plan-scrim').addEventListener('click', closePlanModal);
$('#plan-apply').addEventListener('click', applyPlan);
$('#plan-changes').addEventListener('change', (e) => {
  const box = e.target.closest('[data-plan]');
  if (!box) return;
  const i = Number(box.dataset.plan);
  box.checked ? plan.chosen.add(i) : plan.chosen.delete(i);
  refreshPlanCount();
});

/* ------------------------------------------------------------- editing */

function setPath(obj, path, value) {
  const parts = path.split('.');
  const last = parts.pop();
  let node = obj;
  for (const p of parts) node = node[p];
  node[last] = value;
}

panelDelegate('change', (e) => {
  const el = e.target;
  if (el.id === 'op-pick') {
    const spec = (studio.ops || []).find((o) => o.name === el.value);
    $('#op-help').innerHTML = spec
      ? `${esc(spec.help)}<br><code>${esc(JSON.stringify(
          Object.fromEntries(Object.keys(spec.params).map((k) => [k, '…']))))}</code>`
      : '';
    return;
  }
  if (el.dataset.spec) {
    let value = el.value;
    if (el.type === 'number') value = value === '' ? null : Number(value);
    if (value === '') value = null;
    setPath(studio.spec, el.dataset.spec, value);
    return queuePatch();
  }
  if (el.dataset.detector) {
    const set = new Set(studio.spec.analysis.disabled || []);
    el.checked ? set.delete(el.dataset.detector) : set.add(el.dataset.detector);
    studio.spec.analysis.disabled = [...set];
    return queuePatch();
  }
  if (el.dataset.artifact) {
    const all = studio.options.artifacts;
    const set = new Set(studio.spec.outputs.artifacts || all);
    el.checked ? set.add(el.dataset.artifact) : set.delete(el.dataset.artifact);
    studio.spec.outputs.artifacts = all.filter((a) => set.has(a));
    return queuePatch();
  }
  if (el.dataset.narrate) {
    const all = (studio.ai || {}).narratable_sections || [];
    const set = new Set(studio.spec.ai.narrate_sections || all);
    el.checked ? set.add(el.dataset.narrate) : set.delete(el.dataset.narrate);
    // Written out in the registry's order, not click order, so the stored
    // spec is stable and the cache key does not move when a user unticks and
    // re-ticks the same box.
    studio.spec.ai.narrate_sections = all.filter((s) => set.has(s));
    return queuePatch();
  }
  if (el.dataset.specBool) {
    setPath(studio.spec, el.dataset.specBool, el.checked);
    return queuePatch();
  }
  if (el.dataset.exhibit) {
    const all = studio.options.exhibits;
    const set = new Set(studio.spec.design.exhibits || all);
    el.checked ? set.add(el.dataset.exhibit) : set.delete(el.dataset.exhibit);
    studio.spec.design.exhibits = all.filter((x) => set.has(x));
    panel('design').innerHTML = designPanel();
    refreshBrandPreview();
    return queuePatch();
  }
  if (el.dataset.exhibitWidth) {
    studio.spec.design.exhibit_widths[el.dataset.exhibitWidth] = el.value;
    return queuePatch();
  }
  if (el.dataset.target) {
    const overrides = studio.spec.metrics.overrides || (studio.spec.metrics.overrides = {});
    if (el.value === '') delete overrides[el.dataset.target];
    else overrides[el.dataset.target] = { ...overrides[el.dataset.target], target: Number(el.value) };
    return queuePatch();
  }
});

panelDelegate('input', (e) => {
  if (e.target.dataset.specNumber) {
    const el = e.target;
    setPath(studio.spec, el.dataset.specNumber, Number(el.value));
    const label = el.parentElement.querySelector('.range-value');
    if (label) label.textContent = fmtKnob(Number(el.value));
    return queuePatch();
  }
  const map = { 'brand-primary': 'primary', 'brand-accent': 'accent',
                'brand-logo': 'logo_path' };
  const field = map[e.target.id];
  if (!field) return;
  const brand = studio.spec.design.brand || (studio.spec.design.brand = {});
  brand[field] = e.target.value.trim() || null;
  queueBrandPreview();
  queuePatch();
});

panelDelegate('click', (e) => {
  const pin = e.target.closest('[data-pin]');
  const exclude = e.target.closest('[data-exclude]');
  const dropCol = e.target.closest('[data-drop-col]');

  if (pin) return toggleList('pinned', pin.dataset.pin, 'excluded');
  if (exclude) return toggleList('excluded', exclude.dataset.exclude, 'pinned');
  if (dropCol) {
    studio.spec.model.calculated_columns.splice(Number(dropCol.dataset.dropCol), 1);
    panel('model').innerHTML = modelPanel();
    return queuePatch();
  }
  if (e.target.closest('#kpi-add')) return openFormulaEditor();
  if (e.target.closest('#col-add')) return addCalculatedColumn();
  if (e.target.closest('#ai-plan-btn')) return requestPlan();
  if (e.target.closest('#ai-estimate-btn')) return estimateAi();

  const move = e.target.closest('[data-op-move]');
  const toggle = e.target.closest('[data-op-toggle]');
  const drop = e.target.closest('[data-op-drop]');
  const accept = e.target.closest('[data-accept-op]');
  const steps = studio.spec.cleaning.steps;

  if (move) {
    const i = Number(move.dataset.opMove);
    const j = i + Number(move.dataset.dir);
    if (j < 0 || j >= steps.length) return;
    // Order is meaning here: parse the dates, THEN roll up to months.
    [steps[i], steps[j]] = [steps[j], steps[i]];
    return afterRecipeChange();
  }
  if (toggle) {
    const st = steps[Number(toggle.dataset.opToggle)];
    st.enabled = st.enabled === false;
    return afterRecipeChange();
  }
  if (drop) { steps.splice(Number(drop.dataset.opDrop), 1); return afterRecipeChange(); }
  if (accept) {
    const sg = (studio.suggestions || [])[Number(accept.dataset.acceptOp)];
    if (sg) steps.push({ op: sg.op, table: sg.table, params: sg.params, enabled: true });
    return afterRecipeChange();
  }
  if (e.target.closest('#op-add')) return addCleaningStep();

  const secMove = e.target.closest('[data-sec-move]');
  const secDrop = e.target.closest('[data-sec-drop]');
  const secAdd = e.target.closest('[data-sec-add]');
  if (secMove || secDrop || secAdd) {
    const design = studio.spec.design;
    // Materialise the default order the moment the user touches it: from here
    // on the spec carries the order explicitly rather than meaning "whatever
    // the registry ships".
    const order = design.sections
      || (design.sections = (studio.options.sections || []).map((x) => x.id));
    if (secMove) {
      const i = Number(secMove.dataset.secMove);
      const j = i + Number(secMove.dataset.dir);
      if (j < 0 || j >= order.length) return;
      [order[i], order[j]] = [order[j], order[i]];
    } else if (secDrop) {
      order.splice(order.indexOf(secDrop.dataset.secDrop), 1);
    } else {
      order.push(secAdd.dataset.secAdd);
    }
    panel('design').innerHTML = designPanel();
    refreshBrandPreview();
    return queuePatch();
  }

  const fill = e.target.closest('[data-fill]');
  if (fill) {
    const list = new Set(studio.spec.source.fill_gaps || []);
    const table = fill.dataset.fill;
    list.has(table) ? list.delete(table) : list.add(table);
    studio.spec.source.fill_gaps = [...list];
    panel('source').innerHTML = sourcePanel();
    return queuePatch();
  }
});

function panelDelegate(event, handler) {
  // `openStudio` shows the view before it has the spec — four API calls land
  // after the first paint — and every panel handler dereferences it. A click
  // on the rail in that window used to throw on `studio.spec.cleaning`, which
  // also skipped every handler registered after the throwing one. Nothing here
  // can do anything meaningful without a spec, so wait for one.
  $('#view-studio').addEventListener(event, (e) => {
    if (studio.spec) handler(e);
  });
}

// Pinning and excluding are mutually exclusive: holding both would be an
// instruction the selection engine cannot honour, so setting one clears the other.
function toggleList(listName, id, opposite) {
  const list = new Set(studio.spec.metrics[listName] || []);
  const other = new Set(studio.spec.metrics[opposite] || []);
  list.has(id) ? list.delete(id) : (list.add(id), other.delete(id));
  studio.spec.metrics[listName] = [...list];
  studio.spec.metrics[opposite] = [...other];
  panel('kpis').innerHTML = kpiPanel();
  queuePatch();
}

function afterRecipeChange() {
  panel('clean').innerHTML = cleanPanel();
  queuePatch();
  previewRecipe();
}

async function previewRecipe() {
  // Preview before committing: the row counts are what a user checks, and
  // showing them from a stale lineage would be worse than showing none.
  try {
    const report = await api(`/api/runs/${state.runId}/clean/preview`, {
      method: 'POST',
      body: JSON.stringify({ steps: studio.spec.cleaning.steps }),
    });
    studio.lineage = report.steps || [];
    $('#op-status').className = report.pending_tables?.length ? 'fx-status' : 'fx-status ok';
    $('#op-status').textContent = report.summary;
  } catch (err) {
    $('#op-status').className = 'fx-status err';
    $('#op-status').textContent = err.message;
    studio.lineage = [];
  }
  if (studio.stage === 'clean') panel('clean').innerHTML = cleanPanel();
}

function addCleaningStep() {
  const op = $('#op-pick').value;
  const table = $('#op-table').value || null;
  const raw = $('#op-params').value.trim();
  const status = $('#op-status');
  if (!op) { status.className = 'fx-status err'; status.textContent = 'Choose an operation.'; return; }
  let params = {};
  if (raw) {
    try { params = JSON.parse(raw); }
    catch (err) {
      status.className = 'fx-status err';
      status.textContent = 'Parameters must be valid JSON — ' + err.message;
      return;
    }
  }
  studio.spec.cleaning.steps.push({ op, table, params, enabled: true });
  $('#op-params').value = '';
  afterRecipeChange();
}

async function addCalculatedColumn() {
  const table = $('#col-table').value;
  const name = $('#col-name').value.trim();
  const expression = $('#col-expr').value.trim();
  const status = $('#col-status');
  if (!name || !expression) { status.className = 'fx-status err'; status.textContent = 'Name and formula are both needed.'; return; }

  const check = await api('/api/formula/validate', {
    method: 'POST',
    body: JSON.stringify({ expression, scope: 'row', run_id: state.runId, table }),
  });
  if (!check.ok) {
    status.className = 'fx-status err';
    status.textContent = check.error?.message || `Unknown: ${(check.unknown || []).join(', ')}`;
    return;
  }
  (studio.spec.model.calculated_columns ||= []).push({ table, name, expression, description: '' });
  panel('model').innerHTML = modelPanel();
  queuePatch();
}

/* ---------------------------------------------------------- patch + plan */

function queuePatch() {
  clearTimeout(studio.patchTimer);
  $('#studio-plan').textContent = 'Checking…';
  studio.patchTimer = setTimeout(refreshPlan, 320);
}

async function refreshPlan() {
  try {
    const report = await api(`/api/runs/${state.runId}/spec`, {
      method: 'PUT', body: JSON.stringify(studio.spec),
    });
    studio.dirty = report.dirty || [];
    const n = studio.dirty.length;
    $('#studio-plan').innerHTML = n
      ? `<strong>${n} stage${n > 1 ? 's' : ''}</strong> to rebuild — about
         ${report.estimated_seconds}s. <span style="color:var(--muted)">${studio.dirty.join(' · ')}</span>`
      : 'Everything is up to date.';
    $('#studio-rerun').disabled = n === 0;
  } catch (err) {
    $('#studio-plan').innerHTML = `<span style="color:var(--critical)">${esc(err.message)}</span>`;
    $('#studio-rerun').disabled = true;
  }
}

// Upload from inside the Studio. Profiling is read-only: the file is inspected
// and its fixes offered, and nothing about the run changes until the user acts.
$('#view-studio').addEventListener('click', (e) => {
  if (e.target.closest('#studio-drop')) $('#studio-file')?.click();
});
$('#view-studio').addEventListener('change', async (e) => {
  if (e.target.id !== 'studio-file' || !e.target.files?.[0]) return;
  const box = $('#studio-upload-result');
  box.innerHTML = '<p class="hint">Reading and profiling…</p>';
  const body = new FormData();
  body.append('file', e.target.files[0]);
  try {
    const res = await fetch(API + '/api/ingest/profile', { method: 'POST', body });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const data = await res.json();
    // Profiling is not adoption. The suggestions describe THIS file, and
    // applying them to a run still sourced from generated data would target
    // columns that do not exist there — which is exactly what happened before
    // the two actions were separated.
    studio.upload = data;
    box.innerHTML = renderUploadReport(data);
  } catch (err) {
    box.innerHTML = `<div class="warn-banner">${esc(err.message)}</div>`;
  }
});

function renderUploadReport(data) {
  const best = data.shapes?.[0];
  const read = data.read;
  return `
    <div class="upload-report">
      <div class="upload-head"><strong>${esc(data.filename)}</strong>
        — ${read.rows.toLocaleString()} rows, ${read.columns.length} columns</div>
      ${read.notes?.length ? `<ul class="plain small">${
        read.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
      ${best ? `
        <div class="upload-shape">Looks like a <strong>${esc(best.shape.replace(/_/g, ' '))}</strong>
          → <code>${esc(best.target_table)}</code>${best.usable
            ? '' : ` — but ${esc(best.missing_required.join(', '))} did not map`}</div>
        <table class="kpi-table"><thead><tr>
          <th>Field</th><th>From column</th><th>Confidence</th></tr></thead>
        <tbody>${best.matches.map((m) => `
          <tr><td><span class="k-name">${esc(m.field)}</span>
                  <div class="k-id">${esc(m.reason)}</div></td>
              <td>${esc(m.column || '—')}</td>
              <td><div class="conf conf-${esc(m.status)}">
                    <i style="width:${Math.round(m.confidence * 100)}%"></i>
                  </div>${Math.round(m.confidence * 100)}%</td></tr>`).join('')}
        </tbody></table>` : ''}
      <div class="upload-actions">
        <button class="primary" id="use-upload"${best?.usable ? '' : ' disabled'}>
          Use this file as the source</button>
        <span class="hint">${best?.usable
          ? `${(data.profile.suggestions || []).length} suggested fix(es) will be
             offered in the Clean panel once you do.`
          : 'Required fields did not map, so this file cannot drive a run yet.'}</span>
      </div>
    </div>`;
}

/* Adoption is a separate, explicit act. It switches the run's source to the
 * file, records the mapping, and only then are that file's suggested fixes
 * relevant — they name its columns, which do not exist in generated data. */
$('#view-studio').addEventListener('click', (e) => {
  if (!e.target.closest('#use-upload')) return;
  const data = studio.upload;
  const best = data?.shapes?.[0];
  if (!data || !best?.usable) return;

  studio.spec.source.kind = 'upload';
  studio.spec.source.uploads = [data.stored_as];
  studio.spec.model.mapping = {
    ...(studio.spec.model.mapping || {}),
    [best.target_table]: Object.fromEntries(
      best.matches.filter((m) => m.column).map((m) => [m.field, m.column])),
  };
  // Suggestions target the file's own table key, not every table.
  studio.suggestions = (data.profile.suggestions || []).map((sg) => ({
    ...sg, table: data.table_key,
  }));

  renderStudio();
  queuePatch();
  toast(`Using ${data.filename}. ${studio.suggestions.length} suggested fix(es) in Clean.`);
});

$('#view-studio').addEventListener('click', (e) => {
  const go = e.target.closest('[data-goto-stage]');
  if (!go) return;
  $(`#studio-rail [data-stage="${go.dataset.gotoStage}"]`)?.click();
});

$('#studio-rerun').addEventListener('click', async () => {
  try {
    await api(`/api/runs/${state.runId}/rerun`, { method: 'POST' });
    show('running');
    resetRunProgress();
    $('#run-company').textContent = studio.spec.profile.identity.name;
    pollRun();
  } catch (err) {
    toast('Could not re-run: ' + err.message, true);
  }
});

$('#studio-revert').addEventListener('click', async () => {
  studio.spec = JSON.parse(JSON.stringify(studio.original));
  renderStudio();
  refreshPlan();
});

/* -------------------------------------------------------- formula editor */

const fx = {
  open: () => { $('#fx-modal').hidden = false; $('#fx-scrim').hidden = false; },
  close: () => { $('#fx-modal').hidden = true; $('#fx-scrim').hidden = true; },
};

function openFormulaEditor() {
  ['#fx-name', '#fx-expression', '#fx-owner'].forEach((s) => { $(s).value = ''; });
  $('#fx-status').textContent = '';
  $('#fx-status').className = 'fx-status';
  $('#fx-preview').innerHTML = '';
  $('#fx-add').disabled = true;
  $('#fx-functions').innerHTML = studio.functions.map((f) => `
    <div class="fx-fn"><code>${esc(f.signature)}</code><span>${esc(f.help)}</span></div>`).join('');
  fx.open();
  $('#fx-name').focus();
}

$('#fx-close').addEventListener('click', fx.close);
$('#fx-cancel').addEventListener('click', fx.close);
$('#fx-scrim').addEventListener('click', fx.close);

let fxTimer;
$('#fx-expression').addEventListener('input', () => {
  clearTimeout(fxTimer);
  fxTimer = setTimeout(checkFormula, 340);
});

async function checkFormula() {
  const expression = $('#fx-expression').value.trim();
  const status = $('#fx-status');
  const preview = $('#fx-preview');
  preview.innerHTML = '';
  if (!expression) { status.textContent = ''; status.className = 'fx-status'; $('#fx-add').disabled = true; return; }

  try {
    const check = await api('/api/formula/validate', {
      method: 'POST', body: JSON.stringify({ expression, scope: 'series', run_id: state.runId }),
    });
    if (!check.ok) {
      const message = check.error
        ? check.error.message + (check.error.hint ? ` — ${check.error.hint}` : '')
        : `Unknown name${(check.unknown || []).length > 1 ? 's' : ''}: ${(check.unknown || []).join(', ')}`;
      status.className = 'fx-status err';
      status.textContent = message;
      $('#fx-add').disabled = true;
      return;
    }
    status.className = 'fx-status ok';
    status.innerHTML = check.references.length
      ? `Uses ${check.references.map((r) => `<code>${esc(r)}</code>`).join(' ')}`
      : 'Valid.';
    $('#fx-add').disabled = false;

    const result = await api('/api/formula/preview', {
      method: 'POST', body: JSON.stringify({ expression, scope: 'series', run_id: state.runId }),
    });
    renderSparkline(result);
  } catch (err) {
    status.className = 'fx-status err';
    status.textContent = err.message;
    $('#fx-add').disabled = true;
  }
}

function renderSparkline(result) {
  const points = result.points || [];
  if (!points.length) return;
  const values = points.map((p) => p.value).filter((v) => v !== null);
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values);
  const span = (hi - lo) || 1;
  const unit = $('#fx-unit').value;
  $('#fx-preview').innerHTML = `
    <div style="font-size:12px;color:var(--muted)">Last ${points.length} months —
      latest <strong style="color:var(--text-primary)">${esc(fmtValue(result.current, unit, state.summary?.currency))}</strong></div>
    <div class="fx-spark">${points.map((p) =>
      `<i style="height:${p.value === null ? 2 : Math.max(2, ((p.value - lo) / span) * 40)}px"
          title="${esc(p.month)}: ${esc(fmtValue(p.value, unit, state.summary?.currency))}"></i>`).join('')}</div>`;
}

$('#fx-unit').addEventListener('change', checkFormula);

$('#fx-add').addEventListener('click', async () => {
  const payload = {
    name: $('#fx-name').value.trim() || 'Custom KPI',
    expression: $('#fx-expression').value.trim(),
    unit: $('#fx-unit').value,
    direction: $('#fx-direction').value,
    perspective: $('#fx-perspective').value,
    timing: $('#fx-timing').value,
    owner_role: $('#fx-owner').value.trim() || undefined,
  };
  try {
    // Persisting is optional: a KPI for this run alone lives in the spec, one
    // kept for later is written to the user library and offered on every run.
    if ($('#fx-persist').checked) await api('/api/catalog/kpis', {
      method: 'POST', body: JSON.stringify(payload),
    });

    const id = payload.name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    (studio.spec.metrics.custom ||= []).push({
      id, name: payload.name, perspective: payload.perspective, tier: 2,
      timing: payload.timing, direction: payload.direction,
      formula: payload.expression, unit: payload.unit,
      owner_role: payload.owner_role || 'Unassigned',
      compute: { kind: 'formula', expression: payload.expression },
      origin: 'user',
    });
    fx.close();
    panel('kpis').innerHTML = kpiPanel();
    queuePatch();
    toast(`Added ${payload.name}.`);
  } catch (err) {
    $('#fx-status').className = 'fx-status err';
    $('#fx-status').textContent = err.message;
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#fx-modal').hidden) fx.close();
});
