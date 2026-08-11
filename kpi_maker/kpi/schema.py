"""KPI record sheet — after Neely et al. (Cambridge Performance Measurement).

A metric with no owner, no formula, no source and no target is a number, not a
KPI. This schema makes that distinction structural: the library cannot express
an under-specified metric.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class Perspective(str, Enum):
    """Balanced Scorecard (Kaplan & Norton). Used to force coverage."""
    financial = "financial"
    customer = "customer"
    process = "internal_process"
    learning = "learning_growth"


class Timing(str, Enum):
    leading = "leading"
    lagging = "lagging"


class Direction(str, Enum):
    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"
    target_band = "target_band"


class Tier(int, Enum):
    north_star = 0
    exec_l1 = 1
    functional_l2 = 2
    operational_l3 = 3


class ComputeKind(str, Enum):
    builtin = "builtin"      # a registered Python function in metrics/engine.py
    formula = "formula"      # a user expression, evaluated by kpi_maker.formula


class Compute(BaseModel):
    """How a KPI's number is produced.

    Absent means `{kind: builtin, ref: <the KPI's own id>}`, which is what every
    library record sheet relied on before formulas existed — so all 44 of them
    keep working untouched.
    """
    kind: ComputeKind = ComputeKind.builtin
    ref: Optional[str] = None          # builtin: registry key, defaults to the id
    expression: Optional[str] = None   # formula: the user's expression

    @model_validator(mode="after")
    def _consistent(self) -> Compute:
        if self.kind == ComputeKind.formula and not (self.expression or "").strip():
            raise ValueError("a formula KPI needs a non-empty expression")
        if self.kind == ComputeKind.builtin and self.expression:
            raise ValueError(
                "a builtin KPI cannot carry an expression — set kind: formula")
        return self


class Origin(str, Enum):
    library = "library"
    user = "user"


class Benchmark(BaseModel):
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    source: str  # mandatory: an uncited benchmark is worse than none


class AlertBands(BaseModel):
    """Thresholds in the KPI's own unit. Interpreted per `direction`."""
    green: float
    red: float


class KPI(BaseModel):
    id: str
    name: str
    short_name: Optional[str] = None
    perspective: Perspective
    tier: Tier = Tier.functional_l2
    timing: Timing
    direction: Direction

    # Node in the value-driver tree. Root nodes have parent None. The tree is
    # what gives the dashboard its drill-down and the report its waterfall.
    driver_parent: Optional[str] = None

    formula: str          # human-readable prose; rendered into the definitions
                          # tab and the report appendix. NOT executable — see
                          # `compute` for that.
    unit: str             # currency | pct | months | days | count | ratio | score
    frequency: str = "monthly"
    owner_role: str
    source_systems: List[str] = Field(default_factory=list)

    # How to actually compute it. Defaults to the builtin registered under this
    # KPI's own id, which is how every library entry behaved before formulas.
    compute: Compute = Field(default_factory=Compute)

    # Library KPIs carry a reviewed record sheet; user KPIs are as good as the
    # four fields someone typed. The report appendix says which is which rather
    # than presenting them as equally authoritative.
    origin: Origin = Origin.library

    benchmark: Optional[Benchmark] = None
    alert_bands: Optional[AlertBands] = None
    target_rule: Optional[str] = None

    # An explicit target set by the user, which beats `target_rule` and the
    # benchmark median. Kept separate from `target_rule` so the library's
    # recommendation survives alongside the override and can be restored.
    target_override: Optional[float] = None

    applies_when: Optional[str] = None   # expression over the CompanyProfile
    requires_data: List[str] = Field(default_factory=list)

    # A core KPI is one no scorecard for this business model may omit,
    # whatever the objective. Growth rate, retention and gross margin are the
    # three numbers every board, investor and lender asks for first; letting a
    # scoring tie-break drop one produces a scorecard that looks negligent.
    # Core KPIs are seeded before the scored pass rather than competing in it.
    core: bool = False

    # Goes in the report appendix. A named pitfall is a strong credibility
    # signal and pre-empts the "your number is wrong" conversation.
    pitfalls: Optional[str] = None
    interpretation: Optional[str] = None

    # Objectives this KPI directly serves; drives the selection score.
    serves_objectives: List[str] = Field(default_factory=list)

    @property
    def compute_ref(self) -> str:
        """The metrics-registry key for a builtin KPI."""
        return self.compute.ref or self.id

    @property
    def is_formula(self) -> bool:
        return self.compute.kind == ComputeKind.formula

    @model_validator(mode="after")
    def _bands_match_direction(self) -> KPI:
        b = self.alert_bands
        if b is None:
            return self
        if self.direction == Direction.higher_is_better and b.green <= b.red:
            raise ValueError(
                f"{self.id}: higher_is_better requires green > red "
                f"(got green={b.green}, red={b.red})"
            )
        if self.direction == Direction.lower_is_better and b.green >= b.red:
            raise ValueError(
                f"{self.id}: lower_is_better requires green < red "
                f"(got green={b.green}, red={b.red})"
            )
        return self

    def status(self, value: Optional[float]) -> str:
        """RAG status for a computed value.

        Returns "unscored" — not "unknown" — when the metric has a value but no
        threshold to judge it against. The two are different situations and
        conflating them makes a populated row read as missing data.
        """
        if value is None:
            return "unknown"

        # A target_band metric is healthy INSIDE a range: too little R&D spend
        # is a slow-motion risk, too much is unfunded. Neither direction alone
        # describes it, so score it against the cohort's inter-quartile range.
        if self.direction == Direction.target_band:
            b = self.benchmark
            if b is not None and b.p25 is not None and b.p75 is not None:
                lo, hi = min(b.p25, b.p75), max(b.p25, b.p75)
                if lo <= value <= hi:
                    return "green"
                # Judge severity by how far outside the band it sits.
                span = (hi - lo) or abs(hi) or 1.0
                excess = (lo - value if value < lo else value - hi) / span
                return "red" if excess > 1.0 else "amber"
            return "unscored"

        if self.alert_bands is None:
            return "unscored"

        g, r = self.alert_bands.green, self.alert_bands.red
        if self.direction == Direction.higher_is_better:
            if value >= g:
                return "green"
            return "red" if value <= r else "amber"
        if self.direction == Direction.lower_is_better:
            if value <= g:
                return "green"
            return "red" if value >= r else "amber"
        return "unscored"

    def vs_benchmark(self, value: Optional[float]) -> Optional[str]:
        """Where the value sits in the benchmark cohort."""
        b = self.benchmark
        if value is None or b is None or b.p50 is None:
            return None
        better_is_up = self.direction == Direction.higher_is_better
        p25, p50, p75 = b.p25, b.p50, b.p75
        if better_is_up:
            if p75 is not None and value >= p75:
                return "top_quartile"
            if value >= p50:
                return "above_median"
            if p25 is not None and value <= p25:
                return "bottom_quartile"
            return "below_median"
        # lower_is_better: p75 is the GOOD end by convention in this library
        if p75 is not None and value <= p75:
            return "top_quartile"
        if value <= p50:
            return "above_median"
        if p25 is not None and value >= p25:
            return "bottom_quartile"
        return "below_median"


