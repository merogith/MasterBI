"""Compare a seasonal business against its own season, not against last month.

`_trend_breaks` compares the slope of the last six months against the slope of
the six before it. For a business with a fourth-quarter peak that comparison is
mostly a reading of the calendar: a retailer's January is *always* worse than
its December, so every run whose history ends in the first months of the year
reports that revenue "has turned" when nothing has.

Measured on `kestrel_retail`, moving only the last month of history and
changing nothing else about the business:

    ends 2025-12   1 trend break    revenue_per_fte
    ends 2026-01   0
    ends 2026-02   3               active_buyers, sessions, stockout_days
    ends 2026-03   2               active_buyers, sessions
    ends 2026-06   1               ebitda_margin

Three findings in February and none in January, from the same company. The
generated samples all end in December — the one calendar position at which the
Q4 peak sits at the very end of the series and the bug is invisible — which is
why this survived: it is masked by a hardcoded end of history that is itself an
open bug, and it is *not* masked for anyone uploading their own data.

**The adjustment is estimated from the series, not read off the profile.**
`market.seasonality` is a survey answer, absent on an upload, and true of the
*business* rather than of any particular metric — gross margin percentage is
usually flat in a business whose revenue triples in December. What matters is
whether this series is seasonal, and the series can say.

Classical decomposition, and deliberately nothing fancier: a centred
twelve-month moving average as the trend, the average deviation of each
calendar month from it as the seasonal index, and the rest as remainder. STL
would fit better and would also mean a statsmodels dependency, a smoothing
parameter to choose, and a number nobody can reproduce by hand from the
figures in the appendix.

**Applied only when the series is measurably seasonal**, on Hyndman's seasonal
strength — `1 - Var(remainder) / Var(seasonal + remainder)` — at the
conventional 0.3. A flat series has a seasonal index made of noise, and
dividing by noise would invent trend breaks rather than remove them, so a
weakly seasonal series is left exactly as it was.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Two full years, so every calendar month is observed at least twice. One
#: year gives a single observation per month, which is not an average of
#: anything — the "seasonal index" would just be the series again.
MIN_MONTHS = 24

#: Hyndman's conventional cut for calling a series seasonal (FPP3 §4.3). Below
#: it the seasonal component is not distinguishable from the remainder, and
#: removing it would remove noise-shaped signal.
MIN_STRENGTH = 0.3

#: A multiplicative index only makes sense on a series that stays comfortably
#: away from zero — a margin that crosses zero would produce indices of
#: arbitrary size. Below this the additive form is used instead.
_MULTIPLICATIVE_FLOOR = 0.1


@dataclass
class Adjustment:
    """A seasonally adjusted series, and whether the adjustment was applied."""
    series: pd.Series
    applied: bool
    strength: float
    #: "multiplicative", "additive", or the reason nothing was done.
    method: str


def _is_monthly(series: pd.Series) -> bool:
    index = series.index
    return isinstance(index, pd.PeriodIndex) and index.freqstr.startswith("M")


def _trend(values: np.ndarray) -> np.ndarray:
    """Centred twelve-month moving average — NaN at the six ends, deliberately.

    An even window has no exact centre, so the standard 2x12 form averages two
    twelve-month windows offset by one, and the first and last six months have
    no full window at all.

    **Filling those ends is the trap, and the first attempt fell into it.**
    Holding the trend flat at the nearest computable value leaves the last six
    months' growth inside the "seasonal" ratio, which biases the index using
    exactly the months the detector is about to compare — `sessions` came out
    98% seasonal and still produced a trend break in three of six calendar
    positions. The index is estimated only where the trend is real, and then
    applied everywhere; that is the classical method and it has no ends
    problem.
    """
    frame = pd.Series(values)
    return frame.rolling(12, center=True).mean().rolling(
        2, center=True).mean().to_numpy()


def deseasonalise(series: pd.Series) -> Adjustment:
    """Remove the calendar from a monthly series, when there is one to remove."""
    if not _is_monthly(series):
        return Adjustment(series, False, 0.0, "not a monthly series")
    clean = series.dropna()
    if len(clean) < MIN_MONTHS:
        return Adjustment(series, False, 0.0,
                          f"{len(clean)} months, needs {MIN_MONTHS}")

    values = clean.to_numpy(dtype=float)
    trend = _trend(values)
    covered = ~np.isnan(trend)
    if covered.sum() < 12:
        return Adjustment(series, False, 0.0,
                          f"{int(covered.sum())} months have a full-year trend")

    months = clean.index.month.to_numpy()
    counts = pd.Series(months[covered]).value_counts()
    if len(counts) < 12 or counts.min() < 2:
        # One observation of a calendar month is not an average of anything:
        # the "index" for that month would be its own value, and dividing by it
        # would flatten the month to exactly the trend. Two years of history
        # gives one full year of trend coverage, which is one observation per
        # month — so two years is genuinely not enough to tell a season from a
        # wobble, and saying so beats adjusting by noise.
        return Adjustment(series, False, 0.0,
                          "needs two observations of every calendar month "
                          "inside the trend-covered region")

    multiplicative = bool(np.all(values > 0)
                          and np.all(np.abs(trend[covered]) > 0)
                          and abs(float(np.mean(values)))
                          > _MULTIPLICATIVE_FLOOR * float(np.std(values) + 1e-12))

    detrended = (values[covered] / trend[covered] if multiplicative
                 else values[covered] - trend[covered])
    covered_months = months[covered]
    grouped = pd.Series(detrended).groupby(covered_months)
    index = grouped.mean()
    # Normalise so the adjustment moves the shape of the year without moving
    # its level: a factor averaging 1.07 would quietly deflate every figure 7%.
    index = index / index.mean() if multiplicative else index - index.mean()
    seasonal = index.reindex(months).to_numpy(dtype=float)

    # **Strength is measured leave-one-out, and this is not a refinement.**
    # Scoring the index against the same points that produced it deflates the
    # remainder by construction: with two observations per month, pure noise
    # scores about 0.5 on Hyndman's formula, and a flat series with a little
    # jitter measured 0.42 — comfortably "seasonal" by any threshold, and
    # entirely fictional. Predicting each month from the *other* observations
    # of that month removes the self-fit, and noise then scores about zero.
    totals = grouped.transform("sum").to_numpy()
    n_month = grouped.transform("count").to_numpy()
    held_out = (totals - detrended) / (n_month - 1)
    remainder = (detrended / held_out if multiplicative
                 else detrended - held_out)
    variance = float(np.var(detrended))
    baseline = 1.0 if multiplicative else 0.0
    strength = 0.0 if variance <= 0 else max(
        0.0, min(1.0, 1.0 - float(np.mean((remainder - baseline) ** 2)) / variance))

    if strength < MIN_STRENGTH:
        return Adjustment(series, False, strength,
                          f"seasonal strength {strength:.2f} below {MIN_STRENGTH}")

    adjusted = values / seasonal if multiplicative else values - seasonal
    # Benchmark the adjusted series back to the raw one's level, the way a
    # statistical office benchmarks a seasonally adjusted series to its annual
    # total. Without it the multiplicative form leaves a uniform offset —
    # measured at +4.8% on a pure trend-times-season series — and a finding
    # would quote a figure five percent away from the one on the dashboard for
    # no reason a reader could ever discover.
    level = float(np.mean(values)) - float(np.mean(adjusted))
    adjusted = adjusted * (float(np.mean(values)) / float(np.mean(adjusted))) \
        if multiplicative and abs(float(np.mean(adjusted))) > 1e-12 \
        else adjusted + level
    return Adjustment(pd.Series(adjusted, index=clean.index), True, strength,
                      "multiplicative" if multiplicative else "additive")
