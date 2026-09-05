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

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..fmt import CURRENCY_SYMBOL, fmt_value
from ..metrics.engine import MetricResult
from .theme import (
    FONT_STACK,
    SCENARIO_NOTATION,
    SCENARIO_TOKEN,
    TOKENS,
)

# ContextVar, not a module global: the API runs two pipelines concurrently, so
# a plain global meant the second run's currency could reach the first run's
# axis labels. Same fix as `insight/detectors.py`, same reason.
_CURRENCY: ContextVar[str] = ContextVar("chart_currency", default="USD")
_LOCALE: ContextVar[Optional[str]] = ContextVar("chart_locale", default=None)

# The ACTIVE palette. Mutated in place by `set_mode`, so every builder below
# reads the current mode's tokens at call time. Dark mode is therefore a real
# second render from the dark-validated steps — not an automatic flip of the
# light ones, which the palette rules forbid.
LIGHT = dict(TOKENS["light"])


def set_mode(mode: str, tokens: Optional[Dict[str, str]] = None) -> None:
    """Point the chart layer at a token set.

    `tokens` overrides the shipped palette for this mode, which is how a brand
    colour reaches the charts. Omitted, the behaviour is exactly as before.
    """
    if tokens is None:
        if mode not in TOKENS:
            raise ValueError(f"unknown theme mode {mode!r}")
        tokens = TOKENS[mode]
    LIGHT.clear()
    LIGHT.update(tokens)


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


# --------------------------------------------------------------------------
# The exhibit registry
# --------------------------------------------------------------------------
#
# This was a literal list of nine lambdas inside `build_all`, which made the
# set of exhibits a fact about one function body rather than something the
# spec could select from. Each builder now declares itself and says which
# inputs it wants, so `design.exhibits` can choose and order them.
#
# `tab` and `width` still come from the ChartSpec each builder returns — the
# builder knows whether its chart needs the full width. The spec can override
# the width; it cannot invent an exhibit.

@dataclass
class ChartEntry:
    id: str
    fn: Callable[..., Optional[ChartSpec]]
    takes: Tuple[str, ...]
    # Explicit, because the running order is an editorial decision — it is the
    # order a reader meets the charts in. Definition order in this file is
    # grouped by topic and would silently reshuffle the dashboard.
    order: int = 100


CHARTS: Dict[str, ChartEntry] = {}


def chart(id: str, order: int, takes: Tuple[str, ...] = ("results",)):
    def wrap(fn):
        CHARTS[id] = ChartEntry(id=id, fn=fn, takes=takes, order=order)
        return fn
    return wrap


class UnknownExhibit(ValueError):
    """An exhibit id that is not registered."""


def default_exhibits() -> List[str]:
    return [e.id for e in sorted(CHARTS.values(), key=lambda e: e.order)]


def resolve_exhibits(requested: Optional[Sequence[str]]) -> List[str]:
    """The exhibit ids to build, in order. `None` means all of them.

    Refused rather than skipped on an unknown id, for the same reason sections
    are: a typo should not quietly remove a chart from a board pack.
    """
    if requested is None:
        return default_exhibits()
    unknown = [e for e in requested if e not in CHARTS]
    if unknown:
        raise UnknownExhibit(
            f"unknown exhibit(s): {', '.join(unknown)}. "
            f"Available: {', '.join(default_exhibits())}")
    seen, out = set(), []
    for eid in requested:
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


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
        # `automargin` belongs here rather than in one renderer, and that is the
        # bug it fixes. `viz/export.py` set it for the PNGs with a comment
        # saying the benchmark exhibit "lost the start of every KPI name" —
        # correct, and applied to exactly one of the two consumers. The
        # interactive dashboard renders `spec.figure` itself, so on the screen a
        # user actually looks at, "Position against the peer cohort median"
        # showed **one character per bar**: %, R, n, y, %, y — the last letter
        # of each KPI name squeezed into an 8px left margin. Every horizontal
        # exhibit, every archetype, since the chart was written.
        xaxis=dict(showgrid=False, zeroline=False, linecolor=LIGHT["axis"],
                   tickcolor=LIGHT["axis"], automargin=True,
                   tickfont=dict(color=LIGHT["muted"], size=11)),
        yaxis=dict(gridcolor=LIGHT["grid"], zeroline=False, showline=False,
                   automargin=True,
                   tickfont=dict(color=LIGHT["muted"], size=11)),
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _months(index) -> List[str]:
    return [str(p) for p in index]


def set_currency(currency: str, locale: Optional[str] = None) -> None:
    _CURRENCY.set(currency)
    _LOCALE.set(locale)


def _money(v: float) -> str:
    """Delegates to the shared formatter so a figure reads identically on the
    chart, in the report prose and in the workbook."""
    return fmt_value(v, "currency", _CURRENCY.get(), locale=_LOCALE.get())