def slugify(name: str) -> str:
    """A KPI id from a display name: 'Net Burn / FTE' -> 'net_burn_fte'."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned or "custom_kpi"


def user_kpi(name: str, expression: str, unit: str, direction: str,
             kpi_id: Optional[str] = None, **overrides) -> KPI:
    """Build a KPI from the four fields a user actually has to decide.

    The record sheet mandates eight fields with no defaults, deliberately: a
    metric with no owner, formula, source or target is a number, not a KPI. But
    holding a user's own calculation to that bar means an eight-field form to
    add one expression, and nobody does that twice.

    The four required here are the ones that change what the user sees — `unit`
    drives formatting (a ratio shown as currency is gibberish) and `direction`
    drives the RAG colour (backwards, and green means trouble). The rest get
    honest defaults, `origin` marks the result as user-defined so the appendix
    does not pass it off as reviewed, and anything can still be set through
    **overrides for someone who wants the full sheet.
    """
    return KPI(
        id=kpi_id or slugify(name),
        name=name.strip(),
        perspective=overrides.pop("perspective", Perspective.financial),
        tier=overrides.pop("tier", Tier.functional_l2),
        timing=overrides.pop("timing", Timing.lagging),
        direction=Direction(direction),
        # The prose `formula` field is what renders into the definitions tab.
        # The expression is the most honest description of a user KPI there is.
        formula=overrides.pop("formula", expression),
        unit=unit,
        owner_role=overrides.pop("owner_role", "Unassigned"),
        compute=Compute(kind=ComputeKind.formula, expression=expression),
        origin=Origin.user,
        **overrides,
    )


class KPISet(BaseModel):
    """Output of the selection engine — the contract for everything downstream."""
    north_star: str
    kpis: List[KPI]
    rationale: Dict[str, str] = Field(default_factory=dict)
    dropped: Dict[str, str] = Field(default_factory=dict)

    def by_id(self, kpi_id: str) -> Optional[KPI]:
        return next((k for k in self.kpis if k.id == kpi_id), None)

    def by_tier(self, tier: Tier) -> List[KPI]:
        return [k for k in self.kpis if k.tier == tier]

    def by_perspective(self, p: Perspective) -> List[KPI]:
        return [k for k in self.kpis if k.perspective == p]

    @property
    def leading_share(self) -> float:
        if not self.kpis:
            return 0.0
        return sum(1 for k in self.kpis if k.timing == Timing.leading) / len(self.kpis)
