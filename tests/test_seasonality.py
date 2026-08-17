"""Compare a seasonal business against its own season.

`_trend_breaks` compared the last six months' slope against the six before it,
which for a retailer is mostly a reading of the calendar: January is always
worse than December, so the finding fires because of when you asked.

Measured on `kestrel_retail` with **one dataset and one company**, moving only
the last month of history across twelve consecutive end months — the controlled
version of the experiment, so the business is identical and only the calendar
position changes:

    e-commerce   23 trend breaks -> 16
      active_buyers          seasonal 1.00    4 -> 0
      sessions               seasonal 0.99    4 -> 0
      revenue_per_fte        seasonal 0.88    1 -> 0
      inventory_turns        seasonal 0.62    4 -> 3
      free_cash_flow_margin  seasonal 0.99    1 -> 4
      every metric measuring 0.00              unchanged

    SaaS         48 -> 48, the same four findings in all twelve months

The last two rows are the ones that say the adjustment is doing the right
thing rather than merely doing less. A season **masks** as well as invents:
free cash flow's Q4 working-capital release hid a deterioration that shows up
in four windows once the calendar is taken out. And nothing that measures as
unseasonal moves at all — the B2B sample produces an identical finding set
before and after, in every one of the twelve calendar positions.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kpi_maker.datagen.base as datagen_base  # noqa: E402
import kpi_maker.datagen.ecommerce as ecommerce  # noqa: E402
import kpi_maker.insight.detectors as detectors  # noqa: E402
from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.insight.seasonality import (  # noqa: E402
    MIN_STRENGTH,
    Adjustment,
    deseasonalise,
)
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute  # noqa: E402

# The retail year the generator plants: Black Friday and Christmas carry it,
# January is the hangover. Used here as an independent copy rather than
# imported, so a test does not pass because it shares the bug it is checking.
RETAIL_YEAR = np.array([0.72, 0.74, 0.88, 0.90, 0.95, 0.92,
                        0.88, 1.02, 0.98, 1.10, 1.65, 1.86])


def _months(n: int, end: str = "2026-02") -> pd.PeriodIndex:
    return pd.period_range(end=pd.Period(end, freq="M"), periods=n, freq="M")


def _seasonal(n: int = 36, growth: float = 1.01, end: str = "2026-02",
              noise: float = 0.0, seed: int = 0) -> pd.Series:
    index = _months(n, end)
    trend = 100.0 * growth ** np.arange(n)
    factors = RETAIL_YEAR[[p.month - 1 for p in index]]
    values = trend * factors
    if noise:
        values = values * (1 + np.random.default_rng(seed).normal(0, noise, n))
    return pd.Series(values, index=index)


# --------------------------------------------------------------------------
# The decomposition itself
# --------------------------------------------------------------------------

def test_the_calendar_comes_out_and_the_level_stays():
    raw = _seasonal()
    result = deseasonalise(raw)
    assert result.applied and result.strength > 0.9

    # The seasonal swing is 158% of the trough. What is left must be small
    # enough that the detector's 2% slope-difference gate cannot see it.
    trend = 100.0 * 1.01 ** np.arange(len(raw))
    residual = result.series.to_numpy() / trend - 1.0
    assert residual.max() - residual.min() < 0.02, \
        f"seasonality survived the adjustment: {residual.max() - residual.min():.3f}"

    # And the adjusted series must sit at the same level as the raw one, or a
    # finding quotes a figure that is nowhere on the dashboard.
    assert float(result.series.mean()) == pytest.approx(float(raw.mean()))


def test_a_flat_series_is_not_called_seasonal():
    """The measure has to be leave-one-out, and this is the test that says so.

    Scoring the seasonal index against the same points that produced it
    deflates the remainder by construction: with two observations per calendar
    month, pure noise scores about 0.5. A flat series with 3% jitter measured
    0.42 before the correction — comfortably "seasonal" by any threshold, and
    entirely fictional.
    """
    index = _months(36)
    trend = 100.0 * 1.01 ** np.arange(36)
    noisy = pd.Series(trend * (1 + np.random.default_rng(7).normal(0, 0.03, 36)),
                      index=index)
    result = deseasonalise(noisy)
    assert not result.applied, f"noise scored {result.strength:.2f} as seasonality"
    assert result.strength < MIN_STRENGTH


def test_two_years_cannot_tell_a_season_from_a_wobble():
    """One observation of a calendar month is not an average of anything.

    A centred twelve-month trend covers only the middle of a series, so two
    years of history leaves exactly one observation per month — and dividing a
    month by itself flattens it onto the trend, which would manufacture a
    perfectly smooth series out of nothing.
    """
    assert not deseasonalise(_seasonal(n=24)).applied
    assert deseasonalise(_seasonal(n=36)).applied


def test_a_percentage_that_crosses_zero_is_adjusted_additively():
    """A multiplicative index needs a series that stays away from zero; a
    margin swinging through it would produce factors of arbitrary size."""
    index = _months(36)
    swing = np.array([-0.06, -0.05, -0.02, 0.0, 0.01, 0.0,
                      -0.01, 0.01, 0.02, 0.05, 0.18, 0.22])
    series = pd.Series(swing[[p.month - 1 for p in index]] + 0.01, index=index)
    result = deseasonalise(series)
    assert result.applied and result.method == "additive"
    assert float(result.series.mean()) == pytest.approx(float(series.mean()))


def test_a_series_with_no_month_index_is_left_alone():
    plain = pd.Series(range(36))
    result = deseasonalise(plain)
    assert not result.applied and result.series is plain


# --------------------------------------------------------------------------
# What the detector does with it
# --------------------------------------------------------------------------

@dataclass
class _Kpi:
    id: str = "seasonal_metric"
    name: str = "Seasonal Metric"
    unit: str = "currency"

    class _Direction:
        value = "higher_is_better"
    direction = _Direction()

    class _Tier:
        value = 1
    tier = _Tier()


@dataclass
class _Result:
    series: Optional[pd.Series] = None
    kpi: _Kpi = field(default_factory=_Kpi)
    computed: bool = True


def test_a_fourth_quarter_peak_is_not_a_trend_break():
    """The finding the plan names: a Q4 retail spike reading as an inflection.

    History ending in February puts the Q4 peak and the January collapse
    inside the recent window and the flat spring in the prior one, which is
    the worst calendar position for the raw comparison.
    """
    series = _seasonal(end="2026-02")
    assert detectors._trend_breaks([_Result(series=series)]) == []

    # And the same series is a trend break without the adjustment, so the
    # assertion above is not passing for some unrelated reason.
    real = detectors.deseasonalise
    detectors.deseasonalise = lambda s: Adjustment(s, False, 0.0, "off")
    try:
        raw = detectors._trend_breaks([_Result(series=series)])
    finally:
        detectors.deseasonalise = real
    assert raw, "the raw comparison did not fire, so this test proves nothing"


def test_a_real_turn_still_reports_under_the_same_season():
    """Removing the calendar must not remove the news."""
    series = _seasonal(end="2026-02")
    turned = series.copy()
    # Six months of decline underneath an unchanged seasonal pattern.
    turned.iloc[-6:] = turned.iloc[-6:] * np.linspace(0.97, 0.70, 6)
    found = detectors._trend_breaks([_Result(series=turned)])
    assert found, "a genuine reversal was adjusted away"
    assert "seasonally adjusted" in found[0].statement
    assert found[0].evidence["seasonally_adjusted"] is True


def test_the_adjustment_says_so_only_when_it_happened():
    flat = pd.Series(np.linspace(100, 60, 36), index=_months(36))
    found = detectors._trend_breaks([_Result(series=flat)])
    for finding in found:
        assert "seasonally adjusted" not in finding.statement
        assert finding.evidence["seasonally_adjusted"] is False


# --------------------------------------------------------------------------
# Against the real generator
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def retail_history():
    """One retail company with a long history, so it can be windowed.

    Windowing one dataset is the controlled experiment: regenerating at each
    end month would also change the business, and then a difference in the
    findings says nothing about the calendar.
    """
    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    original = ecommerce.month_range

    def month_range(history_months, warmup=datagen_base.WARMUP_MONTHS):
        months = pd.period_range(end=pd.Period("2026-11", freq="M"),
                                 periods=47 + warmup, freq="M")
        return months, months[warmup:]

    ecommerce.month_range = month_range
    try:
        tables = dict(GENERATORS["ecommerce"](profile).tables)
    finally:
        ecommerce.month_range = original
    return profile, tables, select(profile)


@pytest.mark.parametrize("end", ["2026-02", "2026-03"])
def test_the_calendar_stops_choosing_the_findings(retail_history, end):
    """The two worst calendar positions, on real generated data."""
    profile, tables, kpis = retail_history
    cutoff = pd.Period(end, freq="M")
    windowed = {name: (frame[frame["month"] <= cutoff]
                       if "month" in getattr(frame, "columns", ()) else frame)
                for name, frame in tables.items()}
    results = compute(kpis, windowed, profile)

    adjusted = {f.id for f in detectors._trend_breaks(results)}

    real = detectors.deseasonalise
    detectors.deseasonalise = lambda s: Adjustment(s, False, 0.0, "off")
    try:
        raw = {f.id for f in detectors._trend_breaks(results)}
    finally:
        detectors.deseasonalise = real

    # The two most seasonal metrics in the retail sample — a shop's sessions
    # and buyers triple in December — stop reporting an inflection they never
    # had, and they were reporting one before.
    seasonal = {"trend_sessions", "trend_active_buyers"}
    assert seasonal & raw, f"{end}: the raw run found no seasonal artefact to fix"
    assert not (seasonal & adjusted), f"{end}: still calendar-driven: {adjusted}"

    # Whatever else changes, nothing that is not seasonal may be silenced.
    for finding_id in raw - adjusted - seasonal:
        kpi_id = finding_id[len("trend_"):]
        result = next((r for r in results if r.kpi.id == kpi_id), None)
        if result is None or result.series is None:
            continue
        assert deseasonalise(result.series.dropna()).applied, \
            f"{finding_id} was dropped without being seasonal"