def add_scenario(fig: go.Figure, x, y, scenario: str, *,
                 name: Optional[str] = None,
                 token: Optional[str] = None) -> int:
    """Draw one scenario line in the shared notation. Returns its trace index.

    Every chart that draws a plan or a prior-year line goes through here, so
    the vocabulary is defined once (`theme.SCENARIO_NOTATION`) rather than
    re-decided per builder — which is how the same dash pattern ends up
    meaning "plan" on one exhibit and "target" on the next.

    The trace index is returned because `ChartSpec.trace_tokens` maps indices
    to token roles for the dashboard's theme toggle, and a caller that forgets
    to record one gets a line that does not restyle in dark mode.
    """
    style = SCENARIO_NOTATION[scenario]
    role = token or SCENARIO_TOKEN[scenario]
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines",
        name=name or style["label"],
        line=dict(color=LIGHT[role], width=style["width"], dash=style["dash"]),
        opacity=style["opacity"],
        hovertemplate="%{x}<br>%{y:,.0f}<extra>"
                      + str(name or style["label"]) + "</extra>",
    ))
    return len(fig.data) - 1


def money_axis(fig, axis: str = "y") -> None:
    """Currency ticks in **this run's** currency, at a finance scale.

    Two bugs in one line, both shipped until 5.2 measured them on a euro
    company:

    * **`tickprefix="$"` was hardcoded** in three charts while every other
      number on the page came from `_CURRENCY`. A €45M European SaaS business
      opened its board pack on an axis reading `$0 · $10M · $20M · $30M ·
      $40M`. The hover text, the tiles, the PDF and the workbook were all in
      euros; only the axis was not, which is the worst version of it — nothing
      looks broken, the currency is simply wrong.
    * **`tickformat="~s"` is SI**, so a billion renders as `1G`. Finance
      writes `1B`. SI is right for hertz and wrong for money.

    One function, so a fourth chart cannot reintroduce either. `exponentformat`
    and the explicit tick suffixes do the second half; plotly has no "B" in
    its SI set, so the scale is stated on the axis rather than guessed from a
    suffix nobody in a boardroom reads as "giga".
    """
    symbol = CURRENCY_SYMBOL.get(_CURRENCY.get(), "")
    update = fig.update_yaxes if axis == "y" else fig.update_xaxes
    update(tickprefix=symbol, tickformat="~s")


# --------------------------------------------------------------------------

@chart("plan_vs_actual", order=0, takes=("results",))
def plan_vs_actual(results: List[MetricResult]) -> Optional[ChartSpec]:
    """The scenario notation, on the metric the run is most steered by.

    **Returns None when the run has no plan**, which is most runs, and that is
    the honest behaviour rather than a defect: 5.1's rule is that no plan
    means no variance, and an exhibit is a stronger claim than a table cell.
    A chart with a plan line drawn from a target nobody set would be the
    fabricated-budget failure with a legend on it.

    It exists at all because a notation nothing draws is a stylesheet, not a
    language. `theme.SCENARIO_NOTATION` defines actual/plan/prior once; this
    is the first exhibit to read it, and `add_scenario` is what every later
    one will use so the vocabulary cannot fork.
    """
    planned = [r for r in results if r.plan_basis and r.computed
               and r.series is not None]
    if not planned:
        return None

    # The most senior planned metric, and the id as the tie-break so two KPIs
    # at the same tier cannot make the exhibit depend on dict ordering — the
    # same stability rule `insight/ranking.py` needed for byte-identical
    # re-runs.
    r = min(planned, key=lambda x: (int(x.kpi.tier), x.kpi.id))
    actual = r.series.dropna()
    if actual.empty:
        return None

    # Two years at most: a plan is set for a year, and a five-year x-axis
    # squeezes the comparison the exhibit exists for into its right-hand edge.
    actual = actual.iloc[-24:]
    x = _months(actual.index)

    fig = go.Figure()
    tokens: Dict[int, str] = {}

    prior = r.series.reindex([p - 12 for p in actual.index])
    if prior.notna().sum() >= 6:
        tokens[add_scenario(fig, x, prior.values, "prior")] = \
            SCENARIO_TOKEN["prior"]

    plan = r.plan.reindex(actual.index)
    if plan.notna().any():
        label = SCENARIO_NOTATION["plan"]["label"]
        if r.plan_basis == "derived":
            # Never let a derived path pass for a budget, on a chart least of
            # all: a legend entry reading "Plan" is a stronger claim than the
            # scorecard's badge, because nothing else on the exhibit qualifies
            # it.
            label = "Target path"
        tokens[add_scenario(fig, x, plan.values, "plan", name=label)] = \
            SCENARIO_TOKEN["plan"]

    tokens[add_scenario(fig, x, actual.values, "actual")] = \
        SCENARIO_TOKEN["actual"]

    _base_layout(fig, height=340)
    fig.update_layout(
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(color=LIGHT["text_secondary"], size=11)),
    )
    if r.kpi.unit == "currency":
        money_axis(fig)
    elif r.kpi.unit == "pct":
        fig.update_yaxes(tickformat=".0%")

    variance = r.vs_plan
    if variance is None:
        subtitle = "Actual against plan"
    else:
        ahead = ((variance > 0) == (r.kpi.direction.value != "lower_is_better"))
        word = "ahead of" if ahead else "behind"
        subtitle = (f"{fmt_value(abs(variance), r.kpi.unit, _CURRENCY.get(), locale=_LOCALE.get())} "
                    f"{word} plan in the latest month")
    return ChartSpec(
        id="plan_vs_actual", title=f"{r.kpi.name} vs plan",
        subtitle=subtitle, figure=fig, tab="overview", width="full",
        trace_tokens=tokens,
        note=("The plan line is this KPI's own target rule, not a stated "
              "budget." if r.plan_basis == "derived" else ""),
    )


