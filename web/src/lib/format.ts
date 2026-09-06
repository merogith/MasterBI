/* Value formatting, mirroring `kpi_maker/fmt.py` exactly.
 *
 * A number must read the same here, in the PDF and in the workbook. Three
 * formatters would guarantee they eventually disagree — this is a port of the
 * legacy front end's copy, not a third one. */

const CURRENCY_SYMBOL: Readonly<Record<string, string>> = {
  USD: '$', EUR: '€', GBP: '£', TRY: '₺', JPY: '¥',
  SEK: 'kr', CAD: 'C$', AUD: 'A$', INR: '₹', AED: 'AED ',
};

export const STATUS_LABEL: Readonly<Record<string, string>> = {
  green: 'On track', amber: 'Watch', red: 'Off track',
  unscored: 'Not scored', unknown: 'No data',
};

export const STATUS_GLYPH: Readonly<Record<string, string>> = {
  green: '●', amber: '▲', red: '■', unscored: '◇', unknown: '○',
};

export const SEVERITY_LABEL: Readonly<Record<string, string>> = {
  critical: 'Critical', high: 'High', medium: 'Medium',
  low: 'Low', positive: 'Strength',
};

export function fmtValue(
  value: number | null | undefined,
  unit: string | null | undefined,
  currency = 'USD',
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const v = Number(value);
  if (unit === 'pct') return (v * 100).toFixed(1) + '%';
  if (unit === 'currency') {
    const symbol = CURRENCY_SYMBOL[currency] ?? '';
    for (const [threshold, suffix] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
      if (Math.abs(v) >= threshold) return symbol + (v / threshold).toFixed(1) + suffix;
    }
    return symbol + Math.round(v).toLocaleString();
  }
  if (unit === 'months') return v.toFixed(1) + ' mo';
  if (unit === 'days') return v.toFixed(1) + ' d';
  if (unit === 'hours') return v.toFixed(1) + ' h';
  if (unit === 'count' || unit === 'score') return Math.round(v).toLocaleString();
  return v.toFixed(2);
}

export const fmtBytes = (n: number): string =>
  n > 1e6 ? (n / 1e6).toFixed(1) + ' MB'
    : n > 1e3 ? Math.round(n / 1e3) + ' KB'
      : n + ' B';


/** How far a metric moved against a prior reading, unsigned.
 *
 *  Mirrors `kpi_maker.fmt.fmt_move`, and `tests/test_packaging.py` holds the
 *  two in step — the same arrangement as STATUS_LABEL above and the design
 *  tokens, because TypeScript cannot import from the engine.
 *
 *  A percentage metric moves in POINTS, not per cent, and `pct` values arrive
 *  as fractions. Getting that wrong is not hypothetical: the engine's inline
 *  version printed a 4.4-point move in gross margin as "0.0 pts" on every
 *  dashboard until 5.3g pulled the rule out and ran it against known inputs.
 *
 *  Unsigned on purpose. The direction is an arrow, and whether a move is good
 *  is `KPI.improves_with` — the one place that decides. */
export function fmtMove(
  current: number | null | undefined,
  prior: number | null | undefined,
  unit: string | null,
): string | null {
  if (current === null || current === undefined) return null;
  if (prior === null || prior === undefined || !prior) return null;
  const change = current - prior;
  if (unit === 'pct') return `${(Math.abs(change) * 100).toFixed(1)} pts`;
  return `${Math.round((Math.abs(change) / Math.abs(prior)) * 100)}%`;
}
