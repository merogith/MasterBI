"""Self-contained interactive HTML dashboard.

Design decisions that follow from the dataviz rules:

  * Dark mode is a SECOND RENDER from the dark-validated palette steps, not a
    CSS filter over the light one. Both figure sets are emitted and toggled.
  * A scorecard table is always present. Three light-mode slots sit below 3:1
    against the surface, and the palette's relief rule makes a table view (or
    visible labels) mandatory rather than optional.
  * Status is never colour alone — every RAG chip carries a glyph and a word.
  * Plotly is inlined, so the file works offline and survives being emailed.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional

import pandas as pd

from ..fmt import fmt_value, is_missing
from ..insight.detectors import Finding
from ..kpi.schema import KPISet, Perspective, Tier
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
from ..viz.charts import ChartSpec, build_all
from ..viz.theme import FONT_STACK, STATUS_GLYPH, STATUS_LABEL, TOKENS

TAB_LABELS = {
    "overview": "Overview",
    "growth": "Growth & efficiency",
    "customer": "Customers & retention",
    "people": "People & leverage",
}

SEVERITY_STYLE = {
    "critical": ("critical", "Critical"),
    "high": ("critical", "High"),
    "medium": ("warning", "Medium"),
    "low": ("muted", "Low"),
    "positive": ("good", "Strength"),
}


def _plotly_js() -> str:
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


def _sparkline(series: Optional[pd.Series], color: str, width: int = 104,
               height: int = 28) -> str:
    """Inline SVG sparkline — no library, scales with the tile."""
    if series is None:
        return ""
    s = series.dropna()
    if len(s) < 3:
        return ""
    s = s.iloc[-24:]
    lo, hi = float(s.min()), float(s.max())
    span = (hi - lo) or 1.0
    step = width / (len(s) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - 2 - (float(v) - lo) / span * (height - 4):.1f}"
        for i, v in enumerate(s.values)
    )
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" aria-hidden="true" preserveAspectRatio="none">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def _basis_badge(result: "MetricResult") -> str:
    """Mark a number the generator produced rather than the user's data.

    Only rendered when it is not `measured`. Labelling every measured number
    would make the distinction invisible through repetition, which is the
    opposite of the point.
    """
    if getattr(result, "basis", "measured") == "measured":
        return ""
    label = {"modelled": "Modelled", "mixed": "Part modelled"}.get(
        result.basis, result.basis)
    tip = ("Computed from data the generator supplied because your upload did "
           "not contain it")
    return (f'<span class="basis-badge" title="{html.escape(tip)}">'
            f'{html.escape(label)}</span>')


def _status_chip(status: str) -> str:
    glyph = STATUS_GLYPH.get(status, "○")
    label = STATUS_LABEL.get(status, "No data")
    return (
        f'<span class="chip chip-{html.escape(status)}">'
        f'<span class="chip-glyph" aria-hidden="true">{glyph}</span>'
        f'{html.escape(label)}</span>'
    )


def _stat_tile(r: MetricResult, currency: str) -> str:
    k = r.kpi
    value = fmt_value(r.current, k.unit, currency)
    delta_html = ""
    if not is_missing(r.current) and not is_missing(r.prior_year) and r.prior_year:
        change = r.current - r.prior_year
        better_up = k.direction.value == "higher_is_better"
        good = (change >= 0) if better_up else (change <= 0)
        arrow = "▲" if change >= 0 else "▼"
        if k.unit == "pct":
            text = f"{abs(change):.1f} pts"
        else:
            pct = abs(change) / abs(r.prior_year) if r.prior_year else 0
            text = f"{pct:.0%}"
        cls = "delta-good" if good else "delta-bad"
        delta_html = (
            f'<span class="delta {cls}"><span aria-hidden="true">{arrow}</span> '
            f'{text} <span class="delta-period">vs LY</span></span>'
        )

    target = ""
    if not is_missing(r.target):
        target = f'<div class="tile-target">Target {fmt_value(r.target, k.unit, currency)}</div>'

    return f"""
      <article class="tile">
        <div class="tile-head">
          <h3>{html.escape(k.short_name or k.name)}</h3>
          {_status_chip(r.status)}
        </div>
        <div class="tile-value">{html.escape(value)}</div>
        <div class="tile-foot">{delta_html}{_sparkline(r.series, "var(--series-1)")}</div>
        {target}
      </article>"""


def _hero(north_star: Optional[MetricResult], profile: CompanyProfile,
          growth: Optional[MetricResult]) -> str:
    if north_star is None:
        return ""
    k = north_star.kpi
    value = fmt_value(north_star.current, k.unit, profile.identity.currency)
    sub = ""
    if growth is not None and not is_missing(growth.current):
        sub = f'growing {growth.current:.1%} year on year'
    return f"""
      <section class="hero">
        <div class="hero-label">North Star · {html.escape(k.name)}</div>
        <div class="hero-figure">{html.escape(value)}</div>
        <div class="hero-sub">{html.escape(sub)}</div>
      </section>"""


def _findings_section(findings: List[Finding],
                      narrative: Optional[List[str]] = None) -> str:
    if not findings:
        return ""
    # The dashboard has no section registry to hang prose on, so the narrative
    # lands in the one panel that is already doing the same job: "what the
    # numbers say" is exactly the connective read, and the findings underneath
    # are the evidence for it. Attributed inline rather than in an appendix,
    # because a dashboard has no appendix to send the reader to.
    lead = ""
    if narrative:
        paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in narrative)
        lead = f'<div class="narrative">{paragraphs}</div>'
    attribution = (
        "Narrative written by a language model from the computed KPI table; "
        "every figure in it was checked against that table. The findings below "
        "are deterministic."
        if narrative else
        "Generated by deterministic detectors — every figure below is "
        "computed, none inferred."
    )
    items = []
    for f in findings[:6]:
        cls, label = SEVERITY_STYLE.get(f.severity, ("muted", f.severity))
        rec = (
            f'<p class="finding-rec"><strong>So what:</strong> {html.escape(f.recommendation)}</p>'
            if f.recommendation else ""
        )
        items.append(f"""
        <li class="finding">
          <div class="finding-head">
            <span class="chip chip-{cls}"><span class="chip-glyph" aria-hidden="true">
            {STATUS_GLYPH['red'] if cls == 'critical' else STATUS_GLYPH['amber'] if cls == 'warning' else STATUS_GLYPH['green']}
            </span>{html.escape(label)}</span>
            <h3>{html.escape(f.title)}</h3>
          </div>
          <p>{html.escape(f.statement)}</p>
          {rec}
        </li>""")
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>What the numbers say</h2>
          <p class="panel-sub">{attribution}</p>
        </div>{lead}
        <ol class="findings">{''.join(items)}</ol>
      </section>"""