@chart("arr_trend", order=1, takes=("results",))
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
    money_axis(fig)
    return ChartSpec(
        id="arr_trend", title="Annual Recurring Revenue",
        subtitle="Monthly, trailing 36 months", figure=fig,
        tab="overview", width="full",
        trace_tokens={0: "series_1", 1: "series_1"},
    )


@chart("arr_bridge", order=2, takes=("tables",))
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
    money_axis(fig)
    return ChartSpec(
        id="arr_bridge", title="ARR bridge, last 12 months",
        subtitle="Where the year's ARR movement came from", figure=fig,
        tab="overview", width="full",
        note="Blue adds, red subtracts. The gap between gross additions and closing ARR is leakage.",
    )


@chart("retention", order=4, takes=("results",))
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


@chart("segment_churn", order=5, takes=("tables",))
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


@chart("indexed_growth", order=9, takes=("results", "tables"))
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


@chart("cac_payback", order=7, takes=("results",))
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


@chart("cohort_heatmap", order=6, takes=("tables",))
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


@chart("channel_cost", order=8, takes=("tables",))
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
    money_axis(fig, "x")
    fig.update_xaxes(showgrid=True, gridcolor=LIGHT["grid"])
    n = len(shared)
    return ChartSpec(
        id="channel_cost", title="Cost per qualified lead, by channel",
        subtitle="Last 6 months vs the same period a year earlier", figure=fig,
        tab="growth", width="half",
        trace_tokens={n: "seq_1", n + 1: "seq_5"},
        note="A long rightward bar is a channel getting more expensive, not more productive.",
    )


@chart("benchmark_position", order=3, takes=("results",))
def benchmark_position(results: List[MetricResult]) -> Optional[ChartSpec]:
    """Diverging bar: distance from the cohort median, signed by good/bad."""
    rows = []
    for r in results:
        if not r.computed or r.kpi.benchmark is None or r.kpi.benchmark.p50 is None:
            continue
        if r.current is None or r.kpi.benchmark.p50 == 0:
            continue
        b = r.kpi.benchmark
        if r.kpi.direction.value == "target_band":
            # "Positive is better" is the subtitle's promise, and a target_band
            # metric cannot keep it by distance from the median: being 90% above
            # the R&D median and 90% below it are both bad, and this drew one of
            # them as the best bar on the chart. Measured from the *band*
            # instead — zero inside it, negative by how far outside, whichever
            # side — so the normalisation the subtitle claims is actually true.
            if b.p25 is None or b.p75 is None:
                continue
            lo, hi = min(b.p25, b.p75), max(b.p25, b.p75)
            span = (hi - lo) or abs(hi) or 1.0
            if lo <= r.current <= hi:
                gap = 0.0
            else:
                gap = -abs(lo - r.current if r.current < lo
                           else r.current - hi) / span
        else:
            gap = (r.current - b.p50) / abs(b.p50)
            if r.kpi.direction.value == "lower_is_better":
                gap = -gap                  # so positive always means "better"
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
              currency: str = "USD",
              tokens: Optional[Dict[str, str]] = None,
              exhibits: Optional[Sequence[str]] = None,
              widths: Optional[Dict[str, str]] = None,
              locale: Optional[str] = None) -> List[ChartSpec]:
    set_mode(mode, tokens)
    set_currency(currency, locale)
    widths = widths or {}
    inputs = {"results": results, "tables": tables}

    specs = []
    for eid in resolve_exhibits(exhibits):
        entry = CHARTS[eid]
        try:
            spec = entry.fn(*(inputs[name] for name in entry.takes))
        except KeyError:
            # An exhibit whose fact table was not uploaded simply does not
            # appear. Builders already return None when they have nothing to
            # draw; a missing table is the same situation arriving by a
            # different route, and a partial upload must narrow the dashboard
            # rather than fail to produce one.
            continue
        if spec is None:
            continue
        if eid in widths:
            spec.width = widths[eid]
        specs.append(spec)
    return specs


# --------------------------------------------------------------------------
# E-commerce exhibits
# --------------------------------------------------------------------------
#
# Registered here rather than in a sector module because the registry is the
# thing that decides what a run can draw, and a chart that silently omits
# itself when its table is absent is already the mechanism that keeps a
# subscription run from showing these. Ordered after the subscription set for
# the same reason: `default_exhibits` is the order a reader meets them in, and
# a run only ever produces one sector's worth.

