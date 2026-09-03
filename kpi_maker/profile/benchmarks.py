"""Where a KPI's peer band comes from, and what it is allowed to claim.

Until now a benchmark was a static field on the record sheet, so **one set of
numbers described every business the product can describe**. That was harmless
while there were two archetypes and one of them shipped its own sample profile.
4.2 made it the loudest remaining defect in the product, measured on three real
runs:

    personnel cost ratio    consultancy 79.2%   platform 90.8%   factory 28.7%
    judged against          red 45%, cohort median 30%  -- for all three

So every non-subscription run shipped at least one high-severity finding that
was simply wrong for its archetype, and a reader who checked it would have been
right and the product wrong.

**The interface, and why it is a distribution.** `BenchmarkProvider.get` returns
a `Distribution` — three quartiles plus a citation, a vintage and a sample size
— rather than a number. Selection scores a benchmarked KPI higher, targets
resolve against p25/p50/p75, `target_band` KPIs are scored on the inter-quartile
range, one chart plots distance from the median and one detector reports
quartile position. All five want the spread, and a point estimate would have to
invent one at each of them.

**Where the numbers come from, stated exactly.**

*`CostStructureBenchmarks`* answers the ratios that are already stated
elsewhere in this package. `survey/defaults.py` carries a gross margin and an
operating-cost split per archetype per stage, because a profile has to be built
from something when the user says "I don't know". A KPI like `opex_ratio` or
`marketing_intensity` **is** that prior, arithmetically — so deriving the band
from it is the generated-not-declared pattern this repo uses for design tokens
and the table-to-KPI map, rather than a second statement of the same fact that
can drift from the first.

*The stage spread is the dispersion*, and that is the part worth arguing with.
A cohort contains firms at different maturities, an early-stage firm spends more
of its revenue on everything, and a mature one has converted that spend into
operating leverage. So `p25` is the early-stage value, `p50` established and
`p75` mature — which produces the library's own convention (p75 is the good end,
whichever direction the metric runs) automatically, because maturity improves
both a margin and a cost ratio.

*`PackBenchmarks`* is the record sheet's own declaration, and it answers last —
for the metrics no cost structure implies, and for every sector pack that has
authored a real band.

**What this is not.** These are coarse internal priors and every one of them
says so in the string the report prints. The plan's 4.4 asks for public citable
distributions — Damodaran's industry margins, Eurostat SBS, OECD, SEC/XBRL —
and **this environment cannot reach any of them**: `pages.stern.nyu.edu` and
`ec.europa.eu` both answer 403 at the egress proxy (policy denial, recorded in
`__agentproxy/status`), and unlike 4.1b's ISIC there is no equivalent on PyPI —
every finance package there is a *client* for a host that is also blocked.

Writing Damodaran's name against a number nobody fetched would be worse than
the placeholder it replaced, so the numbers say what they are and the seam is
built so that a published set is a **data** change: add a provider ahead of
these two in `_PROVIDERS` and nothing else moves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from ..survey import defaults as D

#: The three stages the spread is read at. A cohort is a mix of maturities, and
#: this package already states how each one spends.
SPREAD_STAGES = ("early", "established", "mature")


@dataclass(frozen=True)
class Distribution:
    """A peer band, with everything needed to cite it.

    `p25` is the *worst* quartile and `p75` the best, whichever direction the
    metric runs — the convention `KPI.vs_benchmark` already uses and the reason
    the shipped record sheets carry `p25: 0.48, p75: 0.22` on a cost ratio.
    """
    p25: Optional[float]
    p50: Optional[float]
    p75: Optional[float]
    source: str
    n: Optional[int] = None
    vintage: Optional[str] = None
    url: Optional[str] = None

    #: Whether the RAG thresholds should be taken from these quartiles.
    #:
    #: **The rule was already in the library, unstated.** Every one of the
    #: eleven general-pack KPIs carrying both a band and alert thresholds has
    #: `green` within a rounding of `p50` and `red` within a rounding of `p25`,
    #: in both directions — gross margin green 0.40 against p50 0.42, opex ratio
    #: green 0.30 against p50 0.34, personnel cost green 0.28 against p50 0.30.
    #: Somebody derived them by hand and then the derivation was lost, so when
    #: the peer band became archetype-aware the thresholds did not follow and
    #: kept judging a consultancy against a software cohort.
    #:
    #: Set only where the band itself is archetype-derived. A pack that authored
    #: its own thresholds against its own cohort keeps them.
    implies_alert_bands: bool = False


class BenchmarkProvider(Protocol):
    """One answer, or None to let the next provider try.

    `sector` and `archetype` are both passed because they degrade differently,
    exactly as in `profile/sectors.py`: a published dataset is keyed by sector
    (NACE or NAICS), while a cost-structure prior is a fact about the archetype.
    """

    def get(self, kpi_id: str, *, sector: str, archetype: str,
            size_band: Optional[str] = None,
            region: Optional[str] = None) -> Optional[Distribution]:
        ...


# --------------------------------------------------------------------------
# Derived from the cost structure this package already states
# --------------------------------------------------------------------------

#: Share of a business's total cost that is payroll. Five stated numbers, each
#: with a reason, and the only ones here that are not derived from something
#: already in the repo — which is why they are in one place with the reasons
#: attached rather than spread across five record sheets.
#:
#: They exist because `personnel_cost_ratio` was the single loudest wrong
#: finding in the product and no combination of the margin and opex priors
#: implies it on its own: those say what a company *spends*, not how much of
#: that spend is people.
PEOPLE_SHARE_OF_COST: Dict[str, float] = {
    # Engineering, sales and support salaries dominate; hosting and third-party
    # software are most of the rest.
    "saas": 0.62,
    # Most of a retailer's cost is goods bought to be sold again.
    "ecommerce": 0.22,
    # The people *are* the cost of sales. Nothing else in this table is close.
    "project": 0.78,
    # Materials and energy carry cost of sales; direct labour and the salaried
    # roster are the remainder.
    "production": 0.35,
    # A large salaried base against payment processing and trust operations.
    "marketplace": 0.55,
}


def _opex_total(archetype: str, stage: str) -> float:
    return sum(D.opex_for(archetype, stage).values())


def _operating_margin(archetype: str, stage: str) -> float:
    return D.gross_margin_for(archetype, stage) - _opex_total(archetype, stage)


#: KPI id -> what the cost structure says it is, at one stage.
#:
#: Every entry is arithmetic over `survey/defaults.py`. Nothing here invents a
#: level; the only judgement is `PEOPLE_SHARE_OF_COST` above.
_FROM_COST_STRUCTURE = {
    "gross_margin": D.gross_margin_for,
    "operating_margin": _operating_margin,
    "opex_ratio": _opex_total,
    "marketing_intensity": lambda a, s: D.opex_for(a, s)["marketing"],
    "sales_intensity": lambda a, s: D.opex_for(a, s)["sales"],
    "overhead_intensity": lambda a, s: D.opex_for(a, s)["ga"],
    "rnd_intensity": lambda a, s: D.opex_for(a, s)["rnd"],
    "personnel_cost_ratio": lambda a, s: (
        (1.0 - _operating_margin(a, s)) * PEOPLE_SHARE_OF_COST.get(a, 0.5)),
}


class CostStructureBenchmarks:
    """Bands derived from the archetype's own cost priors."""

    def get(self, kpi_id: str, *, sector: str, archetype: str,
            size_band: Optional[str] = None,
            region: Optional[str] = None) -> Optional[Distribution]:
        rule = _FROM_COST_STRUCTURE.get(kpi_id)
        if rule is None or archetype not in PEOPLE_SHARE_OF_COST:
            return None
        early, established, mature = (rule(archetype, stage)
                                      for stage in SPREAD_STAGES)
        return Distribution(
            p25=round(early, 4), p50=round(established, 4), p75=round(mature, 4),
            source=(f"MasterBI cost-structure prior for the {archetype!r} "
                    f"archetype, spread across company stage — a coarse "
                    f"internal band, not a published distribution"),
            vintage="internal prior",
            implies_alert_bands=True,
        )


