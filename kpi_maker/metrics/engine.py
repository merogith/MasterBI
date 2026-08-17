"""Metrics engine: KPISet + generated tables -> MetricResult per KPI.

One registered function per KPI id. Each returns a monthly pandas Series (or
None if the metric cannot be computed from the available tables — which is
recorded, never silently hidden).

This module is the ONLY place a KPI's arithmetic lives. The YAML `formula`
field is documentation for humans; this is the implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from ..kpi.schema import KPI, KPISet
from ..profile.schema import CompanyProfile
from .targets import resolve_target

# LTV horizon cap. Without this, low churn sends LTV to infinity and the
# LTV/CAC ratio becomes fiction. See the `pitfalls` note on ltv_cac_ratio.
LTV_HORIZON_MONTHS = 36

_REGISTRY: Dict[str, Callable[[MetricContext], Optional[pd.Series]]] = {}


def metric(kpi_id: str):
    def wrap(fn):
        _REGISTRY[kpi_id] = fn
        return fn
    return wrap


MEASURED = "measured"
MODELLED = "modelled"
MIXED = "mixed"


class TrackedTables(dict):
    """A table dict that remembers which tables were read.

    This is how a metric's `basis` is determined without asking every KPI to
    declare its inputs. Every read goes through `ctx.fin`, `ctx.mov`,
    `ctx.tables.get(...)` and friends, so recording here catches all of them —
    and catches a new metric automatically instead of relying on whoever wrote
    it to remember.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.accessed: set = set()

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)

    def reset(self) -> None:
        self.accessed = set()


@dataclass
class MetricContext:
    profile: CompanyProfile
    tables: Dict[str, pd.DataFrame]
    # {table: "measured" | "modelled"}. Absent means measured — synthetic runs
    # have no notion of a gap to fill, and every table is as real as any other.
    origins: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tables, TrackedTables):
            self.tables = TrackedTables(self.tables)

    def basis_for(self, tables_read) -> str:
        """measured | modelled | mixed, from what a metric actually touched."""
        kinds = {self.origins.get(t, MEASURED) for t in tables_read
                 if t in self.tables}
        if not kinds or kinds == {MEASURED}:
            return MEASURED
        if kinds == {MODELLED}:
            return MODELLED
        return MIXED

    @property
    def fin(self) -> pd.DataFrame:
        return self.tables["monthly_financials"].set_index("month")

    @property
    def mov(self) -> pd.DataFrame:
        return self.tables["mrr_movements"]

    @property
    def cust(self) -> pd.DataFrame:
        return self.tables["customers"]

    @property
    def pipe(self) -> pd.DataFrame:
        return self.tables["pipeline"].set_index("month")

    @property
    def mkt(self) -> pd.DataFrame:
        return self.tables["marketing"]

    @property
    def hc(self) -> pd.DataFrame:
        return self.tables["headcount"]

    def movement(self, kind: str) -> pd.Series:
        """Monthly total for one movement type, reindexed onto the full timeline."""
        s = (
            self.mov[self.mov["movement_type"] == kind]
            .groupby("month")["delta_mrr"].sum()
        )
        return s.reindex(self.fin.index, fill_value=0.0)

    def customer_mrr_matrix(self) -> pd.DataFrame:
        """month x customer_id MRR. The basis for all cohort retention maths."""
        if not hasattr(self, "_matrix"):
            pivot = self.mov.pivot_table(
                index="month", columns="customer_id", values="delta_mrr",
                aggfunc="sum", fill_value=0.0,
            )
            self._matrix = pivot.cumsum().clip(lower=0.0)
        return self._matrix

    def fte(self) -> pd.Series:
        return self.hc.groupby("month")["fte"].sum().reindex(self.fin.index).ffill()