@chart("revenue_orders", order=20, takes=("tables",))
def revenue_and_orders(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Two lines that answer "is growth price or volume?" in one look."""
    orders = tables.get("orders")
    if orders is None or orders.empty:
        return None
    net = orders["gross_revenue"] - orders["discounts"] - orders["returns"]
    by_month = orders.assign(net=net).groupby("month").agg(
        net=("net", "sum"), orders=("orders", "sum")).sort_index()
    x = _months(by_month.index)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=by_month["net"].values, mode="lines", name="Net revenue",
        line=dict(color=LIGHT["series_1"], width=2),
        fill="tozeroy", fillcolor=_rgba(LIGHT["series_1"], 0.10),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra>Net revenue</extra>",
        customdata=[_money(v) for v in by_month["net"].values],
    ))
    # Orders on their own axis, indexed to the same start, so the two are
    # comparable in shape without implying they are comparable in level.
    scale = (by_month["net"].iloc[0] / by_month["orders"].iloc[0]
             if by_month["orders"].iloc[0] else 1.0)
    fig.add_trace(go.Scatter(
        x=x, y=(by_month["orders"] * scale).values, mode="lines", name="Orders",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{customdata:,.0f} orders</b><extra></extra>",
        customdata=by_month["orders"].values,
    ))
    _base_layout(fig, height=360)
    # The legend is not decoration here, and 5.2 is why. This chart used to
    # tell its two series apart with `dash="dot"` on the second — but dot now
    # means *prior year*, so the dash came off, and that left two solid lines
    # distinguished by colour with `showlegend=False` inherited from
    # `_base_layout`: unreadable, and unreadable in exactly the way 4.2b found
    # on the OEE exhibit. That item turned the legend on for the two charts it
    # touched and this one was not among them, so it kept its dot and its
    # silence. Both are now fixed the same way.
    fig.update_layout(hovermode="x unified", showlegend=True,
                      legend=dict(orientation="h", y=1.12, x=0,
                                  font=dict(color=LIGHT["text_secondary"],
                                            size=11)))
    return ChartSpec(
        id="revenue_orders", title="Net revenue and order volume",
        subtitle="Orders rescaled to the revenue axis at the first month — "
                 "shape is comparable, level is not",
        figure=fig, tab="overview", width="full",
        note="Revenue rising faster than orders is price or basket size; the "
             "other way round is discounting.",
        trace_tokens={0: "series_1", 1: "series_2"},
    )


@chart("aov_conversion", order=21, takes=("tables",))
def aov_and_conversion(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """The two levers that do not need more traffic."""
    orders, traffic = tables.get("orders"), tables.get("traffic")
    if orders is None or traffic is None or orders.empty or traffic.empty:
        return None
    net = orders["gross_revenue"] - orders["discounts"] - orders["returns"]
    by_month = orders.assign(net=net).groupby("month").agg(
        net=("net", "sum"), orders=("orders", "sum")).sort_index()
    aov = (by_month["net"] / by_month["orders"].replace(0, np.nan))
    funnel = traffic.groupby("month").agg(
        sessions=("sessions", "sum"), orders=("orders", "sum")).sort_index()
    conversion = (funnel["orders"] / funnel["sessions"].replace(0, np.nan))

    x = _months(aov.index)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=aov.values, mode="lines", name="AOV",
        line=dict(color=LIGHT["series_1"], width=2),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra>AOV</extra>",
        customdata=[_money(v) for v in aov.values],
    ))
    fig.add_trace(go.Scatter(
        x=_months(conversion.index), y=conversion.values, mode="lines",
        name="Conversion", yaxis="y2",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{y:.2%}</b><extra>Conversion</extra>",
    ))
    _base_layout(fig)
    # The one place a second axis is justified: the two series share a story
    # and cannot share a scale. Both are direct-labelled in the legend.
    #
    # That sentence was here before 5.2 and **the legend was off** — a comment
    # asserting what the code does not do, which is this repo's characteristic
    # bug. Two series, a left axis and a right axis, and nothing on the chart
    # saying which line belonged to which. `showlegend=False` comes from
    # `_base_layout`, so a builder that needs one has to ask; this one said it
    # had one and never did.
    fig.update_layout(
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(color=LIGHT["text_secondary"], size=11)),
        yaxis2=dict(overlaying="y", side="right", tickformat=".1%",
                    showgrid=False, automargin=True,
                    tickfont=dict(color=LIGHT["muted"], size=11)),
    )
    return ChartSpec(
        id="aov_conversion", title="Average order value and conversion rate",
        subtitle="Basket size on the left, conversion on the right",
        figure=fig, tab="growth", width="full",
        note="Both respond faster than acquisition does, and neither needs "
             "more traffic to move.",
        trace_tokens={0: "series_1", 1: "series_2"},
    )


@chart("category_returns", order=22, takes=("tables",))
def category_returns(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Return rate by category — the thing a blended rate hides."""
    orders = tables.get("orders")
    if orders is None or orders.empty or "category" not in orders.columns:
        return None
    grouped = orders.groupby("category").agg(
        gross=("gross_revenue", "sum"), discounts=("discounts", "sum"),
        returns=("returns", "sum"))
    rate = (grouped["returns"] / (grouped["gross"] - grouped["discounts"])
            .replace(0, np.nan)).sort_values()
    if rate.dropna().empty:
        return None

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=rate.values, y=[c.replace("_", " ").title() for c in rate.index],
        orientation="h", marker=dict(color=LIGHT["series_1"]),
        text=[f"{v:.1%}" for v in rate.values], textposition="outside",
        textfont=dict(color=LIGHT["text_secondary"], size=11),
        hovertemplate="%{y}<br><b>%{x:.1%} returned</b><extra></extra>",
    ))
    _base_layout(fig)
    fig.update_layout(xaxis=dict(tickformat=".0%", showgrid=True,
                                 gridcolor=LIGHT["grid"]))
    blended = grouped["returns"].sum() / max(
        (grouped["gross"] - grouped["discounts"]).sum(), 1e-9)
    return ChartSpec(
        id="category_returns", title="Return rate by category",
        subtitle=f"Blended rate {blended:.1%} — the number a dashboard usually shows",
        figure=fig, tab="retention", width="half",
        note="A blended return rate that rises may only mean the worst "
             "category grew. This is why the dimension is carried.",
        trace_tokens={0: "series_1"},
    )