# --------------------------------------------------------------------------
# The record sheet's own declaration
# --------------------------------------------------------------------------

class PackBenchmarks:
    """Whatever the KPI already carried. Answers last, and answers for the
    metrics no cost structure implies — growth, cash conversion, revenue per
    head, attrition, capital intensity."""

    def __init__(self, library: Sequence) -> None:
        self._by_id = {kpi.id: kpi.benchmark for kpi in library
                       if kpi.benchmark is not None}

    def get(self, kpi_id: str, *, sector: str, archetype: str,
            size_band: Optional[str] = None,
            region: Optional[str] = None) -> Optional[Distribution]:
        band = self._by_id.get(kpi_id)
        if band is None:
            return None
        return Distribution(p25=band.p25, p50=band.p50, p75=band.p75,
                            source=band.source, n=getattr(band, "n", None),
                            vintage=getattr(band, "vintage", None),
                            url=getattr(band, "url", None))


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

#: Providers in precedence order — the first that answers wins. A published
#: source goes in front of these, and that is the whole extension point: no
#: caller changes, no record sheet changes, no schema changes.
_EXTRA: List[BenchmarkProvider] = []


def register(provider: BenchmarkProvider) -> None:
    """Put a provider ahead of the built-in ones.

    Used by `tests/test_benchmarks.py` to prove the seam carries a third
    provider, and by whatever ships Damodaran once a network can reach it.
    """
    _EXTRA.insert(0, provider)


