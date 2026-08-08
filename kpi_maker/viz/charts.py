"""Chart builders.

Form is chosen by the data's job, per the dataviz form heuristic:

  ARR over time            -> line (single series, emphasis)  not multi-colour
  ARR movement composition -> waterfall                       not stacked bar
  NRR vs GRR               -> 2-line categorical + reference line
  Churn by segment         -> emphasis bar (one accent, rest gray)
  Growth vs headcount      -> INDEXED lines to a common base of 100
                              (the correct fix for two different scales;
                               a dual axis is never acceptable)
  Cohort retention         -> heatmap, sequential single hue
  Channel cost before/after-> dumbbell, one hue two shades
  Position vs benchmark    -> diverging bar around a zero midpoint

Every figure carries the token role of each trace colour so the dashboard's
theme toggle can restyle without re-rendering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..fmt import fmt_value
from ..metrics.engine import MetricResult
from .theme import FONT_STACK, TOKENS

_CURRENCY = "USD"

# The ACTIVE palette. Mutated in place by `set_mode`, so every builder below
# reads the current mode's tokens at call time. Dark mode is therefore a real
# second render from the dark-validated steps — not an automatic flip of the
# light ones, which the palette rules forbid.
LIGHT = dict(TOKENS["light"])


def set_mode(mode: str) -> None:
    if mode not in TOKENS:
        raise ValueError(f"unknown theme mode {mode!r}")
    LIGHT.clear()
    LIGHT.update(TOKENS[mode])


@dataclass
class ChartSpec:
    id: str
    title: str
    subtitle: str
    figure: go.Figure
    tab: str = "overview"
    width: str = "half"          # half | full
    note: str = ""
    # trace index -> token role, so the theme toggle knows what to recolour
    trace_tokens: Dict[int, str] = field(default_factory=dict)
    colorscale_tokens: List[str] = field(default_factory=list)


def _base_layout(fig: go.Figure, height: int = 320) -> go.Figure:
    """Recessive chrome; the data carries the ink."""
    fig.update_layout(
        template="none",
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_STACK, size=12, color=LIGHT["text_secondary"]),
        hoverlabel=dict(font=dict(family=FONT_STACK, size=12), bordercolor=LIGHT["axis"]),
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, linecolor=LIGHT["axis"],
                   tickcolor=LIGHT["axis"], tickfont=dict(color=LIGHT["muted"], size=11)),
        yaxis=dict(gridcolor=LIGHT["grid"], zeroline=False, showline=False,
                   tickfont=dict(color=LIGHT["muted"], size=11)),
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _months(index) -> List[str]:
    return [str(p) for p in index]


def set_currency(currency: str) -> None:
    global _CURRENCY
    _CURRENCY = currency


def _money(v: float) -> str:
    """Delegates to the shared formatter so a figure reads identically on the
    chart, in the report prose and in the workbook."""
    return fmt_value(v, "currency", _CURRENCY)


# --------------------------------------------------------------------------

def arr_trend(results: List[MetricResult]) -> Optional[ChartSpec]:
    r = next((x for x in results if x.kpi.id == "arr" and x.computed), None)
    if r is None:
        return None
    s = r.series.dropna()
    x = _months(s.index)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=s.values, mode="lines", name="ARR",
        line=dict(color=LIGHT["series_1"], width=2),
        fill="tozeroy", fillcolor=_rgba(LIGHT["series_1"], 0.10),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra></extra>",
        customdata=[_money(v) for v in s.values],
    ))
    # Direct label on the final point rather than a legend box (single series).
    fig.add_trace(go.Scatter(
        x=[x[-1]], y=[s.values[-1]], mode="markers+text",
        marker=dict(color=LIGHT["series_1"], size=9,
                    line=dict(color=LIGHT["surface"], width=2)),
        text=[_money(s.values[-1])], textposition="top left",
        textfont=dict(color=LIGHT["text_primary"], size=13),
        hoverinfo="skip",
    ))
    _base_layout(fig)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(tickprefix="$", tickformat="~s")
    return ChartSpec(
        id="arr_trend", title="Annual Recurring Revenue",
        subtitle="Monthly, trailing 36 months", figure=fig,
        tab="overview", width="full",
        trace_tokens={0: "series_1", 1: "series_1"},
    )


def arr_bridge(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """The single most useful diagnostic in a subscription business."""
    mov = tables["mrr_movements"]
    months = sorted(mov["month"].unique())
    if len(months) < 13:
        return None
    window = months[-12:]
    opening = mov[mov["month"] < window[0]]["delta_mrr"].sum() * 12
    recent = mov[mov["month"].isin(window)]
    parts = recent.groupby("movement_type")["delta_mrr"].sum() * 12

    labels = ["Opening ARR", "New", "Expansion", "Contraction", "Churn", "Closing ARR"]
    values = [
        opening,
        float(parts.get("new", 0.0)),
        float(parts.get("expansion", 0.0)),
        -abs(float(parts.get("contraction", 0.0))),
        -abs(float(parts.get("churn", 0.0))),
        0,
    ]
    measures = ["absolute", "relative", "relative", "relative", "relative", "total"]

    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        text=[_money(abs(v)) if m != "total" else "" for v, m in zip(values, measures)],
        textposition="outside",
        textfont=dict(color=LIGHT["text_primary"], size=11),
        connector=dict(line=dict(color=LIGHT["axis"], width=1)),
        increasing=dict(marker=dict(color=LIGHT["diverge_pos"])),
        decreasing=dict(marker=dict(color=LIGHT["diverge_neg"])),
        totals=dict(marker=dict(color=LIGHT["deemphasis"])),
        hovertemplate="%{x}<br>%{text}<extra></extra>",
    ))
    _base_layout(fig, height=340)
    fig.update_yaxes(tickprefix="$", tickformat="~s")
    return ChartSpec(
        id="arr_bridge", title="ARR bridge, last 12 months",
        subtitle="Where the year's ARR movement came from", figure=fig,
        tab="overview", width="full",
        note="Blue adds, red subtracts. The gap between gross additions and closing ARR is leakage.",
    )


def retention_lines(results: List[MetricResult]) -> Optional[ChartSpec]:
    nrr = next((x for x in results if x.kpi.id == "nrr" and x.computed), None)
    grr = next((x for x in results if x.kpi.id == "grr" and x.computed), None)
    if nrr is None and grr is None:
        return None

    fig = go.Figure()
    tokens: Dict[int, str] = {}
    idx = 0
    for r, tok in ((nrr, "series_1"), (grr, "series_2")):
        if r is None:
            continue
        s = r.series.dropna()
        fig.add_trace(go.Scatter(
            x=_months(s.index), y=s.values * 100, mode="lines",
            name=r.kpi.short_name or r.kpi.name,
            line=dict(color=LIGHT[tok], width=2),
            hovertemplate="%{y:.1f}%<extra>" + (r.kpi.short_name or r.kpi.name) + "</extra>",
        ))
        tokens[idx] = tok
        idx += 1

    # 100% is the meaningful baseline for retention, not zero.
    fig.add_hline(y=100, line=dict(color=LIGHT["axis"], width=1, dash="dot"),
                  annotation_text="100% — book neither grows nor shrinks",
                  annotation_position="bottom right",
                  annotation_font=dict(color=LIGHT["muted"], size=10))
    _base_layout(fig)
    fig.update_layout(hovermode="x unified", showlegend=True,
                      legend=dict(orientation="h", y=1.12, x=0,
                                  font=dict(color=LIGHT["text_secondary"], size=11)))
    fig.update_yaxes(ticksuffix="%")
    return ChartSpec(
        id="retention", title="Net and gross revenue retention",
        subtitle="Trailing 12-month cohort basis", figure=fig,
        tab="customer", width="half", trace_tokens=tokens,
        note="The gap between the two lines is expansion; it is the only thing holding NRR above GRR.",
    )


def segment_churn(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Emphasis form: the worst segment is the story, the rest are context."""
    mov, cust = tables["mrr_movements"], tables["customers"]
    churn = mov[mov["movement_type"] == "churn"]
    if churn.empty:
        return None
    lost = (churn.groupby("segment")["delta_mrr"].sum().abs() * 12)
    base = cust.groupby("segment")["final_acv"].sum() + lost
    rate = (lost / base).dropna().sort_values()
    if rate.empty:
        return None

    worst = rate.idxmax()
    colors = [LIGHT["series_2"] if seg == worst else LIGHT["deemphasis"] for seg in rate.index]

    fig = go.Figure(go.Bar(
        x=rate.values * 100, y=list(rate.index), orientation="h",
        marker=dict(color=colors, cornerradius=4),
        text=[f"{v:.1%}" for v in rate.values], textposition="outside",
        textfont=dict(color=LIGHT["text_primary"], size=12),
        hovertemplate="%{y}: %{x:.1f}% of ARR lost<extra></extra>",
    ))
    _base_layout(fig, height=260)
    fig.update_xaxes(ticksuffix="%", showgrid=True, gridcolor=LIGHT["grid"])
    fig.update_yaxes(showgrid=False)
    return ChartSpec(
        id="segment_churn", title="ARR lost to churn, by segment",
        subtitle=f"{worst} highlighted — the aggregate number hides it", figure=fig,
        tab="customer", width="half",
        note="Blended churn is close to meaningless when segments differ this much.",
    )


