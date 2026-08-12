/* The eight Studio panels.
 *
 * Every control here edits one field of the run spec, which is why they share
 * a handful of primitives rather than each building their own markup — the
 * legacy panels were eight hand-written `innerHTML` templates, and the
 * duplication is what made them drift from each other.
 */
import type { CatalogKpi, CatalogOptions, Spec } from '../lib/api';
import { getPath, setPath, titleCase, toggleIn } from '../lib/spec';

export interface PanelProps {
  spec: Spec;
  options: CatalogOptions;
  catalog: CatalogKpi[];
  onChange: (spec: Spec) => void;
}

const DETECTOR_LABEL: Record<string, string> = {
  status_breaches: 'KPIs off target', benchmark_gaps: 'Behind the benchmark',
  trend_breaks: 'Trend breaks', segment_outliers: 'Segment outliers',
  operating_leverage: 'Operating leverage', arr_bridge: 'ARR bridge',
  channel_efficiency: 'Channel efficiency', runway: 'Cash runway',
};

const ARTIFACT_LABEL: Record<string, string> = {
  dashboard: 'Interactive dashboard', workbook: 'Excel workbook',
  report_pdf: 'PDF report', deck_pptx: 'PowerPoint deck',
  doc_docx: 'Word report', charts_png: 'Chart images',
  csv_bundle: 'Fact table CSVs', facts_csv: 'KPI facts table',
  json_dumps: 'Findings and KPI set JSON',
};

const SECTION_LABEL: Record<string, string> = {
  cover: 'Cover', exec_summary: 'Executive summary', scorecard: 'Scorecard',
  diagnostic: 'Diagnostic', deep_dives: 'Deep dives', benchmarks: 'Benchmarks',
  risks: 'Risks', actions: 'Actions', appendix: 'Appendix',
};

function Section({ title, blurb, children }: {
  title: string; blurb: preact.ComponentChildren; children: preact.ComponentChildren;
}) {
  return (
    <div class="studio-section">
      <h2 class="section-title">{title}</h2>
      <p class="section-sub">{blurb}</p>
      {children}
    </div>
  );
}

function Choice({ label, path, choices, spec, onChange }: {
  label: string; path: string; choices: string[];
} & Pick<PanelProps, 'spec' | 'onChange'>) {
  const value = getPath(spec, path) as string | undefined;
  return (
    <label class="field">
      <span>{label}</span>
      <select data-spec={path} value={value ?? ''}
              onChange={(e) => onChange(setPath(spec, path, e.currentTarget.value))}>
        {choices.map((choice) => (
          <option value={choice} key={choice}>{titleCase(choice)}</option>
        ))}
      </select>
    </label>
  );
}

function NumberField({ label, path, min, max, placeholder, spec, onChange }: {
  label: string; path: string; min?: number; max?: number; placeholder?: string;
} & Pick<PanelProps, 'spec' | 'onChange'>) {
  const value = getPath(spec, path) as number | null | undefined;
  return (
    <label class="field">
      <span>{label}</span>
      <input type="number" data-spec={path} min={min} max={max}
             placeholder={placeholder} value={value ?? ''}
             onChange={(e) => {
               const raw = e.currentTarget.value;
               // Empty is "unset", which is not the same as zero: the server
               // falls back to the profile's own value for a null.
               onChange(setPath(spec, path, raw === '' ? null : Number(raw)));
             }} />
    </label>
  );
}

function Toggle({ label, sub, checked, onToggle, attrs }: {
  label: string; sub?: string; checked: boolean; onToggle: () => void;
  attrs?: Record<string, string>;
}) {
  return (
    <label class="toggle">
      <input type="checkbox" checked={checked} onChange={onToggle} {...(attrs ?? {})} />
      <span>{label}{sub && <span class="toggle-sub">{sub}</span>}</span>
    </label>
  );
}

// -- source ----------------------------------------------------------------

