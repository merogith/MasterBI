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

from typing import Dict, List, NamedTuple, Optional, Tuple

# Sectors whose own generator archetype exists today. Anything absent from this
# map is simulated by ARCHETYPE_APPROXIMATION and flagged.
ARCHETYPE_EXACT: Dict[str, str] = {
    "saas": "saas",
    "ecommerce": "ecommerce",
}

# The nearest shipped archetype for a sector that has none of its own, with the
# reason it is the nearest. The reason is rendered to the user, so it has to
# read as an argument rather than an apology.
ARCHETYPE_APPROXIMATION: Dict[str, Tuple[str, str]] = {
    "retail": ("ecommerce", "physical retail sells discrete units at a price, "
                            "which is the transactional archetype's shape"),
    "distribution": ("ecommerce", "distribution is transactional at a lower "
                                  "margin and a higher volume"),
    "hospitality": ("ecommerce", "covers and room-nights behave like orders "
                                 "with a strong seasonal profile"),
    "logistics": ("ecommerce", "shipments priced per movement behave like "
                               "orders"),
    "manufacturing": ("ecommerce", "units shipped at a price is transactional; "
                                   "what is missing is the capacity ceiling, "
                                   "not the revenue shape"),
    "marketplace": ("ecommerce", "the transactional archetype models the "
                                 "demand side; take rate and the supply side "
                                 "are not simulated"),
    "services": ("ecommerce", "project fees behave like orders; utilisation "
                              "and realisation are not simulated"),
    "healthcare": ("ecommerce", "episodes of care behave like orders; case mix "
                                "and payer mix are not simulated"),
}

# Sectors with their own reviewed KPI pack.
PACKS_EXACT: Dict[str, List[str]] = {
    "saas": ["saas"],
    "ecommerce": ["ecommerce"],
}

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
    if model in ARCHETYPE_EXACT:
        return Resolution(ARCHETYPE_EXACT[model], True, None)
    if model in ARCHETYPE_APPROXIMATION:
        archetype, because = ARCHETYPE_APPROXIMATION[model]
        return Resolution(
            archetype, False,
            f"No data generator has been built for {model!r} yet, so this run "
            f"was simulated with the {archetype!r} archetype — {because}. "
            f"The figures are internally consistent and reconcile, but they are "
            f"not a model of {model!r} economics.",
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
    if model in PACKS_EXACT:
        return Resolution(list(PACKS_EXACT[model]), True, None)
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
    return sorted(set(ARCHETYPE_EXACT) & set(PACKS_EXACT))
