"""What every synthetic generator needs, whatever the business sells.

Extracted from `saas.py`, where it was written first and where it was all
entangled with subscriptions by accident rather than by nature. None of it is
about recurring revenue:

  * **Warm-up and trimming.** Simulate before the reported window so cohorts
    have real history and month 1 is not an artificial cliff. A retailer needs
    that as much as a SaaS vendor.
  * **The calibration loop.** Solve for the acquisition rate that lands on the
    profile's stated customer count and growth. The comments on it — hold the
    seed constant, keep the best attempt not the last, damp the correction —
    were each paid for by a bug, and none of them mention MRR.
  * **`calibration_tolerance`.** A Poisson noise floor. Arithmetic, not sector.
  * **Anomaly planting.** A dataset with nothing wrong in it produces a report
    with nothing to say. What the anomalies *are* is sector-specific; that
    there are some, and that they are documented rather than random, is not.

The generator registry lives here too, so a sector declares itself the way
stages, sections, charts, ops and identities already do.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..profile.schema import Stage

# Months simulated before the reported window. Long enough that the oldest
# reported cohort has a year of history behind it.
WARMUP_MONTHS = 24

# Monthly growth in new-customer acquisition, by company stage.
STAGE_GROWTH = {
    Stage.pre_revenue: 0.08,
    Stage.early: 0.055,
    Stage.growth: 0.030,
    Stage.established: 0.012,
    Stage.mature: 0.004,
    Stage.turnaround: -0.005,
}


@dataclass
class Anomaly:
    """A deliberate, documented event the insight engine is meant to find."""
    kind: str
    start_month: int
    end_month: int
    magnitude: float
    description: str
    segment: str = ""


@dataclass
class GeneratedData:
    tables: Dict[str, pd.DataFrame]
    anomalies: List[Anomaly] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)
    # Empty for generated data, one entry per file for uploads: which fact
    # table each became and the column mapping shape detection proposed.
    #
    # It travels *in the stage output* rather than on `RunContext`, and that is
    # the whole point. A context side channel is set only when its stage runs,
    # so a warm re-run that reuses `source` and rebuilds `model` would find it
    # empty — the mapping would silently vanish and the run would degrade to raw
    # column names with nothing raised. That is the exact `RunContext`
    # side-channel bug the audit found in `lineage`/`origins`, and repeating it
    # in a new place was not worth the smaller diff.
    upload_plans: List[Any] = field(default_factory=list)

    def __getitem__(self, key: str) -> pd.DataFrame:
        return self.tables[key]


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

GENERATORS: Dict[str, Callable] = {}


def generator(archetype: str):
    """Register a generator for a business archetype.

    Keyed on `business_model.type`, which is also what selects the KPI pack, so
    a sector is one name across the whole pipeline rather than a mapping table
    somebody has to keep in step.
    """
    def wrap(fn):
        GENERATORS[archetype] = fn
        return fn
    return wrap


def available() -> List[str]:
    return sorted(GENERATORS)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

#: Pin the end of generated history without touching a spec. Set by the test
#: suite so the whole suite is reproducible whatever month it runs in, and
#: useful to anyone who needs two runs on different days to be comparable.
HISTORY_END_ENV = "MASTERBI_HISTORY_END"


def default_history_end() -> pd.Period:
    """The last month a company could have closed its books on.

    This was a literal December 2025 period, written out twice — here and in
    `subscription.py` — so **every** generated company's history ended in that
    month whatever today's date was: a report dated
    2026 opening on figures fifteen months old. It also parked every sample at
    the one calendar position where a fourth-quarter peak sits at the very end
    of the series, which is what hid the seasonal artefact 3.4b fixes.

    The current month is deliberately excluded: it is not over, and a partial
    month rendered beside twelve complete ones is a cliff at the right-hand
    edge of every chart.
    """
    import os

    pinned = os.environ.get(HISTORY_END_ENV)
    if pinned:
        return pd.Period(pinned, freq="M")
    return pd.Period(pd.Timestamp.utcnow(), freq="M") - 1


def month_range(history_months: int, warmup: int = WARMUP_MONTHS, end=None):
    """Every month to simulate, and the slice of it the user will see.

    `end` comes from the spec, which resolves it **once, at spec construction**,
    rather than being read from the clock here. That is what keeps the cache
    key honest: a run's data is a function of its spec, so re-running a saved
    spec next month reproduces the same artifacts, and asking for fresh data is
    a new spec rather than a silent difference between two identical-looking
    runs.
    """
    total = history_months + warmup
    last = pd.Period(end, freq="M") if end is not None else default_history_end()
    months = pd.period_range(end=last, periods=total, freq="M")
    return months, months[warmup:]


def trim_warmup(tables: Dict[str, pd.DataFrame], cutoff,
                keep_full: Sequence[str] = ()) -> Dict[str, pd.DataFrame]:
    """Drop the warm-up from every time series.

    `keep_full` names entity tables that must keep their whole history —
    cohort analysis needs acquisitions from before the reported window, and a
    customer acquired in the warm-up is a real customer.
    """
    out = dict(tables)
    for name, frame in tables.items():
        if name in keep_full or "month" not in getattr(frame, "columns", ()):
            continue
        out[name] = frame[frame["month"] >= cutoff].reset_index(drop=True)
    return out


def to_reported(anomalies: Sequence[Anomaly],
                warmup: int = WARMUP_MONTHS) -> List[Anomaly]:
    """Re-index planted events onto the reported window, dropping the past ones."""
    return [
        Anomaly(a.kind, a.start_month - warmup, a.end_month - warmup,
                a.magnitude, a.description, a.segment)
        for a in anomalies if a.end_month >= warmup
    ]


# --------------------------------------------------------------------------
# Growth and seasonality
# --------------------------------------------------------------------------

def monthly_growth(profile) -> float:
    """Monthly acquisition growth. A stated growth rate always beats the stage prior.

    Stage is a coarse proxy — it puts every self-described "growth stage"
    company on the same 3%/month curve. When the user actually tells us their
    growth rate, using the prior instead would mean asking a question and then
    ignoring the answer.
    """
    stated = profile.financials.growth_rate_yoy
    if stated is not None:
        # Annual -> monthly, guarding the domain: -100% annual has no monthly
        # equivalent, and runaway rates make the simulation explode.
        return float(np.clip((1.0 + max(stated, -0.90)) ** (1 / 12) - 1.0, -0.05, 0.20))
    return STAGE_GROWTH.get(profile.size.stage, 0.02)


class VolatileRNG:
    """A generator that widens or narrows the spread of its Gaussian draws.

    Wrapping beats threading a multiplier through two dozen call sites, and it
    keeps the draw ORDER identical, which is what makes `volatility=1.0`
    reproduce the previous output bit for bit: `sigma * 1.0` is `sigma`.

    Only `normal` and `lognormal` are scaled. Poisson deliberately is not —
    its variance is a property of its mean, so "more volatile acquisition"
    would have to be a different process rather than a wider one, and
    inventing that here would be dishonest about what the knob does. Coin
    flips (`random`) have no spread to scale either.
    """

    def __init__(self, rng: np.random.Generator, volatility: float = 1.0):
        self._rng = rng
        self._v = float(volatility)

    def normal(self, loc=0.0, scale=1.0, size=None):
        return self._rng.normal(loc, scale * self._v, size)

    def lognormal(self, mean=0.0, sigma=1.0, size=None):
        return self._rng.lognormal(mean, sigma * self._v, size)

    def __getattr__(self, name):
        return getattr(self._rng, name)


def volatile(rng: np.random.Generator, volatility: float):
    """Wrap only when it would change something, so the default path is untouched."""
    return rng if volatility == 1.0 else VolatileRNG(rng, volatility)


def apply_amplitude(seasonality: np.ndarray, amplitude: float) -> np.ndarray:
    """Scale a seasonal curve's deviation from flat.

    Scaling the deviation rather than the factor keeps the annual mean where it
    was: multiplying the factors directly would make a strongly seasonal
    business also a larger one, which is a different question.
    """
    return 1.0 + (np.asarray(seasonality, dtype=float) - 1.0) * float(amplitude)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def calibration_tolerance(target: int) -> float:
    """Relative tolerance that stays achievable at small customer counts.

    Acquisition is a Poisson process, so the noise floor on the surviving
    population is roughly sqrt(N). For a 4-person startup with 24 customers one
    logo is already 4% of the book and a fixed 8% target is unreachable — the
    calibrator would spin and then fail the reconciliation gate. Allow the
    larger of 8% or three customers, which is tight for big books and merely
    honest for small ones.
    """
    if target <= 0:
        return 1.0
    return max(0.08, 3.0 / target, 1.0 / math.sqrt(target))


@dataclass
class Attempt:
    """One pass of the simulation, as the calibrator sees it."""
    count: int                          # surviving customers
    growth: Optional[float]             # measured year-on-year revenue growth
    payload: Any                        # whatever the caller needs back


def calibrate(simulate: Callable[[np.random.Generator, float, float], Attempt],
              *, seed: int, target_count: int, target_growth: Optional[float],
              growth: float, base_new: float = 12.0,
              max_attempts: int = 10) -> Any:
    """Solve for the acquisition rate that lands on the profile's numbers.

    Re-simulating a few times beats deriving the rate in closed form, because
    churn, seasonality and Poisson noise all feed back into the surviving
    population.

    Two knobs, two targets. `base_new` sets the LEVEL of the book (how many
    customers survive to the end) and `growth` sets its SHAPE (how fast revenue
    compounds). Calibrating only the level is what let a company that told us
    it was flat come out growing 26% — the acquisition rate was flat, but a
    young book keeps accumulating regardless. Both are corrected together.
    """
    tolerance = calibration_tolerance(target_count)
    best: Optional[Tuple[float, Any]] = None

    # Small books are cheap to simulate and noisy to hit, so they get more
    # attempts; a 50,000-customer book is the opposite on both counts.
    if target_count and target_count < 300:
        max_attempts = 24
    elif target_growth is not None:
        # Fitting two targets needs more iterations than fitting one.
        max_attempts = max(max_attempts, 18)

    for _ in range(max_attempts):
        # The SEED IS HELD CONSTANT across attempts. Re-seeding each pass would
        # redraw the Poisson noise every time, so the search could never tell a
        # better `base_new` from a luckier draw and would wander instead of
        # converging. Fixing the noise makes the objective deterministic in
        # base_new, which is what lets the damped step below actually work.
        rng = np.random.default_rng(seed)
        attempt = simulate(rng, base_new, growth)

        if target_count <= 0:
            return attempt.payload
        if attempt.count == 0:
            base_new = max(base_new * 2, 1.0)
            continue

        error = abs(attempt.count - target_count) / target_count

        growth_error = 0.0
        if target_growth is not None and attempt.growth is not None:
            growth_error = (abs(attempt.growth - target_growth)
                            / max(abs(target_growth), 0.10))
            # Nudge the monthly acquisition-growth knob toward closing the gap
            # in ANNUAL revenue growth. The step has to be big enough to cross
            # zero within the attempt budget: a business that reports itself as
            # flat or shrinking needs NEGATIVE acquisition growth, and a timid
            # step never gets there from a positive start.
            gap = float(np.clip(target_growth - attempt.growth, -1.5, 1.5))
            growth = float(np.clip(growth + 1.1 * gap / 12.0, -0.09, 0.20))

        combined = error + 0.5 * growth_error
        if best is None or combined < best[0]:
            best = (combined, attempt.payload)
        if error <= tolerance and growth_error <= 0.15:
            return attempt.payload

        # Damped correction. An undamped ratio step overshoots on small books
        # because the same multiplier also changes how many churn away.
        base_new *= 1.0 + 0.75 * (target_count / attempt.count - 1.0)
        base_new = max(base_new, 1e-4)

    if best is None:
        from ..contract.gate import ReconciliationError
        raise ReconciliationError(
            f"could not simulate any surviving customers for target {target_count}"
        )
    return best[1]


def yoy_growth(monthly_revenue: pd.Series, report_start: int) -> Optional[float]:
    """Year-on-year revenue growth over the REPORTED window.

    Measured on what the user will see and judge the number against, not on the
    warm-up.
    """
    if report_start < 0 or len(monthly_revenue) - report_start < 13:
        return None
    end = float(monthly_revenue.iloc[-1])
    prior = float(monthly_revenue.iloc[-13])
    if prior <= 0:
        return None
    return end / prior - 1.0


def segment_financials(financials: pd.DataFrame,
                       activity: Dict[str, Tuple[pd.DataFrame, str, bool]],
                       ) -> pd.DataFrame:
    """Company revenue split across each dimension a run can be sliced by.

    Long: `month, dimension, segment, revenue, share`. One table rather than
    one per dimension, because a subscription business slices by customer
    segment and a transactional one by channel *and* category — a wide table
    would need a different shape per archetype, and everything downstream would
    have to learn which.

    **Built from shares, not from levels, and that is the whole design.** Each
    segment's share of the dimension's activity is measured from the table that
    actually records it, and the company's own revenue is then split by those
    shares. Shares sum to one by construction, so segment revenue sums to
    company revenue *exactly*, for every dimension, whatever route the
    generator took to derive the company figure. Summing simulated per-segment
    levels instead would leave a residual that a Tier 1 identity would rightly
    reject, and the residual would have to be shoved somewhere arbitrary.

    `activity` maps a dimension name to `(frame, value_column, cumulative)`.
    `cumulative` is for a stock built from a flow: MRR is the running sum of
    movements, so a segment's share this month depends on everything it has
    accumulated, not on what it happened to add in that one month.
    """
    months = list(financials["month"])
    revenue = financials.set_index("month")["revenue"]
    rows: List[Dict[str, Any]] = []

    for dimension, (frame, column, cumulative) in activity.items():
        if frame is None or frame.empty or dimension not in frame.columns:
            continue
        grid = (frame.groupby(["month", dimension])[column].sum()
                .unstack(fill_value=0.0).reindex(months, fill_value=0.0)
                .sort_index())
        if cumulative:
            grid = grid.cumsum()

        totals = grid.sum(axis=1)
        for month in months:
            total = float(totals.loc[month])
            if total <= 0:
                # No activity yet. Emitting zero-revenue rows would be a claim
                # that every segment earned nothing, which is different from
                # not knowing how to split a month that has not happened.
                continue
            for segment in grid.columns:
                share = float(grid.loc[month, segment]) / total
                rows.append({
                    "month": month,
                    "dimension": dimension,
                    "segment": str(segment),
                    "revenue": float(revenue.loc[month]) * share,
                    "share": share,
                })

    return pd.DataFrame(rows, columns=["month", "dimension", "segment",
                                       "revenue", "share"])