def indexed_growth(results: List[MetricResult],
                   tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """ARR vs headcount, both indexed to 100.

    Two measures on wildly different scales. The answer is indexing to a common
    base — NEVER a second y-axis.
    """
    arr = next((x for x in results if x.kpi.id == "arr" and x.computed), None)
    if arr is None:
        return None
    hc = tables["headcount"].groupby("month")["fte"].sum()
    a = arr.series.dropna()
    hc = hc.reindex(a.index).ffill()
    if hc.isna().all():
        return None

    fig = go.Figure()
    for name, s, tok in (("ARR", a, "series_1"), ("Headcount", hc, "series_3")):
        idx = s / s.iloc[0] * 100
        fig.add_trace(go.Scatter(
            x=_months(idx.index), y=idx.values, mode="lines", name=name,
            line=dict(color=LIGHT[tok], width=2),
            hovertemplate="%{y:.0f}<extra>" + name + "</extra>",
        ))
        # Direct labels: mandatory here because slot 3 (aqua) carries the
        # sub-3:1 contrast WARN from the palette validation.
        fig.add_annotation(
            x=_months(idx.index)[-1], y=idx.values[-1], text=f"  {name} {idx.values[-1]:.0f}",
            showarrow=False, xanchor="left",
            font=dict(color=LIGHT["text_primary"], size=11),
        )
    _base_layout(fig)
    fig.update_layout(hovermode="x unified", margin=dict(l=8, r=90, t=8, b=8))
    return ChartSpec(
        id="indexed_growth", title="Operating leverage: ARR vs headcount",
        subtitle="Both indexed to 100 at the start of the period", figure=fig,
        tab="people", width="half", trace_tokens={0: "series_1", 1: "series_3"},
        note="The gap between the lines IS operating leverage. Indexed to a common base — never a dual axis.",
    )


def cac_payback(results: List[MetricResult]) -> Optional[ChartSpec]:
    r = next((x for x in results if x.kpi.id == "cac_payback_months" and x.computed), None)
    if r is None:
        return None
    s = r.series.dropna()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=_months(s.index), y=s.values, mode="lines",
        line=dict(color=LIGHT["series_1"], width=2),
        hovertemplate="%{x}<br>%{y:.1f} months<extra></extra>",
    ))
    if r.kpi.alert_bands:
        fig.add_hline(y=r.kpi.alert_bands.green, line=dict(color=LIGHT["good"], width=1, dash="dot"),
                      annotation_text=f"green < {r.kpi.alert_bands.green:.0f}mo",
                      annotation_position="top left",
                      annotation_font=dict(color=LIGHT["muted"], size=10))
        fig.add_hline(y=r.kpi.alert_bands.red, line=dict(color=LIGHT["critical"], width=1, dash="dot"),
                      annotation_text=f"red > {r.kpi.alert_bands.red:.0f}mo",
                      annotation_position="bottom left",
                      annotation_font=dict(color=LIGHT["muted"], size=10))
    _base_layout(fig)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(ticksuffix=" mo")
    return ChartSpec(
        id="cac_payback", title="CAC payback period",
        subtitle="Gross-margin adjusted, 3-month smoothed", figure=fig,
        tab="growth", width="half", trace_tokens={0: "series_1"},
    )