def _scorecard_table(results: List[MetricResult], kpi_set: KPISet,
                     currency: str) -> str:
    """The table view. Mandatory relief for the sub-3:1 palette slots."""
    rows = []
    for perspective in Perspective:
        group = [r for r in results if r.kpi.perspective == perspective]
        if not group:
            continue
        rows.append(
            f'<tr class="group-row"><th colspan="7">'
            f'{html.escape(perspective.value.replace("_", " ").title())}</th></tr>'
        )
        for r in sorted(group, key=lambda x: int(x.kpi.tier)):
            k = r.kpi
            bench = fmt_value(k.benchmark.p50, k.unit, currency) if k.benchmark else "—"
            pos = (r.benchmark_position or "—").replace("_", " ")
            rows.append(f"""
            <tr>
              <td class="kpi-name">{html.escape(k.name)}{_basis_badge(r)}
                <span class="kpi-timing">{html.escape(k.timing.value)}</span></td>
              <td class="num">{html.escape(fmt_value(r.current, k.unit, currency))}</td>
              <td class="num">{html.escape(fmt_value(r.prior_year, k.unit, currency))}</td>
              <td class="num">{html.escape(fmt_value(r.target, k.unit, currency))}</td>
              <td class="num">{html.escape(bench)}</td>
              <td>{_status_chip(r.status)}</td>
              <td class="muted-cell">{html.escape(pos)}</td>
            </tr>""")

    return f"""
      <section class="panel" id="scorecard">
        <div class="panel-head">
          <h2>Full scorecard</h2>
          <p class="panel-sub">
            Every selected KPI, grouped by Balanced Scorecard perspective.
            Leading indicators are {kpi_set.leading_share:.0%} of the set.
          </p>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>KPI</th><th class="num">Current</th><th class="num">12mo ago</th>
              <th class="num">Target</th><th class="num">Cohort median</th>
              <th>Status</th><th>vs cohort</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
      </section>"""