@dataclass
class MetricResult:
    kpi: KPI
    series: Optional[pd.Series]
    current: Optional[float]
    prior_year: Optional[float]
    prior_month: Optional[float]
    target: Optional[float]
    status: str
    benchmark_position: Optional[str]
    computed: bool
    reason: str = ""
    # Whether the number was measured from the user's own data or produced by
    # the generator filling a gap. A modelled number shown as a measured one is
    # the worst thing this product can do, so it travels with the result rather
    # than being reconstructed by whoever renders it.
    basis: str = MEASURED
    tables_used: tuple = ()
    # `{dimension: {level: series}}`, when the run asked for a cut and this
    # metric could actually be measured within it.
    #
    # Nested, because a run can be sliced more than one way — a retailer asks
    # "which channel" and "which category" as different questions — and 3.4's
    # decomposition exists precisely to rank one dimension's contribution
    # against another's. Flattening would make that impossible to express.
    #
    # Empty is the common and honest case: most metrics need a cost line, and
    # costs are not split by segment because nothing measures them that way.
    by_segment: Dict[str, Dict[str, pd.Series]] = field(default_factory=dict)

    def current_by_segment(self, dimension: str) -> Dict[str, Optional[float]]:
        return {level: _last_valid(series)
                for level, series in self.by_segment.get(dimension, {}).items()}

    @property
    def dimensions(self) -> List[str]:
        """Cuts this metric could actually be measured within."""
        return [d for d, levels in self.by_segment.items() if len(levels) > 1]

    @property
    def segmented(self) -> bool:
        return bool(self.dimensions)

    @property
    def yoy_change(self) -> Optional[float]:
        if self.current is None or self.prior_year in (None, 0):
            return None
        return self.current - self.prior_year

    @property
    def vs_target(self) -> Optional[float]:
        if self.current is None or self.target in (None, 0):
            return None
        return self.current - self.target


# --------------------------------------------------------------------------
# Financial
# --------------------------------------------------------------------------

@metric("arr")
def _arr(ctx):
    return ctx.fin["arr"]


@metric("arr_growth_yoy")
def _arr_growth(ctx):
    arr = ctx.fin["arr"]
    return (arr / arr.shift(12) - 1).replace([np.inf, -np.inf], np.nan)


@metric("net_new_arr")
def _net_new_arr(ctx):
    return ctx.mov.groupby("month")["delta_mrr"].sum().reindex(ctx.fin.index, fill_value=0.0) * 12


@metric("gross_margin_pct")
def _gross_margin(ctx):
    return ctx.fin["gross_margin_pct"]


@metric("ebitda_margin")
def _ebitda_margin(ctx):
    # Trailing 12 months: a single month of EBITDA margin is mostly noise.
    return ctx.fin["ebitda"].rolling(12).sum() / ctx.fin["revenue"].rolling(12).sum()


@metric("rule_of_40")
def _rule_of_40(ctx):
    return _arr_growth(ctx) + _ebitda_margin(ctx)


@metric("cac_payback_months")
def _cac_payback(ctx):
    # Trailing 3-month smoothing; a single month is dominated by hiring timing.
    sm = (ctx.fin["sales_cost"] + ctx.fin["marketing_cost"]).rolling(3).mean()
    new_mrr = ctx.movement("new").rolling(3).mean()
    gross_new_mrr = new_mrr * ctx.fin["gross_margin_pct"]
    return (sm / gross_new_mrr).replace([np.inf, -np.inf], np.nan)


@metric("ltv_cac_ratio")
def _ltv_cac(ctx):
    arpa = _arpa(ctx)
    churn = _logo_churn(ctx)
    gm = ctx.fin["gross_margin_pct"]
    monthly_churn = (churn / 12).clip(lower=1e-4)
    lifetime = (1 / monthly_churn).clip(upper=LTV_HORIZON_MONTHS)   # capped, see note
    ltv = arpa * gm * lifetime
    cac = _blended_cac(ctx)
    return (ltv / cac).replace([np.inf, -np.inf], np.nan)


@metric("magic_number")
def _magic_number(ctx):
    # ARR added over the quarter against the PRIOR quarter's S&M spend.
    # No x4 here: the classic formula annualises a quarterly *GAAP revenue*
    # increase, but an ARR delta is already a run-rate figure. Multiplying
    # again overstates sales efficiency by a third.
    quarterly_arr_added = _net_new_arr(ctx).rolling(3).sum()
    prior_sm = (ctx.fin["sales_cost"] + ctx.fin["marketing_cost"]).rolling(3).sum().shift(3)
    return (quarterly_arr_added / prior_sm).replace([np.inf, -np.inf], np.nan)


