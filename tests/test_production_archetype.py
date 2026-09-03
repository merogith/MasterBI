"""The fourth archetype: businesses that make physical things.

Manufacturing and food production both reached a generator by approximating
onto `ecommerce`, and both said in their own taxonomy note exactly what that
cost them — *"what is missing is the capacity ceiling, not the revenue shape"*
and *"yield, shelf life and line efficiency are not simulated"*. Measured before
writing anything, the visible half was the same as the consultancy's in 4.2a: a
factory's dashboard carried **average order value, category returns and buyer
mix**.

Three things the transactional archetype cannot express — a ceiling, OEE
decomposed into its three losses, and stock that rolls forward — plus the
defects that measuring this one exposed in code shared with the other three.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.contract.gate import run_gate  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.datagen import production as P  # noqa: E402
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.spec.schema import GeneratorParams  # noqa: E402

SAMPLE = ROOT / "samples" / "orbis_works.json"

#: `food_production` reaches the archetype and is still an approximation:
#: yield and line efficiency are modelled, shelf life is not.
PRODUCTION_SECTORS = ("manufacturing", "food_production")


@pytest.fixture(scope="module")
def data():
    return GENERATORS["production"](load_profile(SAMPLE))


@pytest.fixture(scope="module")
def profile():
    return load_profile(SAMPLE)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_the_production_sectors_reach_the_production_generator() -> None:
    for sector in PRODUCTION_SECTORS:
        assert sectors.resolve_archetype(sector).value == "production"
    assert "production" in GENERATORS


def test_food_production_still_says_what_it_does_not_simulate() -> None:
    assert sectors.resolve_archetype("manufacturing").exact
    note = sectors.resolve_archetype("food_production").note
    assert note and "shelf life" in note


def test_the_vacuity_guard_is_satisfied_by_real_checks(data, profile) -> None:
    result = run_gate(dict(data.tables), profile, source="synthetic",
                      archetype="production")
    ran = {line.split(":")[0] for line in result.checks}
    for name in ("gross revenue = units shipped x unit price",
                 "output never exceeds the capacity that was scheduled",
                 "OEE = availability x performance x quality",
                 "stock rolls forward"):
        assert name in ran, f"{name!r} did not run: {sorted(ran)}"


def test_every_production_sector_generates_and_reconciles() -> None:
    from kpi_maker.survey import build_profile

    for sector in PRODUCTION_SECTORS:
        for stage in ("early", "growth", "mature"):
            tables = GENERATORS["production"](
                build_profile({"business_model": sector, "stage": stage})).tables
            assert set(tables) >= {"production", "shipments", "inventory",
                                   "customers", "monthly_financials",
                                   "segment_financials", "headcount", "marketing"}


# --------------------------------------------------------------------------
# The three things this archetype can say and the transactional one cannot
# --------------------------------------------------------------------------

def test_units_times_price_is_the_revenue(data) -> None:
    s = data.tables["shipments"]
    assert np.allclose(s["gross_revenue"], s["units_shipped"] * s["unit_price"],
                       rtol=1e-6, atol=1e-6)


def test_the_capacity_ceiling_binds_and_is_never_breached(data) -> None:
    """Both halves, because either alone is satisfiable by a useless model.

    A ceiling nothing ever reaches is decoration: the archetype exists because
    a plant runs out of hours, so the test asserts that it does. And a ceiling
    that is breached is not a ceiling.
    """
    m = data.tables["production"]
    output = m["units_produced"] + m["units_scrapped"]
    assert (output <= m["capacity_units"] + 1e-6).all()
    assert (m["capacity_units"] <= m["nameplate_units"] + 1e-6).all()
    assert (m["runtime_hours"] <= m["planned_hours"] + 1e-6).all()

    last = m[m["month"] >= m["month"].max() - 11]
    at_capacity = (last["planned_hours"] >= P.NAMEPLATE_HOURS - 1e-6).sum()
    assert at_capacity >= 6, (
        f"only {at_capacity} line-months of the last year ran a full schedule; "
        f"a ceiling nothing reaches is not a constraint")
    assert at_capacity < len(last), "every month at capacity is not a plant"


def test_oee_is_the_product_of_its_three_losses(data) -> None:
    """Its whole value is that it decomposes.

    A plant at 75% needs to know whether the line was stopped, slow, or making
    scrap — three different owners — and a total that is not the product of the
    three is a different number wearing the same name.
    """
    m = data.tables["production"]
    assert np.allclose(m["oee"], m["availability"] * m["performance"] * m["quality"],
                       rtol=1e-9, atol=1e-9)
    made = m["units_produced"] + m["units_scrapped"]
    assert np.allclose(m["quality"], m["units_produced"] / made,
                       rtol=1e-6, atol=1e-6)
    weight = m["capacity_units"]
    assert 0.25 <= float((m["oee"] * weight).sum() / weight.sum()) <= 0.95


def test_stock_rolls_forward_and_never_goes_negative(data) -> None:
    inv = data.tables["inventory"]
    assert np.allclose(
        inv["closing_units"],
        inv["opening_units"] + inv["units_produced"] - inv["units_shipped"],
        rtol=1e-6, atol=1e-6)
    assert (inv["closing_units"] >= -1e-6).all()
    assert inv["closing_units"].sum() > 0

    # The ledger and the two operational tables have to agree, or stock is a
    # third quiet set of numbers rather than the balance between them.
    made = data.tables["production"].groupby("month")["units_produced"].sum()
    booked = inv.groupby("month")["units_produced"].sum()
    assert np.allclose(booked.reindex(made.index).fillna(0.0).to_numpy(),
                       made.to_numpy(), rtol=1e-6, atol=1e-6)


def test_a_quiet_month_is_scheduled_shorter_not_run_worse(data) -> None:
    """A sales problem must not appear in the plant manager's numbers.

    When demand is below what a line could run, the line is scheduled for fewer
    hours. Modelling it as poor performance instead would move a commercial
    problem onto an operational metric, which is the same category error as
    reading a services firm's bench time as a delivery failure.
    """
    m = data.tables["production"]
    quiet = m[m["planned_hours"] < P.NAMEPLATE_HOURS * 0.7]
    busy = m[m["planned_hours"] >= P.NAMEPLATE_HOURS - 1e-6]
    assert not quiet.empty and not busy.empty

    # Performance is a property of the line, so it must not differ between the
    # two — while scheduled capacity obviously does.
    assert abs(float(quiet["performance"].mean())
               - float(busy["performance"].mean())) < 0.03
    assert (float(quiet["capacity_units"].mean())
            < float(busy["capacity_units"].mean()) * 0.8)


def test_the_yield_problem_reaches_quality_and_margin(data) -> None:
    """Scrap is planted as units. Everything else is derived from it.

    Nothing in the generator writes down a quality rate or a margin effect: the
    premium line rejects more units, quality falls out of the count, and gross
    margin moves because scrapped units cost what they cost and earn nothing.
    """
    creep = next(a for a in data.anomalies if a.kind == "yield_loss")

    def scrap_rate(frame):
        made = frame["units_produced"] + frame["units_scrapped"]
        return float(frame["units_scrapped"].sum() / made.sum())

    def line_over_time(tables):
        m = tables["production"]
        line = m[m["product_family"] == creep.segment]
        months = sorted(m["month"].unique())
        first = line[line["month"].isin(months[:12])]
        last = line[line["month"].isin(months[-12:])]
        return scrap_rate(first), scrap_rate(last)

    # **Compared within the family over time, not against the other lines.**
    # The first version asserted that premium scraps more than the rest, which
    # it does with the anomaly switched off — its baseline reject rate is
    # nearly twice theirs by design — so the test passed with the plant
    # deleted. What the anomaly does is make the same line get worse.
    before, after = line_over_time(data.tables)
    assert after > before * 1.8, f"{before:.4f} -> {after:.4f}"

    quiet = GENERATORS["production"](
        load_profile(SAMPLE), GeneratorParams(inject_anomalies=False)).tables
    q_before, q_after = line_over_time(quiet)
    assert abs(q_after / q_before - 1.0) < 0.25, f"{q_before:.4f} -> {q_after:.4f}"

    m = data.tables["production"]
    affected = m[m["product_family"] == creep.segment]
    others = m[m["product_family"] != creep.segment]
    assert float(affected["quality"].mean()) < float(others["quality"].mean())


def test_revenue_and_accounts_land_on_the_profile(data, profile) -> None:
    fin = data.tables["monthly_financials"]
    assert abs(fin["revenue"].iloc[-12:].sum() / profile.financials.revenue - 1) < 0.02
    active = int(data.tables["customers"]["is_active"].sum())
    assert abs(active / profile.market.customer_count - 1) < 0.20


# --------------------------------------------------------------------------
# Three defects measuring this archetype exposed, two of them shared
# --------------------------------------------------------------------------

def test_cost_of_sales_is_charged_per_family(data) -> None:
    """One unit cost across families made a mix swing look like a cost problem.

    Family prices span 0.42 to 4.80, so a month that happened to ship a lot of
    the cheap line reported a **negative gross margin** — caught by the gate on
    the first run, which is what the archetype band is for.
    """
    fin = data.tables["monthly_financials"].set_index("month")
    assert fin["gross_margin_pct"].min() > 0.10
    assert fin["gross_margin_pct"].max() < 0.65
    # And the margin still moves, or a constant would pass the two lines above.
    assert fin["gross_margin_pct"].std() > 0.005

    # The property, not a band. A band is what the first version asserted and
    # it passed with one flat unit cost restored — that mutation bottoms out at
    # 12.3%, just inside. What actually goes wrong is that margin starts
    # tracking the *cheapest family's share of the month*, because a unit of the
    # 0.42 line and a unit of the 4.80 line are being costed the same. Measured:
    # -0.44 correlation as shipped, -0.92 with one cost.
    ship = data.tables["shipments"]
    cheapest = min(P.PRODUCT_FAMILIES, key=lambda f: P.PRODUCT_FAMILIES[f]["price"])
    share = (ship[ship["product_family"] == cheapest].groupby("month")
             ["gross_revenue"].sum() / ship.groupby("month")["gross_revenue"].sum())
    correlation = float(share.corr(fin["gross_margin_pct"].reindex(share.index)))
    assert correlation > -0.70, (
        f"gross margin tracks the {cheapest} family's share at {correlation:.2f} "
        f"— a mix swing is being read as a cost problem")


def test_cost_of_sales_follows_shipments_not_production(data) -> None:
    """Costing what the plant *made* expenses a stock build in the month it
    happens, so a factory running ahead of demand reported a negative gross
    margin. Good units are stock until they ship; scrap never becomes stock and
    is expensed as it happens, which is what keeps yield a margin question.
    """
    fin = data.tables["monthly_financials"].set_index("month")
    shipped = data.tables["shipments"].groupby("month")["units_shipped"].sum()
    made = data.tables["production"].groupby("month")["units_produced"].sum()

    # Compared against the window's own median rather than against zero, which
    # is what the first version did and why it survived its mutation: costing
    # production still left that month profitable, just much less so. Measured:
    # the heaviest build month runs 3.6% below median margin as shipped, and
    # 44% below it when production is expensed.
    build = (made - shipped.reindex(made.index).fillna(0.0)) / shipped
    heaviest = build.idxmax()
    assert float(build.loc[heaviest]) > 0.05, "no month builds enough stock to test"

    margin = fin["gross_margin_pct"]
    assert float(margin.loc[heaviest]) > float(margin.median()) * 0.85, (
        f"the month with the biggest stock build reports "
        f"{margin.loc[heaviest]:.1%} against a median of {margin.median():.1%} — "
        f"production is being expensed rather than capitalised")


def test_the_stated_cash_is_the_closing_balance(data, profile) -> None:
    """Three of four generators read it as the balance three years ago.

    `subscription.py` had always anchored the last month; the others added the
    profile's cash to a cumulative sum that started in the warm-up. On a plant
    funding a growing working-capital base that is not a rounding difference —
    Orbis opened its reported window at **-2.2M** against a stated 3.1M.
    """
    fin = data.tables["monthly_financials"]
    assert float(fin["cash"].iloc[-1]) == pytest.approx(
        profile.financials.cash, rel=1e-6)

    # And the same reading now holds across every archetype, which is the half
    # that stops it drifting back.
    for name in ("northwind_saas", "kestrel_retail", "halberd_consulting"):
        other = load_profile(ROOT / "samples" / f"{name}.json")
        archetype = sectors.resolve_archetype(
            other.business_model.type.value).value
        tables = GENERATORS[archetype](other).tables
        assert float(tables["monthly_financials"]["cash"].iloc[-1]) == pytest.approx(
            other.financials.cash, rel=1e-6), name


def test_free_cash_flow_is_lumpy_but_not_absurd(data) -> None:
    """It swung +112% one year and -107% the next, and the seasonal adjustment
    then quoted a third number, so one metric appeared on the same page as
    -2.3% and -106.6%. Two causes: finished goods valued from a cover
    assumption instead of the ledger that had already counted them, and
    receivables driven off a single month rather than trailing revenue.
    """
    fin = data.tables["monthly_financials"]
    margin = fin["free_cash_flow"] / fin["revenue"]
    assert margin.abs().max() < 0.75, f"worst month {margin.abs().max():.2f}"
    # Still lumpy — a plant's monthly cash genuinely is, and smoothing it into a
    # line would be the opposite mistake.
    assert margin.std() > 0.02


# --------------------------------------------------------------------------
# Exhibits
# --------------------------------------------------------------------------

def test_the_production_exhibits_build_and_the_others_stand_down(data) -> None:
    from kpi_maker.viz.charts import CHARTS

    tables = dict(data.tables)
    built = []
    for eid in ("oee_trend", "capacity_headroom", "scrap_by_family"):
        spec = CHARTS[eid].fn(tables)
        assert spec is not None, f"{eid} drew nothing on production tables"
        built.append(spec)
    assert {s.tab for s in built} == {"overview", "growth", "people"}

    saas = GENERATORS["saas"](load_profile(ROOT / "samples" / "northwind_saas.json"))
    for eid in ("oee_trend", "capacity_headroom", "scrap_by_family"):
        assert CHARTS[eid].fn(dict(saas.tables)) is None


def test_the_multi_series_exhibits_label_their_lines(data) -> None:
    """Three lines and no legend is a guessing game, and reading it on screen is
    what said so — the retention exhibit already turns the legend on."""
    from kpi_maker.viz.charts import CHARTS

    tables = dict(data.tables)
    for eid in ("oee_trend", "capacity_headroom"):
        spec = CHARTS[eid].fn(tables)
        assert spec is not None
        assert spec.figure.layout.showlegend is True, eid
        assert all(trace.name for trace in spec.figure.data), eid