def _appendix(results: List[MetricResult], kpi_set: KPISet,
              profile: CompanyProfile, checks: List[str],
              anomaly_notes: List[str]) -> str:
    defs = []
    for r in sorted(results, key=lambda x: (x.kpi.perspective.value, int(x.kpi.tier))):
        k = r.kpi
        bench_src = k.benchmark.source if k.benchmark else "no benchmark applied"
        pit = f'<p class="pitfall"><strong>Pitfall:</strong> {html.escape(k.pitfalls)}</p>' if k.pitfalls else ""
        defs.append(f"""
        <details class="kpi-def">
          <summary><strong>{html.escape(k.name)}</strong>
            <span class="muted-cell">· owner {html.escape(k.owner_role)}</span></summary>
          <p class="formula"><code>{html.escape(k.formula)}</code></p>
          {f'<p>{html.escape(k.interpretation)}</p>' if k.interpretation else ''}
          {pit}
          <p class="muted-cell">Sources: {html.escape(', '.join(k.source_systems) or 'n/a')}
             · Frequency: {html.escape(k.frequency)}
             · Benchmark: {html.escape(bench_src)}</p>
        </details>""")

    defaulted = [
        f"<li><code>{html.escape(path)}</code> — {html.escape(src)}</li>"
        for path, src in profile.provenance.items()
        if src.startswith("benchmark_default")
    ]
    not_selected = [
        f"<li><code>{html.escape(kid)}</code> — {html.escape(reason)}</li>"
        for kid, reason in sorted(kpi_set.dropped.items())
    ]
    not_computed = [
        f"<li><code>{html.escape(r.kpi.id)}</code> — {html.escape(r.reason)}</li>"
        for r in results if not r.computed
    ]

    return f"""
      <section class="panel" id="appendix">
        <div class="panel-head">
          <h2>Appendix</h2>
          <p class="panel-sub">Definitions, assumptions and data quality. Read this before acting on anything above.</p>
        </div>

        <h3>Methodology</h3>
        <p class="body-text">
          KPIs were selected from the library by evaluating each metric's applicability
          expression against this company profile, then scored on objective alignment,
          Balanced Scorecard coverage, leading/lagging balance and audience fit. Coverage
          is constrained to between 2 and 7 KPIs per perspective, so no single perspective
          can dominate the scorecard.
        </p>

        <h3>Assumptions and provenance</h3>
        {'<ul class="plain">' + ''.join(defaulted) + '</ul>' if defaulted else
         '<p class="body-text">No fields were filled from benchmark defaults.</p>'}
        <p class="body-text warn-text">
          <strong>Benchmark caveat.</strong> The peer figures in this dashboard are
          illustrative placeholders assembled from public commentary, not a licensed
          benchmark dataset. They are suitable for calibrating a sample, not for
          external reporting.
        </p>

        <h3>Data quality checks</h3>
        <p class="body-text">{len(checks)} reconciliation assertions passed before this
           dashboard rendered. Nothing renders on data that fails them.</p>
        <ul class="plain small">{''.join(f'<li>{html.escape(c)}</li>' for c in checks)}</ul>

        {'<h3>Known events in this dataset</h3><ul class="plain">' +
         ''.join(f'<li>{html.escape(n)}</li>' for n in anomaly_notes) + '</ul>'
         if anomaly_notes else ''}

        <h3>KPI definitions</h3>
        <div class="defs">{''.join(defs)}</div>

        {'<h3>KPIs considered but not selected</h3><ul class="plain small">' +
         ''.join(not_selected) + '</ul>' if not_selected else ''}
        {'<h3>Selected but not computable</h3><ul class="plain small">' +
         ''.join(not_computed) + '</ul>' if not_computed else ''}
      </section>"""


