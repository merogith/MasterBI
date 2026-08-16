import type { Kpi } from '../lib/api';

/* Where a number came from, on the number.
 *
 * `MetricResult.basis` is derived automatically by `TrackedTables` — every read
 * of a fact table is recorded, so a metric is `measured`, `modelled` or `mixed`
 * without anyone having to declare it. It has been computed for as long as the
 * metrics engine has existed, it travels to the browser in every row of
 * `facts.csv`, and **nothing rendered it**. Same for `provenance` on the
 * profile, which is the survey's central promise: "anything you don't know is
 * filled from sector benchmarks and clearly footnoted". Footnoted in the PDF
 * appendix, and nowhere a user of the app would ever see it.
 *
 * One component, so a figure means the same thing wherever it appears.
 */
const BASIS: Readonly<Record<string, { label: string; help: string }>> = {
  measured: {
    label: 'measured',
    help: 'Computed only from data you supplied.',
  },
  modelled: {
    label: 'modelled',
    help: 'Every table behind this figure was synthesised, because you did not '
      + 'supply one. Treat it as an illustration of shape, not as your number.',
  },
  mixed: {
    label: 'part modelled',
    help: 'Some of the tables behind this figure were synthesised because you '
      + 'did not supply them. The measured part is real; the rest is not.',
  },
};

export function BasisChip({ basis }: { basis: string | null | undefined }) {
  const entry = basis ? BASIS[basis] : undefined;
  if (!entry) return null;
  return (
    <span class={`basis basis-${basis}`} title={entry.help}>{entry.label}</span>
  );
}

/** How this figure sits against its benchmark, and *whose* benchmark.
 *
 *  A band with no citation is worse than none — the KPI schema already refuses
 *  an uncited benchmark, so the citation exists and only had to be shown. */
export function BenchmarkChip({ kpi }: { kpi: Kpi }) {
  if (kpi.benchmark_p50 === null || kpi.benchmark_p50 === undefined) return null;
  const position = kpi.benchmark_position ?? 'unknown';
  const words: Record<string, string> = {
    top_quartile: 'top quartile',
    above_median: 'above median',
    below_median: 'below median',
    bottom_quartile: 'bottom quartile',
  };
  return (
    <span class={`benchmark bench-${position}`}
          title={`Sector median ${kpi.benchmark_p50}`}>
      {words[position] ?? position.replace(/_/g, ' ')}
    </span>
  );
}
