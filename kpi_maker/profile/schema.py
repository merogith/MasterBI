"""CompanyProfile — the single input contract for the whole pipeline.

Every mode (Samples / Survey / AI Builder) produces one of these. Nothing
downstream knows or cares which mode it came from.

Python 3.9 compatible: use typing.Optional/List/Dict, not PEP 604 unions.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------
# Controlled vocabularies. These are the values that BRANCH the pipeline —
# a KPI's `applies_when` expression is evaluated against them, so adding a
# value here without adding KPI coverage will silently narrow a dashboard.
# --------------------------------------------------------------------------

class BusinessModel(str, Enum):
    saas = "saas"
    ecommerce = "ecommerce"
    retail = "retail"
    manufacturing = "manufacturing"
    services = "services"
    marketplace = "marketplace"
    distribution = "distribution"
    hospitality = "hospitality"
    healthcare = "healthcare"
    logistics = "logistics"


class CustomerType(str, Enum):
    b2b = "B2B"
    b2c = "B2C"
    b2b2c = "B2B2C"
    b2g = "B2G"


class RevenueModel(str, Enum):
    subscription = "subscription"
    transactional = "transactional"
    project = "project"
    licensing = "licensing"
    usage = "usage"
    commission = "commission"
    advertising = "advertising"


class SalesMotion(str, Enum):
    self_serve = "self_serve"
    inside = "inside"
    field = "field"
    channel = "channel"
    tender = "tender"


class Stage(str, Enum):
    pre_revenue = "pre_revenue"
    early = "early"
    growth = "growth"
    established = "established"
    mature = "mature"
    turnaround = "turnaround"


class Objective(str, Enum):
    growth = "growth"
    margin_expansion = "margin_expansion"
    retention = "retention"
    cash_liquidity = "cash_liquidity"
    quality = "quality"
    market_entry = "market_entry"
    turnaround = "turnaround"
    fundraise = "fundraise"
    exit_prep = "exit_prep"
    cost_reduction = "cost_reduction"


class Audience(str, Enum):
    board = "board"
    exec = "exec"
    department_head = "department_head"
    investor = "investor"
    bank = "bank"
    operational_team = "operational_team"


class DataMaturity(str, Enum):
    none = "none"
    spreadsheets = "spreadsheets"
    bi_tool = "bi_tool"
    warehouse = "warehouse"
    data_team = "data_team"


class KpiExperience(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


# --------------------------------------------------------------------------
# Profile blocks
# --------------------------------------------------------------------------

class Identity(BaseModel):
    name: str
    country: str = "US"
    currency: str = "USD"
    fiscal_year_start: str = "01-01"
    language: str = "en"


class Industry(BaseModel):
    taxonomy: str = "internal"
    code: Optional[str] = None
    internal_sector: str
    vertical_tags: List[str] = Field(default_factory=list)


class ContractTerms(str, Enum):
    monthly = "monthly"
    annual = "annual"
    multi_year = "multi_year"
    mixed = "mixed"
    # A retailer sells a thing and the transaction is over. Without this the
    # only honest options were to call a single purchase a "monthly contract"
    # or to leave the field lying — and the deferred-revenue maths below reads
    # directly off it, so a lie here would invent a balance that does not exist.
    none = "none"


class BusinessModelBlock(BaseModel):
    type: BusinessModel
    customer_type: CustomerType
    revenue_model: List[RevenueModel]
    sales_motion: SalesMotion

    # Contract structure drives every forward-revenue metric. Billings,
    # deferred revenue and RPO are meaningless without knowing how much of the
    # book is invoiced up front — a monthly-billed business has essentially no
    # deferred revenue, an annual-prepay business has a year of it.
    contract_terms: ContractTerms = ContractTerms.mixed
    annual_prepay_share: float = Field(default=0.55, ge=0.0, le=1.0)
    # 0 is legal and means "no contract" — a single transaction has no term.
    avg_contract_months: float = Field(default=12.0, ge=0.0, le=60.0)


class Ownership(str, Enum):
    family = "family"
    vc_backed = "vc_backed"
    pe_backed = "pe_backed"
    public = "public"
    state = "state"
    coop = "coop"


class Size(BaseModel):
    headcount_total: int
    headcount_by_function: Dict[str, int] = Field(default_factory=dict)
    revenue_band: str
    stage: Stage
    age_years: int = 5
    # Ownership shapes reporting conventions and which benchmark cohort
    # applies: a PE-backed business reports on debt covenants, a VC-backed one
    # on burn and runway, a public one on guidance.
    ownership: Ownership = Ownership.vc_backed

    @model_validator(mode="after")
    def _functions_reconcile(self) -> Size:
        # A soft gate: the function split must not contradict the total. This is
        # the earliest place inconsistent input can be caught, and catching it
        # here saves a confusing "revenue per FTE" number 4 stages downstream.
        if self.headcount_by_function:
            split = sum(self.headcount_by_function.values())
            if abs(split - self.headcount_total) > max(2, 0.1 * self.headcount_total):
                raise ValueError(
                    f"headcount_by_function sums to {split} but headcount_total "
                    f"is {self.headcount_total} (>10% apart)"
                )
        return self


class Segment(BaseModel):
    """A customer segment. Shares must sum to 1.0 across the market block."""
    name: str
    share: float = Field(ge=0.0, le=1.0)
    avg_acv: float
    logo_churn_annual: float = Field(ge=0.0, le=1.0)
    expansion_annual: float = 0.0


class Market(BaseModel):
    geographies: Dict[str, float] = Field(default_factory=dict)
    segments: List[Segment] = Field(default_factory=list)
    customer_count: int = 0
    concentration_top10_pct: float = 0.0
    seasonality: str = "none"

    @model_validator(mode="after")
    def _shares_sum(self) -> Market:
        for label, values in (
            ("segments", [s.share for s in self.segments]),
            ("geographies", list(self.geographies.values())),
        ):
            if values and abs(sum(values) - 1.0) > 0.01:
                raise ValueError(f"{label} shares sum to {sum(values):.3f}, expected 1.0")
        return self


class Financials(BaseModel):
    revenue: float
    gross_margin_pct: float = Field(ge=-1.0, le=1.0)
    opex_split: Dict[str, float] = Field(default_factory=dict)
    cash: float = 0.0
    net_debt: float = 0.0

    # Actual growth, when the user knows it. Without this the generator infers
    # growth from company stage alone, which means two businesses that describe
    # themselves as "growing" get identical trajectories no matter what they
    # actually told us. An explicit answer must beat an inferred one.
    growth_rate_yoy: Optional[float] = Field(default=None, ge=-0.95, le=10.0)


class OrgCulture(BaseModel):
    data_maturity: DataMaturity = DataMaturity.spreadsheets
    decision_cadence: str = "monthly"
    planning_horizon: str = "annual"
    risk_appetite: str = "balanced"
    kpi_experience: KpiExperience = KpiExperience.medium


class Intent(BaseModel):
    primary_objective: Objective
    secondary: List[Objective] = Field(default_factory=list)
    horizon_months: int = 12
    audience: Audience = Audience.exec


class DataAvailability(BaseModel):
    has: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    """The contract. Validated once, then trusted everywhere downstream."""

    identity: Identity
    industry: Industry
    business_model: BusinessModelBlock
    size: Size
    market: Market
    financials: Financials
    org_culture: OrgCulture = Field(default_factory=OrgCulture)
    intent: Intent
    data_availability: DataAvailability = Field(default_factory=DataAvailability)

    # How we came to know each field. Keys are dotted paths; values are one of
    # "user_survey" | "benchmark_default:<key>" | "ai_inferred:<source>" | "sample".
    # Anything defaulted must be footnoted in the report rather than presented
    # as fact — this dict is what makes that possible.
    provenance: Dict[str, str] = Field(default_factory=dict)

    # Seed for the synthetic data generator. Same profile + same seed always
    # yields the same company; reproducibility is a product feature, not a
    # debugging convenience.
    seed: int = 42

    # Number of months of history to generate / expect.
    history_months: int = 36

    @model_validator(mode="after")
    def _revenue_ties_to_the_book(self) -> CompanyProfile:
        """Cross-block identity: customers x blended ACV must approximate revenue.

        These three numbers are usually collected independently (different
        questions, sometimes different people), so they drift. Catching it here
        is the difference between a report built on a contradiction and one the
        user gets to correct. The tolerance is wide because the surviving book's
        segment mix legitimately differs from the acquisition mix.
        """
        blended_acv = sum(s.share * s.avg_acv for s in self.market.segments)
        customers = self.market.customer_count
        if blended_acv <= 0 or customers <= 0 or self.financials.revenue <= 0:
            return self

        implied = customers * blended_acv
        drift = abs(implied - self.financials.revenue) / self.financials.revenue
        if drift > 0.35:
            raise ValueError(
                f"revenue does not reconcile with the customer book: "
                f"{customers:,} customers x {blended_acv:,.0f} blended ACV "
                f"= {implied:,.0f}, but financials.revenue is "
                f"{self.financials.revenue:,.0f} ({drift:.0%} apart). "
                f"Fix one of: customer_count (implied {self.financials.revenue / blended_acv:,.0f}), "
                f"segment avg_acv (implied blended {self.financials.revenue / customers:,.0f}), "
                f"or revenue (implied {implied:,.0f})."
            )
        return self

    def provenance_of(self, path: str) -> str:
        return self.provenance.get(path, "user_survey")

    def is_defaulted(self, path: str) -> bool:
        return self.provenance_of(path).startswith("benchmark_default")

    @property
    def confidence(self) -> float:
        """Share of tracked fields that came from the user rather than a prior."""
        if not self.provenance:
            return 1.0
        defaulted = sum(1 for v in self.provenance.values() if v.startswith("benchmark_default"))
        return round(1.0 - defaulted / len(self.provenance), 2)