def _chart_html(specs: List[ChartSpec], mode: str) -> str:
    """Emit one tab-panel per tab. Plotly's JS is inlined once in <head>, so
    every figure here embeds without it."""
    blocks: Dict[str, List[str]] = {}
    for spec in specs:
        inner = spec.figure.to_html(
            full_html=False,
            include_plotlyjs=False,
            div_id=f"fig-{mode}-{spec.id}",
            config={"displayModeBar": False, "responsive": True},
        )
        note = f'<p class="chart-note">{html.escape(spec.note)}</p>' if spec.note else ""
        blocks.setdefault(spec.tab, []).append(f"""
          <figure class="card card-{spec.width}">
            <figcaption>
              <h3>{html.escape(spec.title)}</h3>
              <p>{html.escape(spec.subtitle)}</p>
            </figcaption>
            <div class="plot">{inner}</div>
            {note}
          </figure>""")
    return "".join(
        f'<div class="tab-panel" data-tab="{tab}">{"".join(items)}</div>'
        for tab, items in blocks.items()
    )


def _css_vars(tokens: Dict[str, str]) -> str:
    return "\n".join(f"    --{k.replace('_', '-')}: {v};" for k, v in tokens.items())


def render_dashboard(profile: CompanyProfile, kpi_set: KPISet,
                     results: List[MetricResult], findings: List[Finding],
                     tables: Dict[str, pd.DataFrame], checks: List[str],
                     anomaly_notes: List[str],
                     specs_light: Optional[List[ChartSpec]] = None,
                     specs_dark: Optional[List[ChartSpec]] = None,
                     tokens_light: Optional[Dict[str, str]] = None,
                     tokens_dark: Optional[Dict[str, str]] = None,
                     logo=None,
                     narrative: Optional[Dict[str, List[str]]] = None) -> str:
    computed = [r for r in results if r.computed]
    north_star = next((r for r in computed if r.kpi.id == kpi_set.north_star), None)
    growth = next((r for r in computed if r.kpi.id == "arr_growth_yoy"), None)

    l1 = [r for r in computed if r.kpi.tier in (Tier.north_star, Tier.exec_l1)]
    l1 = [r for r in l1 if r is not north_star][:6]

    currency = profile.identity.currency
    # Accept pre-built specs so the pipeline can build each theme once and share
    # it with the print deliverables. Building them here was costing three
    # `build_all` passes per run for two distinct results.
    if specs_light is None:
        specs_light = build_all(results, tables, mode="light", currency=currency)
    if specs_dark is None:
        specs_dark = build_all(results, tables, mode="dark", currency=currency)

    tabs_present = []
    for spec in specs_light:
        if spec.tab not in tabs_present:
            tabs_present.append(spec.tab)

    tab_buttons = "".join(
        f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{t}">'
        f'{html.escape(TAB_LABELS.get(t, t.title()))}</button>'
        for i, t in enumerate(tabs_present)
    )

    period = ""
    fin = tables.get("monthly_financials")
    if fin is not None and not fin.empty:
        period = f"{fin['month'].iloc[0]} to {fin['month'].iloc[-1]}"

    # Both palettes, because the dashboard ships its own theme toggle — a
    # brand has to be derived twice or switching to dark loses it.
    # Inlined as a data URI: the dashboard is one self-contained file that has
    # to survive being emailed, so a <img src="logo.png"> would arrive broken.
    # Both the markup and the rule are conditional, so an unbranded dashboard
    # is byte-for-byte what it was before logos existed.
    logo_html = "" if logo is None else (
        f'\n      <img class="brand-logo" src="{logo.data_uri()}" alt="">')
    logo_css = "" if logo is None else (
        "\n.brand-logo { height: 30px; width: auto; max-width: 220px; "
        "display: block; margin-bottom: 8px; }")
    # Conditional for the same reason the logo rule is: a dashboard that was
    # never narrated must be byte-for-byte what it was before the AI layer
    # existed, and four lines of dead CSS would break that claim on every run.
    narrative_css = "" if not (narrative or {}).get("exec_summary") else (
        "\n.narrative { margin: 0 0 18px; padding: 0 0 0 14px; "
        "border-left: 2px solid var(--border); color: var(--text-secondary); "
        "font-size: 14px; line-height: 1.65; }"
        "\n.narrative p { margin: 0 0 10px; }"
        "\n.narrative p:last-child { margin-bottom: 0; }")
    light_css = _css_vars(tokens_light or TOKENS["light"])
    dark_css = _css_vars(tokens_dark or TOKENS["dark"])

    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(profile.identity.name)} — Performance Dashboard</title>