@chart("buyer_mix", order=23, takes=("tables",))
def buyer_mix(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """New against repeat buyers — retail's retention picture."""
    buyers = tables.get("buyers")
    if buyers is None or buyers.empty:
        return None
    frame = buyers.sort_values("month")
    x = _months(frame["month"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=frame["repeat_buyers"].values, name="Repeat",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{x}<br><b>%{y:,.0f} repeat</b><extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=x, y=frame["new_buyers"].values, name="New",
        marker=dict(color=LIGHT["series_2"]),
        hovertemplate="%{x}<br><b>%{y:,.0f} new</b><extra></extra>",
    ))
    _base_layout(fig, height=360)
    fig.update_layout(barmode="stack", hovermode="x unified")
    share = frame["repeat_buyers"].sum() / max(frame["active_buyers"].sum(), 1e-9)
    return ChartSpec(
        id="buyer_mix", title="Who bought: new against repeat",
        subtitle=f"{share:.0%} of purchases came from buyers who had bought before",
        figure=fig, tab="retention", width="full",
        note="A business growing on new buyers alone is renting its revenue. "
             "The repeat band is the part that does not have to be bought twice.",
        trace_tokens={0: "series_1", 1: "series_2"},
    )


# --------------------------------------------------------------------------
# Project exhibits
# --------------------------------------------------------------------------
#
# An archetype nobody can look at is an archetype nobody will use. Before these
# three, a consultancy's dashboard carried *average order value*, *category
# returns* and *buyer mix* — the transactional exhibits, because those are what
# the tables it was being simulated with could draw. Moving the four
# project-shaped sectors onto their own generator removed those four charts and
# would have left one, so the three questions a services board actually opens
# with are drawn here instead.

@chart("utilisation_realisation", order=30, takes=("tables",))
def utilisation_and_realisation(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """The two ratios that decide whether an hour was worth working.

    Together rather than separately, and on one axis because both are
    percentages of the same hour: utilisation is whether it was sold,
    realisation is whether it was paid for at the rate it was sold at. A firm
    can be busy and unprofitable, and only the pair says which.
    """
    ts = tables.get("timesheets")
    if ts is None or ts.empty:
        return None
    grouped = ts.assign(
        standard=ts["billable_hours"] * ts["standard_rate"]).groupby("month").agg(
        billable=("billable_hours", "sum"), available=("available_hours", "sum"),
        fee=("fee_revenue", "sum"), standard=("standard", "sum")).sort_index()
    utilisation = grouped["billable"] / grouped["available"].replace(0, np.nan)
    realisation = grouped["fee"] / grouped["standard"].replace(0, np.nan)
    x = _months(grouped.index)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=utilisation.values, mode="lines", name="Utilisation",
        line=dict(color=LIGHT["series_1"], width=2),
        hovertemplate="%{x}<br><b>%{y:.1%}</b><extra>Utilisation</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=realisation.values, mode="lines", name="Realisation",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{y:.1%}</b><extra>Realisation</extra>",
    ))
    _base_layout(fig, height=360)
    fig.update_layout(hovermode="x unified", yaxis=dict(tickformat=".0%"))
    # Three series with no labels is a guessing game — the retention exhibit
    # already turns the legend on for exactly this reason, and reading the
    # chart on screen is what said this one needed it too.
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=1.12, x=0,
                                  font=dict(color=LIGHT["text_secondary"],
                                            size=11)))
    return ChartSpec(
        id="utilisation_realisation",
        title="Utilisation and realisation",
        subtitle="Share of available hours that were billed, and share of "
                 "standard fee those hours actually earned",
        figure=fig, tab="overview", width="full",
        note="Utilisation falling is a sales problem. Realisation falling is a "
             "delivery or a pricing one, and it does not show in the revenue "
             "line until the engagement closes.",
        trace_tokens={0: "series_1", 1: "series_2"},
    )


