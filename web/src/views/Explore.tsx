import { useEffect, useState } from 'preact/hooks';
import { api, fileUrl } from '../lib/api';
import { navigate } from '../lib/router';
import { useIsStatic } from '../lib/useStatic';

type Measure = { name: string; column: string; aggregation: string; denominator: string };
type Filter = { column: string; operator: string; value: string };
type Analysis = { title: string; context: string; table: string; dimension: string; date_grain: string;
  measures: Measure[]; chart: string; color: string; filters: Filter[]; trim_text: boolean;
  drop_duplicates: boolean; sections: string[] };
type Project = { id: string; spec: Analysis; history: number; revision: number; synthetic: boolean;
  tables: { id: string; name: string; rows: number; columns: string[] }[];
  messages: { role: string; content: string }[]; proposal: Analysis | null; spent_usd: number;
  result: { rows: { label: string; values: (number | null)[]; records: number }[];
    totals: { name: string; value: number | null; definition: string }[];
    included_rows: number; source_rows: number; groups: number; warnings: string[]; summary: string[];
    columns: { name: string; type: string; missing: number; distinct: number }[];
    preview: Record<string, string>[] } };
const fmt = (v: number | null) => v === null ? 'Unavailable' : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
const metric = (): Measure => ({ name: 'Records', column: '', aggregation: 'count', denominator: '' });

function Chart({ project }: { project: Project }) {
  const rows = project.result.rows;
  const values = rows.map(r => r.values[0] ?? 0);
  const low = Math.min(0, ...values), high = Math.max(0, ...values);
  const span = high - low || 1;
  if (project.spec.chart === 'table') return null;
  if (project.spec.chart === 'line') {
    const x = (i: number) => 65 + i * 650 / Math.max(1, rows.length - 1);
    const y = (v: number) => 280 - (v - low) / span * 230;
    return <svg class="explore-chart" viewBox="0 0 800 350" role="img" aria-label={`${project.spec.measures[0]?.name} by ${project.spec.dimension}`}>
      <line x1="65" x2="715" y1={y(0)} y2={y(0)} stroke="currentColor" opacity=".3" />
      {rows.map((r, i) => <g key={r.label}>
        {i > 0 && r.values[0] != null && rows[i-1]?.values[0] != null && <line x1={x(i-1)} y1={y(values[i-1] ?? 0)} x2={x(i)} y2={y(values[i] ?? 0)} stroke={project.spec.color} stroke-width="3" />}
        {r.values[0] != null && <circle cx={x(i)} cy={y(values[i] ?? 0)} r="5" fill={project.spec.color}><title>{r.label}: {fmt(r.values[0] ?? null)}</title></circle>}
        {(i % Math.max(1, Math.ceil(rows.length / 6)) === 0) && <text x={x(i)} y="315" text-anchor="middle">{r.label.slice(0, 12)}</text>}
      </g>)}
      <text x="5" y="50">{fmt(high)}</text><text x="5" y="280">{fmt(low)}</text>
    </svg>;
  }
  const x = (v: number) => 210 + (v - low) / span * 430;
  return <svg class="explore-chart" viewBox={`0 0 800 ${Math.max(100, rows.length * 34 + 20)}`} role="img" aria-label={`${project.spec.measures[0]?.name} by ${project.spec.dimension || 'all records'}`}>
    <line x1={x(0)} x2={x(0)} y1="0" y2={rows.length * 34} stroke="currentColor" opacity=".3" />
    {rows.map((r, i) => <g key={r.label}>
      <text x="0" y={i*34+21}>{r.label.slice(0, 25)}<title>{r.label}</title></text>
      {r.values[0] != null && <rect x={Math.min(x(0), x(values[i] ?? 0))} y={i*34+5} width={Math.max(1, Math.abs(x(values[i] ?? 0)-x(0)))} height="24" rx="3" fill={project.spec.color}><title>{r.label}: {fmt(r.values[0] ?? null)}</title></rect>}
      <text x="660" y={i*34+21}>{fmt(r.values[0] ?? null)}</text>
    </g>)}
  </svg>;
}