<script>{_plotly_js()}</script>
<style>
:root {{
{light_css}
  --font: {FONT_STACK};
}}
:root[data-theme="dark"] {{
{dark_css}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--page); color: var(--text-primary);
  font-family: var(--font); font-size: 14px; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1280px; margin: 0 auto; padding: 24px 20px 72px; }}
header.top {{
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; margin-bottom: 24px;
}}
header.top h1 {{ margin: 0; font-size: 22px; letter-spacing: -0.01em; }}
.meta {{ color: var(--text-secondary); font-size: 13px; margin-top: 2px; }}
.theme-toggle {{
  border: 1px solid var(--border); background: var(--surface);
  color: var(--text-secondary); border-radius: 999px; padding: 7px 14px;
  font: inherit; font-size: 13px; cursor: pointer;
}}
.theme-toggle:hover {{ color: var(--text-primary); }}

.hero {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 22px 24px; margin-bottom: 14px;
}}
.hero-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--muted); }}
.hero-figure {{ font-size: 52px; line-height: 1.05; font-weight: 600;
  letter-spacing: -0.02em; margin: 6px 0 2px; }}
.hero-sub {{ color: var(--text-secondary); }}

.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px; margin-bottom: 24px; }}
.tile {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px; }}
.tile-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.tile-head h3 {{ margin: 0; font-size: 12px; font-weight: 500; color: var(--text-secondary); }}
.tile-value {{ font-size: 26px; font-weight: 600; letter-spacing: -0.01em; margin: 8px 0 4px; }}
.tile-foot {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.tile-target {{ font-size: 11px; color: var(--muted); margin-top: 6px; }}
.spark {{ opacity: 0.85; }}
.delta {{ font-size: 12px; font-variant-numeric: tabular-nums; }}
.delta-good {{ color: var(--delta-up); }}
.delta-bad {{ color: var(--critical); }}
.delta-period {{ color: var(--muted); }}

.chip {{ display: inline-flex; align-items: center; gap: 4px; font-size: 11px;
  padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border);
  color: var(--text-secondary); white-space: nowrap; }}
.chip-glyph {{ font-size: 9px; line-height: 1; }}
.chip-green, .chip-good {{ color: var(--good); }}
.chip-amber, .chip-warning {{ color: var(--serious); }}
.chip-red, .chip-critical {{ color: var(--critical); }}
.chip-unknown, .chip-muted {{ color: var(--muted); }}

.panel {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 22px 24px; margin-bottom: 20px; }}
.panel-head h2 {{ margin: 0 0 2px; font-size: 17px; }}
.panel-sub {{ margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; }}

.findings {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }}
.finding {{ border-left: 2px solid var(--border); padding-left: 14px; }}
.finding-head {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.finding-head h3 {{ margin: 0; font-size: 15px; }}
.finding p {{ margin: 6px 0 0; color: var(--text-secondary); }}
.finding-rec {{ color: var(--text-primary) !important; }}

.tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin: 4px 0 16px; }}
.tab-btn {{ border: 1px solid transparent; background: transparent; font: inherit;
  font-size: 13px; color: var(--text-secondary); padding: 7px 14px;
  border-radius: 999px; cursor: pointer; }}
.tab-btn:hover {{ color: var(--text-primary); }}
.tab-btn.active {{ background: var(--surface); border-color: var(--border);
  color: var(--text-primary); }}
