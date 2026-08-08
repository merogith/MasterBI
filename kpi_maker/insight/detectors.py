"""Deterministic insight detectors.

These produce the *facts* — with the arithmetic already done. A narrative agent
(Mode 3) is given only this output and is forbidden from introducing a number
that does not appear here. That constraint is what makes the prose safe.

Everything in this module runs with zero LLM calls, which is why Modes 1 and 2
cost nothing to operate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..fmt import fmt_value
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile

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

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 9)


# Currency for the run in progress, set by detect_all. Findings are prose, so a
# number here must read exactly as it does in the dashboard and the workbook —
# hence the single shared formatter rather than a local copy.
_CURRENCY = "USD"


def _fmt(value: Optional[float], unit: str) -> str:
    return fmt_value(value, unit, _CURRENCY)


def detect_all(results: List[MetricResult], tables: Dict[str, pd.DataFrame],
               profile: CompanyProfile, spec=None) -> List[Finding]:
    """Run the deterministic detectors.

    `spec` is a `spec.AnalysisSpec` (or None for all of them, unfiltered).
    Selecting detectors is a real control rather than cosmetics: a bank cares
    about the runway and concentration findings and not much else, and a
    twenty-item findings list buries the two that matter.
    """
    global _CURRENCY
    _CURRENCY = profile.identity.currency
    by_id = {r.kpi.id: r for r in results if r.computed}

    registry = {
        "status_breaches": lambda: _status_breaches(results),
        "benchmark_gaps": lambda: _benchmark_gaps(results),
        "trend_breaks": lambda: _trend_breaks(results),
        "segment_outliers": lambda: _segment_outliers(tables, profile),
        "operating_leverage": lambda: _operating_leverage(by_id),
        "arr_bridge": lambda: _arr_bridge(tables, profile),
        "channel_efficiency": lambda: _channel_efficiency(tables),
        "runway": lambda: _runway(by_id),
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
            skipped.append(f"{name}: needs the {exc} table")
        except Exception as exc:                        # noqa: BLE001
            skipped.append(f"{name}: {type(exc).__name__}: {exc}")

    findings = _merge_same_kpi(findings)
    findings.sort(key=lambda f: (f.rank, f.id))

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


DETECTOR_NAMES = [
    "status_breaches", "benchmark_gaps", "trend_breaks", "segment_outliers",
    "operating_leverage", "arr_bridge", "channel_efficiency", "runway",
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
    """Compare the recent slope against the preceding slope on the same window."""
    out = []
    for r in results:
        if not r.computed or r.series is None:
            continue
        s = r.series.dropna()
        if len(s) < window * 2:
            continue
        recent = s.iloc[-window:]
        prior = s.iloc[-window * 2:-window]
        recent_slope = np.polyfit(range(len(recent)), recent.values, 1)[0]
        prior_slope = np.polyfit(range(len(prior)), prior.values, 1)[0]

        scale = abs(s.mean()) or 1.0
        if abs(recent_slope - prior_slope) / scale < 0.02:
            continue

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
                f"{r.kpi.name} improved over the prior {window} months but has "
                f"reversed direction over the last {window}, moving from "
                f"{_fmt(float(prior.iloc[0]), r.kpi.unit)} to "
                f"{_fmt(float(recent.iloc[-1]), r.kpi.unit)}. The inflection is "
                f"recent enough to still be addressable."
            ),
            evidence={"recent_slope": float(recent_slope), "prior_slope": float(prior_slope)},
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
    if rate[worst] < rate.median() * 1.4:
        return []

    return [Finding(
        id="segment_churn_outlier",
        severity="high",
        title=f"Churn is concentrated in the {worst} segment",
        statement=(
            f"The {worst} segment lost {_fmt(float(lost[worst]), 'currency')} of ARR over "
            f"the period, a rate of {rate[worst]:.1%} against {rate.drop(worst).mean():.1%} "
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


def _operating_leverage(by_id: Dict[str, MetricResult]) -> List[Finding]:
    arr_g, hc_g = by_id.get("arr_growth_yoy"), by_id.get("headcount_growth")
    if not arr_g or not hc_g or arr_g.current is None or hc_g.current is None:
        return []
    gap = arr_g.current - hc_g.current
    if gap >= 0.05:
        return [Finding(
            id="operating_leverage_positive",
            severity="positive",
            title="The business is gaining operating leverage",
            statement=(
                f"ARR grew {arr_g.current:.1%} against headcount growth of "
                f"{hc_g.current:.1%} — a {gap:.1%} gap. Revenue is scaling faster "
                f"than the cost base."
            ),
            evidence={"arr_growth": arr_g.current, "headcount_growth": hc_g.current, "gap": gap},
            kpi_ids=["arr_per_fte", "arr_growth_yoy"],
        )]
    if gap <= -0.03:
        return [Finding(
            id="operating_leverage_negative",
            severity="high",
            title="Headcount is growing faster than revenue",
            statement=(
                f"Headcount grew {hc_g.current:.1%} while ARR grew {arr_g.current:.1%}. "
                f"The business is buying growth with people rather than leverage, which "
                f"compounds into the cost base."
            ),
            evidence={"arr_growth": arr_g.current, "headcount_growth": hc_g.current, "gap": gap},
            kpi_ids=["arr_per_fte", "headcount_growth"],
            recommendation="Freeze net new hiring outside revenue-generating roles until ARR per FTE recovers.",
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
    severity = "critical" if leak > 0.6 else "high" if leak > 0.4 else "low"
    return [Finding(
        id="arr_bridge",
        severity=severity,
        title=f"{leak:.0%} of gross new ARR is lost to churn and contraction",
        statement=(
            f"Over the last twelve months the business added "
            f"{_fmt(new, 'currency')} of new-logo ARR and {_fmt(expansion, 'currency')} "
            f"of expansion, against {_fmt(churn, 'currency')} lost to churn and "
            f"{_fmt(contraction, 'currency')} to contraction. Net new ARR was "
            f"{_fmt(gross_add - churn - contraction, 'currency')}, meaning {leak:.0%} of "
            f"everything won was given back."
        ),
        evidence={"new": new, "expansion": expansion, "churn": churn,
                  "contraction": contraction, "leakage_ratio": leak},
        kpi_ids=["net_new_arr", "nrr", "grr"],
        recommendation=(
            "Retention work compounds faster than acquisition work at this leakage "
            "rate — a point of GRR is worth more than a point of win rate."
            if leak > 0.4 else None
        ),
        impact="high" if leak > 0.4 else "medium",
        effort="medium",
    )]


def _channel_efficiency(tables: Dict[str, pd.DataFrame]) -> List[Finding]:
    mkt = tables["marketing"]
    months = sorted(mkt["month"].unique())
    recent = mkt[mkt["month"].isin(months[-6:])]
    prior = mkt[mkt["month"].isin(months[-18:-12])] if len(months) >= 18 else None
    if prior is None or prior.empty:
        return []

    def cost_per_sql(df):
        g = df.groupby("channel").agg(spend=("spend", "sum"), sqls=("sqls", "sum"))
        return (g["spend"] / g["sqls"].replace(0, np.nan)).dropna()

    now, before = cost_per_sql(recent), cost_per_sql(prior)
    shared = now.index.intersection(before.index)
    if shared.empty:
        return []
    change = (now[shared] / before[shared] - 1).sort_values(ascending=False)
    worst = change.index[0]
    if change[worst] < 0.25:
        return []

    return [Finding(
        id="channel_cost_inflation",
        severity="high",
        title=f"Cost per qualified lead in {worst.replace('_', ' ')} is up {change[worst]:.0%}",
        statement=(
            f"{worst.replace('_', ' ').title()} now costs "
            f"{_fmt(float(now[worst]), 'currency')} per SQL, up {change[worst]:.0%} from "
            f"{_fmt(float(before[worst]), 'currency')} a year ago. Other channels moved "
            f"{change.drop(worst).mean():.0%} on average, so this is channel-specific "
            f"rather than a market-wide shift."
        ),
        evidence={"current_cps": float(now[worst]), "prior_cps": float(before[worst]),
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
    if r.current >= 12:
        return []
    return [Finding(
        id="runway_alert",
        severity="critical" if r.current < 9 else "high",
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