# A burn smaller than this share of revenue is rounding, not a cash problem.
# Dividing by it produces technically-correct nonsense like "2,005 months of
# runway", which is worse than reporting nothing.
MATERIAL_BURN_PCT_OF_REVENUE = 0.005
RUNWAY_MEANINGFUL_CEILING_MONTHS = 120


@metric("burn_multiple")
def _burn_multiple(ctx):
    burn_ttm = ctx.fin["net_burn"].rolling(12).sum()
    net_new_ttm = _net_new_arr(ctx).rolling(12).sum()
    result = (burn_ttm / net_new_ttm).replace([np.inf, -np.inf], np.nan)
    # A profitable business has no burn multiple; NaN is the honest answer.
    material = burn_ttm > ctx.fin["revenue"].rolling(12).sum() * MATERIAL_BURN_PCT_OF_REVENUE
    return result.where(material & (net_new_ttm > 0))


@metric("cash_runway_months")
def _runway(ctx):
    burn = ctx.fin["net_burn"].rolling(3).mean()
    material = burn > ctx.fin["revenue"] * MATERIAL_BURN_PCT_OF_REVENUE
    runway = (ctx.fin["cash"] / burn).replace([np.inf, -np.inf], np.nan)
    runway = runway.where(material)
    # Beyond ten years the number stops being a runway and starts being an
    # artefact of a near-zero denominator. Report nothing rather than fiction.
    return runway.where(runway <= RUNWAY_MEANINGFUL_CEILING_MONTHS)


# --------------------------------------------------------------------------
# Customer
# --------------------------------------------------------------------------

@metric("nrr")
def _nrr(ctx):
    m = ctx.customer_mrr_matrix()
    out = {}
    for i, month in enumerate(m.index):
        if i < 12:
            continue
        base_month = m.index[i - 12]
        base = m.loc[base_month]
        cohort = base[base > 0].index          # customers present 12 months ago
        if len(cohort) == 0:
            continue
        start = base[cohort].sum()
        end = m.loc[month, cohort].sum()       # includes their expansion and churn
        out[month] = end / start if start else np.nan
    return pd.Series(out).reindex(ctx.fin.index)


@metric("grr")
def _grr(ctx):
    m = ctx.customer_mrr_matrix()
    out = {}
    for i, month in enumerate(m.index):
        if i < 12:
            continue
        base_month = m.index[i - 12]
        base = m.loc[base_month]
        cohort = base[base > 0].index
        if len(cohort) == 0:
            continue
        start = base[cohort].sum()
        # Cap each customer at their starting MRR: expansion gets no credit.
        end = np.minimum(m.loc[month, cohort].values, base[cohort].values).sum()
        out[month] = end / start if start else np.nan
    return pd.Series(out).reindex(ctx.fin.index)


@metric("logo_churn_rate")
def _logo_churn(ctx):
    churned = (
        ctx.mov[ctx.mov["movement_type"] == "churn"]
        .groupby("month").size().reindex(ctx.fin.index, fill_value=0)
    )
    active = _customer_count(ctx)
    churned_ttm = churned.rolling(12).sum()
    # Denominator is the AVERAGE active base over the window, not the base 12
    # months ago. On a fast-growing book the starting count is much smaller
    # than the population actually exposed to churn, which inflates the rate.
    base = active.rolling(12).mean()
    return (churned_ttm / base).replace([np.inf, -np.inf], np.nan)


@metric("expansion_share")
def _expansion_share(ctx):
    new = ctx.movement("new").rolling(12).sum()
    exp = ctx.movement("expansion").rolling(12).sum()
    total = new + exp
    return (exp / total).replace([np.inf, -np.inf], np.nan)


@metric("arpa")
def _arpa(ctx):
    return (ctx.fin["mrr"] / _customer_count(ctx)).replace([np.inf, -np.inf], np.nan)


@metric("customer_count")
def _customer_count(ctx):
    m = ctx.customer_mrr_matrix()
    return (m > 0).sum(axis=1).reindex(ctx.fin.index).astype(float)