def providers(library: Sequence) -> List[BenchmarkProvider]:
    return [*_EXTRA, CostStructureBenchmarks(), PackBenchmarks(library)]


def resolve(library: Sequence, profile) -> List:
    """Re-band every KPI in the library for the company being reported on.

    Applied to the *library* rather than to the selected set, because selection
    scores a benchmarked KPI half a point higher and resolves its target
    against the band — so a benchmark that arrived afterwards would be a
    benchmark the choice of scorecard never saw.

    Returns new KPI objects and leaves the input list alone. **Not because
    `load_library` is cached — it is not, and asserting that it was is how the
    first version of this docstring and its test were both wrong.** The reason
    is that a caller can hold one library and resolve it more than once:
    `authoring/lint.py` loads a pack, then selects for a sector inside the same
    call. Mutating in place would leave the second resolution reading the first
    company's cohort.
    """
    from ..kpi.schema import Benchmark
    from . import sectors

    sector = profile.business_model.type.value
    archetype = sectors.resolve_archetype(sector).value
    size_band = getattr(profile.size, "revenue_band", None)
    region = profile.identity.country

    chain = providers(library)
    out = []
    for kpi in library:
        band = None
        for provider in chain:
            band = provider.get(kpi.id, sector=sector, archetype=archetype,
                                size_band=size_band, region=region)
            if band is not None:
                break
        if band is None:
            out.append(kpi)
            continue
        update = {"benchmark": Benchmark(
            p25=band.p25, p50=band.p50, p75=band.p75, source=band.source,
            n=band.n, vintage=band.vintage, url=band.url)}
        bands = _alert_bands(kpi, band)
        if bands is not None:
            update["alert_bands"] = bands
        out.append(kpi.model_copy(update=update))
    return out


def _alert_bands(kpi, band: Distribution):
    """Green at the median, red at the bottom quartile — or None to keep the
    sheet's own.

    Only for a KPI that **already had** thresholds. A `target_band` metric is
    deliberately scored on the inter-quartile range instead and carries none, so
    inventing a pair here would change how it is judged rather than where.
    """
    from ..kpi.schema import AlertBands, Direction

    if not band.implies_alert_bands or kpi.alert_bands is None:
        return None
    if kpi.direction == Direction.target_band:
        return None
    if band.p50 is None or band.p25 is None:
        return None
    # `_bands_match_direction` requires a strict ordering, and a flat spread —
    # an archetype whose early and mature priors happen to coincide — would
    # otherwise raise at model construction rather than simply not applying.
    if kpi.direction == Direction.higher_is_better and band.p50 <= band.p25:
        return None
    if kpi.direction == Direction.lower_is_better and band.p50 >= band.p25:
        return None
    return AlertBands(green=band.p50, red=band.p25)