@chart("backlog_cover", order=31, takes=("tables",))
def backlog_and_book_to_bill(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Sold work against work delivered — the leading half of a services P&L."""
    backlog = tables.get("backlog")
    if backlog is None or backlog.empty:
        return None
    frame = backlog.sort_values("month")
    x = _months(frame["month"])
    delivered = frame["revenue_recognised"].replace(0, np.nan)
    ratio = frame["bookings"] / delivered

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=frame["closing_backlog"].values, name="Closing backlog",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra>Backlog</extra>",
        customdata=[_money(v) for v in frame["closing_backlog"].values],
    ))
    fig.add_trace(go.Scatter(
        x=x, y=ratio.values, mode="lines", name="Book-to-bill", yaxis="y2",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{y:.2f}x</b><extra>Book-to-bill</extra>",
    ))
    _base_layout(fig, height=360)
    # A second axis for the same reason `aov_conversion` has one: a stock in
    # currency and a ratio share the story and cannot share a scale. The 1.0
    # line is the whole point of the ratio, so it is drawn rather than left to
    # be read off an axis.
    fig.update_layout(
        hovermode="x unified",
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    automargin=True,
                    tickfont=dict(color=LIGHT["muted"], size=11)),
        shapes=[dict(type="line", xref="paper", x0=0, x1=1, yref="y2",
                     y0=1.0, y1=1.0,
                     line=dict(color=LIGHT["axis"], width=1, dash="dash"))],
    )
    months_cover = (frame["closing_backlog"].iloc[-1]
                    / max(frame["revenue_recognised"].tail(12).mean(), 1e-9))
    return ChartSpec(
        id="backlog_cover", title="Backlog and book-to-bill",
        subtitle=f"{months_cover:.1f} months of delivered revenue sitting in "
                 f"sold work at the end of the period",
        figure=fig, tab="growth", width="full",
        note="Below the dashed line the firm is delivering faster than it is "
             "selling, and the revenue line will follow two quarters later.",
        trace_tokens={0: "series_1", 1: "series_2"},
    )


@chart("service_line_margin", order=32, takes=("tables",))
def service_line_margin(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Fee earned against fee at standard rate, by service line.

    The gap is what each line concedes — through discount, through scope, or
    through the wrong people doing the work — and a blended realisation number
    hides which line is conceding it.
    """
    ts = tables.get("timesheets")
    if ts is None or ts.empty or "service_line" not in ts.columns:
        return None
    grouped = ts.assign(
        standard=ts["billable_hours"] * ts["standard_rate"]).groupby(
        "service_line").agg(fee=("fee_revenue", "sum"),
                            standard=("standard", "sum"))
    grouped = grouped[grouped["standard"] > 0]
    if grouped.empty:
        return None
    grouped["realisation"] = grouped["fee"] / grouped["standard"]
    grouped = grouped.sort_values("realisation")
    labels = [str(name).replace("_", " ") for name in grouped.index]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["realisation"].values, y=labels, orientation="h",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{y}<br><b>%{x:.1%} realised</b><extra></extra>",
    ))
    _base_layout(fig, height=300)
    fig.update_layout(
        xaxis=dict(tickformat=".0%", showgrid=True, gridcolor=LIGHT["grid"]),
        shapes=[dict(type="line", yref="paper", y0=0, y1=1, xref="x",
                     x0=1.0, x1=1.0,
                     line=dict(color=LIGHT["axis"], width=1, dash="dash"))],
    )
    worst = labels[0]
    return ChartSpec(
        id="service_line_margin",
        title="Realisation by service line",
        subtitle=f"Fee earned as a share of fee at standard rate — "
                 f"{worst} concedes the most",
        figure=fig, tab="people", width="half",
        note="The dashed line is full standard rate. Anything short of it was "
             "given away, and the line that gives away most is rarely the one "
             "with the lowest headline margin.",
        trace_tokens={0: "series_1"},
    )


# --------------------------------------------------------------------------
# Production exhibits
# --------------------------------------------------------------------------
#
# Same reasoning as the project set: moving manufacturing and food production
# off `ecommerce` correctly takes away the four transactional exhibits they were
# borrowing — a factory was being shown average order value and buyer mix — and
# an archetype with nothing to look at is one nobody will use. These are the
# three questions a plant review opens with.

@chart("oee_trend", order=40, takes=("tables",))
def oee_trend(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """OEE and the three losses it decomposes into.

    Drawn together because the total on its own is not actionable: a plant at
    75% wants to know whether the line was stopped, slow, or making scrap, and
    those are three different people's problems. Weighted by scheduled capacity
    so a big line counts for more than a small one.
    """
    make = tables.get("production")
    if make is None or make.empty:
        return None
    weighted = make.assign(w=make["capacity_units"])
    grouped = weighted.groupby("month").apply(
        lambda g: pd.Series({
            part: float((g[part] * g["w"]).sum() / max(g["w"].sum(), 1e-9))
            for part in ("availability", "performance", "quality", "oee")
        }), include_groups=False).sort_index()
    x = _months(grouped.index)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=grouped["oee"].values, mode="lines", name="OEE",
        line=dict(color=LIGHT["series_1"], width=2.5),
        hovertemplate="%{x}<br><b>%{y:.1%}</b><extra>OEE</extra>",
    ))
    for part, token, dash in (("availability", "series_2", "dot"),
                              ("quality", "series_3", "dash")):
        fig.add_trace(go.Scatter(
            x=x, y=grouped[part].values, mode="lines", name=part.title(),
            line=dict(color=LIGHT[token], width=1.6, dash=dash),
            hovertemplate="%{x}<br><b>%{y:.1%}</b>"
                          f"<extra>{part.title()}</extra>",
        ))
    _base_layout(fig, height=360)
    fig.update_layout(hovermode="x unified", yaxis=dict(tickformat=".0%"))
    # Three series with no labels is a guessing game — the retention exhibit
    # already turns the legend on for exactly this reason, and reading the
    # chart on screen is what said this one needed it too.
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=1.12, x=0,
                                  font=dict(color=LIGHT["text_secondary"],
                                            size=11)))
    # Performance is deliberately not drawn: MAX_CATEGORICAL_SERIES is three,
    # and availability and quality are the two that move here. The number is in
    # the identity and in the workbook for anyone who wants it.
    return ChartSpec(
        id="oee_trend", title="Overall equipment effectiveness, and where it goes",
        subtitle="OEE against the two losses that move it — the line was not "
                 "running, or what it made was scrap",
        figure=fig, tab="overview", width="full",
        note="A total on its own is not actionable. Availability is a "
             "maintenance conversation and quality is an engineering one, and "
             "the blended number hides which of the two you are having.",
        trace_tokens={0: "series_1", 1: "series_2", 2: "series_3"},
    )


