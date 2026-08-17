import type { ComponentChild } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import { BasisChip, BenchmarkChip } from './Basis';
import type { Kpi, RecordSheet, Summary } from '../lib/api';
import { fmtValue, STATUS_GLYPH, STATUS_LABEL } from '../lib/format';

/* The scorecard as the semantic layer it already is.
 *
 * Every field of a KPI's record sheet — formula, grain, owner role, source
 * systems, benchmark **and its citation**, target rule, alert bands, pitfalls,
 * interpretation — has existed in `kpi/library/*.yaml` since the beginning and
 * reached exactly one place: the PDF appendix, sixty pages in. The argument for
 * a semantic layer is that one reviewed definition should be visible everywhere
 * the number is, and this product had the definitions and showed none of them.
 *
 * So: sort by any column, choose the columns, export what you are looking at,
 * and open any metric's record sheet from the row it is on.
 *
 * Two decisions worth stating.
 *
 * **The export is of what you can see**, in the order you sorted it, not a
 * second copy of `facts.csv` — that artifact already exists as a download and
 * duplicating it here would be a worse version of it. An export that silently
 * differs from the table above it is the kind of small dishonesty this project
 * spends its time removing.
 *
 * **A missing field renders as missing.** A record sheet may legitimately have
 * no benchmark or no target rule; showing "—" says so, and inventing a
 * placeholder would put an uncited number on the one screen whose whole purpose
 * is provenance.
 */

type SortKey = 'name' | 'value' | 'status' | 'perspective' | 'tier' | 'basis';

interface Column {
  key: SortKey | 'basis' | 'benchmark' | 'reason';
  label: string;
  /** Numeric columns right-align and sort descending on first click, because
   *  "worst first" is what anyone clicking a value column wants. */
  numeric?: boolean;
  sortable?: boolean;
  help?: string;
}

const COLUMNS: readonly Column[] = [
  { key: 'name', label: 'KPI', sortable: true },
  { key: 'value', label: 'Value', numeric: true, sortable: true,
    help: 'Sorted within unit — a currency figure and a percentage are not '
      + 'comparable numbers.' },
  { key: 'status', label: 'Status', sortable: true },
  { key: 'perspective', label: 'Perspective', sortable: true },
  { key: 'tier', label: 'Tier', numeric: true, sortable: true },
  { key: 'basis', label: 'Where it came from' },
  { key: 'benchmark', label: 'vs sector' },
  { key: 'reason', label: 'Why it is here' },
];

const DEFAULT_COLUMNS = ['name', 'value', 'status', 'basis', 'benchmark', 'reason'];
const STORAGE_KEY = 'masterbi.scorecard.columns';

/** Status sorts by how much it should worry you, not alphabetically — "green"
 *  before "red" would put the healthy metrics at the top of a list someone is
 *  reading to find problems. */
const STATUS_ORDER: Record<string, number> = {
  red: 0, amber: 1, green: 2, unscored: 3, unknown: 4,
};

