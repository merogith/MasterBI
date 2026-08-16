import { useState } from 'preact/hooks';
import { BasisChip } from '../components/Basis';
import { Provenance } from '../components/Provenance';
import { Empty } from '../components/State';
import { RESULTS_TOUR, Tour } from '../components/Tour';
import { fileUrl, type Summary } from '../lib/api';
import { fmtBytes, fmtValue, SEVERITY_LABEL, STATUS_GLYPH, STATUS_LABEL } from '../lib/format';
import { href, navigate } from '../lib/router';
import { useIsStatic } from '../lib/useStatic';

/** Findings shown before the fold. Ten is about where a reader stops, and the
 *  eleventh is not less true — so the rest are one click away rather than
 *  discarded. A real sample run produces nineteen; nine of them used to be
 *  dropped with nothing on screen saying so. */
const FINDINGS_SHOWN = 10;

function StatusChip({ status }: { status: string | null }) {
  const key = status ?? 'unknown';
  return (
    <span class={`chip status-${key}`}>
      {STATUS_GLYPH[key] ?? '○'} {STATUS_LABEL[key] ?? 'No data'}
    </span>
  );
}

export function Results({ summary }: { summary: Summary }) {
  const isStatic = useIsStatic();
  const [allFindings, setAllFindings] = useState(false);
  const currency = summary.currency ?? 'USD';
  const dashboard = summary.artifacts.find((a) => a.name === 'dashboard.html');

  const findings = summary.findings ?? [];
  const shown = allFindings ? findings : findings.slice(0, FINDINGS_SHOWN);
  const hidden = findings.length - shown.length;

  return (
    <section class="view" id="view-results">
      <div class="res-head">
        <div>
          <h1 id="res-company">{summary.company}</h1>
          <p class="res-meta" id="res-meta">
            {[summary.period, summary.objective, summary.audience]
              .filter(Boolean).join(' · ')}
          </p>
        </div>
        <div class="res-actions">
          <a class="back" href={href('/')} onClick={(e) => { e.preventDefault(); navigate('/'); }}>
            ← Home
          </a>
          {/* Reachable on the demo too: the Studio opens read-only there,
              showing what the run was built from. */}
          <button class="ghost" id="res-adjust"
                  onClick={() => navigate(`/runs/${summary.run_id}/studio`)}>
            {isStatic ? 'Inspect in Studio' : 'Adjust in Studio'}
          </button>
          {dashboard && (
            <a class="primary" id="res-open-dashboard" target="_blank" rel="noopener"
               href={fileUrl(dashboard.url)}>
              Open dashboard ↗
            </a>
          )}
        </div>
      </div>

      <div id="res-warnings">
        {summary.warnings?.map((warning, i) => (
          <div class="warn-banner" key={i}>{warning}</div>
        ))}
      </div>

      <div class="tiles" id="res-tiles">
        {summary.tiles.map((kpi) => (
          <article class="tile" key={kpi.kpi_id}>
            <div class="tile-head">
              <span class="tile-name">{kpi.name}</span>
              <StatusChip status={kpi.status} />
            </div>
            <div class="tile-value">{fmtValue(kpi.current, kpi.unit, currency)}</div>
            {/* Where the number came from, on the number. Computed by the
                metrics engine for every result since it was written, and
                rendered nowhere until now. */}
            <BasisChip basis={kpi.basis} />
          </article>
        ))}
      </div>

      {/* The tiles are the top-tier KPIs, not the whole scorecard — a
          deliberate edit rather than a truncation. Saying which, and how many
          there are in total, is the difference between an edit and a loss. */}
      {summary.kpis.length > summary.tiles.length && (
        <p class="section-sub" id="res-kpi-count">
          {summary.tiles.length} headline KPIs of {summary.kpis.length} computed.
          The full scorecard is below, in the dashboard and in the workbook.
        </p>
      )}

      <Provenance summary={summary} />

      {findings.length === 0 ? (
        <Empty title="No findings">
          The detectors found nothing worth flagging in this data — which is
          itself a result, not a failure.
        </Empty>
      ) : (
        <>
          <ol class="findings" id="res-findings">
            {shown.map((finding, i) => (
              <li class={`finding sev-${finding.severity}`} key={i}>
                <span class="sev">{SEVERITY_LABEL[finding.severity] ?? finding.severity}</span>
                <span>{finding.title ?? finding.statement ?? ''}</span>
              </li>
            ))}
          </ol>
          {(hidden > 0 || allFindings) && (
            <button class="linkish" id="res-more-findings" type="button"
                    onClick={() => setAllFindings(!allFindings)}>
              {allFindings
                ? `Show the top ${FINDINGS_SHOWN} only`
                : `Show all ${findings.length} findings (${hidden} more)`}
            </button>
          )}
        </>
      )}

      <div class="download-grid" id="res-downloads">
        {summary.artifacts.map((artifact) => (
          <a class="dl-card" key={artifact.name} download
             href={fileUrl(artifact.url)}>
            <span class="dl-kind">{artifact.kind}</span>
            <span class="dl-label">{artifact.label}</span>
            <span class="dl-blurb">{artifact.blurb}</span>
            <span class="dl-size">{fmtBytes(artifact.size)}</span>
          </a>
        ))}
      </div>

      {/* Last in the DOM so its targets exist by the time it looks for them,
          and so it never precedes the content it is describing for a screen
          reader working down the page. */}
      <Tour steps={RESULTS_TOUR} />
    </section>
  );
}
