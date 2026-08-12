import { filesBase, type Summary } from '../lib/api';
import { fmtBytes, fmtValue, SEVERITY_LABEL, STATUS_GLYPH, STATUS_LABEL } from '../lib/format';
import { navigate } from '../lib/router';

function StatusChip({ status }: { status: string | null }) {
  const key = status ?? 'unknown';
  return (
    <span class={`chip status-${key}`}>
      {STATUS_GLYPH[key] ?? '○'} {STATUS_LABEL[key] ?? 'No data'}
    </span>
  );
}

export function Results({ summary }: { summary: Summary }) {
  const currency = summary.currency ?? 'USD';
  const dashboard = summary.artifacts.find((a) => a.name === 'dashboard.html');

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
          <a class="back" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>
            ← Home
          </a>
          <button class="ghost" id="res-adjust"
                  onClick={() => navigate(`/runs/${summary.run_id}/studio`)}>
            Adjust in Studio
          </button>
          {dashboard && (
            <a class="primary" id="res-open-dashboard" target="_blank" rel="noopener"
               href={filesBase() + dashboard.url}>
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
          </article>
        ))}
      </div>

      <ol class="findings" id="res-findings">
        {summary.findings.slice(0, 10).map((finding, i) => (
          <li class={`finding sev-${finding.severity}`} key={i}>
            <span class="sev">{SEVERITY_LABEL[finding.severity] ?? finding.severity}</span>
            <span>{finding.title ?? finding.statement ?? ''}</span>
          </li>
        ))}
      </ol>

      <div class="download-grid" id="res-downloads">
        {summary.artifacts.map((artifact) => (
          <a class="dl-card" key={artifact.name} download
             href={filesBase() + artifact.url}>
            <span class="dl-kind">{artifact.kind}</span>
            <span class="dl-label">{artifact.label}</span>
            <span class="dl-blurb">{artifact.blurb}</span>
            <span class="dl-size">{fmtBytes(artifact.size)}</span>
          </a>
        ))}
      </div>
    </section>
  );
}