def cohort_heatmap(tables: Dict[str, pd.DataFrame], quarters: int = 8,
                   horizon: int = 12) -> Optional[ChartSpec]:
    """Revenue retention by acquisition cohort. Sequential single hue."""
    mov, cust = tables["mrr_movements"], tables["customers"]
    matrix = mov.pivot_table(index="month", columns="customer_id", values="delta_mrr",
                             aggfunc="sum", fill_value=0.0).cumsum().clip(lower=0.0)
    if matrix.empty:
        return None

    cust = cust.copy()
    cust["cohort_q"] = cust["acquired_month"].apply(lambda p: p.asfreq("Q"))
    cohorts = sorted(cust["cohort_q"].unique())[-quarters:]

    z, labels = [], []
    for q in cohorts:
        ids = [c for c in cust.loc[cust["cohort_q"] == q, "customer_id"] if c in matrix.columns]
        if not ids:
            continue
        start = q.asfreq("M", how="start")
        if start not in matrix.index:
            start = matrix.index[matrix.index >= start][0] if (matrix.index >= start).any() else None
        if start is None:
            continue
        base = matrix.loc[start, ids].sum()
        if base <= 0:
            continue
        row = []
        for k in range(horizon):
            month = start + k
            row.append(float(matrix.loc[month, ids].sum() / base * 100) if month in matrix.index else None)
        z.append(row)
        labels.append(str(q))

    if not z:
        return None

    seq = [LIGHT["seq_1"], LIGHT["seq_2"], LIGHT["seq_3"], LIGHT["seq_4"], LIGHT["seq_5"]]
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"M{k}" for k in range(horizon)], y=labels,
        colorscale=[[i / (len(seq) - 1), c] for i, c in enumerate(seq)],
        zmin=0, zmax=140, xgap=2, ygap=2,          # 2px surface gap between cells
        hovertemplate="Cohort %{y}, month %{x}<br>%{z:.0f}% of initial ARR<extra></extra>",
        colorbar=dict(title=dict(text="% of<br>initial ARR", font=dict(size=10, color=LIGHT["muted"])),
                      tickfont=dict(size=10, color=LIGHT["muted"]),
                      outlinewidth=0, thickness=10, len=0.85),
    ))
    _base_layout(fig, height=300)
    fig.update_yaxes(autorange="reversed")
    return ChartSpec(
        id="cohort_heatmap", title="Revenue retention by acquisition cohort",
        subtitle="% of the cohort's initial ARR still held, by month since signup",
        figure=fig, tab="customer", width="full",
        colorscale_tokens=["seq_1", "seq_2", "seq_3", "seq_4", "seq_5"],
        note="Above 100% means the cohort's expansion outran its churn.",
    )


