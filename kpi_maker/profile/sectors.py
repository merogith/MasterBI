"""Which archetype simulates a sector, and which packs define its KPIs.

`BusinessModel` declares ten sectors. Two of them — `saas` and `ecommerce` —
have their own generator archetype and their own KPI pack. Until this module
existed the other eight resolved to nothing at all, and a survey respondent who
picked "manufacturing" got a `FileNotFoundError: KPI pack not found:
manufacturing` out of the selection engine, or a `ValueError: No data generator
for business model 'manufacturing'` out of the source stage.

The rule here is that **every declared sector resolves to something runnable,
and a run that had to approximate says so.** A scorecard built on the
cross-sector financial pack and labelled as such is honest. A traceback is not,
and neither is a manufacturer silently receiving a dashboard full of cart
abandonment.

Two separate questions, deliberately answered separately:

* **Which archetype generates the data?** Only the shape of the simulation
  matters. A retailer, a distributor and a restaurant group all sell discrete
  units at a price, so `ecommerce` (the transactional archetype) is a fair
  simulation of all three even before their own archetypes exist.
* **Which packs define the KPIs?** Here approximation is much more visible, so
  it is much more conservative: a sector without its own pack gets `general`,
  the cross-sector financial pack, and *not* the nearest sector's. Sell-through
  and GMROI on a haulage company would be worse than showing fewer metrics.

ROADMAP M2 replaces these approximations sector by sector. When a sector gains
its own archetype and pack, move it from the approximate map to the exact one
and delete nothing else — every caller reads through the two functions below.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

from . import taxonomy

# The two maps that used to live here are gone: they stated, in a second
# place, facts `taxonomy.yaml` now owns. Adding a sector meant editing this
# file, the `BusinessModel` enum and `SECTOR_LABELS` in the survey, with
# nothing to notice when one of the three was missed — and each way of missing
# one fails differently, which is why none of them was obvious.
#
# The functions below are unchanged in signature and behaviour. Every caller
# still reads through them, so 4.2 moving a sector onto its own archetype is
# still a one-line edit — it is now a one-line edit to the taxonomy.

# The pack every sector can always fall back on: metrics computable from the
# P&L, the headcount roster and the marketing spend table, which every
# archetype emits.
GENERAL_PACK = "general"


class Resolution(NamedTuple):
    """What a sector resolved to, and whether that was its own content.

    `note` is None for an exact match and a full sentence otherwise. Callers
    render it rather than composing their own, so the wording of "we
    approximated" is decided in one place.
    """
    value: object          # str for an archetype, List[str] for packs
    exact: bool
    note: Optional[str]


def resolve_archetype(model: str) -> Resolution:
    """The generator that simulates this sector."""
    sector = taxonomy.load().get(model)
    if sector is not None and sector.exact_archetype:
        return Resolution(sector.archetype, True, None)
    if sector is not None:
        return Resolution(
            sector.archetype, False,
            f"No data generator has been built for {model!r} yet, so this run "
            f"was simulated with the {sector.archetype!r} archetype — "
            f"{sector.why}. The figures are internally consistent and "
            f"reconcile, but they are not a model of {model!r} economics.",
        )
    # An unknown model is a schema change that got ahead of this map. Degrade
    # rather than raise: the transactional archetype is the general case.
    return Resolution(
        "ecommerce", False,
        f"{model!r} is not a sector this build knows how to simulate, so the "
        f"transactional archetype was used. Treat the figures as illustrative.",
    )


def resolve_packs(model: str) -> Resolution:
    """The KPI packs that define this sector's scorecard."""
    sector = taxonomy.load().get(model)
    if sector is not None and sector.exact_packs:
        return Resolution(list(sector.packs), True, None)
    return Resolution(
        [GENERAL_PACK], False,
        f"No KPI pack has been authored for {model!r} yet, so this scorecard "
        f"uses the cross-sector financial pack. It covers the P&L, workforce "
        f"and marketing efficiency, and omits the operational metrics that "
        f"would make it a {model!r} scorecard.",
    )


def is_approximate(model: str) -> bool:
    """True when either half of this sector's content had to be approximated."""
    return not (resolve_archetype(model).exact and resolve_packs(model).exact)


def approximation_notes(model: str) -> List[str]:
    """Every sentence a run for this sector should carry. Empty when exact."""
    return [r.note for r in (resolve_archetype(model), resolve_packs(model))
            if r.note is not None]


def supported_sectors() -> List[str]:
    """Sectors with both their own archetype and their own pack."""
    return sorted(s.id for s in taxonomy.load().sectors
                  if s.exact_archetype and s.exact_packs)


def declared_sectors() -> List[str]:
    """Every sector the product offers, in the order the survey shows them."""
    return taxonomy.load().ids()


def classification(model: str) -> Optional[str]:
    """This sector's official codes, for the report appendix. None if unknown.

    The plan's reason for carrying them: a scorecard that names the standard
    its sector was matched against is arguing from something, and one that
    names nothing is asking to be believed.
    """
    sector = taxonomy.load().get(model)
    return None if sector is None else sector.classification()