@chart("capacity_headroom", order=41, takes=("tables",))
def capacity_headroom(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """What the plant made against what it could have, month by month."""
    make = tables.get("production")
    if make is None or make.empty:
        return None
    grouped = make.groupby("month").agg(
        made=("units_produced", "sum"), scrapped=("units_scrapped", "sum"),
        scheduled=("capacity_units", "sum"),
        nameplate=("nameplate_units", "sum")).sort_index()
    x = _months(grouped.index)
    output = grouped["made"] + grouped["scrapped"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=grouped["nameplate"].values, mode="lines", name="Nameplate",
        line=dict(color=LIGHT["axis"], width=1, dash="dash"),
        hovertemplate="%{x}<br><b>%{y:,.0f} units</b><extra>Nameplate</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=grouped["scheduled"].values, mode="lines", name="Scheduled",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{y:,.0f} units</b><extra>Scheduled</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=output.values, mode="lines", name="Made",
        line=dict(color=LIGHT["series_1"], width=2),
        fill="tozeroy", fillcolor=_rgba(LIGHT["series_1"], 0.10),
        hovertemplate="%{x}<br><b>%{y:,.0f} units</b><extra>Made</extra>",
    ))
    _base_layout(fig, height=360)
    fig.update_layout(hovermode="x unified")
    # Three series with no labels is a guessing game — the retention exhibit
    # already turns the legend on for exactly this reason, and reading the
    # chart on screen is what said this one needed it too.
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=1.12, x=0,
                                  font=dict(color=LIGHT["text_secondary"],
                                            size=11)))
    headroom = 1.0 - float(grouped["scheduled"].tail(12).sum()
                           / max(grouped["nameplate"].tail(12).sum(), 1e-9))
    return ChartSpec(
        id="capacity_headroom", title="Output against the ceiling",
        subtitle=f"{headroom:.0%} of nameplate capacity unscheduled over the "
                 f"last twelve months",
        figure=fig, tab="growth", width="full",
        note="The gap between made and scheduled is OEE. The gap between "
             "scheduled and nameplate is how much more the plant could sell "
             "before anyone has to buy a machine.",
        trace_tokens={0: "axis", 1: "series_2", 2: "series_1"},
    )


