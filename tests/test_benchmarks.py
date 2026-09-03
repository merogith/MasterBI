"""Where a peer band comes from, and what it is allowed to claim.

A benchmark was a static field on the record sheet, so **one set of numbers
described every business the product can describe**. Measured on three real runs
after 4.2:

    personnel cost ratio    consultancy 79.2%   platform 90.8%   factory 28.7%
    judged against          red 45%, cohort median 30%  -- all three

Every non-subscription run shipped at least one high-severity finding that was
wrong for its archetype, and a reader who checked it would have been right and
the product wrong.

The plan asked for published distributions — Damodaran, Eurostat SBS, OECD,
SEC/XBRL. **This environment reaches none of them**: `pages.stern.nyu.edu` and
`ec.europa.eu` both answer 403 at the egress proxy, and unlike 4.1b's ISIC there
is no equivalent on PyPI — every finance package there is a client for a host
that is also blocked. So the bands are derived from priors this package already
states, they say so in the string the report prints, and the last test in this
file is the one that keeps them honest.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.selection import load_library, select  # noqa: E402
from kpi_maker.profile import benchmarks, sectors  # noqa: E402
from kpi_maker.survey import build_profile  # noqa: E402

ARCHETYPE_SAMPLES = {
    "saas": "northwind_saas",
    "ecommerce": "kestrel_retail",
    "project": "halberd_consulting",
    "production": "orbis_works",
    "marketplace": "lumen_exchange",
}


def _selected(name):
    profile = load_profile(ROOT / "samples" / f"{name}.json")
    return profile, select(profile)


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------

def test_a_registered_provider_takes_precedence() -> None:
    """The seam is exercised rather than asserted by hope.

    A published source drops in *ahead* of the built-in providers and nothing
    else moves — no caller change, no record sheet change, no schema change.
    Since 4.4 could not fetch one, this stands in for it: if a third provider
    could not win, the extension point would be decoration.
    """
    class Stub:
        def get(self, kpi_id, *, sector, archetype, size_band=None, region=None):
            if kpi_id != "gross_margin":
                return None
            return benchmarks.Distribution(
                p25=0.11, p50=0.22, p75=0.33,
                source="Stub Statistical Office", n=4200, vintage="2025",
                url="https://example.invalid/stub")

    library = load_library(["general"], include_user=False)
    profile = build_profile({"business_model": "services"})

    before = {k.id: k.benchmark for k in benchmarks.resolve(library, profile)}
    assert before["gross_margin"].p50 != 0.22

    benchmarks.register(Stub())
    try:
        after = {k.id: k.benchmark for k in benchmarks.resolve(library, profile)}
    finally:
        benchmarks._EXTRA.clear()

    band = after["gross_margin"]
    assert (band.p25, band.p50, band.p75) == (0.11, 0.22, 0.33)
    assert band.n == 4200 and band.vintage == "2025"
    assert band.url == "https://example.invalid/stub"
    # ...and only that KPI moved.
    assert after["opex_ratio"].p50 == before["opex_ratio"].p50


def test_resolution_leaves_the_library_it_was_given_alone() -> None:
    """A caller can hold one library and resolve it more than once.

    The first version of this test compared two separate `load_library` calls
    and claimed the loader was cached. **It is not** — every call re-parses the
    YAML and builds fresh models — so the test compared two independent objects
    and could not fail, and it stayed green with an in-place mutation
    reintroduced. What matters is the list actually passed in: `lint.py` loads a
    pack and then selects for a sector inside the same call, so a mutation would
    have the second resolution reading the first company's cohort.
    """
    library = load_library(["general"], include_user=False)
    before = {k.id: (k.benchmark.p50 if k.benchmark else None) for k in library}

    services = benchmarks.resolve(
        library, build_profile({"business_model": "services"}))
    assert {k.id: (k.benchmark.p50 if k.benchmark else None)
            for k in library} == before, "resolve mutated its input"

    factory = benchmarks.resolve(
        library, build_profile({"business_model": "manufacturing"}))
    assert {k.id: (k.benchmark.p50 if k.benchmark else None)
            for k in library} == before

    # The two resolutions disagree, or resolving twice proved nothing.
    a = next(k.benchmark.p50 for k in services if k.id == "gross_margin")
    b = next(k.benchmark.p50 for k in factory if k.id == "gross_margin")
    assert a != b


# --------------------------------------------------------------------------
# The defect this item exists for
# --------------------------------------------------------------------------

def test_the_band_differs_by_archetype() -> None:
    """One cohort for five kinds of business was the whole bug."""
    bands = {}
    for archetype in ARCHETYPE_SAMPLES:
        sector = next(s for s in ("saas", "ecommerce", "services",
                                  "manufacturing", "marketplace")
                      if sectors.resolve_archetype(s).value == archetype)
        library = load_library(["general"], include_user=False)
        resolved = benchmarks.resolve(
            library, build_profile({"business_model": sector}))
        by_id = {k.id: k.benchmark for k in resolved}
        bands[archetype] = by_id["personnel_cost_ratio"].p50

    assert len({round(v, 4) for v in bands.values()}) == 5, bands
    # The people-heavy archetypes sit above the ones that buy what they sell.
    assert bands["project"] > bands["saas"] > bands["marketplace"]
    assert bands["marketplace"] > bands["production"] > bands["ecommerce"]


def test_the_alert_thresholds_follow_the_band() -> None:
    """The rule was already in the library, unstated and hand-rounded.

    All eleven general-pack KPIs carrying both a band and thresholds had green
    within a rounding of p50 and red within a rounding of p25 — gross margin
    green 0.40 against p50 0.42, opex ratio green 0.30 against p50 0.34. Somebody
    derived them and the derivation was lost, so when the band became
    archetype-aware the thresholds did not follow and went on judging a
    consultancy against a software cohort.
    """
    from kpi_maker.kpi.schema import Direction

    library = load_library(["general"], include_user=False)
    resolved = benchmarks.resolve(
        library, build_profile({"business_model": "services"}))

    checked = 0
    for kpi in resolved:
        if kpi.id not in benchmarks._FROM_COST_STRUCTURE or not kpi.alert_bands:
            continue
        checked += 1
        assert kpi.alert_bands.green == pytest.approx(kpi.benchmark.p50)
        assert kpi.alert_bands.red == pytest.approx(kpi.benchmark.p25)
        # The model's own validator would have caught an inversion, so this is
        # about the *meaning*: red must be the worse side in either direction.
        if kpi.direction == Direction.higher_is_better:
            assert kpi.alert_bands.red < kpi.alert_bands.green
        else:
            assert kpi.alert_bands.red > kpi.alert_bands.green
    assert checked >= 6, checked


def test_a_pack_that_authored_its_own_band_keeps_it() -> None:
    """The derivation answers for the cross-sector pack, which five archetypes
    share. A sector pack was written against its own cohort and must not be
    overwritten by a prior about the archetype."""
    library = load_library(["saas"], include_user=False)
    declared = {k.id: k.benchmark for k in library if k.benchmark}
    resolved = {k.id: k.benchmark for k in benchmarks.resolve(
        library, build_profile({"business_model": "saas"})) if k.benchmark}

    assert declared, "the saas pack should declare benchmarks"
    for kpi_id, band in declared.items():
        assert resolved[kpi_id].p50 == band.p50, kpi_id
        assert resolved[kpi_id].source == band.source, kpi_id


def test_the_derived_bands_are_derived() -> None:
    """Not a second copy of the cost priors that can drift from the first.

    Change what `survey/defaults.py` says a retailer spends on marketing and the
    retailer's marketing-intensity band must move with it. That is the
    generated-not-declared pattern this repo uses for design tokens and the
    table-to-KPI map.
    """
    from kpi_maker.survey import defaults as D

    library = load_library(["general"], include_user=False)
    profile = build_profile({"business_model": "retail"})

    def band():
        return next(k.benchmark.p50 for k in benchmarks.resolve(library, profile)
                    if k.id == "marketing_intensity")

    before = band()
    original = D.ARCHETYPE_OPEX_BY_STAGE["ecommerce"]["established"]["marketing"]
    D.ARCHETYPE_OPEX_BY_STAGE["ecommerce"]["established"]["marketing"] = 0.42
    try:
        assert band() == pytest.approx(0.42)
    finally:
        D.ARCHETYPE_OPEX_BY_STAGE["ecommerce"]["established"]["marketing"] = original
    assert band() == pytest.approx(before)


def test_the_band_is_resolved_before_the_scorecard_is_chosen() -> None:
    """Selection scores a benchmarked KPI half a point higher and resolves its
    target against the quartiles, so a band applied afterwards would be a band
    the choice of scorecard never saw."""
    for name in ARCHETYPE_SAMPLES.values():
        _, kpi_set = _selected(name)
        assert kpi_set.kpis
        for kpi in kpi_set.kpis:
            if kpi.benchmark is None:
                continue
            assert (kpi.benchmark.source or "").strip(), kpi.id


def test_personnel_cost_lands_inside_its_own_archetypes_band() -> None:
    """The finding that started this item: 79.2% judged against a 45% red.

    Not an exact hit — a benchmark that a company always matches is not a
    benchmark — but within the band's own spread rather than three times outside
    it.
    """
    from kpi_maker.metrics.engine import compute

    for archetype, name in ARCHETYPE_SAMPLES.items():
        profile, kpi_set = _selected(name)
        kpi = kpi_set.by_id("personnel_cost_ratio")
        if kpi is None:
            continue                      # sector packs use their own ids
        tables = GENERATORS[archetype](profile).tables
        result = next(r for r in compute(kpi_set, dict(tables), profile)
                      if r.kpi.id == "personnel_cost_ratio")
        low, high = sorted((kpi.benchmark.p75, kpi.benchmark.p25))
        assert low * 0.7 <= result.current <= high * 1.3, (
            f"{archetype}: {result.current:.3f} against a "
            f"{low:.3f}-{high:.3f} band")


# --------------------------------------------------------------------------
# The bug measuring this one exposed
# --------------------------------------------------------------------------

def test_no_roster_costs_more_than_its_company_spends() -> None:
    """Four of five generators drew a per-head salary with nothing tying the
    total to the P&L.

    Measured: payroll was **88% of total cost for software, 88% for services and
    101% for the platform** — no rent, no hosting, no tools, no travel, and in
    one case more wages than the business had money. `personnel_cost_ratio`
    divides one of those numbers by the other, so the KPI at the centre of this
    item was meaningless on three archetypes.
    """
    for archetype, name in ARCHETYPE_SAMPLES.items():
        profile = load_profile(ROOT / "samples" / f"{name}.json")
        tables = GENERATORS[archetype](profile).tables
        fin, roster = tables["monthly_financials"], tables["headcount"]
        payroll = float(roster["cost"].sum())
        total = float((fin["cogs"] + fin["total_opex"]).sum())
        share = payroll / total
        assert share == pytest.approx(
            benchmarks.PEOPLE_SHARE_OF_COST[archetype], abs=0.01), archetype
        assert share < 0.95, f"{archetype}: payroll is {share:.0%} of all cost"


def test_relative_pay_by_function_survives_the_anchoring() -> None:
    """The total comes from the P&L; the *spread* is still modelled.

    Flattening pay across functions would make cost per head a constant and take
    the mix story out of the roster, which is a real one — a company that grows
    its engineering share gets more expensive per head without anyone getting a
    rise.
    """
    for archetype, name in ARCHETYPE_SAMPLES.items():
        profile = load_profile(ROOT / "samples" / f"{name}.json")
        roster = GENERATORS[archetype](profile).tables["headcount"]
        by_function = roster.groupby("function").agg(
            cost=("cost", "sum"), fte=("fte", "sum"))
        by_function = by_function[by_function["fte"] > 0]
        per_head = by_function["cost"] / by_function["fte"]
        assert per_head.max() > per_head.min() * 1.25, archetype


# --------------------------------------------------------------------------
# Honesty
# --------------------------------------------------------------------------

#: Matched on word boundaries, because a substring match is how a guard grows a
#: false positive: `sec` fires inside "technology-**sec**tor", which is what the
#: first version of this test did — and 4.3a's lesson is that a check crying
#: wolf is worse than no check.
PUBLISHED = (r"damodaran", r"eurostat", r"oecd", r"sec\b", r"xbrl",
             r"t[uü]ik", r"compustat", r"capital iq", r"statista")


def test_no_band_claims_a_published_source_it_cannot_cite() -> None:
    """The guard that makes the real dataset safe to drop in later.

    A band naming Damodaran or Eurostat has to carry the vintage and the URL
    that make the claim checkable. Writing the name against a number nobody
    fetched would be worse than the placeholder it replaced — and it is exactly
    the shortcut available when a source is blocked by a network, which is how
    this item found itself.
    """
    for name in ARCHETYPE_SAMPLES.values():
        _, kpi_set = _selected(name)
        for kpi in kpi_set.kpis:
            band = kpi.benchmark
            if band is None:
                continue
            source = (band.source or "").lower()
            named = [p for p in PUBLISHED if re.search(rf"\b{p}", source)]
            if not named:
                continue
            assert band.vintage and band.url, (
                f"{kpi.id} cites {named[0]!r} with no vintage or URL to check "
                f"it against")


def test_every_prior_says_that_it_is_one() -> None:
    """A band with no vintage is a prior, not a measurement, and the report
    prints the source string — so the string has to carry the caveat rather
    than relying on a reader to infer it from an empty field."""
    library = load_library(["general"], include_user=False)
    resolved = benchmarks.resolve(
        library, build_profile({"business_model": "services"}))

    derived = [k for k in resolved if k.id in benchmarks._FROM_COST_STRUCTURE]
    assert derived
    for kpi in derived:
        source = kpi.benchmark.source
        assert "not a published distribution" in source, kpi.id
        assert "project" in source, f"{kpi.id} does not name the archetype"

    # And the authoring linter counts them, so nobody has to remember.
    from kpi_maker.authoring.validate import _is_a_prior

    assert all(_is_a_prior(k.benchmark) for k in derived)
