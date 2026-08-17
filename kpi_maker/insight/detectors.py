"""Deterministic insight detectors.

These produce the *facts* — with the arithmetic already done. A narrative agent
(Mode 3) is given only this output and is forbidden from introducing a number
that does not appear here. That constraint is what makes the prose safe.

Everything in this module runs with zero LLM calls, which is why Modes 1 and 2
cost nothing to operate.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from ..fmt import fmt_percent, fmt_value
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
from .seasonality import deseasonalise

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "positive": 4}


@dataclass
class Finding:
    id: str
    severity: str            # critical | high | medium | low | positive
    title: str
    statement: str           # every number in here is computed, never inferred
    evidence: Dict[str, float] = field(default_factory=dict)
    kpi_ids: List[str] = field(default_factory=list)
    recommendation: Optional[str] = None
    impact: Optional[str] = None
    effort: Optional[str] = None   # low | medium | high
    # A pre-written clause for when this finding is folded into another, so the
    # merge never has to slice prose apart and guess which sentence to keep.
    merge_note: Optional[str] = None
    # How long ago the event this finding describes happened. `None` means "the
    # current state", which is what most detectors report and is not the same
    # as an unknown date — see `insight/ranking.recency`.
    months_ago: Optional[float] = None
    # Set by `ranking.rank_all`, so the dashboard, the report and the deck order
    # findings identically. They each sorted for themselves before, which is how
    # three renderers of one list can disagree about what matters most.
    score: float = 0.0

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 9)


# Formatting context for the run in progress, set by detect_all. Findings are
# prose, so a number here must read exactly as it does in the dashboard and the
# workbook — hence one shared formatter rather than a local copy.
#
# ContextVars, not module globals. These were plain globals, and the API runs
# pipelines on a `ThreadPoolExecutor(max_workers=2)`: two concurrent runs in
# different currencies meant whichever called `detect_all` second overwrote the
# symbol the first was still formatting with, and a board pack came out with
# the wrong one. A ContextVar is per-thread, so the fix costs three lines and
# no signature changes across twenty-one call sites.
_CURRENCY: ContextVar[str] = ContextVar("currency", default="USD")
_LOCALE: ContextVar[Optional[str]] = ContextVar("locale", default=None)

# Per-detector thresholds. `AnalysisSpec.params` overrides any of them by
# detector name; before this they were inline literals, so tuning "how big a
# jump counts as a channel problem" meant editing the engine.
DEFAULT_PARAMS: Dict[str, Dict[str, float]] = {
    "segment_outliers": {"worst_vs_median": 1.4},
    "arr_bridge": {"critical_leak": 0.6, "high_leak": 0.4},
    "channel_efficiency": {"min_increase": 0.25},
    "operating_leverage": {"positive_gap": 0.05, "negative_gap": -0.03},
    "runway": {"warn_months": 12, "critical_months": 9},
}

# `None`, not `{}`: a mutable ContextVar default is one object shared by every
# context that never sets it, so a caller mutating it would leak across runs.
_PARAMS: ContextVar[Optional[Dict[str, Dict[str, float]]]] = ContextVar(
    "params", default=None)


def _param(detector: str, name: str) -> float:
    """One threshold, user override first, shipped default second."""
    override = (_PARAMS.get() or {}).get(detector, {})
    if name in override:
        return float(override[name])
    return float(DEFAULT_PARAMS[detector][name])


def _stable(value: float) -> float:
    """Drop the bits a threaded BLAS reduction does not reproduce."""
    return float(f"{float(value):.9g}")


def _fmt(value: Optional[float], unit: str) -> str:
    return fmt_value(value, unit, _CURRENCY.get(), locale=_LOCALE.get())


def _pct(value: float, decimals: int = 1) -> str:
    """A percentage in the run's locale, at the precision the sentence wants.

    These were raw `f"{_pct(x, 1)}"` literals, so a finding could read
    "$767,0K of ARR ... a rate of 40.7%" — the currency localised and the
    percentage not, in the same sentence.
    """
    return fmt_percent(value, decimals, _LOCALE.get())


def detect_all(results: List[MetricResult], tables: Dict[str, pd.DataFrame],
               profile: CompanyProfile, spec=None,
               locale: Optional[str] = None,
               watched: Optional[Set[str]] = None) -> List[Finding]:
    """Run the deterministic detectors.

    `spec` is a `spec.AnalysisSpec` (or None for all of them, unfiltered).
    Selecting detectors is a real control rather than cosmetics: a bank cares
    about the runway and concentration findings and not much else, and a
    twenty-item findings list buries the two that matter.
    """
    _CURRENCY.set(profile.identity.currency)
    _LOCALE.set(locale)
    _PARAMS.set(dict(spec.params) if spec is not None and spec.params else None)
    by_id = {r.kpi.id: r for r in results if r.computed}

    registry = {
        "status_breaches": lambda: _status_breaches(results),
        "benchmark_gaps": lambda: _benchmark_gaps(results),
        "trend_breaks": lambda: _trend_breaks(results),
        "segment_outliers": lambda: _segment_outliers(tables, profile),
        "operating_leverage": lambda: _operating_leverage(by_id, tables),
        "arr_bridge": lambda: _arr_bridge(tables, profile),
        "channel_efficiency": lambda: _channel_efficiency(tables),
        "runway": lambda: _runway(by_id),
        "driver_decomposition": lambda: _driver_decomposition(results),
        "concentration": lambda: _concentration(tables),
    }

    wanted = list(registry) if spec is None or spec.detectors is None else [
        name for name in spec.detectors if name in registry]
    disabled = set(spec.disabled) if spec is not None else set()
    skipped: List[str] = []

    findings: List[Finding] = []
    for name in wanted:
        if name in disabled:
            continue
        try:
            findings += registry[name]()
        except KeyError as exc:
            # A partial upload has fewer fact tables than the generator makes,
            # and a detector that needs one of the absent ones simply has
            # nothing to say. The metrics engine already treats missing data as
            # "not computable" rather than an error; a detector crashing the
            # whole run would make an honest partial upload impossible.
            #
            # Two of them are not missing a table by accident, though: the ARR
            # bridge and the churn-by-segment outlier are statements about a
            # subscription book, and a retailer does not have one. "Needs the
            # mrr_movements table" invites someone to go and find a file that
            # does not exist for their business, so say the true thing instead.
            if name in _SUBSCRIPTION_ONLY:
                skipped.append(
                    f"{name}: describes a subscription book, which this "
                    f"business does not have")
            else:
                skipped.append(f"{name}: needs the {exc} table")
        except Exception as exc:                        # noqa: BLE001
            skipped.append(f"{name}: {type(exc).__name__}: {exc}")

    findings = _merge_same_kpi(findings)
    # Ordering was `(severity, id)` — alphabetical by detector id inside the
    # band that matters, which is to say arbitrary. A reader takes the top three
    # seriously and skims the rest.
    #
    # `watched` is passed in rather than read off `spec`, because pinning lives
    # on `MetricsSpec` and this function is handed `AnalysisSpec`. Reaching
    # across for it would tie the detectors to a part of the spec they have no
    # other business knowing about.
    from .ranking import rank_all
    findings = rank_all(findings, set(watched or ()))

    if spec is not None and spec.min_severity:
        # "positive" ranks last in SEVERITY_ORDER, so a floor of "medium" keeps
        # strengths as well as issues — dropping them would turn every report
        # into a list of problems.
        floor = SEVERITY_ORDER.get(spec.min_severity, 9)
        findings = [f for f in findings
                    if f.rank <= floor or f.severity == "positive"]
    if spec is not None and spec.max_findings is not None:
        findings = findings[:spec.max_findings]

    # Exposed rather than swallowed: "why is there no channel finding?" must
    # have an answer, the same way every dropped KPI does.
    detect_all.skipped = skipped
    return findings


#: Detectors whose subject matter only exists for a subscription business.
#: Not a data gap — a category difference, and reported as one.
_SUBSCRIPTION_ONLY = {"arr_bridge", "segment_outliers"}

DETECTOR_NAMES = [
    "status_breaches", "benchmark_gaps", "trend_breaks", "segment_outliers",
    "operating_leverage", "arr_bridge", "channel_efficiency", "runway",
    "driver_decomposition", "concentration",
]


def _merge_same_kpi(findings: List[Finding]) -> List[Finding]:
    """Fold a bottom-quartile benchmark finding into the RAG breach for the same
    KPI. Two entries for one metric at the top of a report reads as padding —
    and the benchmark comparison is context for the breach, not a second issue."""
    breaches = {f.kpi_ids[0]: f for f in findings
                if f.id.startswith("breach_") and f.kpi_ids}
    merged, drop = [], set()
    for f in findings:
        if not f.id.startswith("bench_low_") or not f.kpi_ids:
            continue
        breach = breaches.get(f.kpi_ids[0])
        if breach is None:
            continue
        if f.merge_note:
            breach.statement += " " + f.merge_note
        breach.evidence.update(f.evidence)
        if not breach.recommendation:
            breach.recommendation = f.recommendation
        drop.add(f.id)
    for f in findings:
        if f.id not in drop:
            merged.append(f)
    return merged


# --------------------------------------------------------------------------

def _status_breaches(results: List[MetricResult]) -> List[Finding]:
    out = []
    for r in results:
        if not r.computed or r.status not in ("red", "amber"):
            continue
        k = r.kpi
        severity = "high" if r.status == "red" else "medium"
        if k.tier.value <= 1 and r.status == "red":
            severity = "critical"

        # Report the band the value has ACTUALLY crossed. Amber means it is past
        # green but not yet at red; quoting the red threshold there produces a
        # statement that contradicts its own number.
        direction = "below" if k.direction.value == "higher_is_better" else "above"
        green = k.alert_bands.green if k.alert_bands else None
        red = k.alert_bands.red if k.alert_bands else None
        if r.status == "red":
            crossed = (
                f"{direction} the red threshold of {_fmt(red, k.unit)}"
            )
            threshold = red
        else:
            crossed = (
                f"{direction} the green threshold of {_fmt(green, k.unit)}, "
                f"but not yet at the red threshold of {_fmt(red, k.unit)}"
            )
            threshold = green

        out.append(Finding(
            id=f"breach_{k.id}",
            severity=severity,
            title=f"{k.name} is {'off track' if r.status == 'red' else 'drifting'}",
            statement=(
                f"{k.name} stands at {_fmt(r.current, k.unit)}, {crossed}. "
                f"Twelve months ago it was {_fmt(r.prior_year, k.unit)}."
            ),
            evidence={"current": r.current, "threshold": threshold,
                      "green": green, "red": red, "prior_year": r.prior_year},
            kpi_ids=[k.id],
            recommendation=k.interpretation,
            impact="high" if severity in ("critical", "high") else "medium",
        ))
    return out


def _benchmark_gaps(results: List[MetricResult]) -> List[Finding]:
    out = []
    for r in results:
        if not r.computed or r.benchmark_position is None:
            continue
        k = r.kpi
        if r.benchmark_position == "bottom_quartile":
            out.append(Finding(
                id=f"bench_low_{k.id}",
                severity="high",
                title=f"{k.name} sits in the bottom quartile of the peer cohort",
                statement=(
                    f"{k.name} is {_fmt(r.current, k.unit)} against a cohort median of "
                    f"{_fmt(k.benchmark.p50, k.unit)}. Closing to median is the single "
                    f"clearest improvement available on this metric."
                ),
                evidence={"current": r.current, "median": k.benchmark.p50},
                kpi_ids=[k.id],
                recommendation=f"Set a 12-month target at the cohort median ({_fmt(k.benchmark.p50, k.unit)}).",
                impact="high",
                effort="medium",
                merge_note=(
                    f"That is bottom-quartile against a cohort median of "
                    f"{_fmt(k.benchmark.p50, k.unit)}."
                ),
            ))
        elif r.benchmark_position == "top_quartile":
            out.append(Finding(
                id=f"bench_high_{k.id}",
                severity="positive",
                title=f"{k.name} is top-quartile",
                statement=(
                    f"{k.name} at {_fmt(r.current, k.unit)} is ahead of the cohort "
                    f"75th percentile ({_fmt(k.benchmark.p75, k.unit)}). This is a "
                    f"defensible strength to build the equity story on."
                ),
                evidence={"current": r.current, "p75": k.benchmark.p75},
                kpi_ids=[k.id],
            ))
    return out


def _trend_breaks(results: List[MetricResult], window: int = 6) -> List[Finding]:
    """Compare the recent slope against the preceding slope on the same window.

    On the *seasonally adjusted* series where there is a season to adjust for.
    A retailer's January is always worse than its December, so the raw
    comparison reads the calendar rather than the business: the same company
    produced three trend breaks with its history ending in February and none
    ending in January. See `insight/seasonality.py` for the measurements and
    for why the adjustment is estimated from the series rather than read off
    `market.seasonality`.
    """
    out = []
    for r in results:
        if not r.computed or r.series is None:
            continue
        adjustment = deseasonalise(r.series.dropna())
        s = adjustment.series.dropna()
        if len(s) < window * 2:
            continue
        recent = s.iloc[-window:]
        prior = s.iloc[-window * 2:-window]
        # Rounded to nine significant figures because `polyfit` goes through
        # LAPACK's least-squares path, whose reduction order depends on how
        # many BLAS threads happen to be available. That moves the last two or
        # three bits between processes on identical input — enough to make
        # findings.json differ run to run, which costs the no-regression gate
        # its ability to tell a real change from noise. Nine figures is far
        # beyond any precision a twelve-point regression slope carries, and the
        # comparisons below are unaffected at any plausible magnitude.
        recent_slope = _stable(np.polyfit(range(len(recent)), recent.values, 1)[0])
        prior_slope = _stable(np.polyfit(range(len(prior)), prior.values, 1)[0])

        scale = abs(s.mean()) or 1.0
        if abs(recent_slope - prior_slope) / scale < 0.02:
            continue

        # The statement used to quote the first value of the prior window against
        # the last value of the recent one, which for a decelerating series that
        # is still rising reads "reversed direction ... moving from 2,850,434 to
        # 3,347,383" — a sentence contradicting its own numbers. Quote what each
        # window did instead, taken from the same slopes the test above uses, so
        # the prose cannot disagree with the finding.
        prior_change = prior_slope * (window - 1)
        recent_change = recent_slope * (window - 1)

        better_is_up = r.kpi.direction.value == "higher_is_better"
        deteriorating = (recent_slope < prior_slope) if better_is_up else (recent_slope > prior_slope)
        turned = np.sign(recent_slope) != np.sign(prior_slope)
        if not (deteriorating and turned):
            continue

        out.append(Finding(
            id=f"trend_{r.kpi.id}",
            severity="high" if r.kpi.tier.value <= 1 else "medium",
            title=f"{r.kpi.name} has turned",
            statement=(
                f"{r.kpi.name} {'rose' if prior_change > 0 else 'fell'} by "
                f"{_fmt(abs(prior_change), r.kpi.unit)} over the prior {window} "
                f"months and {'rose' if recent_change > 0 else 'fell'} by "
                f"{_fmt(abs(recent_change), r.kpi.unit)} over the last {window}, "
                f"and now stands at {_fmt(float(recent.iloc[-1]), r.kpi.unit)}"
                + (f" (seasonally adjusted — {_pct(adjustment.strength, 0)} of "
                   f"this metric's variation is the calendar)"
                   if adjustment.applied else "")
                + ". The inflection is recent enough to still be addressable."
            ),
            # `current` and `prior` are the two numbers the statement above
            # already quotes. They are here so the ranker can size the move:
            # with slopes alone every trend break scored identically, which put
            # a metric that halved level with one that wobbled.
            evidence={"current": float(recent.iloc[-1]),
                      "prior": float(prior.iloc[0]),
                      "recent_slope": float(recent_slope),
                      "prior_slope": float(prior_slope),
                      "seasonally_adjusted": adjustment.applied,
                      "seasonal_strength": round(adjustment.strength, 4)},
            kpi_ids=[r.kpi.id],
            impact="high",
            effort="medium",
        ))
    return out


def _segment_outliers(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[Finding]:
    """Aggregate churn hides everything. Always look segmented."""
    mov, cust = tables["mrr_movements"], tables["customers"]
    churn = mov[mov["movement_type"] == "churn"]
    if churn.empty:
        return []

    lost = churn.groupby("segment")["delta_mrr"].sum().abs() * 12
    base = cust.groupby("segment")["final_acv"].sum() + lost
    rate = (lost / base).dropna()
    if len(rate) < 2:
        return []

    worst = rate.idxmax()
    if rate[worst] < rate.median() * _param("segment_outliers", "worst_vs_median"):
        return []

    return [Finding(
        id="segment_churn_outlier",
        severity="high",
        title=f"Churn is concentrated in the {worst} segment",
        statement=(
            f"The {worst} segment lost {_fmt(float(lost[worst]), 'currency')} of ARR over "
            f"the period, a rate of {_pct(rate[worst], 1)} against {_pct(rate.drop(worst).mean(), 1)} "
            f"across the other segments. Aggregate retention masks this entirely."
        ),
        evidence={"segment_rate": float(rate[worst]), "other_rate": float(rate.drop(worst).mean()),
                  "arr_lost": float(lost[worst])},
        kpi_ids=["logo_churn_rate", "grr"],
        recommendation=(
            f"Run a win/loss review on {worst} churn specifically before making any "
            f"company-wide retention investment — the aggregate number will mislead."
        ),
        impact="high",
        effort="low",
    )]


def _yoy_total(frame: pd.DataFrame, column: str, months: int = 12
               ) -> Optional[tuple]:
    """Trailing-twelve-month total against the twelve before it.

    A trailing year on both sides is what makes this comparison safe for a
    seasonal business without any adjustment at all: every month appears once
    on each side, so December's peak cancels itself.
    """
    if frame is None or column not in frame.columns or "month" not in frame.columns:
        return None
    monthly = frame.groupby("month")[column].sum().sort_index()
    if len(monthly) < months * 2:
        return None
    recent = float(monthly.iloc[-months:].sum())
    prior = float(monthly.iloc[-months * 2:-months].sum())
    if abs(prior) < 1e-9:
        return None
    return recent, prior, recent / prior - 1.0


def _operating_leverage(by_id: Dict[str, MetricResult],
                        tables: Dict[str, pd.DataFrame]) -> List[Finding]:
    """Is revenue growing faster than the cost base, whatever the business is?

    This read `by_id["arr_growth_yoy"]` and `by_id["headcount_growth"]`, so it
    fired for subscriptions and for nothing else — an e-commerce run has no
    `arr_growth_yoy` and never will. The question it asks is not a subscription
    question, though: every business either scales revenue faster than its
    people or it does not.

    So it is computed from `monthly_financials.revenue` and `headcount.fte`,
    the two tables **every** archetype emits, rather than from ids only one
    pack declares. The KPI ids below are for drill-through; whichever of them
    the run happens to have selected is linked, and none of them gates the
    finding.
    """
    revenue = _yoy_total(tables.get("monthly_financials"), "revenue")
    people = _yoy_total(tables.get("headcount"), "fte")
    if revenue is None or people is None:
        return []
    rev_growth, hc_growth = revenue[2], people[2]
    gap = rev_growth - hc_growth

    def links(*candidates: str) -> List[str]:
        return [c for c in candidates if c in by_id] or ["revenue_per_fte"]

    if gap >= _param("operating_leverage", "positive_gap"):
        return [Finding(
            id="operating_leverage_positive",
            severity="positive",
            title="The business is gaining operating leverage",
            statement=(
                f"Revenue grew {_pct(rev_growth, 1)} over the last twelve months "
                f"against headcount growth of {_pct(hc_growth, 1)} — a "
                f"{_pct(gap, 1)} gap. Revenue is scaling faster than the cost base."
            ),
            evidence={"current": rev_growth, "expected": hc_growth, "gap": gap},
            kpi_ids=links("arr_per_fte", "revenue_per_fte", "revenue_per_head",
                          "arr_growth_yoy", "revenue_growth_yoy"),
        )]
    if gap <= _param("operating_leverage", "negative_gap"):
        return [Finding(
            id="operating_leverage_negative",
            severity="high",
            title="Headcount is growing faster than revenue",
            statement=(
                f"Headcount grew {_pct(hc_growth, 1)} over the last twelve months "
                f"while revenue grew {_pct(rev_growth, 1)}. The business is buying "
                f"growth with people rather than leverage, which compounds into "
                f"the cost base."
            ),
            evidence={"current": rev_growth, "expected": hc_growth, "gap": gap},
            kpi_ids=links("arr_per_fte", "revenue_per_fte", "revenue_per_head",
                          "headcount_growth"),
            recommendation=("Freeze net new hiring outside revenue-generating "
                            "roles until revenue per head recovers."),
            impact="high",
            effort="low",
        )]
    return []


def _arr_bridge(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[Finding]:
    """Decompose the last 12 months of ARR movement — the core diagnostic."""
    mov = tables["mrr_movements"]
    months = sorted(mov["month"].unique())[-12:]
    recent = mov[mov["month"].isin(months)]
    parts = recent.groupby("movement_type")["delta_mrr"].sum() * 12

    new = float(parts.get("new", 0.0))
    expansion = float(parts.get("expansion", 0.0))
    churn = float(abs(parts.get("churn", 0.0)))
    contraction = float(abs(parts.get("contraction", 0.0)))
    gross_add = new + expansion
    if gross_add <= 0:
        return []

    leak = (churn + contraction) / gross_add
    critical_leak = _param("arr_bridge", "critical_leak")
    high_leak = _param("arr_bridge", "high_leak")
    severity = ("critical" if leak > critical_leak
                else "high" if leak > high_leak else "low")
    return [Finding(
        id="arr_bridge",
        severity=severity,
        title=f"{_pct(leak, 0)} of gross new ARR is lost to churn and contraction",
        statement=(
            f"Over the last twelve months the business added "
            f"{_fmt(new, 'currency')} of new-logo ARR and {_fmt(expansion, 'currency')} "
            f"of expansion, against {_fmt(churn, 'currency')} lost to churn and "
            f"{_fmt(contraction, 'currency')} to contraction. Net new ARR was "
            f"{_fmt(gross_add - churn - contraction, 'currency')}, meaning {_pct(leak, 0)} of "
            f"everything won was given back."
        ),
        evidence={"new": new, "expansion": expansion, "churn": churn,
                  "contraction": contraction, "leakage_ratio": leak},
        kpi_ids=["net_new_arr", "nrr", "grr"],
        recommendation=(
            "Retention work compounds faster than acquisition work at this leakage "
            "rate — a point of GRR is worth more than a point of win rate."
            if leak > high_leak else None
        ),
        impact="high" if leak > high_leak else "medium",
        effort="medium",
    )]


#: Marketing outcome columns, best first. `sqls` is the subscription funnel's
#: qualified lead; `leads` is what every other archetype's marketing table
#: records. Hardcoding `sqls` made this detector raise `Column(s) ['sqls'] do
#: not exist` on an e-commerce run — reported to the user as
#: `needs the "Column(s) ['sqls'] do not exist" table`, which is not a sentence.
_FUNNEL_COLUMNS = ("sqls", "leads", "orders")


def _channel_efficiency(tables: Dict[str, pd.DataFrame]) -> List[Finding]:
    mkt = tables["marketing"]
    outcome = next((c for c in _FUNNEL_COLUMNS if c in mkt.columns), None)
    if outcome is None or "spend" not in mkt.columns:
        return []
    noun = {"sqls": "qualified lead", "leads": "lead", "orders": "order"}[outcome]
    months = sorted(mkt["month"].unique())
    recent = mkt[mkt["month"].isin(months[-6:])]
    prior = mkt[mkt["month"].isin(months[-18:-12])] if len(months) >= 18 else None
    if prior is None or prior.empty:
        return []

    def cost_per_outcome(df):
        g = df.groupby("channel").agg(spend=("spend", "sum"),
                                      outcome=(outcome, "sum"))
        return (g["spend"] / g["outcome"].replace(0, np.nan)).dropna()

    now, before = cost_per_outcome(recent), cost_per_outcome(prior)
    shared = now.index.intersection(before.index)
    if shared.empty:
        return []
    change = (now[shared] / before[shared] - 1).sort_values(ascending=False)
    worst = change.index[0]
    if change[worst] < _param("channel_efficiency", "min_increase"):
        return []

    return [Finding(
        id="channel_cost_inflation",
        severity="high",
        title=f"Cost per {noun} in {worst.replace('_', ' ')} is up {_pct(change[worst], 0)}",
        statement=(
            f"{worst.replace('_', ' ').title()} now costs "
            f"{_fmt(float(now[worst]), 'currency')} per {noun}, "
            f"up {_pct(change[worst], 0)} from "
            f"{_fmt(float(before[worst]), 'currency')} a year ago. Other channels moved "
            f"{_pct(change.drop(worst).mean(), 0)} on average, so this is channel-specific "
            f"rather than a market-wide shift."
        ),
        evidence={"current": float(now[worst]), "prior": float(before[worst]),
                  "change": float(change[worst]), "other_channels_avg": float(change.drop(worst).mean())},
        kpi_ids=["blended_cac", "cac_payback_months"],
        recommendation=(
            f"Reallocate budget from {worst.replace('_', ' ')} toward the channels holding "
            f"cost flat, and re-test at a lower bid before restoring spend."
        ),
        impact="medium",
        effort="low",
    )]


def _runway(by_id: Dict[str, MetricResult]) -> List[Finding]:
    r = by_id.get("cash_runway_months")
    if not r or r.current is None:
        return []
    if r.current >= _param("runway", "warn_months"):
        return []
    return [Finding(
        id="runway_alert",
        severity=("critical" if r.current < _param("runway", "critical_months")
                  else "high"),
        title=f"Cash runway is {r.current:.0f} months",
        statement=(
            f"At the trailing three-month burn rate the current cash balance funds "
            f"{r.current:.0f} months of operation. Below twelve months the financing "
            f"calendar starts to drive operating decisions."
        ),
        evidence={"runway_months": r.current},
        kpi_ids=["cash_runway_months", "burn_multiple"],
        recommendation="Model a downside case at current growth and prepare the raise now.",
        impact="high",
        effort="high",
    )]


def _driver_decomposition(results: List[MetricResult]) -> List[Finding]:
    """Which part of the business moved the number.

    The detector the plan calls the highest-value analytical addition, and the
    one that could not be written until `MetricResult.by_segment` existed. It
    says one of two things and never confuses them — see `insight/decompose.py`
    for why additivity is measured against this run's own numbers rather than
    inferred from a metric's unit.
    """
    from .decompose import decompose, worth_reporting

    out: List[Finding] = []
    for r in results:
        if not r.computed or not r.segmented:
            continue
        for dimension in r.dimensions:
            found = decompose(r, dimension)
            if found is None or not worth_reporting(found):
                continue
            lead = found.leader
            if lead is None:
                continue

            unit = r.kpi.unit
            cut = dimension.replace("_", " ")
            if found.kind == "contribution":
                statement = (
                    f"{r.kpi.name} moved {_fmt(found.total_change, unit)} over "
                    f"the last twelve months, and {lead.segment} accounts for "
                    f"{_pct(abs(lead.share_of_move), 0)} of that "
                    f"({_fmt(lead.change, unit)}). The other "
                    f"{len(found.parts) - 1} {cut}s together account for the rest."
                )
                title = f"{lead.segment} drove most of the move in {r.kpi.name}"
                evidence = {"current": lead.current, "prior": lead.prior,
                            "total_change": found.total_change,
                            "share_of_move": lead.share_of_move}
            else:
                statement = (
                    f"{r.kpi.name} reads {_fmt(r.current, unit)} blended, but "
                    f"{lead.segment} is at {_fmt(lead.current, unit)}. A blended "
                    f"figure averages that away, which is exactly what it is for "
                    f"and exactly why it should not be read alone."
                )
                title = f"{r.kpi.name} differs sharply by {cut}"
                evidence = {"current": lead.current, "blended": r.current,
                            "total_change": found.total_change}

            # The two kinds of finding ask different questions, and using one
            # test for both mislabels half of them. A *contribution* is bad when
            # the leader pulled the metric the wrong way. A *dispersion* is bad
            # when the outlier sits worse than the blend — NRR at 81% against a
            # 103% blend is a problem however the segment moved, and this read
            # `positive` until the two were separated.
            higher_is_better = r.kpi.direction.value == "higher_is_better"
            if found.kind == "contribution":
                worse = (lead.change < 0) if higher_is_better else (lead.change > 0)
            elif lead.current is None or r.current is None:
                worse = False
            else:
                worse = ((lead.current < r.current) if higher_is_better
                         else (lead.current > r.current))
            out.append(Finding(
                id=f"decomp_{r.kpi.id}_{dimension}",
                severity=("high" if worse and r.kpi.tier.value <= 1
                          else "medium" if worse else "positive"),
                title=title,
                statement=statement,
                evidence={k: v for k, v in evidence.items() if v is not None},
                kpi_ids=[r.kpi.id],
                recommendation=(
                    f"Look at {lead.segment} on its own before drawing a "
                    f"company-wide conclusion from {r.kpi.name}."),
                impact="high",
                effort="low",
            ))
    return out


def _concentration(tables: Dict[str, pd.DataFrame]) -> List[Finding]:
    """How much of the revenue rides on one segment, channel or category.

    Herfindahl-Hirschman: the sum of squared shares, which is the standard
    measure and needs no threshold invented for it — competition authorities
    treat 0.25 as highly concentrated and 0.15 as moderately so, and those are
    the numbers used here rather than ones chosen to make the finding fire.

    Computed from `segment_financials`, so it works for every archetype that
    emits one rather than for subscriptions alone. `atlas_enterprise`'s whole
    story is concentration, and it was authored into the sample's profile
    instead of being computed from anything.
    """
    seg = tables.get("segment_financials")
    if seg is None or seg.empty:
        return []

    out: List[Finding] = []
    latest = seg["month"].max()
    for dimension, part in seg[seg["month"] == latest].groupby("dimension"):
        shares = part.set_index("segment")["share"].sort_values(ascending=False)
        if len(shares) < 2:
            continue
        hhi = float((shares ** 2).sum())
        if hhi < 0.15:
            continue
        top = shares.index[0]
        cut = str(dimension).replace("_", " ")
        out.append(Finding(
            id=f"concentration_{dimension}",
            severity="high" if hhi >= 0.25 else "medium",
            title=f"Revenue is concentrated by {cut}",
            statement=(
                f"{top} accounts for {_pct(float(shares.iloc[0]), 0)} of revenue, "
                f"and the {cut} mix scores {hhi:.2f} on the Herfindahl index "
                f"({'highly' if hhi >= 0.25 else 'moderately'} concentrated). "
                f"A shock to {top} lands on the whole company."
            ),
            evidence={"current": hhi, "threshold": 0.25,
                      "top_share": float(shares.iloc[0])},
            kpi_ids=["revenue_concentration_top10"],
            recommendation=(
                f"Model the downside of losing a third of {top} before "
                f"committing to next year's plan."),
            impact="high",
            effort="medium",
        ))
    return out