export function Explore({ projectId }: { projectId?: string }) {
  const isStatic = useIsStatic();
  const [project, setProject] = useState<Project | null>(null);
  const [draft, setDraft] = useState<Analysis | null>(null);
  const [recent, setRecent] = useState<{ id: string; title: string }[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState('dashboard');
  const [message, setMessage] = useState('');
  const [tier, setTier] = useState('standard');
  const [budget, setBudget] = useState(1);
  const [estimate, setEstimate] = useState<{ estimated_max_usd: number; within_budget: boolean } | null>(null);
  const [ai, setAi] = useState<{ available: boolean; reason?: string } | null>(null);
  const accept = (p: Project) => { setProject(p); setDraft(p.spec); setEstimate(null); };
  const endpoint = `/api/explore/projects/${project?.id ?? ''}`;

  useEffect(() => {
    let live = true;
    setProject(null); setDraft(null); setError('');
    if (projectId) api<Project>(`/api/explore/projects/${projectId}`).then(p => { if (live) accept(p); }, e => { if (live) setError(String(e.message)); });
    else api<{ id: string; title: string }[]>('/api/explore/projects').then(p => { if (live) setRecent(p); }, () => {});
    api<{ available: boolean; reason?: string }>('/api/ai/status').then(p => { if (live) setAi(p); }, () => {});
    return () => { live = false; };
  }, [projectId, isStatic]);

  async function work(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function demo(kind: string) {
    await work(async () => { const p = await api<Project>(`/api/explore/demo/${kind}`, { method: 'POST' }); navigate(`/explore/${p.id}`); });
  }
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    await work(async () => {
      const body = new FormData(); Array.from(files).forEach(f => body.append('files', f));
      const response = await fetch('/api/explore/upload', { method: 'POST', body });
      const p = await response.json() as Project & { detail?: unknown };
      if (!response.ok) throw new Error(typeof p.detail === 'string' ? p.detail : 'Could not read these files. Check the format and limits.');
      navigate(`/explore/${p.id}`);
    });
  }
  async function apply(spec: Analysis) {
    if (!project) return;
    await work(async () => accept(await api<Project>(endpoint, { method: 'PUT', body: JSON.stringify({ spec, revision: project.revision }) })));
  }
  const update = (changes: Partial<Analysis>) => draft && setDraft({ ...draft, ...changes });
  const chatBody = () => JSON.stringify({ message, tier, budget_usd: budget });
  const table = draft && project?.tables.find(t => t.id === draft.table);
  const dirty = draft && project && JSON.stringify(draft) !== JSON.stringify(project.spec);

  return <section class="view explore" id="view-explore">
    <div class="explore-heading"><div><p class="eyebrow">MasterBI · Data workspace</p>
      <h1>{project ? project.spec.title : 'Your data. Your questions.'}</h1>
      <p class="lede">{project ? `${project.result.included_rows.toLocaleString()} records included · ${project.synthetic ? 'Synthetic demonstration' : 'Uploaded data'} · ${isStatic ? 'Prebuilt gallery' : 'Saved locally'}` : 'Explore a spreadsheet, shape the analysis, and take the story with you.'}</p></div>
      {project && <button onClick={() => navigate('/explore')}>All projects</button>}
    </div>
    {error && <p class="explore-error" role="alert">{error}</p>}
    {busy && <p role="status">Working…</p>}
    {isStatic && <p class="explore-notice">You are viewing the gallery. Open MasterBI locally to upload data, chat and save edits.</p>}
    {!projectId && <>
      <div class="explore-upload">
        <h2>Start with a file</h2><p>One to five CSV or XLSX files. Every sheet stays separate; choose the table to analyze.</p>
        <label class="primary explore-file">Choose files<input type="file" accept=".csv,.xlsx" multiple disabled={busy || isStatic} onChange={e => { void upload(e.currentTarget.files); e.currentTarget.value = ''; }} /></label>
        <p class="hint">10 MB per file · 100,000 rows per table · No automatic joins or invented missing data</p>
      </div>
      <h2>Explore a case study</h2><div class="mode-grid">
        {[['retail', 'Retail', 'Compare sales and returns across the year.'], ['saas', 'SaaS', 'Understand customer revenue without double-counting monthly snapshots.'], ['pokemon', 'Pokemon VGC', 'Compare team performance with a weighted win rate and sample sizes.']].map(([kind, title, description]) => <button class="mode-card" disabled={busy} onClick={() => void demo(kind ?? '')} key={kind}><p class="eyebrow">Synthetic case study</p><h2>{title}</h2><p>{description}</p><span>Explore →</span></button>)}
      </div>
      {recent.length > 0 && <><h2>Your projects</h2><div class="explore-recent">{recent.map(p => <button key={p.id} onClick={() => navigate(`/explore/${p.id}`)}>{p.title} →</button>)}</div></>}
    </>}
    {project && draft && <>
      <div class="explore-toolbar"><div class="explore-tabs" aria-label="Workspace views">
        {['dashboard', 'studio', 'data'].map(t => <button class={tab === t ? 'primary' : ''} aria-pressed={tab === t} onClick={() => setTab(t)}>{t[0]?.toUpperCase()}{t.slice(1)}</button>)}
      </div><div class="explore-downloads">{['pdf', 'pptx', 'html', 'csv'].map(kind => <a href={fileUrl(isStatic ? `files/explore/${project.id}/report.${kind}` : `${endpoint}/export/${kind}`)} download>{kind.toUpperCase()} ↓</a>)}</div></div>
      <div class="explore-layout"><div>
        {tab === 'dashboard' && <>
          <div class="explore-kpis">{project.result.totals.map(t => <article class="explore-panel" key={t.name}><p>{t.name}</p><strong>{fmt(t.value)}</strong><small>{t.definition}</small></article>)}</div>
          <article class="explore-panel"><h2>{project.spec.measures[0]?.name} {project.spec.dimension ? `by ${project.spec.dimension}` : ''}</h2><Chart project={project} /><p class="hint">{project.result.rows.length} of {project.result.groups} groups shown. Headline metrics include every filtered record.</p><ResultsTable project={project} /></article>
          <article class="explore-panel"><h2>What the numbers show</h2>{project.result.summary.map(s => <p key={s}>{s}</p>)}</article>
          <article class="explore-panel"><h2>Quality and interpretation</h2><p>{project.spec.context || 'Tell the assistant what one row represents, what the columns mean, and what you want to find out.'}</p>{project.result.warnings.map(w => <p key={w}>{w}</p>)}<p class="hint">Descriptive results. Group differences do not establish cause. Missing values are never silently replaced with zero.</p></article>
        </>}
        {tab === 'studio' && <article class="explore-panel"><h2>Shape your analysis</h2><fieldset disabled={busy || isStatic} class="explore-fields">
          <label>Report title<input value={draft.title} maxLength={100} onInput={e => update({ title: e.currentTarget.value })} /></label>
          <label>What does this data describe?<textarea value={draft.context} maxLength={2000} onInput={e => update({ context: e.currentTarget.value })} placeholder="One row is… Units are… My question is…" /></label>
          <label>Table<select value={draft.table} onChange={e => update({ table: e.currentTarget.value, dimension: '', filters: [], measures: [metric()], date_grain: 'original' })}>{project.tables.map(t => <option value={t.id}>{t.name} · {t.rows} rows</option>)}</select></label>
          <div class="explore-pair"><label>Group by<select value={draft.dimension} onChange={e => update({ dimension: e.currentTarget.value, date_grain: 'original' })}><option value="">All records</option>{table?.columns.map(c => <option>{c}</option>)}</select></label>
          <label>Date grouping<select value={draft.date_grain} onChange={e => update({ date_grain: e.currentTarget.value })}>{['original', 'day', 'month', 'year'].map(v => <option>{v}</option>)}</select></label></div>
          <h3>Metrics</h3>{draft.measures.map((m, i) => {
            const change = (v: Partial<Measure>) => update({ measures: draft.measures.map((item, j) => j === i ? { ...item, ...v } : item) });
            return <div class="explore-metric" key={i}><label>Name<input value={m.name} maxLength={80} onInput={e => change({ name: e.currentTarget.value })} /></label>
              <div class="explore-pair"><label>Calculation<select value={m.aggregation} onChange={e => change({ aggregation: e.currentTarget.value })}>{['count', 'sum', 'mean', 'median', 'min', 'max', 'distinct', 'ratio'].map(v => <option value={v}>{v === 'count' ? 'Count rows' : v}</option>)}</select></label>
              {m.aggregation !== 'count' && <label>Column<select value={m.column} onChange={e => change({ column: e.currentTarget.value })}><option value="">Choose…</option>{table?.columns.map(c => <option>{c}</option>)}</select></label>}</div>
              {m.aggregation === 'ratio' && <label>Denominator (sum)<select value={m.denominator} onChange={e => change({ denominator: e.currentTarget.value })}><option value="">Choose…</option>{table?.columns.map(c => <option>{c}</option>)}</select><small>Ratio of sums over paired, nonmissing rows. Zero denominator returns unavailable.</small></label>}
              {draft.measures.length > 1 && <button onClick={() => update({ measures: draft.measures.filter((_, j) => j !== i) })}>Remove metric</button>}
            </div>;
          })}<button disabled={draft.measures.length >= 6} onClick={() => update({ measures: [...draft.measures, { ...metric(), name: `Metric ${draft.measures.length+1}` }] })}>+ Add metric</button>
          <h3>Filters</h3>{draft.filters.map((f,i) => <div class="explore-metric" key={i}>
            <label>Column<select value={f.column} onChange={e => update({ filters: draft.filters.map((v,j) => j===i ? { ...v, column: e.currentTarget.value } : v) })}>{table?.columns.map(c => <option>{c}</option>)}</select></label>
            <label>Condition<select value={f.operator} onChange={e => update({ filters: draft.filters.map((v,j) => j===i ? { ...v, operator: e.currentTarget.value } : v) })}>{['equals', 'not_equals', 'contains', 'gte', 'lte'].map(v => <option>{v}</option>)}</select></label>
            <label>Value<input value={f.value} onInput={e => update({ filters: draft.filters.map((v,j) => j===i ? { ...v, value: e.currentTarget.value } : v) })} /></label>
            <button onClick={() => update({ filters: draft.filters.filter((_,j) => i !== j) })}>Remove filter</button>
          </div>)}<button disabled={draft.filters.length >= 8} onClick={() => update({ filters: [...draft.filters, { column: table?.columns[0] ?? '', operator: 'equals', value: '' }] })}>+ Add filter</button>
          <h3>Cleaning and design</h3>
          <label><input type="checkbox" checked={draft.trim_text} onChange={e => update({ trim_text: e.currentTarget.checked })} /> Trim surrounding whitespace</label>
          <label><input type="checkbox" checked={draft.drop_duplicates} onChange={e => update({ drop_duplicates: e.currentTarget.checked })} /> Remove exact duplicate rows</label>
          <div class="explore-pair"><label>Chart<select value={draft.chart} onChange={e => update({ chart: e.currentTarget.value })}>{['bar','line','table'].map(v => <option>{v}</option>)}</select></label><label>Accent<input type="color" value={draft.color} onInput={e => update({ color: e.currentTarget.value })} /></label></div>
          <h3>Report sections</h3>{['summary','chart','quality','data'].map(s => <label><input type="checkbox" checked={draft.sections.includes(s)} onChange={() => update({ sections: draft.sections.includes(s) ? draft.sections.filter(v => v !== s) : [...draft.sections,s] })} /> {s}</label>)}
          <div class="explore-toolbar"><button class="primary" disabled={!dirty} onClick={() => void apply(draft)}>Apply and recalculate</button><button disabled={!project.history} onClick={() => void work(async () => accept(await api<Project>(`${endpoint}/undo`, { method: 'POST' })))}>Undo last edit</button></div>
          {dirty && <p role="status">Unsaved edits. Apply to update the dashboard and all exports.</p>}
        </fieldset></article>}
        {tab === 'data' && <article class="explore-panel"><h2>Know your data</h2><p>Active table: {project.tables.find(t => t.id === project.spec.table)?.name}. Change tables in Studio.</p><div class="explore-scroll"><table><thead><tr><th>Column</th><th>Detected type</th><th>Missing</th><th>Distinct</th></tr></thead><tbody>{project.result.columns.map(c => <tr><td>{c.name}</td><td>{c.type}</td><td>{c.missing}</td><td>{c.distinct}</td></tr>)}</tbody></table></div><h3>First eight included rows</h3><div class="explore-scroll"><table><thead><tr>{project.result.columns.map(c => <th>{c.name}</th>)}</tr></thead><tbody>{project.result.preview.map(r => <tr>{project.result.columns.map(c => <td>{r[c.name] || '—'}</td>)}</tr>)}</tbody></table></div><p class="hint">Source files remain unchanged. Cleaning and filters are reversible views.</p></article>}
      </div>
      <aside class="explore-panel explore-assistant"><p class="eyebrow">Your analysis partner</p><h2>Ask. Refine. Explore.</h2><p class="hint">The assistant sees column metadata, computed results and this chat. It proposes edits for you to review.</p>
        <label>OpenAI model<select value={tier} disabled={busy} onChange={e => { setTier(e.currentTarget.value); setEstimate(null); }}><option value="standard">Standard · GPT-5.6 Luna</option><option value="advanced">Advanced · GPT-6 Astra</option></select></label>
        <label>Project AI budget (USD)<input type="number" min="0.01" max="20" step="0.1" value={budget} onInput={e => { setBudget(Number(e.currentTarget.value)); setEstimate(null); }} /></label><p class="hint">Estimated spend so far: ${project.spent_usd.toFixed(4)}. Advanced costs more.</p>
        {!ai?.available && <p class="explore-notice">AI needs an OpenAI API key on your local server. Set OPENAI_API_KEY after installing requirements-ai.txt. Dashboard, Studio and exports work without it.</p>}
        <div class="explore-conversation" aria-live="polite">{project.messages.length === 0 && <p>Try: “What should I clarify before analyzing this?” or “Add an average metric and group by category.”</p>}{project.messages.map((m,i) => <div class={`explore-message ${m.role}`} key={i}><strong>{m.role === 'user' ? 'You' : 'Assistant'}</strong><p>{m.content}</p></div>)}</div>
        {project.proposal && <div class="explore-proposal"><h3>Suggested changes</h3>{Object.entries(project.proposal).filter(([k,v]) => JSON.stringify(v) !== JSON.stringify(project.spec[k as keyof Analysis])).map(([k,v]) => <p key={k}><strong>{k.replaceAll('_', ' ')}</strong><br />{JSON.stringify(v)}</p>)}<button class="primary" disabled={busy || isStatic} onClick={() => project.proposal && void apply(project.proposal)}>Apply suggestion</button><button onClick={() => { setDraft(project.proposal); setTab('studio'); }}>Review in Studio</button></div>}
        <div class="explore-recent">{['Suggest useful analyses and ask what I should clarify.', 'Explain the current results and limitations.', 'Suggest a cleaning step for this table.'].map(s => <button disabled={busy} onClick={() => { setMessage(s); setEstimate(null); }}>{s}</button>)}</div>
        <label>Your question<textarea value={message} maxLength={2000} rows={4} placeholder="Describe your goal or ask for a change…" onInput={e => { setMessage(e.currentTarget.value); setEstimate(null); }} /></label>
        <button disabled={busy || !message.trim() || !ai?.available || isStatic} onClick={() => void work(async () => setEstimate(await api(`${endpoint}/estimate`, { method: 'POST', body: chatBody() })))}>Estimate request</button>
        {estimate && <><p class="hint">Estimated ceiling: ${estimate.estimated_max_usd.toFixed(4)}. {estimate.within_budget ? 'Within project budget.' : 'Over project budget.'}</p><button class="primary" disabled={busy || !estimate.within_budget} onClick={() => void work(async () => { accept(await api<Project>(`${endpoint}/chat`, { method: 'POST', body: chatBody() })); setMessage(''); })}>Send to OpenAI</button></>}
      </aside></div>
    </>}
  </section>;
}
function ResultsTable({ project }: { project: Project }) {
  return <div class="explore-scroll"><table><caption>Computed values</caption><thead><tr><th>{project.spec.dimension || 'Group'}</th>{project.spec.measures.map(m => <th>{m.name}</th>)}<th>Records</th></tr></thead><tbody>{project.result.rows.map(r => <tr key={r.label}><td>{r.label}</td>{r.values.map(v => <td>{fmt(v)}</td>)}<td>{r.records}</td></tr>)}</tbody></table></div>;
}
