"""KPI record sheet — after Neely et al. (Cambridge Performance Measurement).

A metric with no owner, no formula, no source and no target is a number, not a
KPI. This schema makes that distinction structural: the library cannot express
an under-specified metric.
"""
from __future__ import annotations

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

    formula: str          # human-readable; the metrics engine implements by id
    unit: str             # currency | pct | months | days | count | ratio | score
    frequency: str = "monthly"
    owner_role: str
    source_systems: List[str] = Field(default_factory=list)

    benchmark: Optional[Benchmark] = None
    alert_bands: Optional[AlertBands] = None
    target_rule: Optional[str] = None

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

    @model_validator(mode="after")
    def _bands_match_direction(self) -> "KPI":
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