export function SourcePanel({ spec, options, onChange }: PanelProps) {
  const generator = (getPath(spec, 'source.generator') ?? {}) as Spec;
  const isUpload = Boolean((getPath(spec, 'source.uploads') as unknown[] | undefined)?.length);
  const fill = (getPath(spec, 'source.fill_gaps') ?? []) as string[];

  return (
    <Section title="Where the numbers come from"
             blurb={isUpload
               ? `Your own files. They enter the same fact tables generated data
                  does, so everything downstream treats them identically.`
               : `Synthetic data, generated from the profile. The same seed always
                  produces the same company — reproducibility is a feature, not a
                  coincidence.`}>
      <h3 class="studio-sub">Fill gaps from the generator</h3>
      <p class="hint">A table you have not uploaded is modelled instead, so a
         partial upload still produces a complete pack. Untick one to leave it
         empty and let the KPIs that need it drop out with a reason.</p>
      <div class="toggle-grid">
        {options.fact_tables.map((table) => (
          <Toggle key={table} label={titleCase(table)}
                  attrs={{ 'data-fill': table }}
                  checked={fill.includes(table)}
                  onToggle={() => onChange(
                    toggleIn(spec, 'source.fill_gaps', table, fill))} />
        ))}
      </div>

      {!isUpload && (
        <>
          <h3 class="studio-sub">Generator</h3>
          <div class="field-row">
            <NumberField label="Seed" path="source.generator.seed"
                         placeholder="from the profile" spec={spec} onChange={onChange} />
            <NumberField label="Months of history" min={13} max={120}
                         path="source.generator.history_months"
                         placeholder="from the profile" spec={spec} onChange={onChange} />
          </div>
          <div class="field-row">
            <label class="field">
              <span>Seasonality</span>
              <input type="range" min={0} max={3} step={0.25}
                     data-spec-number="source.generator.seasonality_amplitude"
                     value={String(generator['seasonality_amplitude'] ?? 1)}
                     onInput={(e) => onChange(setPath(
                       spec, 'source.generator.seasonality_amplitude',
                       Number(e.currentTarget.value)))} />
              <span class="range-value">
                {Number(generator['seasonality_amplitude'] ?? 1).toFixed(2)}
              </span>
            </label>
            <label class="field">
              <span>Volatility</span>
              <input type="range" min={0} max={3} step={0.25}
                     data-spec-number="source.generator.volatility"
                     value={String(generator['volatility'] ?? 1)}
                     onInput={(e) => onChange(setPath(
                       spec, 'source.generator.volatility',
                       Number(e.currentTarget.value)))} />
              <span class="range-value">
                {Number(generator['volatility'] ?? 1).toFixed(2)}
              </span>
            </label>
            <Toggle label="Plant deliberate events"
                    sub="churn spike, CAC inflation, margin compression"
                    checked={generator['inject_anomalies'] !== false}
                    onToggle={() => onChange(setPath(
                      spec, 'source.generator.inject_anomalies',
                      generator['inject_anomalies'] === false))} />
          </div>
          <p class="hint">Seasonality scales the swing around the annual average,
             so 0 is a flat year and 2 is twice the usual peak-to-trough.
             Volatility widens month-to-month noise without moving the trend.
             Any of these regenerates the company and everything after it — the
             most expensive edit in the Studio.</p>
        </>
      )}
    </Section>
  );
}

// -- cleaning --------------------------------------------------------------

