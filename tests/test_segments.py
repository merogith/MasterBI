"""Slicing a KPI by segment, and why this was a data-model problem first.

The plan sized 3.2 as a metric-layer change: add an optional `by` dimension so
a KPI carries per-segment series alongside the blended one. Measured before
writing any of it, against both archetypes:

    saas       segment-carrying tables: customers, mrr_movements
               KPIs sliceable by segment: 0 of 25
    ecommerce  segment-carrying tables: customers
               KPIs sliceable by segment: 0 of 18

**Zero.** Every metric reads `monthly_financials` — many only for its month
spine — and `monthly_financials` has no segment column by construction. A
per-segment NRR needs a per-segment denominator that nothing emitted. So the
first half of this item is a fact table, not a parameter.

`segment_financials` is that table, and it is built from **shares rather than
levels**: each segment's share of the dimension's activity is measured from the
table that records it, and the company's own revenue is split by those shares.
Shares sum to one by construction, so segment revenue sums to company revenue
exactly — which is what lets it be a Tier 1 identity rather than a tolerance.

The honest limit is asserted here too: costs are **not** split. Scaling COGS by
revenue share would assume every segment earns the same margin, and a margin
that is an assumption dressed as a measurement is the one failure this project
treats as unacceptable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.contract import run_gate  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import (  # noqa: E402
    compute,
    dimensions,
    levels,
    slice_tables,
)

ARCHETYPES = {
    "saas": ROOT / "samples" / "northwind_saas.json",
    "ecommerce": ROOT / "samples" / "kestrel_retail.json",
}


@pytest.fixture(scope="module", params=sorted(ARCHETYPES))
def run(request):
    """A generated company, its tables and its selected KPIs."""
    archetype = request.param
    profile = load_profile(ARCHETYPES[archetype])
    data = GENERATORS[archetype](profile)
    return archetype, profile, dict(data.tables), select(profile)


# --------------------------------------------------------------------------
# The fact table
# --------------------------------------------------------------------------

def test_every_archetype_emits_a_dimension_to_slice_by(run):
    archetype, _profile, tables, _kpis = run
    dims = dimensions(tables)
    assert dims, f"{archetype} offers no dimension at all"
    expected = {"saas": ["segment"], "ecommerce": ["category", "channel"]}
    assert dims == expected[archetype], \
        f"{archetype} sliceable by {dims}, expected {expected[archetype]}"


def test_segment_revenue_sums_to_company_revenue_exactly(run):
    """Not "close to". Built from shares so the residual is zero by design.

    A per-segment figure that does not add back is not a decomposition, it is a
    second and quieter set of numbers — and a reader who sums the segments and
    gets a different total has caught the product lying.
    """
    _archetype, _profile, tables, _kpis = run
    fin = tables["monthly_financials"].set_index("month")["revenue"]
    seg = tables["segment_financials"]

    for dimension, part in seg.groupby("dimension"):
        totals = part.groupby("month")["revenue"].sum()
        expected = fin.reindex(totals.index)
        assert np.allclose(totals.to_numpy(), expected.to_numpy(), rtol=1e-9), \
            f"{dimension} off by up to {(totals - expected).abs().max():,.2f}"
        shares = part.groupby("month")["share"].sum()
        assert np.allclose(shares.to_numpy(), 1.0, atol=1e-9)


def test_the_gate_enforces_the_split(run):
    """It is a Tier 1 identity, so a bad split stops the run rather than
    reaching a board pack."""
    archetype, profile, tables, _kpis = run
    result = run_gate(tables, profile, source="synthetic", archetype=archetype)
    assert any("segment revenue sums to company revenue: pass" in c
               for c in result.checks), \
        "the identity did not run, so this test proves nothing"


def test_a_broken_split_is_refused(run):
    """The check has to be able to fail, or it is decoration."""
    from kpi_maker.contract import ReconciliationError

    archetype, profile, tables, _kpis = run
    broken = dict(tables)
    seg = broken["segment_financials"].copy()
    seg.loc[seg.index[0], "revenue"] *= 1.5
    broken["segment_financials"] = seg

    with pytest.raises(ReconciliationError, match="segment revenue"):
        run_gate(broken, profile, source="synthetic", archetype=archetype)


def test_the_split_covers_only_the_reported_window(run):
    """The warm-up is trimmed from every time series, this one included.

    `subscription.py` trims by an explicit list of table names, so a new table
    keeps its warm-up unless someone remembers — and 24 months of pre-history
    in one table and not the others is a join waiting to go wrong.
    """
    _archetype, _profile, tables, _kpis = run
    months = set(tables["monthly_financials"]["month"])
    assert set(tables["segment_financials"]["month"]) <= months


# --------------------------------------------------------------------------
# Slicing
# --------------------------------------------------------------------------

def test_a_slice_carries_revenue_but_not_invented_costs(run):
    """The line between measurement and assumption, asserted.

    Revenue per segment is measured. COGS per segment is not, and splitting it
    by revenue share would assume uniform margins across segments — a number
    that looks right and is not. A metric needing it must go without.
    """
    _archetype, _profile, tables, _kpis = run
    dimension = dimensions(tables)[0]
    level = levels(tables, dimension)[0]

    sliced = slice_tables(tables, dimension, level)
    fin = sliced["monthly_financials"]

    assert "revenue" in fin.columns
    for invented in ("cogs", "gross_profit", "ebitda", "total_opex"):
        assert invented not in fin.columns, \
            f"{invented} was split across segments, which nothing measures"


def test_a_slice_drops_tables_that_do_not_carry_the_dimension(run):
    """Sharing them would silently mix grains.

    Company-wide headcount inside a per-segment revenue-per-head produces a
    number that looks fine and means nothing. Dropping the table makes the
    metric say it needs one, which is the truth for this slice.
    """
    _archetype, _profile, tables, _kpis = run
    dimension = dimensions(tables)[0]
    level = levels(tables, dimension)[0]
    sliced = slice_tables(tables, dimension, level)

    assert "headcount" not in sliced, \
        "company-wide headcount leaked into a single segment's tables"
    for name, frame in sliced.items():
        if name == "monthly_financials":
            continue
        assert dimension in frame.columns
        assert set(frame[dimension].unique()) <= {level}


# --------------------------------------------------------------------------
# The metric layer
# --------------------------------------------------------------------------

def test_kpis_carry_a_per_segment_series(run):
    """The thing that was zero before any of this."""
    _archetype, profile, tables, kpis = run
    results = compute(kpis, tables, profile, by=dimensions(tables))

    segmented = [r for r in results if r.segmented]
    assert len(segmented) >= 5, \
        f"only {len(segmented)} KPIs could be sliced: " \
        f"{[r.kpi.id for r in segmented]}"

    for result in segmented:
        for dimension in result.dimensions:
            assert set(result.by_segment[dimension]) <= set(
                levels(tables, dimension))


def test_the_blended_series_is_never_replaced(run):
    """`by` adds a view, it does not swap one in.

    A segment that behaves differently from the average is the finding, and you
    cannot see that without both numbers.
    """
    _archetype, profile, tables, kpis = run
    blended = {r.kpi.id: r.current for r in compute(kpis, tables, profile)
               if r.computed}
    with_segments = {r.kpi.id: r.current
                     for r in compute(kpis, tables, profile,
                                      by=dimensions(tables)) if r.computed}
    assert blended == with_segments


def test_an_additive_metric_sums_back_to_the_blended_figure(run):
    """Where a KPI is a sum, the segments must add up to it.

    Not true of every metric — a rate does not sum, and asserting that it did
    would be arithmetic nonsense — so this is checked on revenue, which is the
    one the split is built from.
    """
    archetype, profile, tables, kpis = run
    results = compute(kpis, tables, profile, by=dimensions(tables))

    additive = {"net_revenue", "revenue_ttm", "arr", "orders_count"}
    checked = 0
    for result in results:
        if result.kpi.id not in additive or not result.segmented:
            continue
        for dimension in result.dimensions:
            parts = [v for v in result.current_by_segment(dimension).values()
                     if v is not None]
            if not parts or result.current is None:
                continue
            assert sum(parts) == pytest.approx(result.current, rel=1e-6), \
                f"{result.kpi.id} by {dimension}: parts {sum(parts):,.0f} " \
                f"vs blended {result.current:,.0f}"
            checked += 1

    if archetype == "saas":
        # Nothing to check, for a reason worth stating: `arr` reads the `arr`
        # *column* of `monthly_financials`, and a slice carries revenue alone.
        # So the subscription pack's additive metric correctly does not survive
        # slicing rather than surviving it with a made-up denominator.
        assert checked == 0
        pytest.skip("no additive subscription KPI survives slicing, by design")
    assert checked > 0, "no additive metric was segmented, so nothing was checked"


def test_a_blended_number_can_hide_a_segment(run):
    """The reason any of this exists.

    `northwind_saas`'s own story is "a third of everything won is given
    straight back — and the churn is concentrated in one segment the blended
    number completely hides". Blended NRR reads 103%; SMB is at 81%. Until
    this item the engine could not express that at all.
    """
    archetype, profile, tables, kpis = run
    if archetype != "saas":
        pytest.skip("the retention story belongs to the subscription sample")

    results = compute(kpis, tables, profile, by=["segment"])
    nrr = next((r for r in results if r.kpi.id == "nrr"), None)
    assert nrr is not None and nrr.segmented, "NRR was not sliced by segment"

    per_segment = nrr.current_by_segment("segment")
    assert nrr.current is not None and nrr.current > 1.0, \
        "the blended figure should look healthy — that is the point"
    assert min(v for v in per_segment.values() if v is not None) < 0.95, \
        f"no segment is materially worse than the blend: {per_segment}"


def test_no_dimension_means_exactly_the_previous_behaviour(run):
    """An upload with no `segment_financials` must be untouched by all of this."""
    _archetype, profile, tables, kpis = run
    without = {k: v for k, v in tables.items() if k != "segment_financials"}

    assert dimensions(without) == []
    results = compute(kpis, without, profile, by=dimensions(without))
    assert all(not r.by_segment for r in results)
    assert any(r.computed for r in results)
