"""The fifth archetype, and the one that closes 4.2: platforms that take a cut.

`marketplace` reached a generator by approximating onto `ecommerce`, and its own
taxonomy note named what that lost: *"the transactional archetype models the
demand side; take rate and the supply side are not simulated"*. Measured before
writing anything, the visible half was the same as the other two archetypes': a
platform's dashboard carried **average order value, category returns and buyer
mix**.

One thing makes this archetype different from the other four, and everything
here follows from it: a marketplace has **two books**. It fails from the seller
side far more often than from the buyer side, and in the revenue line the two
are indistinguishable.

The last group of tests closes Phase 4.2 rather than this item — all five
archetypes, and the properties that have to hold across every one of them.
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
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.spec.schema import GeneratorParams  # noqa: E402

SAMPLE = ROOT / "samples" / "lumen_exchange.json"

#: `real_estate` reaches the archetype because an agency earns a fee on a
#: transaction it never owns, and stays an approximation because a landlord's
#: economics are not modelled.
MARKETPLACE_SECTORS = ("marketplace", "real_estate")


@pytest.fixture(scope="module")
def data():
    return GENERATORS["marketplace"](load_profile(SAMPLE))


@pytest.fixture(scope="module")
def profile():
    return load_profile(SAMPLE)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_the_marketplace_sectors_reach_the_marketplace_generator() -> None:
    for sector in MARKETPLACE_SECTORS:
        assert sectors.resolve_archetype(sector).value == "marketplace"
    assert "marketplace" in GENERATORS


def test_real_estate_still_says_what_it_does_not_simulate() -> None:
    assert sectors.resolve_archetype("marketplace").exact
    note = sectors.resolve_archetype("real_estate").note
    assert note and "landlord" in note


def test_logistics_deliberately_did_not_move() -> None:
    """An asset-owning haulier is not a two-sided market.

    "Logistics brokerage" is genuinely marketplace-shaped and the sector as
    labelled is not — its aliases are haulage, freight and courier. Claiming it
    would be the kind of coverage that reads well and models the wrong business.
    """
    assert sectors.resolve_archetype("logistics").value == "ecommerce"


def test_the_vacuity_guard_is_satisfied_by_real_checks(data, profile) -> None:
    result = run_gate(dict(data.tables), profile, source="synthetic",
                      archetype="marketplace")
    ran = {line.split(":")[0] for line in result.checks}
    for name in ("net revenue = GMV x take rate",
                 "matches never exceed either side of the market",
                 "net revenue ties to the P&L"):
        assert name in ran, f"{name!r} did not run: {sorted(ran)}"


# --------------------------------------------------------------------------
# Which number is revenue
# --------------------------------------------------------------------------

def test_the_take_is_the_revenue_and_gmv_is_not(data, profile) -> None:
    """The mistake the whole archetype is arranged to prevent.

    A platform that reports GMV where it means the take describes a business
    many times its real size at a fraction of its real margin. `revenue` is the
    take; GMV lives in its own table and is materially larger.
    """
    gmv = data.tables["gmv"]
    fin = data.tables["monthly_financials"]

    assert np.allclose(gmv["net_revenue"],
                       gmv["gross_merchandise_value"] * gmv["take_rate"],
                       rtol=1e-6, atol=1e-6)

    take = gmv.groupby("month")["net_revenue"].sum()
    revenue = fin.set_index("month")["revenue"]
    assert np.allclose(take.reindex(revenue.index).fillna(0.0).to_numpy(),
                       revenue.to_numpy(), rtol=1e-6, atol=1e-6)

    # And the profile's stated revenue is the take, not the market's size.
    assert abs(revenue.iloc[-12:].sum() / profile.financials.revenue - 1) < 0.02
    value = gmv[gmv["month"] >= gmv["month"].max() - 11][
        "gross_merchandise_value"].sum()
    assert value > profile.financials.revenue * 3


def test_the_margin_is_measured_on_the_take(data) -> None:
    """55% and not 8%, which is the difference between an agent and a merchant."""
    fin = data.tables["monthly_financials"]
    assert 0.55 <= fin["gross_margin_pct"].mean() <= 0.95


# --------------------------------------------------------------------------
# Two books, and a market between them
# --------------------------------------------------------------------------

def test_matches_never_exceed_either_side(data) -> None:
    liq = data.tables["liquidity"]
    assert (liq["matches"] <= liq["supply_listings"] + 1e-6).all()
    assert (liq["matches"] <= liq["demand_requests"] + 1e-6).all()
    live = liq[liq["demand_requests"] > 0]
    assert np.allclose(live["match_rate"],
                       live["matches"] / live["demand_requests"],
                       rtol=1e-6, atol=1e-6)


def test_the_market_actually_clears(data) -> None:
    """Neither a market that never clears nor one that always does.

    Both extremes satisfy `matches <= min(supply, demand)` and neither is a
    marketplace: the first has nothing to report and the second has no
    constraint to be short of.
    """
    liq = data.tables["liquidity"]
    blended = float(liq["matches"].sum() / liq["demand_requests"].sum())
    assert blended > 0.25, blended

    # Measured against the **scarcer side**, not against demand, and that is the
    # half that catches anything. A ceiling of 0.90 on the demand-side rate is
    # satisfied by a frictionless market whenever supply is short, which it
    # usually is — raising match efficiency to 0.99 left the blended rate at
    # 0.55 and the first version of this test green. A listing and a request
    # that never find each other are the normal case in every market that has
    # ever existed, and a model without that has no market in it.
    scarcer = np.minimum(liq["supply_listings"], liq["demand_requests"]).sum()
    assert float(liq["matches"].sum() / scarcer) < 0.90


def test_no_category_is_structurally_starved(data) -> None:
    """Supply is drawn against share/capacity, and it has to be.

    Drawing sellers on the same category shares as buyers leaves the categories
    whose listings absorb fewer matches permanently short: premium listings take
    1.4 matches against long tail's 5.1, so premium cleared **11% of its
    requests in every month of the run** — a famine no anomaly can deepen and no
    recovery can end, in the category whose transactions are worth the most.
    """
    liq = data.tables["liquidity"]
    ratios = liq.groupby("category").apply(
        lambda g: g["supply_listings"].sum() / max(g["demand_requests"].sum(), 1e-9),
        include_groups=False)
    assert ratios.min() > 0.35, ratios.round(2).to_dict()
    # ...and supply is still the scarcer side, which is what the shortage
    # anomaly needs in order to bite at all.
    assert ratios.median() < 1.20, ratios.round(2).to_dict()


def test_sellers_leave_when_they_do_not_sell(data) -> None:
    """Crediting a match to the whole category made churn exactly zero.

    Every live seller had `last_active` bumped every month, so **578 of 578**
    read as active and the lapse branch could never fire. A seller who never
    sells is precisely the one who leaves.
    """
    suppliers = data.tables["suppliers"]
    active = int(suppliers["is_active"].sum())
    assert 0 < active < len(suppliers), f"{active} of {len(suppliers)}"
    assert active / len(suppliers) < 0.90


def test_a_supply_shortage_shows_up_as_a_supply_shortage(data) -> None:
    """The story only this archetype can tell.

    Listings in one category fall while demand keeps arriving. In the revenue
    line that is indistinguishable from weak demand; in the liquidity table it
    is not, and it is the one a platform can still act on.
    """
    shortage = next(a for a in data.anomalies if a.kind == "supply_shortage")
    liq = data.tables["liquidity"]
    months = sorted(liq["month"].unique())
    window = set(months[max(shortage.start_month, 0):
                        min(shortage.end_month, len(months) - 1) + 1])

    affected = liq[liq["category"] == shortage.segment]
    inside = affected[affected["month"].isin(window)]
    outside = affected[~affected["month"].isin(window)]
    assert not inside.empty and not outside.empty

    def rate(frame):
        return float(frame["matches"].sum() / frame["demand_requests"].sum())

    assert rate(inside) < rate(outside) * 0.75, (
        f"{rate(outside):.3f} outside vs {rate(inside):.3f} inside")

    # Demand did not fall — that is the whole distinction.
    per_month = lambda f: float(f["demand_requests"].sum() / f["month"].nunique())  # noqa: E731
    assert per_month(inside) > per_month(outside) * 0.75

    quiet = GENERATORS["marketplace"](
        load_profile(SAMPLE), GeneratorParams(inject_anomalies=False)).tables
    q = quiet["liquidity"]
    q = q[q["category"] == shortage.segment]
    q_in = q[q["month"].isin(window)]
    q_out = q[~q["month"].isin(window)]
    assert rate(q_in) > rate(q_out) * 0.85, "the shortage fires without an anomaly"


def test_take_rate_erosion_is_visible_on_the_category_that_conceded(data) -> None:
    """Stated per category rather than backed out of revenue.

    A rate derived from revenue would absorb the anomaly and report a smaller
    business at an unchanged commission, which is the opposite of what happened.
    """
    pressure = next(a for a in data.anomalies if a.kind == "take_rate_pressure")
    gmv = data.tables["gmv"]
    months = sorted(gmv["month"].unique())
    after = set(months[max(pressure.start_month, 0):])

    affected = gmv[gmv["category"] == pressure.segment]

    def rate(frame):
        return float(frame["net_revenue"].sum()
                     / frame["gross_merchandise_value"].sum())

    before = affected[~affected["month"].isin(after)]
    assert rate(affected[affected["month"].isin(after)]) < rate(before) * 0.92

    # Per category, not pooled. Pooling the other three compares a *blended*
    # rate across two periods whose category mix differs — the premium shortage
    # moves weight between categories with different commissions — so it read a
    # 2.4% move where every stated rate was unchanged. Mix confounded with rate
    # is the same error the archetype's own concentration finding exists to
    # avoid.
    for category, group in gmv[gmv["category"] != pressure.segment].groupby(
            "category"):
        assert rate(group[group["month"].isin(after)]) == pytest.approx(
            rate(group[~group["month"].isin(after)]), rel=1e-6), category


def test_the_stated_cash_is_the_closing_balance(data, profile) -> None:
    fin = data.tables["monthly_financials"]
    assert float(fin["cash"].iloc[-1]) == pytest.approx(
        profile.financials.cash, rel=1e-6)


# --------------------------------------------------------------------------
# Exhibits
# --------------------------------------------------------------------------

def test_the_marketplace_exhibits_build_and_the_others_stand_down(data) -> None:
    from kpi_maker.viz.charts import CHARTS

    tables = dict(data.tables)
    built = []
    for eid in ("gmv_and_take", "liquidity_trend", "take_by_category"):
        spec = CHARTS[eid].fn(tables)
        assert spec is not None, f"{eid} drew nothing on marketplace tables"
        built.append(spec)
    assert {s.tab for s in built} == {"overview", "growth", "people"}

    saas = GENERATORS["saas"](load_profile(ROOT / "samples" / "northwind_saas.json"))
    for eid in ("gmv_and_take", "liquidity_trend", "take_by_category"):
        assert CHARTS[eid].fn(dict(saas.tables)) is None


def test_a_secondary_axis_leaves_room_for_its_own_labels(data) -> None:
    """`_base_layout` cannot reach an axis a builder adds afterwards.

    It sets `automargin` on `yaxis` and `xaxis`, and every chart that overlays a
    second axis creates it later — so on the take-rate exhibit the right-hand
    ticks rendered as a column of single characters: 1, 1, 1, 1. Three charts
    across three archetypes had it.
    """
    from kpi_maker.viz.charts import CHARTS

    for eid, tables in (("gmv_and_take", dict(data.tables)),):
        spec = CHARTS[eid].fn(tables)
        assert spec is not None
        assert spec.figure.layout.yaxis2.automargin is True, eid

    # The other two live on other archetypes, so assert the rule at the source
    # rather than generating three datasets to check three flags.
    source = (ROOT / "kpi_maker" / "viz" / "charts.py").read_text(encoding="utf-8")
    overlays = source.count('yaxis2=dict(overlaying="y"')
    assert overlays > 0
    assert source.count("automargin=True") >= overlays + 2, (
        "a chart overlays a second axis without asking for automargin")


# --------------------------------------------------------------------------
# Phase 4.2 as a whole
# --------------------------------------------------------------------------

ARCHETYPES = ("saas", "ecommerce", "project", "production", "marketplace")


def test_every_archetype_is_registered_and_checkable() -> None:
    """The premise the vacuity guard rests on, asserted once for all five.

    `_guard_against_vacuity` refuses a generated archetype that contributes no
    structural identity — which only protects anything if every archetype has
    some. A sixth added without them would be refused at runtime; this says so
    at build time instead.
    """
    from kpi_maker.contract.identities import CHECKS, Tier
    from kpi_maker.contract.schemas import SCHEMAS_BY_ARCHETYPE

    assert set(GENERATORS) == set(ARCHETYPES)
    assert set(SCHEMAS_BY_ARCHETYPE) == set(ARCHETYPES)

    for archetype in ARCHETYPES:
        structural = [c for c in CHECKS
                      if c.tier is Tier.structural
                      and c.archetypes is not None
                      and archetype in c.archetypes]
        assert len(structural) >= 4, (
            f"{archetype} has {len(structural)} structural identities of its "
            f"own; the vacuity guard would refuse it")


def test_every_archetype_has_something_to_look_at() -> None:
    """An archetype with no exhibit is one nobody will use.

    Moving a sector onto its own generator correctly removes the transactional
    charts it was borrowing, so each of the three new ones had to bring its own.
    """
    from kpi_maker.viz.charts import CHARTS

    owned = {
        "saas": ("arr_trend", "arr_bridge", "cohort_heatmap"),
        "ecommerce": ("revenue_orders", "aov_conversion", "buyer_mix"),
        "project": ("utilisation_realisation", "backlog_cover",
                    "service_line_margin"),
        "production": ("oee_trend", "capacity_headroom", "scrap_by_family"),
        "marketplace": ("gmv_and_take", "liquidity_trend", "take_by_category"),
    }
    assert set(owned) == set(ARCHETYPES)
    for archetype, exhibits in owned.items():
        for eid in exhibits:
            assert eid in CHARTS, f"{archetype} names a missing exhibit {eid!r}"


def test_every_archetype_has_its_own_financial_priors_and_margin_band() -> None:
    """Applied to twenty sectors, one set of numbers describes a SaaS company.

    `saas` is deliberately absent from the archetype tables — it falls through
    to the stage tables unchanged — so the assertion is that everything *else*
    has its own, and that the four disagree rather than being four copies.
    """
    from kpi_maker.contract.identities import MARGIN_BANDS
    from kpi_maker.survey import defaults as D

    for archetype in ARCHETYPES:
        if archetype != "saas":
            assert archetype in D.ARCHETYPE_GROSS_MARGIN_BY_STAGE, archetype
            assert archetype in D.ARCHETYPE_OPEX_BY_STAGE, archetype
        assert archetype in MARGIN_BANDS, archetype

    margins = {a: D.gross_margin_for(a, "growth") for a in ARCHETYPES}
    assert len(set(margins.values())) == len(ARCHETYPES), margins
    # And they are ordered the way the cost of sales says they should be.
    assert margins["production"] < margins["project"] < margins["ecommerce"]
    # The two agent-ish models sit above the three that own what they sell.
    # Which of *them* is higher is not something to assert: a platform's cost of
    # sales is payment processing and a software vendor's is hosting and
    # support, and the first of those is genuinely the smaller. The first
    # version ranked them and was wrong by a point.
    assert margins["ecommerce"] < min(margins["marketplace"], margins["saas"])


def test_every_declared_sector_still_runs_on_its_archetype() -> None:
    """The check that would have caught the eight crashing business models.

    Re-asserted here because 4.2 moved seven sectors between archetypes, and a
    sector pointing at a generator that does not exist degrades silently.
    """
    from kpi_maker.profile import taxonomy

    for sector in taxonomy.load().sectors:
        assert sector.archetype in GENERATORS, (
            f"{sector.id} names archetype {sector.archetype!r}, which no "
            f"generator provides")