def channel_dumbbell(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Before -> after per channel. One hue, two shades."""
    mkt = tables["marketing"]
    months = sorted(mkt["month"].unique())
    if len(months) < 18:
        return None
    recent = mkt[mkt["month"].isin(months[-6:])]
    prior = mkt[mkt["month"].isin(months[-18:-12])]

    def cps(df):
        g = df.groupby("channel").agg(spend=("spend", "sum"), sqls=("sqls", "sum"))
        return (g["spend"] / g["sqls"].replace(0, np.nan)).dropna()

    now, before = cps(recent), cps(prior)
    shared = sorted(now.index.intersection(before.index), key=lambda c: now[c])
    if not shared:
        return None

    fig = go.Figure()
    for ch in shared:
        fig.add_trace(go.Scatter(
            x=[before[ch], now[ch]], y=[ch, ch], mode="lines",
            line=dict(color=LIGHT["axis"], width=2), hoverinfo="skip", showlegend=False,
        ))
    fig.add_trace(go.Scatter(
        x=[before[c] for c in shared], y=shared, mode="markers", name="12 months ago",
        marker=dict(color=LIGHT["seq_1"], size=11, line=dict(color=LIGHT["surface"], width=2)),
        hovertemplate="%{y}<br>12 months ago: $%{x:,.0f}/SQL<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=[now[c] for c in shared], y=shared, mode="markers", name="Now",
        marker=dict(color=LIGHT["seq_5"], size=11, line=dict(color=LIGHT["surface"], width=2)),
        hovertemplate="%{y}<br>Now: $%{x:,.0f}/SQL<extra></extra>",
    ))
    _base_layout(fig, height=280)
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=1.15, x=0,
                                  font=dict(color=LIGHT["text_secondary"], size=11)))
    fig.update_xaxes(tickprefix="$", showgrid=True, gridcolor=LIGHT["grid"])
    n = len(shared)
    return ChartSpec(
        id="channel_cost", title="Cost per qualified lead, by channel",
        subtitle="Last 6 months vs the same period a year earlier", figure=fig,
        tab="growth", width="half",
        trace_tokens={n: "seq_1", n + 1: "seq_5"},
        note="A long rightward bar is a channel getting more expensive, not more productive.",
    )


def benchmark_position(results: List[MetricResult]) -> Optional[ChartSpec]:
    """Diverging bar: distance from the cohort median, signed by good/bad."""
    rows = []
    for r in results:
        if not r.computed or r.kpi.benchmark is None or r.kpi.benchmark.p50 is None:
            continue
        if r.current is None or r.kpi.benchmark.p50 == 0:
            continue
        gap = (r.current - r.kpi.benchmark.p50) / abs(r.kpi.benchmark.p50)
        if r.kpi.direction.value == "lower_is_better":
            gap = -gap                      # so positive always means "better"
        rows.append((r.kpi.short_name or r.kpi.name, float(np.clip(gap, -1.5, 1.5))))
    if not rows:
        return None

    rows.sort(key=lambda t: t[1])
    names = [n for n, _ in rows]
    gaps = [g for _, g in rows]
    colors = [LIGHT["diverge_pos"] if g >= 0 else LIGHT["diverge_neg"] for g in gaps]

    fig = go.Figure(go.Bar(
        x=gaps, y=names, orientation="h",
        marker=dict(color=colors, cornerradius=4),
        text=[f"{g:+.0%}" for g in gaps], textposition="outside",
        textfont=dict(color=LIGHT["text_primary"], size=11),
        hovertemplate="%{y}<br>%{text} vs cohort median<extra></extra>",
    ))
    fig.add_vline(x=0, line=dict(color=LIGHT["axis"], width=1))
    _base_layout(fig, height=max(280, 26 * len(rows)))
    fig.update_xaxes(tickformat="+.0%", showgrid=True, gridcolor=LIGHT["grid"])
    fig.update_yaxes(showgrid=False)
    return ChartSpec(
        id="benchmark_position", title="Position against the peer cohort median",
        subtitle="Positive is better on every metric — direction already normalised",
        figure=fig, tab="overview", width="full",
        note="Benchmarks are illustrative placeholders, not a licensed dataset. See the appendix.",
    )


def build_all(results: List[MetricResult],
              tables: Dict[str, pd.DataFrame],
              mode: str = "light",
              currency: str = "USD") -> List[ChartSpec]:
    set_mode(mode)
    set_currency(currency)
    builders = [
        lambda: arr_trend(results),
        lambda: arr_bridge(tables),
        lambda: benchmark_position(results),
        lambda: retention_lines(results),
        lambda: segment_churn(tables),
        lambda: cohort_heatmap(tables),
        lambda: cac_payback(results),
        lambda: channel_dumbbell(tables),
        lambda: indexed_growth(results, tables),
    ]
    specs = []
    for build in builders:
        try:
            spec = build()
        except KeyError as exc:
            # An exhibit whose fact table was not uploaded simply does not
            # appear. Builders already return None when they have nothing to
            # draw; a missing table is the same situation arriving by a
            # different route, and a partial upload must narrow the dashboard
            # rather than fail to produce one.
            continue
        if spec is not None:
            specs.append(spec)
    return specs