export function CleanPanel({ spec, onChange }: PanelProps) {
  const steps = (getPath(spec, 'cleaning.steps') ?? []) as Spec[];

  function replace(next: Spec[]) {
    onChange(setPath(spec, 'cleaning.steps', next));
  }

  return (
    <Section title="Cleaning"
             blurb={`An ordered recipe, applied before anything is computed.
                     Order matters and is preserved: a rename after a filter is
                     not the same as a rename before it.`}>
      {steps.length === 0
        ? <p class="empty">No cleaning steps. The data is used as it arrives.</p>
        : (
          <div class="op-list">
            {steps.map((step, index) => (
              <div class={`op-card${step['enabled'] === false ? ' off' : ''}`}
                   key={index}>
                <div class="op-num">{index + 1}</div>
                <div class="op-body">
                  <div class="op-title">
                    {String(step['op'])}
                    <span class="op-target">{String(step['table'] ?? 'every table')}</span>
                  </div>
                  <div class="op-line">{JSON.stringify(step['params'])}</div>
                </div>
                <div class="op-actions">
                  <button data-op-toggle={index}
                          onClick={() => replace(steps.map((s, i) =>
                            i === index ? { ...s, enabled: s['enabled'] === false } : s))}>
                    {step['enabled'] === false ? 'Enable' : 'Disable'}
                  </button>
                  <button data-op-move={index} data-dir="-1" disabled={index === 0}
                          onClick={() => {
                            const next = [...steps];
                            const above = next[index - 1] as Spec;
                            next[index - 1] = next[index] as Spec;
                            next[index] = above;
                            replace(next);
                          }}>↑</button>
                  <button data-op-move={index} data-dir="1"
                          disabled={index === steps.length - 1}
                          onClick={() => {
                            const next = [...steps];
                            const below = next[index + 1] as Spec;
                            next[index + 1] = next[index] as Spec;
                            next[index] = below;
                            replace(next);
                          }}>↓</button>
                  <button data-op-drop={index}
                          onClick={() => replace(steps.filter((_, i) => i !== index))}>
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
    </Section>
  );
}

// -- model -----------------------------------------------------------------

export function ModelPanel({ spec, onChange }: PanelProps) {
  const columns = (getPath(spec, 'model.calculated_columns') ?? []) as Spec[];

  return (
    <Section title="Calculated columns"
             blurb={<>Add a field to a fact table with a formula, evaluated one
                      row at a time — <code>final_acv - initial_acv</code> on
                      customers. KPIs can then aggregate over what you build
                      here.</>}>
      <table class="kpi-table">
        <thead><tr><th>Column</th><th>Formula</th><th /></tr></thead>
        <tbody>
          {columns.length === 0
            ? <tr><td colSpan={3} class="empty">No calculated columns yet.</td></tr>
            : columns.map((column, index) => (
              <tr key={index}>
                <td>
                  <span class="k-name">{String(column['name'])}</span>
                  <div class="k-id">{String(column['table'])}</div>
                </td>
                <td><code style={{ fontSize: '12px' }}>{String(column['expression'])}</code></td>
                <td>
                  <div class="k-state">
                    <button data-drop-col={index}
                            onClick={() => onChange(setPath(
                              spec, 'model.calculated_columns',
                              columns.filter((_, i) => i !== index)))}>
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </Section>
  );
}

// -- KPIs ------------------------------------------------------------------

export function KpiPanel({ spec, catalog, onChange }: PanelProps) {
  const pinned = (getPath(spec, 'metrics.pinned') ?? []) as string[];
  const excluded = (getPath(spec, 'metrics.excluded') ?? []) as string[];

  return (
    <Section title="Which KPIs, and your own"
             blurb={`Pin one to force it onto the scorecard, or exclude one to
                     drop it. The Balanced Scorecard coverage warnings still
                     fire — you can overrule the selection engine, and it will
                     tell you what that cost.`}>
      <table class="kpi-table">
        <thead><tr><th>KPI</th><th>State</th></tr></thead>
        <tbody>
          {catalog.map((kpi) => (
            <tr class={excluded.includes(kpi.id) ? 'excluded' : ''} key={kpi.id}>
              <td>
                <span class="k-name">{kpi.name}</span>
                {kpi.origin === 'user' && <span class="tag-user">yours</span>}
                <div class="k-id">
                  {kpi.id} · {kpi.unit} · {(kpi.timing ?? '').replace('_', ' ')}
                </div>
              </td>
              <td>
                <div class="k-state">
                  <button data-pin={kpi.id}
                          class={pinned.includes(kpi.id) ? 'on' : ''}
                          onClick={() => {
                            let next = toggleIn(spec, 'metrics.pinned', kpi.id, pinned);
                            // Pinned and excluded are contradictory intents;
                            // holding both would make the run's behaviour
                            // depend on which list the engine reads first.
                            next = setPath(next, 'metrics.excluded',
                                           excluded.filter((id) => id !== kpi.id));
                            onChange(next);
                          }}>
                    Pin
                  </button>
                  <button data-exclude={kpi.id}
                          class={excluded.includes(kpi.id) ? 'off' : ''}
                          onClick={() => {
                            let next = toggleIn(spec, 'metrics.excluded', kpi.id, excluded);
                            next = setPath(next, 'metrics.pinned',
                                           pinned.filter((id) => id !== kpi.id));
                            onChange(next);
                          }}>
                    Exclude
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

// -- analysis --------------------------------------------------------------

export function AnalysisPanel({ spec, options, onChange }: PanelProps) {
  const disabled = (getPath(spec, 'analysis.disabled') ?? []) as string[];

  return (
    <Section title="What to look for"
             blurb={`Deterministic detectors — each one produces findings with
                     the numbers already computed. Turning some off is a real
                     editing choice: a bank cares about runway and
                     concentration, and twenty findings bury the two that
                     matter.`}>
      <div class="toggle-grid">
        {options.detectors.map((detector) => (
          <Toggle key={detector} attrs={{ 'data-detector': detector }}
                  label={DETECTOR_LABEL[detector] ?? titleCase(detector)}
                  sub={detector} checked={!disabled.includes(detector)}
                  onToggle={() => onChange(
                    toggleIn(spec, 'analysis.disabled', detector, disabled))} />
        ))}
      </div>
      <div class="field-row" style={{ marginTop: '18px' }}>
        <NumberField label="Most findings to keep" min={1} max={60}
                     path="analysis.max_findings" placeholder="all"
                     spec={spec} onChange={onChange} />
      </div>
    </Section>
  );
}

// -- design ----------------------------------------------------------------

export function DesignPanel({ spec, options, onChange }: PanelProps) {
  const design = (getPath(spec, 'design') ?? {}) as Spec;
  const all = options.sections;
  const order = (design['sections'] as string[] | null) ?? all.map((s) => s.id);
  const titleOf = Object.fromEntries(all.map((s) => [s.id, s.title]));
  const chosen = (design['exhibits'] as string[] | null) ?? options.exhibits;
  const widths = (design['exhibit_widths'] ?? {}) as Record<string, string>;
  const dropped = all.filter((section) => !order.includes(section.id));

  return (
    <Section title="How it looks"
             blurb={`Theme drives the dashboard and every exhibit. A brand colour
                     is measured before it is used: it must stay readable against
                     the page and stay distinguishable from the other two series,
                     including for readers with colour vision deficiency.`}>
      <div class="field-row">
        <Choice label="Theme" path="design.theme" choices={options.themes}
                spec={spec} onChange={onChange} />
        <Choice label="Page size" path="design.page_size" choices={['A4', 'Letter']}
                spec={spec} onChange={onChange} />
      </div>

      <h3 class="studio-sub">Brand</h3>
      <div class="field-row">
        {([['primary', 'Primary colour', '#2a78d6'],
           ['accent', 'Secondary (optional)', 'leave blank'],
           ['logo_path', 'Logo (PNG or JPEG)', 'path or uploaded filename']] as const)
          .map(([field, label, placeholder]) => (
            <label class="field" key={field}>
              <span>{label}</span>
              <input type="text" id={`brand-${field}`} placeholder={placeholder}
                     value={String((design['brand'] as Spec | undefined)?.[field] ?? '')}
                     onInput={(e) => onChange(setPath(
                       spec, `design.brand.${field}`,
                       e.currentTarget.value.trim() || null))} />
            </label>
          ))}
      </div>

      <h3 class="studio-sub">Sections</h3>
      <p class="hint">Order and inclusion apply to the PDF, the editable report
         and the deck alike. Sections are renumbered as you move them.</p>
      <ol class="order-list">
        {order.map((id, index) => (
          <li class="order-row" key={id}>
            <span class="order-num">{index + 1}</span>
            <span class="order-name">{titleOf[id] ?? id}</span>
            <span class="order-actions">
              <button data-sec-move={index} data-dir="-1" disabled={index === 0}
                      title="Move up"
                      onClick={() => {
                        const next = [...order];
                        const above = next[index - 1] as string;
                        next[index - 1] = next[index] as string;
                        next[index] = above;
                        onChange(setPath(spec, 'design.sections', next));
                      }}>↑</button>
              <button data-sec-move={index} data-dir="1"
                      disabled={index === order.length - 1} title="Move down"
                      onClick={() => {
                        const next = [...order];
                        const below = next[index + 1] as string;
                        next[index + 1] = next[index] as string;
                        next[index] = below;
                        onChange(setPath(spec, 'design.sections', next));
                      }}>↓</button>
              <button data-sec-drop={id} title="Remove from the report"
                      onClick={() => onChange(setPath(
                        spec, 'design.sections',
                        order.filter((other) => other !== id)))}>✕</button>
            </span>
          </li>
        ))}
      </ol>
      {dropped.length > 0 && (
        <p class="hint">Not included:{' '}
          {dropped.map((section) => (
            <button class="chip-btn" data-sec-add={section.id} key={section.id}
                    onClick={() => onChange(setPath(
                      spec, 'design.sections', [...order, section.id]))}>
              + {section.title}
            </button>
          ))}
        </p>
      )}

      <h3 class="studio-sub">Exhibits</h3>
      <p class="hint">A chart with no data behind it is skipped whether or not
         it is ticked here.</p>
      <div class="toggle-grid">
        {options.exhibits.map((exhibit) => {
          const on = chosen.includes(exhibit);
          return (
            <label class="toggle" key={exhibit}>
              <input type="checkbox" data-exhibit={exhibit} checked={on}
                     onChange={() => onChange(setPath(
                       spec, 'design.exhibits',
                       on ? chosen.filter((id) => id !== exhibit)
                          : [...chosen, exhibit]))} />
              <span>{exhibit.replace(/_/g, ' ')}</span>
              <select data-exhibit-width={exhibit} disabled={!on}
                      value={widths[exhibit] ?? 'half'}
                      onChange={(e) => onChange(setPath(
                        spec, 'design.exhibit_widths',
                        { ...widths, [exhibit]: e.currentTarget.value }))}>
                {options.widths.map((width) => (
                  <option value={width} key={width}>{width}</option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
    </Section>
  );
}

// -- outputs ---------------------------------------------------------------

export function OutputsPanel({ spec, options, onChange }: PanelProps) {
  const chosen = (getPath(spec, 'outputs.artifacts') ?? options.artifacts) as string[];

  return (
    <Section title="What to produce"
             blurb={`Rendering is around 80% of a run, and the chart image export
                     alone is 37%. Asking for only what you need is the single
                     biggest speed-up available — dashboard-only finishes in
                     about a second rather than seven.`}>
      <div class="toggle-grid">
        {options.artifacts.map((artifact) => (
          <Toggle key={artifact} attrs={{ 'data-artifact': artifact }}
                  label={ARTIFACT_LABEL[artifact] ?? titleCase(artifact)}
                  checked={chosen.includes(artifact)}
                  onToggle={() => onChange(setPath(
                    spec, 'outputs.artifacts',
                    chosen.includes(artifact)
                      ? chosen.filter((id) => id !== artifact)
                      : [...chosen, artifact]))} />
        ))}
      </div>
    </Section>
  );
}

// -- AI --------------------------------------------------------------------

export function AiPanel({ spec, onChange, status }: PanelProps & {
  status: { available: boolean; reason?: string; default_model?: string;
            narratable_sections?: string[] };
}) {
  const ai = (getPath(spec, 'ai') ?? {}) as Spec;

  if (!status.available) {
    // Say what is missing and what to type. An "AI unavailable" badge with no
    // remedy is the same as no feature at all.
    return (
      <Section title="AI"
               blurb={`Off, and the pipeline does not need it — every artifact is
                       produced without a model. ${status.reason ?? 'Not configured.'}`}>
        <pre class="ai-setup">{'pip install -r requirements-ai.txt\nexport ANTHROPIC_API_KEY=…'}</pre>
      </Section>
    );
  }

  const narratable = status.narratable_sections ?? [];
  const chosen = (ai['narrate_sections'] as string[] | null) ?? narratable;

  return (
    <Section title="AI"
             blurb={`Off by default. The narrator writes the connective paragraph
                     in each section from the computed KPI table — never the
                     underlying data — and every figure it writes is checked
                     against that table before it is printed. Neither the
                     narrator nor the planner can change a number.`}>
      <label class="toggle ai-master">
        <input type="checkbox" data-spec-bool="ai.enabled"
               checked={Boolean(ai['enabled'])}
               onChange={() => onChange(setPath(spec, 'ai.enabled', !ai['enabled']))} />
        <span>Write the narrative on the next run
          <span class="toggle-sub">{String(ai['model'] ?? status.default_model ?? '')}</span>
        </span>
      </label>

      <h3 class="studio-sub">Sections to narrate</h3>
      <div class="toggle-grid">
        {narratable.map((section) => (
          <Toggle key={section} attrs={{ 'data-narrate': section }}
                  label={SECTION_LABEL[section] ?? titleCase(section)}
                  checked={chosen.includes(section)}
                  onToggle={() => onChange(setPath(
                    spec, 'ai.narrate_sections',
                    chosen.includes(section)
                      ? chosen.filter((id) => id !== section)
                      : [...chosen, section]))} />
        ))}
      </div>
    </Section>
  );
}