@metric("revenue_concentration_top10")
def _concentration(ctx):
    m = ctx.customer_mrr_matrix()
    out = {}
    for month in m.index:
        row = m.loc[month]
        row = row[row > 0].sort_values(ascending=False)
        out[month] = row.head(10).sum() / row.sum() if row.sum() else np.nan
    return pd.Series(out).reindex(ctx.fin.index)


# --------------------------------------------------------------------------
# Internal process
# --------------------------------------------------------------------------

@metric("win_rate")
def _win_rate(ctx):
    return ctx.pipe["win_rate"].rolling(3).mean()


@metric("pipeline_coverage")
def _pipeline_coverage(ctx):
    return ctx.pipe["pipeline_coverage"]


@metric("sales_cycle_days")
def _sales_cycle(ctx):
    return ctx.pipe["sales_cycle_days"].rolling(3).mean()


@metric("blended_cac")
def _blended_cac(ctx):
    sm = (ctx.fin["sales_cost"] + ctx.fin["marketing_cost"]).rolling(3).mean()
    won = ctx.pipe["opps_won"].rolling(3).mean()
    return (sm / won).replace([np.inf, -np.inf], np.nan)


@metric("lead_to_mql_rate")
def _lead_to_mql(ctx):
    g = ctx.mkt.groupby("month")[["leads", "mqls"]].sum()
    return (g["mqls"] / g["leads"]).replace([np.inf, -np.inf], np.nan).reindex(ctx.fin.index)


@metric("mql_to_sql_rate")
def _mql_to_sql(ctx):
    g = ctx.mkt.groupby("month")[["mqls", "sqls"]].sum()
    return (g["sqls"] / g["mqls"]).replace([np.inf, -np.inf], np.nan).reindex(ctx.fin.index)


# --------------------------------------------------------------------------
# Learning & growth
# --------------------------------------------------------------------------

@metric("arr_per_fte")
def _arr_per_fte(ctx):
    return (ctx.fin["arr"] / ctx.fte()).replace([np.inf, -np.inf], np.nan)


@metric("headcount_growth")
def _headcount_growth(ctx):
    fte = ctx.fte()
    return (fte / fte.shift(12) - 1).replace([np.inf, -np.inf], np.nan)


@metric("employee_attrition")
def _attrition(ctx):
    if "leavers" not in ctx.hc.columns:
        return None
    leavers = ctx.hc.groupby("month")["leavers"].sum().reindex(ctx.fin.index, fill_value=0)
    avg_fte = ctx.fte().rolling(12).mean()
    return (leavers.rolling(12).sum() / avg_fte).replace([np.inf, -np.inf], np.nan)


@metric("rnd_pct_revenue")
def _rnd_pct(ctx):
    return ctx.fin["rnd_cost"] / ctx.fin["revenue"]


# --------------------------------------------------------------------------
# Forward revenue and cash quality (public-company reporting standards)
# --------------------------------------------------------------------------

@metric("billings")
def _billings(ctx):
    if "billings" not in ctx.fin.columns:
        return None
    # Trailing twelve months: a single month of billings is dominated by
    # renewal timing, especially on an enterprise book.
    return ctx.fin["billings"].rolling(12).sum()


@metric("crpo_growth")
def _crpo_growth(ctx):
    if "crpo" not in ctx.fin.columns:
        return None
    crpo = ctx.fin["crpo"]
    return (crpo / crpo.shift(12) - 1).replace([np.inf, -np.inf], np.nan)


@metric("fcf_margin")
def _fcf_margin(ctx):
    if "free_cash_flow" not in ctx.fin.columns:
        return None
    return (ctx.fin["free_cash_flow"].rolling(12).sum()
            / ctx.fin["revenue"].rolling(12).sum()).replace([np.inf, -np.inf], np.nan)


@metric("sm_pct_revenue")
def _sm_pct(ctx):
    sm = (ctx.fin["sales_cost"] + ctx.fin["marketing_cost"]).rolling(12).sum()
    return (sm / ctx.fin["revenue"].rolling(12).sum()).replace([np.inf, -np.inf], np.nan)


