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

def month_range(history_months: int, warmup: int = WARMUP_MONTHS):
    """Every month to simulate, and the slice of it the user will see."""
    total = history_months + warmup
    months = pd.period_range(end=pd.Period("2025-12", freq="M"),
                             periods=total, freq="M")
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
