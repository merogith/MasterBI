import { useState } from 'preact/hooks';
import { BasisChip, BenchmarkChip } from './Basis';
import type { Kpi, Summary } from '../lib/api';
import { fmtValue } from '../lib/format';

/* Everything the engine already worked out about its own answer.
 *
 * The selection engine records why each KPI is on the scorecard and why each
 * rejected one is not — on a real SaaS run, 25 rationales and **19 dropped
 * KPIs with a reason each**. The profile records, per field, whether the figure
 * came from the user or from a sector prior. Every metric records what it
 * actually read. All of it reached the browser in the run payload. None of it
 * was rendered anywhere.
 *
 * That is the product's best credibility moment and it was invisible, which is
 * worse than not having it: a scorecard that cannot say why a metric is on it
 * asks to be taken on faith, and this one never needed to be.
 */

function Scorecard({ kpis, rationale, currency }: {
  kpis: Kpi[];
  rationale: Record<string, string>;
  currency: string;
}) {
  return (
    <div class="table-wrap">
      <table class="scorecard" id="res-scorecard">
        <thead>
          <tr>
            <th>KPI</th><th class="num">Value</th><th>Where it came from</th>
            <th>vs sector</th><th>Why it is here</th>
          </tr>
        </thead>
        <tbody>
          {kpis.map((kpi) => (
            <tr key={kpi.kpi_id} class={kpi.computed ? '' : 'not-computed'}>
              <td>
                <strong>{kpi.name}</strong>
                {kpi.owner && <><br /><span class="watch-for">{kpi.owner}</span></>}
              </td>
              <td class="num">
                {kpi.computed
                  ? fmtValue(kpi.current, kpi.unit, currency)
                  : <span class="watch-for">not computed</span>}
              </td>
              <td><BasisChip basis={kpi.basis} /></td>
              <td><BenchmarkChip kpi={kpi} /></td>
              <td class="reason-cell">
                {/* For an uncomputed KPI the engine's own reason is far more
                    use than its selection rationale: "needs the headcount
                    table, which this run does not have" is actionable. */}
                {kpi.computed ? rationale[kpi.kpi_id] ?? '' : kpi.reason ?? ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Provenance({ summary }: { summary: Summary }) {
  const [open, setOpen] = useState(false);
  const kpis = summary.kpis ?? [];
  const dropped = Object.entries(summary.dropped ?? {});
  const provenance = summary.provenance ?? {};
  const entries = Object.entries(provenance);
  const assumed = entries.filter(([, v]) => v.startsWith('benchmark_default'));
  const measured = entries.length - assumed.length;

  const ran = summary.stages_ran?.length ?? 0;
  const reused = summary.stages_reused?.length ?? 0;

  return (
    <section class="provenance" id="res-provenance">
      <button class="provenance-toggle" type="button" id="res-provenance-toggle"
              aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>How this was built</span>
        <span class="provenance-summary">
          {[
            summary.north_star?.name && `north star: ${summary.north_star.name}`,
            `${kpis.filter((k) => k.computed).length} of ${kpis.length} KPIs computed`,
            dropped.length > 0 && `${dropped.length} considered and dropped`,
            entries.length > 0
              && `${measured} of ${entries.length} profile fields from you`,
            ran > 0 && `${ran} stages rebuilt, ${reused} reused`
              + (summary.seconds ? ` in ${summary.seconds.toFixed(1)}s` : ''),
          ].filter(Boolean).join(' · ')}
        </span>
        <span class="provenance-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div class="provenance-body">
          <h3 class="section-title">The scorecard, and why each metric is on it</h3>
          <Scorecard kpis={kpis} rationale={summary.rationale ?? {}}
                     currency={summary.currency ?? 'USD'} />

          {dropped.length > 0 && (
            <>
              <h3 class="section-title">
                Considered and left off ({dropped.length})
              </h3>
              <p class="section-sub">
                Each of these was a candidate for this business and was dropped
                for a stated reason — not an oversight, and not silence.
              </p>
              <ul class="dropped-list" id="res-dropped">
                {dropped.map(([id, reason]) => (
                  <li key={id}><code>{id}</code> — {reason}</li>
                ))}
              </ul>
            </>
          )}

          {assumed.length > 0 && (
            <>
              <h3 class="section-title">
                Filled from sector benchmarks ({assumed.length})
              </h3>
              <p class="section-sub">
                The survey's promise, kept where you can see it: these are the
                fields nobody told us, so a prior was used. Every one is
                footnoted in the report appendix too.
              </p>
              <ul class="dropped-list" id="res-assumed">
                {assumed.map(([path, source]) => (
                  <li key={path}>
                    <code>{path}</code> — {source.replace('benchmark_default:', '')}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