@metric("quick_ratio")
def _quick_ratio(ctx):
    gained = (ctx.movement("new") + ctx.movement("expansion")).rolling(12).sum()
    lost = (ctx.movement("churn").abs() + ctx.movement("contraction").abs()).rolling(12).sum()
    return (gained / lost.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


@metric("enterprise_customers")
def _enterprise_customers(ctx, threshold: float = 100_000.0):
    """Customers above the $100k ARR line, month by month."""
    m = ctx.customer_mrr_matrix()
    arr = m * 12.0
    return (arr >= threshold).sum(axis=1).reindex(ctx.fin.index).astype(float)


# --------------------------------------------------------------------------
# Product engagement — the leading half of the scorecard
# --------------------------------------------------------------------------

def _usage(ctx) -> Optional[pd.DataFrame]:
    table = ctx.tables.get("product_usage")
    return None if table is None or table.empty else table.set_index("month")


@metric("activation_rate")
def _activation(ctx):
    usage = _usage(ctx)
    if usage is None:
        return None
    new = usage["new_accounts"].rolling(3).sum()
    activated = usage["activated_accounts"].rolling(3).sum()
    return (activated / new.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


@metric("engagement_ratio")
def _engagement(ctx):
    usage = _usage(ctx)
    if usage is None:
        return None
    return (usage["dau"] / usage["mau"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


@metric("time_to_value_days")
def _time_to_value(ctx):
    usage = _usage(ctx)
    if usage is None:
        return None
    return usage["median_time_to_value_days"].rolling(3).mean()


# --------------------------------------------------------------------------
# Sales capacity
# --------------------------------------------------------------------------

def _capacity(ctx) -> Optional[pd.DataFrame]:
    table = ctx.tables.get("sales_capacity")
    return None if table is None or table.empty else table.set_index("month")


@metric("quota_attainment")
def _quota_attainment(ctx):
    cap = _capacity(ctx)
    if cap is None:
        return None
    return cap["quota_attainment"].rolling(3).mean()


@metric("arr_per_rep")
def _arr_per_rep(ctx):
    cap = _capacity(ctx)
    if cap is None:
        return None
    # Annualised from a trailing quarter of bookings per productive rep.
    return cap["arr_per_productive_rep"].rolling(12).sum()


@metric("rep_ramp_months")
def _rep_ramp(ctx):
    cap = _capacity(ctx)
    if cap is None:
        return None
    return cap["avg_ramp_months"].rolling(3).mean()


@metric("pipeline_created")
def _pipeline_created(ctx):
    pipe = ctx.pipe
    if "open_pipeline_value" not in pipe.columns:
        return None
    # New pipeline entering the funnel each month, smoothed over a quarter.
    created = pipe["open_pipeline_value"] / max(float(pipe["sales_cycle_days"].mean()) / 30.0, 1.0)
    return created.rolling(3).mean()


@metric("employee_enps")
def _enps(ctx):
    # Requires an employee survey feed; the generator does not fabricate one,
    # so this reports honestly as not computable rather than inventing a score.
    return None


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def _resolve_target(kpi: KPI, current: Optional[float],
                    prior_year: Optional[float] = None,
                    prior_month: Optional[float] = None) -> Optional[float]:
    """Evaluate the KPI's target rule. See `metrics/targets.py` for why it is
    no longer a lookup table of four exact strings."""
    return resolve_target(kpi, current, prior_year, prior_month)


def _last_valid(series: pd.Series, offset: int = 0) -> Optional[float]:
    s = series.dropna()
    if len(s) <= offset:
        return None
    value = s.iloc[-1 - offset] if offset else s.iloc[-1]
    return None if pd.isna(value) else float(value)


def _year_ago(series: pd.Series) -> Optional[float]:
    """The value twelve months before the latest one, by date not by position.

    This was `series.iloc[-13]`, which is the same thing only when the index
    has no gaps and the series does not end in nulls. Neither is guaranteed:
    `current` comes from `_last_valid`, which skips trailing NaNs, so a series
    whose final two months are empty compared a real `current` against a point
    fourteen months before it and reported the gap as year-on-year change.

    Uploaded data is where this bites — a month missing from the middle of a
    export shifts every comparison before it by one.
    """
    s = series.dropna()
    if s.empty:
        return None

    index = s.index
    if isinstance(index, (pd.PeriodIndex, pd.DatetimeIndex)):
        try:
            # A PeriodIndex subtracts whole periods; a DatetimeIndex needs a
            # calendar offset, so that Feb 29 lands on Feb 28 rather than 366
            # days earlier.
            target = (index[-1] - 12 if isinstance(index, pd.PeriodIndex)
                      else index[-1] - pd.DateOffset(months=12))
            value = s.get(target)
        except (TypeError, ValueError):
            value = None
        if value is None or (hasattr(value, "__len__") and len(value) != 1):
            return None
        return None if pd.isna(value) else float(value)

    # No date index to reason with — fall back to position, which is what the
    # whole codebase assumed before this helper existed.
    return float(s.iloc[-13]) if len(s) >= 13 and not pd.isna(s.iloc[-13]) else None


class _Evaluator:
    """Computes a KPI's series, resolving formula references on demand.

    Two things make this more than a loop:

    **Formulas can reference other KPIs.** `SAFE_DIV(arr, customer_count)` is
    the natural way to write ARPA, and it is only natural because 35 builtins
    already exist to compose. So a name in a formula is resolved by computing
    that KPI, recursively, and memoising the result.

    **A referenced KPI need not be selected.** If the user's formula needs
    `customer_count` and the selection engine did not pick it, that is a detail
    of their arithmetic, not a request to put it on the dashboard. So references
    resolve against the whole known universe of KPIs and the dependency is
    computed quietly rather than erroring.
    """

    def __init__(self, ctx: MetricContext, universe: Dict[str, KPI]) -> None:
        self.ctx = ctx
        self.universe = universe
        self._cache: Dict[str, Optional[pd.Series]] = {}
        self._in_progress: List[str] = []
        self._tables_used: Dict[str, set] = {}

    def series_for(self, kpi_id: str) -> Optional[pd.Series]:
        if kpi_id in self._cache:
            # Replay the cached KPI's reads into whoever is asking: it will not
            # touch a table again, and without this a caller that only uses
            # memoised dependencies reports having read nothing.
            self.ctx.tables.accessed |= self._tables_used.get(kpi_id, set())
            return self._cache[kpi_id]

        if kpi_id in self._in_progress:
            cycle = " -> ".join(self._in_progress + [kpi_id])
            raise ValueError(f"circular reference between KPIs: {cycle}")

        kpi = self.universe.get(kpi_id)
        if kpi is None:
            raise ValueError(f"unknown KPI {kpi_id!r}")

        self._in_progress.append(kpi_id)
        # Swap in a fresh access set rather than diffing against the previous
        # one. `accessed` accumulates, so a difference silently subtracts every
        # table an EARLIER KPI happened to touch: arr_per_fte reads financials
        # and headcount, but if `arr` ran first it reported headcount alone and
        # came out "modelled" instead of "mixed". Save, isolate, restore.
        outer = self.ctx.tables.accessed
        self.ctx.tables.accessed = set()
        try:
            series = self._compute_one(kpi)
        finally:
            mine = self.ctx.tables.accessed
            # Nested reads from a referenced KPI belong to this one too, which
            # is exactly what makes the basis transitive.
            self.ctx.tables.accessed = outer | mine
            self._in_progress.pop()
        self._tables_used[kpi_id] = mine

        self._cache[kpi_id] = series
        return series

    def _compute_one(self, kpi: KPI) -> Optional[pd.Series]:
        if not kpi.is_formula:
            fn = _REGISTRY.get(kpi.compute_ref)
            if fn is None:
                raise ValueError("no implementation registered for this KPI id")
            return fn(self.ctx)

        from ..formula import FormulaError, evaluate
        from ..formula.evaluate import SeriesResolver

        resolver = SeriesResolver(
            tables=self.ctx.tables,
            month_index=self.ctx.fin.index,
            kpi_lookup=self._lookup,
            profile=self.ctx.profile,
        )
        try:
            return evaluate(kpi.compute.expression or "", resolver)
        except FormulaError as exc:
            raise ValueError(f"formula error: {exc}") from exc

    def tables_used(self, kpi_id: str) -> tuple:
        return tuple(sorted(self._tables_used.get(kpi_id, ())))

    def _lookup(self, name: str) -> Optional[pd.Series]:
        """Name resolution hook for the formula engine.

        Returns None for anything that is not a KPI id, which tells the resolver
        to carry on and try a table column — a formula referencing a nonexistent
        name should be reported by the resolver, which knows what the
        alternatives were, rather than here.
        """
        if name not in self.universe:
            return None
        return self.series_for(name)


def _universe(kpi_set: KPISet) -> Dict[str, KPI]:
    """Every KPI a formula may reference: the selected set plus the library.

    The selected set alone is not enough — see `_Evaluator`. Selected entries
    win on an id clash, because a per-run override of a library KPI should be
    what other formulas in that run see too.
    """
    from ..kpi.selection import load_all_known
    known = {k.id: k for k in load_all_known()}
    known.update({k.id: k for k in kpi_set.kpis})
    return known


SEGMENT_TABLE = "segment_financials"


def dimensions(tables: Dict[str, pd.DataFrame]) -> List[str]:
    """Which cuts this run can be sliced by, in a stable order."""
    frame = tables.get(SEGMENT_TABLE)
    if frame is None or frame.empty:
        return []
    return sorted(frame["dimension"].unique())


def levels(tables: Dict[str, pd.DataFrame], dimension: str) -> List[str]:
    frame = tables.get(SEGMENT_TABLE)
    if frame is None or frame.empty:
        return []
    return sorted(frame.loc[frame["dimension"] == dimension, "segment"].unique())


def slice_tables(tables: Dict[str, pd.DataFrame], dimension: str,
                 level: str) -> Dict[str, pd.DataFrame]:
    """The same fact tables, restricted to one segment.

    Three rules, and the second and third are the ones that keep this honest:

    * **A table carrying the dimension is filtered.** `mrr_movements` has a
      `segment` column, so a per-segment NRR is a real measurement.
    * **`monthly_financials` is replaced by revenue alone**, taken from
      `segment_financials`. The cost lines are *not* split. Scaling COGS by
      revenue share would assume every segment earns the same margin, which is
      an assumption dressed as a measurement — and a wrong margin in a board
      pack is the one failure this project treats as unacceptable. A metric
      needing `cogs` therefore reports that it needs it, per segment, which is
      true.
    * **A table without the dimension is dropped, not shared.** Company-wide
      headcount inside a per-segment revenue-per-head would silently mix grains
      and produce a number that looks fine and means nothing. Dropping it makes
      the metric say "needs the headcount table", which is the truth for this
      slice.
    """
    segment_fin = tables.get(SEGMENT_TABLE)
    if segment_fin is None or segment_fin.empty:
        return {}

    rows = segment_fin[(segment_fin["dimension"] == dimension)
                       & (segment_fin["segment"] == level)]
    if rows.empty:
        return {}

    out: Dict[str, pd.DataFrame] = {
        "monthly_financials": rows[["month", "revenue"]]
        .sort_values("month").reset_index(drop=True),
    }
    for name, frame in tables.items():
        if name in (SEGMENT_TABLE, "monthly_financials"):
            continue
        if frame is not None and dimension in getattr(frame, "columns", ()):
            out[name] = frame[frame[dimension] == level].reset_index(drop=True)
    return out


def compute(kpi_set: KPISet, tables: Dict[str, pd.DataFrame],
            profile: CompanyProfile,
            origins: Optional[Dict[str, str]] = None,
            by: Union[None, str, Sequence[str]] = None) -> List[MetricResult]:
    """Every selected KPI, blended — and per segment when `by` names a dimension.

    The blended series is always the company's. `by` adds a second view beside
    it rather than replacing it: a segment that behaves differently from the
    average is the finding, and you cannot see that without both.
    """
    ctx = MetricContext(profile=profile, tables=tables, origins=origins or {})
    evaluator = _Evaluator(ctx, _universe(kpi_set))
    results: List[MetricResult] = []

    for kpi in kpi_set.kpis:
        try:
            series = evaluator.series_for(kpi.id)
        except KeyError as exc:
            # A partial upload has fewer tables than the generator makes. Say
            # which one is missing: a bare KeyError repr tells the user nothing
            # about what to supply, and "why isn't X on my dashboard" must
            # always have an answer.
            results.append(MetricResult(
                kpi=kpi, series=None, current=None, prior_year=None, prior_month=None,
                target=None, status="unknown", benchmark_position=None,
                computed=False,
                reason=f"needs the {exc} table, which this run does not have",
            ))
            continue
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            results.append(MetricResult(
                kpi=kpi, series=None, current=None, prior_year=None, prior_month=None,
                target=None, status="unknown", benchmark_position=None,
                computed=False, reason=str(exc),
            ))
            continue

        if series is None or series.dropna().empty:
            results.append(MetricResult(
                kpi=kpi, series=None, current=None, prior_year=None, prior_month=None,
                target=None, status="unknown", benchmark_position=None,
                computed=False, reason="insufficient data in the available tables",
            ))
            continue

        series = series.astype(float)
        current = _last_valid(series)
        prior_month = _last_valid(series, offset=1)
        prior_year = _year_ago(series)

        used = evaluator.tables_used(kpi.id)
        results.append(MetricResult(
            kpi=kpi,
            series=series,
            current=current,
            prior_year=prior_year,
            prior_month=prior_month,
            target=_resolve_target(kpi, current, prior_year, prior_month),
            status=kpi.status(current),
            benchmark_position=kpi.vs_benchmark(current),
            computed=True,
            basis=ctx.basis_for(used),
            tables_used=used,
        ))

    wanted = [by] if isinstance(by, str) else list(by or ())
    for dimension in wanted:
        _attach_segments(results, kpi_set, tables, profile, origins, dimension)
    return results


def _attach_segments(results: List[MetricResult], kpi_set: KPISet,
                     tables: Dict[str, pd.DataFrame], profile: CompanyProfile,
                     origins: Optional[Dict[str, str]], dimension: str) -> None:
    """Re-evaluate every KPI once per level of `dimension`, in place.

    A whole second evaluation per segment rather than something cleverer: the
    formula engine, the builtin registry and the reference resolver all read
    from `ctx.tables`, so handing them a sliced set is the only way to get a
    per-segment answer that goes through exactly the same arithmetic as the
    blended one. Anything else would be a second implementation of every
    metric, which is the drift this repo spends its tests preventing.

    A metric that cannot be computed within a slice is simply absent from
    `by_segment`. That is the common case and it is the honest one: most
    metrics need a cost line, and costs are not split by segment because
    nothing in the data measures them that way.
    """
    universe = _universe(kpi_set)
    by_id = {r.kpi.id: r for r in results}

    for level in levels(tables, dimension):
        sliced = slice_tables(tables, dimension, level)
        if not sliced:
            continue
        ctx = MetricContext(profile=profile, tables=sliced, origins=origins or {})
        evaluator = _Evaluator(ctx, universe)
        for kpi in kpi_set.kpis:
            try:
                series = evaluator.series_for(kpi.id)
            except Exception:                            # noqa: BLE001
                # Expected, and frequent: this slice does not carry a table or
                # a column the metric needs. The blended figure stands alone.
                continue
            if series is None or series.dropna().empty:
                continue
            result = by_id.get(kpi.id)
            if result is not None:
                result.by_segment.setdefault(dimension, {})[level] = \
                    series.astype(float)


def facts_table(results: List[MetricResult]) -> pd.DataFrame:
    """The compact, LLM-safe view of the numbers.

    This — not the row-level data — is what any narrative agent is allowed to
    see. A few KB, every number already computed, nothing to hallucinate from.
    """
    rows = []
    for r in results:
        rows.append({
            "kpi_id": r.kpi.id,
            "name": r.kpi.name,
            "perspective": r.kpi.perspective.value,
            "tier": int(r.kpi.tier),
            "timing": r.kpi.timing.value,
            "unit": r.kpi.unit,
            "direction": r.kpi.direction.value,
            "current": r.current,
            "prior_month": r.prior_month,
            "prior_year": r.prior_year,
            "yoy_change": r.yoy_change,
            "target": r.target,
            "vs_target": r.vs_target,
            "status": r.status,
            "benchmark_p50": r.kpi.benchmark.p50 if r.kpi.benchmark else None,
            "benchmark_position": r.benchmark_position,
            "owner": r.kpi.owner_role,
            "computed": r.computed,
            "basis": r.basis,
            "reason": r.reason,
        })
    return pd.DataFrame(rows)
