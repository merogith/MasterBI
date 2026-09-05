"""The three sector packs 4.2's archetypes made possible, and what authoring
them exposed in the engine that selects them.

4.2 built `project`, `production` and `marketplace` and measured afterwards
that **five tables per archetype were emitted and read by no KPI**:

    project      NEVER READ: backlog, customers, projects, segment_financials, timesheets
    production   NEVER READ: customers, inventory, production, segment_financials, shipments
    marketplace  NEVER READ: customers, gmv, liquidity, segment_financials, suppliers

So a consultancy, a factory and a platform each got nine P&L ratios and
nothing they manage themselves by. These tests pin the sheets that closed
that, and — more importantly — the three engine defects that only appeared
once a supplementary pack met a cross-sector one:

1. **`serves_objectives` is an unvalidated `List[str]`**, matched by string
   against `Objective` at the heaviest weight in `_score`. Two values that are
   not enum members had been shipping since their packs were authored.
2. **The pack order in `resolve_packs` carried no weight.** `[project,
   general]` is specific-first and nothing read it, so a sector's defining
   metric competed with a cross-sector ratio on equal terms.
3. **`core: true` seeds past the tier caps**, so the exec tier holds however
   many cores exist rather than the six it advertises. The e-commerce pack
   declared ten against a cap of six and had been shipping a retailer an
   inverted pyramid — 10 exec metrics over 6 functional ones.

Every test here was verified to fail with its fix reverted; the mutation is
named in each docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.authoring import lint_group, validate_sheet  # noqa: E402
from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.schema import KPI, Tier  # noqa: E402
from kpi_maker.kpi.selection import (  # noqa: E402
    NORTH_STAR_BY_MODEL,
    TIER_CAPS,
    load_library,
    pack_of,
    select,
)
from kpi_maker.metrics.engine import compute  # noqa: E402
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.profile.schema import Objective  # noqa: E402

#: (pack, archetype, sample, the sheets that pack exists to provide)
PACKS = [
    ("project", "project", "halberd_consulting", [
        "utilisation_rate", "realisation_rate", "average_bill_rate",
        "bench_ratio", "backlog_cover_months", "book_to_bill",
        "bookings_growth_yoy", "senior_hours_share", "backlog_growth_yoy"]),
    ("production", "production", "orbis_works", [
        "oee", "equipment_availability", "line_performance", "scrap_rate",
        "schedule_utilisation", "finished_stock_cover",
        "average_selling_price", "discount_rate", "return_rate",
        "shipped_volume_growth_yoy"]),
    ("marketplace", "marketplace", "lumen_exchange", [
        "gmv_ttm", "take_rate", "gmv_growth_yoy", "average_transaction_value",
        "match_rate", "supply_demand_ratio", "listing_sell_through",
        "demand_growth_yoy", "supply_growth_yoy"]),
]


@pytest.fixture(scope="module")
def computed():
    """Every sheet in every new pack, evaluated against its own archetype."""
    out = {}
    for pack, archetype, sample, _ in PACKS:
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        tables = dict(GENERATORS[archetype](profile).tables)
        library = load_library([pack], include_user=False)
        # Select nothing — compute the whole pack, so a sheet that is correct
        # but loses a cap is still checked. A record sheet that does not
        # produce a number is a definition, not a metric.
        kpi_set = select(profile)
        kpi_set.kpis = library
        out[pack] = {r.kpi.id: r for r in compute(kpi_set, tables, profile)}
    return out


# --------------------------------------------------------------------------
# The sheets exist, compute, and read the tables nothing read before
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pack,archetype,sample,sheets", PACKS,
                         ids=[p[0] for p in PACKS])
def test_every_sheet_in_the_pack_produces_a_number(pack, archetype, sample,
                                                   sheets, computed):
    """Mutation: point any expression at a column its table does not carry.

    The formula sandbox accepts an unknown `table.column` — it resolves at
    evaluation, not at parse — so a pack can validate cleanly and compute
    nothing, which is exactly what 4.3b's first real run found for two
    entity-grain sheets.
    """
    results = computed[pack]
    for kpi_id in sheets:
        result = results.get(kpi_id)
        assert result is not None, f"{kpi_id} is not in the {pack} pack"
        assert result.computed, f"{kpi_id} did not compute: {result.reason}"
        assert result.current is not None, f"{kpi_id} computed to None"


@pytest.mark.parametrize("pack,archetype,sample,sheets", PACKS,
                         ids=[p[0] for p in PACKS])
def test_the_pack_reads_the_tables_the_archetype_exists_for(pack, archetype,
                                                            sample, sheets):
    """The measurement that motivated 4.3b, asserted so it cannot come back.

    Mutation: delete the sheets that read a table — `timesheets`, `production`
    or `liquidity` — and the archetype is back to emitting data no metric
    reads.
    """
    from kpi_maker.formula.introspect import aggregate_columns

    read = set()
    for kpi in load_library([pack], include_user=False):
        if kpi.is_formula:
            read |= {ref.split(".")[0]
                     for ref in aggregate_columns(kpi.compute.expression or "")}

    emitted = set(GENERATORS[archetype](
        load_profile(ROOT / "samples" / f"{sample}.json")).tables)
    # Entity-grain tables (`customers`, `projects`, `suppliers`) genuinely
    # cannot back a monthly KPI — `SUM()` groups by month and they have no
    # month column — so they are not in this claim. What is: every table the
    # archetype added that *can* be aggregated is now read by something.
    monthly = {"timesheets", "backlog", "production", "inventory",
               "shipments", "gmv", "liquidity"} & emitted
    assert monthly and monthly <= read, \
        f"{pack} still reads none of {sorted(monthly - read)}"


def test_oee_aggregates_the_way_the_reconciliation_gate_blends_it(computed):
    """The one piece of arithmetic in these packs that had to be derived.

    `AVG(production.oee)` averages line-months of very different sizes and
    produces a number that is nobody's OEE. The pack instead uses good units
    over scheduled capacity, which is exactly availability x performance x
    quality once the three telescope, and which must agree with the
    capacity-weighted blend `contract/identities.py` checks — or the dashboard
    and the gate are describing different plants.

    **The first version of this test was worthless and the reason is the usual
    one.** It recomputed both sides in pandas and never read the record sheet,
    so `AVG(production.oee)` in the YAML left it green: it was asserting the
    generator is self-consistent, which nothing here disputes. It now takes
    the number the pack actually produces, through the metrics engine.

    Mutation: `expression: AVG(production.oee)` — 0.7494 against the gate's
    0.7585, nearly a point of OEE, which is the difference between two
    quarters' improvement and none.
    """
    import numpy as np

    profile = load_profile(ROOT / "samples" / "orbis_works.json")
    m = dict(GENERATORS["production"](profile).tables)["production"]
    grouped = m.groupby("month")

    from_gate = m.groupby("month").apply(
        lambda d: (d["oee"] * d["capacity_units"]).sum()
        / d["capacity_units"].sum(), include_groups=False)
    from_pack = computed["production"]["oee"].current
    assert from_pack == pytest.approx(float(from_gate.iloc[-1]), rel=1e-9), \
        f"pack says {from_pack}, the gate blends {float(from_gate.iloc[-1])}"

    # And the decomposition the sheet's whole value rests on still closes.
    availability = grouped["runtime_hours"].sum() / grouped["planned_hours"].sum()
    made = grouped["units_produced"].sum() + grouped["units_scrapped"].sum()
    quality = grouped["units_produced"].sum() / made
    performance = (made / grouped["capacity_units"].sum()) * (
        grouped["planned_hours"].sum() / grouped["runtime_hours"].sum())
    assert np.allclose(availability * performance * quality, from_gate,
                       rtol=1e-9, atol=1e-12)
    for term, series in (("equipment_availability", availability),
                         ("line_performance", performance)):
        assert computed["production"][term].current == pytest.approx(
            float(series.iloc[-1]), rel=1e-9), term


def test_gmv_and_revenue_are_not_the_same_number():
    """A marketplace's P&L reports the take, not the trade, and the two are an
    order of magnitude apart. A pack that conflated them would describe a
    business eight times the size at a fifth of the margin.

    Mutation: point `gmv_ttm` at `monthly_financials.revenue` and the ratio
    collapses to 1.0.
    """
    profile = load_profile(ROOT / "samples" / "lumen_exchange.json")
    tables = dict(GENERATORS["marketplace"](profile).tables)
    library = load_library(["marketplace"], include_user=False)
    kpi_set = select(profile)
    kpi_set.kpis = library
    results = {r.kpi.id: r for r in compute(kpi_set, tables, profile)}

    gmv = results["gmv_ttm"].current
    take = results["take_rate"].current
    assert gmv > 5 * float(profile.financials.revenue), \
        f"GMV {gmv:,.0f} is not distinguishable from revenue"
    assert 0.02 < take < 0.35, f"take rate {take:.3f} is not a commission"


# --------------------------------------------------------------------------
# Defect 1 — objectives that no profile can ever state
# --------------------------------------------------------------------------

def test_no_shipped_sheet_claims_an_objective_that_does_not_exist():
    """`serves_objectives` is `List[str]`, so pydantic cannot catch this.

    `_score` matches it by string against `Objective` at 5.0 for the primary
    and 2.0 for each secondary — the heaviest weight in the function — and
    `_explain` reads it again for the rationale the user sees. A value that is
    not an enum member is worth nothing, twice, and looks like intent both
    times.

    Mutation: restore `efficiency` on e-commerce's inventory cover or
    `digital_transformation` on the general pack's R&D intensity, which is
    what this found.
    """
    valid = {o.value for o in Objective}
    offenders = {
        kpi.id: sorted(set(kpi.serves_objectives) - valid)
        for kpi in load_library(None, include_user=False)
        if set(kpi.serves_objectives) - valid
    }
    assert not offenders, offenders


def test_the_linter_rejects_an_objective_that_is_not_an_enum_member():
    """The rule, not just the data — a rule with no use is how the eight
    offenders above accumulated."""
    base = load_library(["general"], include_user=False)[0].model_dump()
    bad = KPI.model_validate({**base, "serves_objectives": ["synergy"]})
    findings = validate_sheet(bad, known_ids={bad.id})
    assert any(f.rule == "objective" and f.level == "error" for f in findings), \
        [str(f) for f in findings]

    ok = KPI.model_validate({**base, "serves_objectives": ["growth"]})
    assert not [f for f in validate_sheet(ok, known_ids={ok.id})
                if f.rule == "objective"]


# --------------------------------------------------------------------------
# Defect 2 — the sector's own pack outranks the fallback beside it
# --------------------------------------------------------------------------

def test_the_pack_order_is_specific_first_and_is_read():
    """`resolve_packs` has always returned `[project, general]` in that order
    and nothing read it until 4.3b.

    Mutation: make `pack_of` return `{}`, and the specificity bonus is gone.
    """
    for pack, _, _, _ in PACKS:
        rank = pack_of([pack, "general"])
        own = {k.id for k in load_library([pack], include_user=False)}
        general = {k.id for k in load_library(["general"], include_user=False)}
        assert own, pack
        assert all(rank[i] == 0 for i in own), pack
        assert all(rank[i] == 1 for i in general - own), pack

    # No pack list means no sector to be specific about, so the bonus must be
    # uniform — which is the same as absent, and is what keeps the
    # load-everything view (`load_all_known`) neutral.
    assert pack_of(None) == {}


def test_the_specificity_bonus_changes_the_scorecard_it_claims_to(monkeypatch):
    """What the weight actually buys, isolated by turning it off.

    Written after the first version of these tests passed with `pack_of`
    stubbed to `{}` — which meant nothing here depended on the weight at all,
    and the three drops it was added to chase turned out to have two other
    causes. Rather than delete it or leave it unjustified, this pins its
    measured effect: a plant trades two general-pack cost ratios for
    availability and stock cover, a consultancy trades its opex ratio for its
    bill rate.

    Mutation: set `W_PACK_SPECIFICITY = 0.0`.
    """
    from kpi_maker.kpi import selection

    def chosen(sample):
        return {k.id for k in select(
            load_profile(ROOT / "samples" / f"{sample}.json")).kpis}

    with_bonus = {s: chosen(s) for s in ("halberd_consulting", "orbis_works")}
    monkeypatch.setattr(selection, "W_PACK_SPECIFICITY", 0.0)
    without = {s: chosen(s) for s in with_bonus}

    assert with_bonus["halberd_consulting"] - without["halberd_consulting"] \
        == {"average_bill_rate"}
    assert with_bonus["orbis_works"] - without["orbis_works"] \
        == {"equipment_availability", "finished_stock_cover"}
    # And what it displaces is a cross-sector ratio, never a sector sheet.
    for sample, gone in (("halberd_consulting", {"opex_ratio"}),
                         ("orbis_works", {"free_cash_flow_ratio",
                                          "overhead_intensity"})):
        assert without[sample] - with_bonus[sample] == gone


@pytest.mark.parametrize("pack,archetype,sample,sheets", PACKS,
                         ids=[p[0] for p in PACKS])
def test_the_scorecard_carries_what_the_business_is_run_on(pack, archetype,
                                                           sample, sheets):
    """The outcome the two previous defects were blocking, per archetype.

    Measured before the fixes: realisation scored highest of every tier-1
    candidate on the consultancy and was dropped; take rate went the same way
    on the platform; schedule utilisation lost the process cap on the factory.
    All three are the metric its archetype exists to make expressible.
    """
    must_have = {
        "project": {"utilisation_rate", "realisation_rate",
                    "backlog_cover_months"},
        "production": {"oee", "schedule_utilisation", "scrap_rate"},
        "marketplace": {"gmv_ttm", "take_rate", "match_rate"},
    }[pack]
    selected = {k.id for k in select(
        load_profile(ROOT / "samples" / f"{sample}.json")).kpis}
    assert must_have <= selected, sorted(must_have - selected)


P_AND_L = {"revenue_ttm", "revenue_growth_yoy", "gross_margin",
           "operating_margin"}


@pytest.mark.parametrize("pack,archetype,sample,sheets", PACKS,
                         ids=[p[0] for p in PACKS])
def test_the_cross_sector_pl_survives_every_sector_pack(pack, archetype,
                                                        sample, sheets):
    """A board pack keeps its P&L whichever archetype it is for.

    Mutation: any of the four losing `core: true` in `general.yaml`.
    """
    selected = {k.id for k in select(
        load_profile(ROOT / "samples" / f"{sample}.json")).kpis}
    assert selected >= P_AND_L, sorted(P_AND_L - selected)


def test_what_protects_the_pl_is_core_seeding_and_not_a_small_weight(monkeypatch):
    """Stated because the first version of the test above got it wrong.

    That test raised `W_PACK_SPECIFICITY` to 6.0 as its mutation and stayed
    green, which read as "the weight is safely small" and is not the reason:
    all four P&L sheets are `core`, so they are seeded before the greedy loop
    and **no scoring weight can displace them at all**. Measured, the bonus's
    whole blast radius at ten times its shipped value is one swap on one
    sample — the caps and the core seeds dominate the outcome, which is worth
    knowing before anyone tunes this number expecting leverage.
    """
    from kpi_maker.kpi import selection

    general = {k.id: k for k in load_library(["general"], include_user=False)}
    assert all(general[i].core for i in P_AND_L), \
        [i for i in P_AND_L if not general[i].core]

    def chosen():
        return {s: {k.id for k in select(
            load_profile(ROOT / "samples" / f"{s}.json")).kpis}
            for s in ("halberd_consulting", "orbis_works", "lumen_exchange")}

    shipped = chosen()
    monkeypatch.setattr(selection, "W_PACK_SPECIFICITY", 12.0)
    tenfold = chosen()
    for sample, before in shipped.items():
        assert tenfold[sample] >= P_AND_L, sample
        assert len(before ^ tenfold[sample]) <= 2, \
            f"{sample}: {sorted(before ^ tenfold[sample])}"


# --------------------------------------------------------------------------
# Defect 3 — core seeds bypass the tier caps
# --------------------------------------------------------------------------

def test_no_pack_group_seeds_more_exec_cores_than_the_cap_allows():
    """`core: true` is seeded before the greedy loop and is never checked
    against `TIER_CAPS`, so a group with more tier-1 cores than the cap ships
    an exec tier bigger than the one it advertises.

    Measured when this was written: the e-commerce pack declared **ten**
    against a cap of six, and a retailer's scorecard was 1/10/6/3 — more exec
    metrics than functional ones, which inverts the pyramid the tiers exist to
    create. Nine of the ten were lagging, which is also the cause 4.3a treated
    downstream by adding leading sheets.

    Mutation: restore `core: true` on `orders_count`, `active_buyers`,
    `return_rate`, `ebitda_margin` and `free_cash_flow_margin`.
    """
    from kpi_maker.authoring.lint import load_groups

    cap = TIER_CAPS[Tier.exec_l1]
    over = {}
    for group, packs in load_groups().items():
        cores = [k for k in load_library(packs, include_user=False)
                 if k.core and k.tier == Tier.exec_l1]
        if len(cores) > cap:
            over[group] = sorted(k.id for k in cores)
    assert not over, over


def test_the_linter_reports_a_group_whose_cores_fill_the_exec_tier():
    """A group at exactly the cap is legal and is still worth saying out loud:
    no tier-1 sheet in it can be selected however it scores. `general+project`
    carries this warning knowingly — see that pack's header.

    Mutation: delete the `core-cap` rule and the trap goes back to being
    reported as "tier 1 already at cap", which reads like a scoring outcome
    and is not one: the loop never ran a round.
    """
    report = lint_group(["project", "general"], group="general+project")
    warnings = [f for f in report.findings if f.rule == "core-cap"]
    assert warnings, [str(f) for f in report.findings]
    assert "book_to_bill" in warnings[0].message
    assert report.ok, "it is a warning, not an error — the author decides"

    # And it must not fire on a group that leaves room, or it is noise.
    assert not [f for f in lint_group(["ecommerce"]).findings
                if f.rule == "core-cap"]


def test_the_retailer_gets_a_pyramid_and_not_a_flat_list():
    """The observable half of the previous test, on the sample it broke.

    Before: tiers 0:1 1:10 2:6 3:3 — ten exec metrics over six functional.
    """
    kpi_set = select(load_profile(ROOT / "samples" / "kestrel_retail.json"))
    counts = {}
    for kpi in kpi_set.kpis:
        counts[int(kpi.tier)] = counts.get(int(kpi.tier), 0) + 1
    assert counts.get(2, 0) > counts.get(1, 0), counts


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_every_sector_that_reaches_an_archetype_reaches_its_pack():
    """The two questions `sectors.py` answers separately still have to agree
    about the sectors where both are exact.

    Mutation: revert a `packs:` entry in `taxonomy.yaml` to `[general]` and
    the sector silently loses the pack its own archetype emits data for.
    """
    for archetype, pack in (("project", "project"), ("production", "production"),
                            ("marketplace", "marketplace")):
        reached = [s for s in sectors.supported_sectors()
                   if sectors.resolve_archetype(s).value == archetype]
        assert reached, archetype
        for sector in reached:
            assert sectors.resolve_packs(sector).value[0] == pack, sector


def test_the_north_star_map_names_only_ids_a_pack_defines():
    """0.1 removed `gmv`, `oee` and `utilization_rate` because no pack defined
    them and all three fell through to the tier-0 fallback, so the map read as
    coverage the library did not have. 4.3b re-adds one.

    Mutation: add `"manufacturing": "oee_blended"` and this goes red.
    """
    for sector, kpi_id in NORTH_STAR_BY_MODEL.items():
        packs = sectors.resolve_packs(sector).value
        known = {k.id for k in load_library(packs, include_user=False)}
        assert kpi_id in known, f"{sector} names {kpi_id}, which {packs} lacks"

    # And the one that was re-added actually becomes the north star.
    kpi_set = select(load_profile(ROOT / "samples" / "lumen_exchange.json"))
    assert kpi_set.north_star == "gmv_ttm", kpi_set.north_star