function sortValue(kpi: Kpi, key: SortKey): string | number {
  switch (key) {
    // Grouped by unit first, and that is not fussiness: $12.0M, 60.2% and 699
    // customers in one numeric order is a ranking of nothing. Within a unit the
    // comparison is real, and across units the grouping says so.
    case 'value': return kpi.current ?? Number.NEGATIVE_INFINITY;
    case 'status': return STATUS_ORDER[kpi.status ?? 'unknown'] ?? 9;
    case 'perspective': return kpi.perspective ?? '';
    case 'tier': return kpi.tier ?? 99;
    case 'basis': return kpi.basis ?? '';
    default: return kpi.name ?? '';
  }
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The record sheet itself: the governed definition, one row per field.
 *
 *  Rendered beside the number rather than in an appendix, which is the entire
 *  point — "what does this actually measure, and what will it mislead me
 *  about" is a question asked while looking at the figure. */
function Sheet({ sheet, kpi, currency, rationale, onClose }: {
  sheet: RecordSheet;
  kpi: Kpi | undefined;
  currency: string;
  rationale: string | undefined;
  onClose: () => void;
}) {
  const band = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : fmtValue(value, sheet.unit ?? null, currency);

  const rows: [string, ComponentChild][] = [
    ['Definition', sheet.formula ?? '—'],
    ['Grain', [sheet.frequency, sheet.timing].filter(Boolean).join(' · ') || '—'],
    ['Owner', sheet.owner_role ?? '—'],
    ['Source systems', (sheet.source_systems ?? []).join(', ') || '—'],
    ['Perspective', sheet.perspective ?? '—'],
    ['Alert bands', sheet.alert_bands
      ? `green ${band(sheet.alert_bands.green)} · red ${band(sheet.alert_bands.red)}`
      : '—'],
    ['Target rule', sheet.target_rule ?? '—'],
    ['Benchmark', sheet.benchmark
      ? <>
          p25 {band(sheet.benchmark.p25)} · p50 {band(sheet.benchmark.p50)}
          {' · '}p75 {band(sheet.benchmark.p75)}
          {/* The citation is mandatory in `kpi/schema.py` — "an uncited
              benchmark is worse than none" — and until now nobody outside the
              PDF could read it. */}
          {sheet.benchmark.source && <><br /><em>{sheet.benchmark.source}</em></>}
        </>
      : 'none published for this metric'],
    ['Applies when', sheet.applies_when ?? 'every business in this pack'],
    ['Needs', (sheet.requires_data ?? []).join(', ') || 'no data beyond the fact tables'],
  ];

  /* Escape is handled by the dialog, and focus lands on it in a ref callback.
   *
   * It was a `keydown` listener registered in `useEffect`, which CI caught and
   * a local run never did: the sheet was on screen — rendered, painted,
   * clickable — while the listener that closes it did not exist yet, because
   * Preact defers effects past paint. On the runner Escape did nothing.
   *
   * A ref callback runs during commit, synchronously with the node being
   * attached, so by the time anyone can see the panel it already has focus and
   * its own `onKeyDown`. Which is also what a dialog owes a keyboard user:
   * 7.4 will do the focus trap, and this is the half that cannot wait. */
  return (
    <div class="sheet-backdrop" onClick={onClose}>
      <aside class="record-sheet" id="kpi-sheet" role="dialog" aria-modal="true"
             aria-label={`${sheet.name} record sheet`} tabIndex={-1}
             ref={(node) => node?.focus()}
             onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
             onClick={(e) => e.stopPropagation()}>
        <header class="sheet-head">
          <div>
            <h3>{sheet.name}</h3>
            <code class="sheet-id">{sheet.id}</code>
          </div>
          <button class="ghost" type="button" id="kpi-sheet-close"
                  onClick={onClose} aria-label="Close">✕</button>
        </header>

        {kpi && (
          <p class="sheet-current">
            <strong>{kpi.computed
              ? fmtValue(kpi.current, kpi.unit, currency)
              : 'not computed'}</strong>
            <BasisChip basis={kpi.basis} />
            {!kpi.computed && kpi.reason && <span class="watch-for">{kpi.reason}</span>}
          </p>
        )}

        <dl class="sheet-fields">
          {rows.map(([label, value]) => (
            <div class="sheet-row" key={label}>
              <dt>{label}</dt><dd>{value}</dd>
            </div>
          ))}
        </dl>

        {sheet.interpretation && (
          <section class="sheet-prose">
            <h4>How to read it</h4><p>{sheet.interpretation}</p>
          </section>
        )}
        {sheet.pitfalls && (
          <section class="sheet-prose">
            <h4>What it will mislead you about</h4><p>{sheet.pitfalls}</p>
          </section>
        )}
        {rationale && (
          <section class="sheet-prose">
            <h4>Why it is on this scorecard</h4><p>{rationale}</p>
          </section>
        )}
      </aside>
    </div>
  );
}

type DriverNodes = Record<string, { name: string; parent: string | null }>;

/** What this metric rolls up into: "ARR -> NRR -> GRR".
 *
 *  The record sheets have described this the whole time. It answers "why is
 *  this on here" with a structural claim rather than a scoring one — GRR is on
 *  the scorecard because it drives NRR, which drives ARR — and that is the half
 *  of the question a selection score cannot answer. */
export function DriverPath({ kpiId, nodes }: { kpiId: string; nodes: DriverNodes }) {
  const path: string[] = [];
  const seen = new Set<string>();
  let current: string | null = kpiId;
  while (current !== null && !seen.has(current)) {
    const node: DriverNodes[string] | undefined = nodes[current];
    if (node === undefined) break;
    seen.add(current);
    path.unshift(node.name);
    current = node.parent;
  }
  if (path.length < 2) return null;
  return (
    <span class="driver-path" title="What this metric rolls up into">
      {path.join(' → ')}
    </span>
  );
}

export function Scorecard({ summary }: { summary: Summary }) {
  const kpis = summary.kpis ?? [];
  const nodes: DriverNodes = summary.drivers?.nodes ?? {};
  const sheets = summary.sheets ?? {};
  const rationale = summary.rationale ?? {};
  const currency = summary.currency ?? 'USD';

  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>(
    { key: 'tier', desc: false });
  const [visible, setVisible] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      const parsed = saved ? JSON.parse(saved) : null;
      return Array.isArray(parsed) && parsed.length > 0 ? parsed : DEFAULT_COLUMNS;
    } catch { return DEFAULT_COLUMNS; }
  });
  const [chooser, setChooser] = useState(false);
  const [openKpi, setOpenKpi] = useState<string | null>(null);

  /* Written where the choice is made, not in an effect afterwards.
   *
   * It was `useEffect(..., [visible])`, which reads as the idiomatic way to
   * persist state and cost two CI failures: Preact flushes effects after paint
   * via `requestAnimationFrame`, and a headless runner with no compositor can
   * defer that past the reload the user (or the test) triggers next. The
   * checkbox was ticked, the table showed the new column, and the preference
   * was gone on the next page load — on CI every time, locally never.
   *
   * A preference belongs to the click, so it is saved by the click. */
  const remember = (next: string[]) => {
    setVisible(next);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  };

  const columns = COLUMNS.filter((c) => visible.includes(c.key));

  const rows = useMemo(() => {
    const sorted = [...kpis].sort((a, b) => {
      let cmp = 0;
      if (sort.key === 'value') {
        cmp = (a.unit ?? '').localeCompare(b.unit ?? '');
        // The unit grouping is not reversed by the sort direction — flipping it
        // would put percentages above currency and call that "descending".
        if (cmp !== 0) return cmp;
      }
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      if (typeof left === 'number' && typeof right === 'number') cmp = left - right;
      else cmp = String(left).localeCompare(String(right));
      // Name is the final tie-break so the order never depends on the order the
      // engine happened to emit rows in, which is not a fact about the metrics.
      if (cmp === 0) cmp = (a.name ?? '').localeCompare(b.name ?? '');
      return sort.desc ? -cmp : cmp;
    });
    return sorted;
  }, [kpis, sort]);

  const cell = (kpi: Kpi, key: Column['key']): ComponentChild => {
    switch (key) {
      case 'name': return (
        <button class="linkish kpi-open" type="button"
                onClick={() => setOpenKpi(kpi.kpi_id)}
                title="Open the record sheet">
          {kpi.name}
        </button>
      );
      case 'value': return kpi.computed
        ? fmtValue(kpi.current, kpi.unit, currency)
        : <span class="watch-for">not computed</span>;
      case 'status': return (
        <span class={`chip status-${kpi.status ?? 'unknown'}`}>
          {STATUS_GLYPH[kpi.status ?? 'unknown'] ?? '○'}{' '}
          {STATUS_LABEL[kpi.status ?? 'unknown'] ?? 'No data'}
        </span>
      );
      case 'perspective': return kpi.perspective ?? '—';
      case 'tier': return kpi.tier ?? '—';
      case 'basis': return <BasisChip basis={kpi.basis} />;
      case 'benchmark': return <BenchmarkChip kpi={kpi} />;
      case 'reason': return (
        <>
          {/* For an uncomputed KPI the engine's own reason is far more use
              than its selection rationale: "needs the headcount table, which
              this run does not have" is actionable. */}
          {kpi.computed ? rationale[kpi.kpi_id] ?? '' : kpi.reason ?? ''}
          <DriverPath kpiId={kpi.kpi_id} nodes={nodes} />
        </>
      );
      default: return '';
    }
  };

  const exportCsv = () => {
    const text = (kpi: Kpi, key: Column['key']): string => {
      switch (key) {
        case 'name': return kpi.name ?? kpi.kpi_id;
        case 'value': return kpi.computed && kpi.current !== null
          ? String(kpi.current) : '';
        case 'status': return kpi.status ?? '';
        case 'perspective': return kpi.perspective ?? '';
        case 'tier': return kpi.tier === null || kpi.tier === undefined ? '' : String(kpi.tier);
        case 'basis': return kpi.basis ?? '';
        case 'benchmark': return kpi.benchmark_position ?? '';
        case 'reason': return kpi.computed ? rationale[kpi.kpi_id] ?? '' : kpi.reason ?? '';
        default: return '';
      }
    };
    const lines = [
      ['kpi_id', ...columns.map((c) => c.label)].map(csvCell).join(','),
      ...rows.map((kpi) =>
        [kpi.kpi_id, ...columns.map((c) => text(kpi, c.key))].map(csvCell).join(',')),
    ];
    // ﻿ so Excel reads the file as UTF-8 rather than the local code page,
    // which is the same reason `ingest/readers.py` has to strip one.
    const blob = new Blob([`﻿${lines.join('\r\n')}\r\n`],
                         { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${summary.company.replace(/\W+/g, '_').toLowerCase()}_scorecard.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const toggleSort = (key: SortKey, numeric: boolean | undefined) => {
    setSort((current) => current.key === key
      ? { key, desc: !current.desc }
      : { key, desc: Boolean(numeric) });
  };

  const open = openKpi ? sheets[openKpi] : undefined;

  return (
    <section class="scorecard-surface" id="res-scorecard-surface">
      <div class="scorecard-bar">
        <h3 class="section-title">The scorecard</h3>
        <div class="scorecard-tools">
          <button class="ghost" type="button" id="sc-columns"
                  aria-expanded={chooser} onClick={() => setChooser(!chooser)}>
            Columns ({columns.length})
          </button>
          <button class="ghost" type="button" id="sc-export" onClick={exportCsv}>
            Export CSV
          </button>
        </div>
      </div>

      {chooser && (
        <fieldset class="column-chooser" id="sc-chooser">
          <legend>Columns</legend>
          {COLUMNS.map((column) => (
            <label key={column.key}>
              <input type="checkbox" checked={visible.includes(column.key)}
                     // The KPI name is the row's identity and its way into the
                     // record sheet; a table of anonymous numbers is not a
                     // configuration anyone wants.
                     disabled={column.key === 'name'}
                     onChange={() => remember(visible.includes(column.key)
                       ? visible.filter((k) => k !== column.key)
                       : [...visible, column.key])} />
              {column.label}
            </label>
          ))}
        </fieldset>
      )}

      <div class="table-wrap">
        <table class="scorecard" id="res-scorecard">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} class={column.numeric ? 'num' : undefined}
                    aria-sort={sort.key === column.key
                      ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
                  {column.sortable ? (
                    <button class="th-sort" type="button"
                            id={`sc-sort-${column.key}`} title={column.help}
                            onClick={() => toggleSort(column.key as SortKey,
                                                      column.numeric)}>
                      {column.label}
                      <span aria-hidden="true">
                        {sort.key === column.key ? (sort.desc ? ' ▾' : ' ▴') : ''}
                      </span>
                    </button>
                  ) : column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((kpi) => (
              <tr key={kpi.kpi_id} class={kpi.computed ? '' : 'not-computed'}>
                {columns.map((column) => (
                  <td key={column.key}
                      class={[column.numeric ? 'num' : '',
                              column.key === 'reason' ? 'reason-cell' : '']
                              .filter(Boolean).join(' ')}>
                    {cell(kpi, column.key)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {open && (
        <Sheet sheet={open} kpi={kpis.find((k) => k.kpi_id === openKpi)}
               currency={currency} rationale={rationale[open.id]}
               onClose={() => setOpenKpi(null)} />
      )}
    </section>
  );
}