@chart("scrap_by_family", order=42, takes=("tables",))
def scrap_by_family(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Scrap rate by product family — what a blended yield number hides."""
    make = tables.get("production")
    if make is None or make.empty or "product_family" not in make.columns:
        return None
    grouped = make.groupby("product_family").agg(
        good=("units_produced", "sum"), scrap=("units_scrapped", "sum"))
    grouped = grouped[(grouped["good"] + grouped["scrap"]) > 0]
    if grouped.empty:
        return None
    grouped["rate"] = grouped["scrap"] / (grouped["good"] + grouped["scrap"])
    grouped = grouped.sort_values("rate")
    labels = [str(name).replace("_", " ") for name in grouped.index]
    blended = float(grouped["scrap"].sum()
                    / (grouped["good"].sum() + grouped["scrap"].sum()))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["rate"].values, y=labels, orientation="h",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{y}<br><b>%{x:.2%} scrapped</b><extra></extra>",
    ))
    _base_layout(fig, height=300)
    fig.update_layout(
        xaxis=dict(tickformat=".1%", showgrid=True, gridcolor=LIGHT["grid"]),
        shapes=[dict(type="line", yref="paper", y0=0, y1=1, xref="x",
                     x0=blended, x1=blended,
                     line=dict(color=LIGHT["axis"], width=1, dash="dash"))],
    )
    worst = labels[-1]
    return ChartSpec(
        id="scrap_by_family", title="Scrap rate by product family",
        subtitle=f"Blended {blended:.1%}, and {worst} is the line carrying it",
        figure=fig, tab="people", width="half",
        note="Scrapped units cost what they cost and earn nothing, so this is a "
             "gross-margin chart wearing an engineering label. The dashed line "
             "is the company blend.",
        trace_tokens={0: "series_1"},
    )


# --------------------------------------------------------------------------
# Marketplace exhibits
# --------------------------------------------------------------------------
#
# The third and last set, for the same reason as the other two: moving a sector
# onto its own generator correctly removes the transactional exhibits it was
# borrowing, and an archetype with nothing to look at is one nobody will use.

@chart("gmv_and_take", order=50, takes=("tables",))
def gmv_and_take(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """The two numbers a platform board reads, and the reason they are two.

    GMV is the market's size and the take is the platform's revenue. Showing
    only the first flatters; showing only the second hides where the business
    actually sits. The take rate is on its own axis because it is a rate, and
    because a platform growing GMV while conceding commission is the failure
    this pairing exists to expose.
    """
    gmv = tables.get("gmv")
    if gmv is None or gmv.empty:
        return None
    grouped = gmv.groupby("month").agg(
        value=("gross_merchandise_value", "sum"),
        take=("net_revenue", "sum")).sort_index()
    rate = grouped["take"] / grouped["value"].replace(0, np.nan)
    x = _months(grouped.index)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=grouped["value"].values, name="GMV",
        marker=dict(color=_rgba(LIGHT["series_1"], 0.35)),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra>GMV</extra>",
        customdata=[_money(v) for v in grouped["value"].values],
    ))
    fig.add_trace(go.Bar(
        x=x, y=grouped["take"].values, name="Net revenue",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{x}<br><b>%{customdata}</b><extra>Net revenue</extra>",
        customdata=[_money(v) for v in grouped["take"].values],
    ))
    fig.add_trace(go.Scatter(
        x=x, y=rate.values, mode="lines", name="Take rate", yaxis="y2",
        line=dict(color=LIGHT["series_2"], width=2),
        hovertemplate="%{x}<br><b>%{y:.2%}</b><extra>Take rate</extra>",
    ))
    _base_layout(fig, height=360)
    fig.update_layout(
        barmode="overlay", hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(color=LIGHT["text_secondary"], size=11)),
        yaxis2=dict(overlaying="y", side="right", tickformat=".1%",
                    showgrid=False, automargin=True,
                    tickfont=dict(color=LIGHT["muted"], size=11)),
    )
    blended = float(grouped["take"].tail(12).sum()
                    / max(grouped["value"].tail(12).sum(), 1e-9))
    return ChartSpec(
        id="gmv_and_take", title="GMV, net revenue and the take rate",
        subtitle=f"{blended:.1%} of everything transacted stayed with the "
                 f"platform over the last twelve months",
        figure=fig, tab="overview", width="full",
        note="Only the solid bar is revenue. A platform reporting the outline "
             "as its top line is describing a business twenty times its size at "
             "a fifth of its margin.",
        trace_tokens={0: "series_1", 1: "series_1", 2: "series_2"},
    )


@chart("liquidity_trend", order=51, takes=("tables",))
def liquidity_trend(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Both sides of the market and what cleared between them.

    A marketplace fails from the seller side far more often than from the buyer
    side, and in the revenue line the two are indistinguishable. Here they are
    not: demand holding up while supply falls away is a recruitment problem, and
    it is the one thing a platform can still act on when it happens.
    """
    liq = tables.get("liquidity")
    if liq is None or liq.empty:
        return None
    grouped = liq.groupby("month").agg(
        supply=("supply_listings", "sum"), demand=("demand_requests", "sum"),
        matches=("matches", "sum")).sort_index()
    x = _months(grouped.index)

    fig = go.Figure()
    for column, label, token, dash in (
            ("demand", "Requests", "series_2", "dot"),
            ("supply", "Listings", "series_3", "dash"),
            ("matches", "Matched", "series_1", None)):
        fig.add_trace(go.Scatter(
            x=x, y=grouped[column].values, mode="lines", name=label,
            line=dict(color=LIGHT[token], width=2.2 if dash is None else 1.6,
                      dash=dash) if dash else dict(color=LIGHT[token], width=2.2),
            hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra>" + label + "</extra>",
        ))
    _base_layout(fig, height=360)
    fig.update_layout(
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(color=LIGHT["text_secondary"], size=11)))
    rate = float(grouped["matches"].tail(12).sum()
                 / max(grouped["demand"].tail(12).sum(), 1e-9))
    return ChartSpec(
        id="liquidity_trend", title="Supply, demand and what cleared",
        subtitle=f"{rate:.0%} of requests found a match over the last twelve "
                 f"months",
        figure=fig, tab="growth", width="full",
        note="Whichever line is lower is the side the platform is short of. "
             "Matched can never cross either of them, so the gap to the lower "
             "line is the market's own friction.",
        trace_tokens={0: "series_2", 1: "series_3", 2: "series_1"},
    )


@chart("take_by_category", order=52, takes=("tables",))
def take_by_category(tables: Dict[str, pd.DataFrame]) -> Optional[ChartSpec]:
    """Commission by category — what a blended take rate averages away."""
    gmv = tables.get("gmv")
    if gmv is None or gmv.empty or "category" not in gmv.columns:
        return None
    grouped = gmv.groupby("category").agg(
        value=("gross_merchandise_value", "sum"), take=("net_revenue", "sum"))
    grouped = grouped[grouped["value"] > 0]
    if grouped.empty:
        return None
    grouped["rate"] = grouped["take"] / grouped["value"]
    grouped = grouped.sort_values("rate")
    labels = [str(name).replace("_", " ") for name in grouped.index]
    blended = float(grouped["take"].sum() / grouped["value"].sum())

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["rate"].values, y=labels, orientation="h",
        marker=dict(color=LIGHT["series_1"]),
        hovertemplate="%{y}<br><b>%{x:.2%} take</b><extra></extra>",
    ))
    _base_layout(fig, height=300)
    fig.update_layout(
        xaxis=dict(tickformat=".1%", showgrid=True, gridcolor=LIGHT["grid"]),
        shapes=[dict(type="line", yref="paper", y0=0, y1=1, xref="x",
                     x0=blended, x1=blended,
                     line=dict(color=LIGHT["axis"], width=1, dash="dash"))],
    )
    return ChartSpec(
        id="take_by_category", title="Take rate by category",
        subtitle=f"Blended {blended:.1%}, and the spread is the negotiating "
                 f"position",
        figure=fig, tab="people", width="half",
        note="A category conceding commission usually has sellers with "
             "somewhere else to go. The dashed line is the platform blend.",
        trace_tokens={0: "series_1"},
    )