.tab-panel {{ display: none; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
.tab-panel.active {{ display: grid; }}
.card {{ background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 18px 20px; margin: 0; min-width: 0; }}
.card-full {{ grid-column: 1 / -1; }}
.card figcaption h3 {{ margin: 0 0 2px; font-size: 15px; }}
.card figcaption p {{ margin: 0 0 12px; color: var(--text-secondary); font-size: 12.5px; }}
.chart-note {{ margin: 10px 0 0; font-size: 12px; color: var(--muted);
  border-top: 1px solid var(--border); padding-top: 9px; }}
.plot {{ min-width: 0; overflow-x: auto; }}

.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
th {{ font-weight: 500; color: var(--muted); font-size: 11.5px;
  text-transform: uppercase; letter-spacing: 0.05em; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.group-row th {{ color: var(--text-primary); font-size: 12px; padding-top: 16px;
  text-transform: none; letter-spacing: 0; }}
.kpi-name {{ font-weight: 500; }}
.basis-badge {{
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 5px;
  font-size: 10px; font-weight: 650; letter-spacing: .03em; text-transform: uppercase;
  background: var(--warning); color: #1a1a19; vertical-align: middle;
}}
.kpi-timing {{ color: var(--muted); font-weight: 400; font-size: 11px; margin-left: 6px; }}
.muted-cell {{ color: var(--muted); }}

.body-text {{ color: var(--text-secondary); max-width: 78ch; }}
.warn-text {{ border-left: 2px solid var(--serious); padding-left: 12px; }}
.plain {{ color: var(--text-secondary); padding-left: 18px; max-width: 90ch; }}
.plain.small {{ font-size: 12.5px; }}
h3 {{ font-size: 14px; margin: 22px 0 8px; }}
.kpi-def {{ border-bottom: 1px solid var(--border); padding: 9px 0; }}
.kpi-def summary {{ cursor: pointer; font-size: 13px; }}
.kpi-def p {{ margin: 7px 0; color: var(--text-secondary); font-size: 12.5px; max-width: 80ch; }}
.formula code {{ background: var(--page); padding: 2px 6px; border-radius: 5px;
  font-size: 12px; }}
.pitfall {{ color: var(--serious) !important; }}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}

.theme-set {{ display: none; }}
.theme-set.active {{ display: block; }}
@media (max-width: 900px) {{ .tab-panel.active {{ grid-template-columns: 1fr; }} }}{logo_css}{narrative_css}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>{logo_html}
      <h1>{html.escape(profile.identity.name)}</h1>
      <div class="meta">
        Performance dashboard · {html.escape(period)} ·
        {html.escape(profile.business_model.type.value.upper())} ·
        {html.escape(profile.identity.country)} ·
        prepared for the {html.escape(profile.intent.audience.value)} ·
        profile confidence {profile.confidence:.0%}
      </div>
    </div>
    <button class="theme-toggle" id="themeToggle" aria-label="Toggle colour theme">
      Dark mode
    </button>
  </header>

  {_hero(north_star, profile, growth)}
  <div class="tiles">{''.join(_stat_tile(r, profile.identity.currency) for r in l1)}</div>

  {_findings_section(findings, (narrative or {}).get("exec_summary"))}

  <div class="tabs" role="tablist">{tab_buttons}</div>
  <div class="theme-set active" data-mode="light">{_chart_html(specs_light, "light")}</div>
  <div class="theme-set" data-mode="dark">{_chart_html(specs_dark, "dark")}</div>

  {_scorecard_table(results, kpi_set, profile.identity.currency)}
  {_appendix(results, kpi_set, profile, checks, anomaly_notes)}
</div>

<script>
(function () {{
  var root = document.documentElement;
  var toggle = document.getElementById('themeToggle');
  var activeTab = document.querySelector('.tab-btn') ?
      document.querySelector('.tab-btn').dataset.tab : null;

  function showTab(tab) {{
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(function (b) {{
      b.classList.toggle('active', b.dataset.tab === tab);
    }});
    document.querySelectorAll('.theme-set').forEach(function (set) {{
      var on = set.classList.contains('active');
      set.querySelectorAll('.tab-panel').forEach(function (p) {{
        var show = p.dataset.tab === tab;
        p.classList.toggle('active', show);
        if (show && on) {{
          p.querySelectorAll('.js-plotly-plot').forEach(function (g) {{
            window.Plotly && Plotly.Plots.resize(g);
          }});
        }}
      }});
    }});
  }}

  document.querySelectorAll('.tab-btn').forEach(function (b) {{
    b.addEventListener('click', function () {{ showTab(b.dataset.tab); }});
  }});

  function setTheme(mode) {{
    root.setAttribute('data-theme', mode);
    toggle.textContent = mode === 'dark' ? 'Light mode' : 'Dark mode';
    document.querySelectorAll('.theme-set').forEach(function (set) {{
      set.classList.toggle('active', set.dataset.mode === mode);
    }});
    showTab(activeTab);
    try {{ localStorage.setItem('kpiTheme', mode); }} catch (e) {{}}
  }}

  toggle.addEventListener('click', function () {{
    setTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  }});

  var saved = null;
  try {{ saved = localStorage.getItem('kpiTheme'); }} catch (e) {{}}
  setTheme(saved || (window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
}})();
</script>
</body>
</html>"""
