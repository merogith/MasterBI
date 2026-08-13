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
  unscored: 'No target', unknown: 'No data',
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
