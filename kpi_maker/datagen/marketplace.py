"""Synthetic data generator for platforms that match two sides and take a cut.

The fifth and last archetype. `marketplace` reached a generator by
approximating onto `ecommerce`, and its own taxonomy note named exactly what
that lost: *"the transactional archetype models the demand side; take rate and
the supply side are not simulated"*. Measured before writing anything, the
visible half was the same as the other two: a platform's dashboard carried
**average order value, category returns and buyer mix**.

**What is different here, and it is one thing.** Every other archetype in this
package describes one side of a transaction. A marketplace has two books — a
book of buyers and a book of sellers — and it fails from the seller side far
more often than from the buyer side. `matches <= min(supply, demand)` is the
only identity in `contract/identities.py` that is about a *market* rather than
about a business, and it is what makes a liquidity problem legible as something
other than weak demand.

**Which number is revenue.** The platform's revenue is the **take**, and GMV is
a separate and much larger number in its own table. That is the agent treatment
ASC 606 requires of anyone who never owns the goods, and getting it the other
way round would report a business twenty times its real size at a fifth of its
real margin. `profile.financials.revenue` is therefore read as net revenue, and
GMV is derived from it and the take rate rather than the other way about.

**Where the structure comes from.** Buyers ask, sellers list, the market
matches whichever side is scarcer, GMV is what those matches were worth, and
revenue is the cut. One direction, so the identities hold by construction — and
the take rate is a *stated* per-category rate rather than something backed out
of revenue, which is what lets the erosion anomaly be visible as an erosion.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract.gate import ReconciliationError, run_gate
from ..profile.schema import CompanyProfile
from ..spec.schema import GeneratorParams
from .base import (
    WARMUP_MONTHS,
    Anomaly,
    Attempt,
    GeneratedData,
    apply_amplitude,
    calibrate,
    generator,
    month_range,
    monthly_growth,
    payroll_budget,
    people_share,
    segment_financials,
    to_reported,
    trim_warmup,
    volatile,
    yoy_growth,
)

# Platform demand is closer to retail than to industry — people transact when
# they have time and money — but without retail's fourth-quarter cliff, because
# a marketplace's categories rarely peak together. Mean exactly 1.0.
MARKETPLACE_SEASONALITY = np.array([
    0.92, 0.94, 1.02, 1.04, 1.06, 1.02,
    0.96, 0.94, 1.04, 1.06, 1.02, 0.98,
])

# `take` is the platform's stated commission, which differs by category far more
# than most people expect: a category with strong sellers negotiates it down,
# and a category the platform has to seed pays for the seeding.
# `value` is the relative worth of one transaction, and `capacity` how many
# matches one active listing can absorb in a month.
CATEGORIES: Dict[str, dict] = {
    "core":      {"share": 0.42, "take": 0.115, "value": 1.00, "capacity": 3.2},
    "premium":   {"share": 0.18, "take": 0.085, "value": 4.30, "capacity": 1.4},
    "long_tail": {"share": 0.27, "take": 0.155, "value": 0.46, "capacity": 5.1},
    "services":  {"share": 0.13, "take": 0.190, "value": 2.10, "capacity": 2.2},
}

# Two-sided acquisition, which is the marketing budget's real shape: a platform
# buys demand and recruits supply, and the two do not respond to the same
# channels.
CHANNELS = {"paid_search": 0.31, "paid_social": 0.24,
            "seller_recruitment": 0.27, "referral": 0.18}
UNPAID_CHANNELS = {"referral"}

#: Buyers per seller arriving. See the note at the acquisition loop.
SELLER_ARRIVAL_RATIO = 4.4

#: Base match efficiency — the share of the scarcer side that actually
#: transacts. Never 1.0: a listing and a request that never find each other are
#: the normal case in every market that has ever existed.
BASE_MATCH_EFFICIENCY = 0.71

#: How long after their last transaction each side still counts as active.
BUYER_LAPSE_MONTHS = 6
SUPPLIER_LAPSE_MONTHS = 4

#: Days of the take sitting in receivables. Short: a platform usually holds the
#: money before it passes it on, which is the one working-capital advantage of
#: never owning the goods.
DEBTOR_DAYS = 21.0


def _plan_anomalies(profile, rng, n_total) -> List[Anomaly]:
    """Deliberate, documented events, chosen to be findable in this shape of data."""
    mid = WARMUP_MONTHS + int(0.45 * (n_total - WARMUP_MONTHS))
    return [
        Anomaly(
            kind="supply_shortage", start_month=mid + 1, end_month=mid + 8,
            magnitude=0.55, segment="premium",
            description=(
                "Active listings in premium fall to about 55% of trend for "
                "eight months, so the match rate collapses while demand keeps "
                "arriving. This is a supply failure and it looks exactly like "
                "weak demand in the revenue line."),
        ),
        Anomaly(
            kind="take_rate_pressure", start_month=mid + 4, end_month=n_total,
            magnitude=0.82, segment="core",
            description=(
                "The commission on the core category settles ~18% lower and "
                "does not recover — the shape of a platform whose largest "
                "sellers have found leverage."),
        ),
        Anomaly(
            kind="buyer_leakage", start_month=mid + 6, end_month=n_total,
            magnitude=1.55, segment="services",
            description=(
                "Buyers in services lapse about 55% faster once they have "
                "transacted, which is what disintermediation looks like from "
                "inside the platform: the first match works and the second one "
                "happens somewhere else."),
        ),
    ]


def _active(anomaly: Optional[Anomaly], t: int) -> bool:
    return anomaly is not None and anomaly.start_month <= t <= anomaly.end_month


# --------------------------------------------------------------------------
# Two books
# --------------------------------------------------------------------------

def _simulate_market(profile, rng, months, segments, growth, anomalies,
                     base_new=260.0, amplitude: float = 1.0):
    """Buyers ask, sellers list, and the market clears whichever side is scarcer.

    Both books are simulated because a marketplace has two, and the shortage
    anomaly is only expressible if the supply side is a thing that can be short.
    """
    n_months = len(months)
    curve = (np.ones(12) if profile.market.seasonality == "none"
             else apply_amplitude(MARKETPLACE_SEASONALITY, amplitude))

    categories = list(CATEGORIES)
    category_p = np.array([CATEGORIES[c]["share"] for c in categories])
    category_p = category_p / category_p.sum()
    n_categories = len(categories)
    capacity = np.array([CATEGORIES[c]["capacity"] for c in categories])
    value = np.array([CATEGORIES[c]["value"] for c in categories])
    # Sellers are drawn against share/capacity, so that each category ends up
    # with listings in proportion to the demand it attracts. Drawing both sides
    # on the same share leaves the low-capacity categories permanently starved:
    # premium listings absorb 1.4 matches against long tail's 5.1, so premium
    # cleared **11% of its requests in every month of the run** — a structural
    # famine that no anomaly can make worse and no recovery can fix, in the one
    # category whose transactions are worth four times the rest.
    seller_p = category_p / capacity
    seller_p = seller_p / seller_p.sum()

    shares = np.array([s.share for s in segments], dtype=float)
    shares = shares / shares.sum()
    spend = np.array([max(s.avg_acv, 1.0) for s in segments], dtype=float)
    lapse = np.array([s.logo_churn_annual for s in segments], dtype=float)
    monthly_lapse = 1.0 - (1.0 - np.clip(lapse, 0.0, 0.95)) ** (1 / 12)

    shortage = next((a for a in anomalies if a.kind == "supply_shortage"), None)
    leakage = next((a for a in anomalies if a.kind == "buyer_leakage"), None)
    shortage_idx = (categories.index(shortage.segment)
                    if shortage and shortage.segment in categories else -1)
    leak_idx = (categories.index(leakage.segment)
                if leakage and leakage.segment in categories else -1)

    # Buyers, as parallel arrays — the book runs to tens of thousands.
    buyer_segment = np.empty(0, dtype=np.int64)
    buyer_category = np.empty(0, dtype=np.int64)
    buyer_spend = np.empty(0, dtype=float)
    buyer_joined = np.empty(0, dtype=np.int64)
    buyer_last = np.empty(0, dtype=np.int64)
    buyer_orders = np.zeros(0, dtype=np.int64)
    buyer_value = np.zeros(0, dtype=float)
    buyer_alive = np.empty(0, dtype=bool)

    # Sellers. A tenth the count of buyers, which is the ratio that makes supply
    # the side that runs out.
    seller_category = np.empty(0, dtype=np.int64)
    seller_joined = np.empty(0, dtype=np.int64)
    seller_last = np.empty(0, dtype=np.int64)
    seller_listings = np.zeros(0, dtype=float)
    seller_gmv = np.zeros(0, dtype=float)
    seller_alive = np.empty(0, dtype=bool)

    demand = np.zeros((n_months, n_categories))
    supply = np.zeros((n_months, n_categories))
    matched = np.zeros((n_months, n_categories))
    units = np.zeros((n_months, n_categories))     # GMV in relative units

    for t in range(n_months):
        seas = curve[months[t].month - 1]
        arrivals = max(base_new * ((1 + growth) ** t) * seas, 0.0)

        n_new = int(rng.poisson(arrivals))
        if n_new:
            draws = rng.choice(len(segments), size=n_new, p=shares)
            cats = rng.choice(n_categories, size=n_new, p=category_p)
            buyer_segment = np.concatenate([buyer_segment, draws])
            buyer_category = np.concatenate([buyer_category, cats])
            buyer_spend = np.concatenate([buyer_spend, rng.lognormal(
                np.log(np.maximum(spend[draws], 1.0)), 0.55)])
            buyer_joined = np.concatenate([buyer_joined, np.full(n_new, t, dtype=np.int64)])
            buyer_last = np.concatenate([buyer_last, np.full(n_new, t, dtype=np.int64)])
            buyer_orders = np.concatenate([buyer_orders, np.zeros(n_new, dtype=np.int64)])
            buyer_value = np.concatenate([buyer_value, np.zeros(n_new)])
            buyer_alive = np.concatenate([buyer_alive, np.ones(n_new, dtype=bool)])

        # Sellers arrive at a fraction of the buyer rate, and the fraction is
        # chosen so supply is the *scarcer* side by a little rather than by a
        # lot. At one in eleven the market cleared 26% of requests — supply four
        # times short, every month, which leaves the shortage anomaly no room to
        # be an anomaly and describes a platform nobody would keep using.
        n_sellers = int(rng.poisson(arrivals / SELLER_ARRIVAL_RATIO))
        if n_sellers:
            cats = rng.choice(n_categories, size=n_sellers, p=seller_p)
            seller_category = np.concatenate([seller_category, cats])
            seller_joined = np.concatenate([seller_joined, np.full(n_sellers, t, dtype=np.int64)])
            seller_last = np.concatenate([seller_last, np.full(n_sellers, t, dtype=np.int64)])
            seller_listings = np.concatenate([seller_listings, np.zeros(n_sellers)])
            seller_gmv = np.concatenate([seller_gmv, np.zeros(n_sellers)])
            seller_alive = np.concatenate([seller_alive, np.ones(n_sellers, dtype=bool)])

        live_buyers = np.flatnonzero(buyer_alive)
        live_sellers = np.flatnonzero(seller_alive)
        if live_buyers.size == 0 or live_sellers.size == 0:
            continue

        # --- what each side brings this month ------------------------------
        asks = rng.poisson(np.full(live_buyers.size, 0.9 * seas))
        asking = live_buyers[asks > 0]
        request_counts = asks[asks > 0]

        listings = np.maximum(rng.normal(
            capacity[seller_category[live_sellers]], 0.4), 0.0)
        if shortage_idx >= 0 and _active(shortage, t):
            listings[seller_category[live_sellers] == shortage_idx] *= shortage.magnitude

        demand_by_cat = np.bincount(
            buyer_category[asking], weights=request_counts.astype(float),
            minlength=n_categories) if asking.size else np.zeros(n_categories)
        supply_by_cat = np.bincount(
            seller_category[live_sellers], weights=listings,
            minlength=n_categories)

        # --- clearing ------------------------------------------------------
        # The scarcer side sets the ceiling, and efficiency is what stops a
        # model in which every listing meets every buyer.
        efficiency = float(np.clip(rng.normal(BASE_MATCH_EFFICIENCY, 0.03),
                                   0.30, 0.95))
        fills = np.minimum(demand_by_cat, supply_by_cat) * efficiency

        demand[t] = demand_by_cat
        supply[t] = supply_by_cat
        matched[t] = fills
        units[t] = fills * value

        # --- attribute the matches back to both books ----------------------
        for ci in range(n_categories):
            if fills[ci] <= 0:
                continue
            share_filled = fills[ci] / max(demand_by_cat[ci], 1e-9)
            mine = asking[buyer_category[asking] == ci]
            if mine.size:
                won = mine[rng.random(mine.size) < min(share_filled, 1.0)]
                buyer_orders[won] += 1
                buyer_value[won] += value[ci] * buyer_spend[won] / 12.0
                buyer_last[won] = t
            sellers_here = live_sellers[seller_category[live_sellers] == ci]
            if sellers_here.size:
                # Everyone who is live listed; only the ones whose listings
                # cleared transacted. Crediting the whole category made every
                # seller look active in every month, so seller churn was
                # **exactly zero** — 578 of 578 active on the sample — and the
                # `quiet` branch below could never fire. A seller who never
                # sells is precisely the one who leaves, and that is the
                # mechanism a marketplace lives or dies by.
                seller_listings[sellers_here] += (
                    supply_by_cat[ci] / sellers_here.size)
                cleared_share = min(
                    1.0, fills[ci] / max(supply_by_cat[ci], 1e-9))
                cleared = sellers_here[
                    rng.random(sellers_here.size) < cleared_share]
                if cleared.size:
                    seller_gmv[cleared] += units[t, ci] / cleared.size
                    seller_last[cleared] = t

        # --- lapsing -------------------------------------------------------
        idle = live_buyers[~np.isin(live_buyers, asking)]
        if idle.size:
            rate = monthly_lapse[buyer_segment[idle]].copy()
            if leak_idx >= 0 and _active(leakage, t):
                # Leakage bites the buyers who have already transacted, which is
                # the point: the first match works and the second one happens
                # somewhere else.
                bitten = (buyer_category[idle] == leak_idx) & (buyer_orders[idle] > 0)
                rate[bitten] *= leakage.magnitude
            buyer_alive[idle[rng.random(idle.size) < rate]] = False

        quiet = live_sellers[seller_last[live_sellers] < t]
        if quiet.size:
            seller_alive[quiet[rng.random(quiet.size) < 0.06]] = False

    horizon = n_months - 1
    kept = np.flatnonzero(buyer_orders > 0)
    customers = pd.DataFrame({
        "customer_id": [f"B{i + 1:06d}" for i in kept],
        "segment": [segments[i].name for i in buyer_segment[kept]],
        "acquired_month": [months[i] for i in buyer_joined[kept]],
        "last_order_month": [months[i] for i in buyer_last[kept]],
        "orders": buyer_orders[kept].astype(float),
        "revenue": buyer_value[kept],
        "is_active": buyer_alive[kept]
        & (buyer_last[kept] >= horizon - BUYER_LAPSE_MONTHS),
    })
    live = np.flatnonzero(seller_listings > 0)
    suppliers = pd.DataFrame({
        "supplier_id": [f"S{i + 1:06d}" for i in live],
        "category": [categories[i] for i in seller_category[live]],
        "joined_month": [months[i] for i in seller_joined[live]],
        "last_active_month": [months[i] for i in seller_last[live]],
        "listings": seller_listings[live],
        "gross_merchandise_value": seller_gmv[live],
        "is_active": seller_alive[live]
        & (seller_last[live] >= horizon - SUPPLIER_LAPSE_MONTHS),
    })
    return customers, suppliers, demand, supply, matched, units


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------

def _build_gmv(months, matched, units, anomalies, scale: float) -> pd.DataFrame:
    """Month x category, with the take rate stated rather than backed out.

    Stating it is what makes the erosion visible as an erosion: a rate derived
    from revenue would absorb the anomaly and report a smaller business at an
    unchanged commission, which is the opposite of what happened.
    """
    categories = list(CATEGORIES)
    pressure = next((a for a in anomalies if a.kind == "take_rate_pressure"), None)

    rows: List[dict] = []
    for t, month in enumerate(months):
        for ci, category in enumerate(categories):
            if matched[t, ci] <= 0:
                continue
            take = CATEGORIES[category]["take"]
            if pressure and pressure.segment == category and _active(pressure, t):
                take *= pressure.magnitude
            gmv = float(units[t, ci]) * scale
            rows.append({
                "month": month, "category": category,
                "orders": float(matched[t, ci]),
                "gross_merchandise_value": gmv,
                "take_rate": take,
                "net_revenue": gmv * take,
            })
    return pd.DataFrame(rows)


def _build_liquidity(months, demand, supply, matched) -> pd.DataFrame:
    """Both sides and what cleared between them, month by month."""
    categories = list(CATEGORIES)
    rows: List[dict] = []
    for t, month in enumerate(months):
        for ci, category in enumerate(categories):
            asked = float(demand[t, ci])
            if asked <= 0 and supply[t, ci] <= 0:
                continue
            fills = float(matched[t, ci])
            rows.append({
                "month": month, "category": category,
                "supply_listings": float(supply[t, ci]),
                "demand_requests": asked,
                "matches": fills,
                "match_rate": fills / asked if asked > 0 else 0.0,
            })
    return pd.DataFrame(rows)


def _build_financials(profile, gmv, months, rng) -> pd.DataFrame:
    """The P&L on the take, never on GMV.

    Cost of sales for a platform is payment processing, fraud and the part of
    support that scales with transactions — real, and a small fraction of the
    commission rather than of the goods. That is why the archetype's margin band
    starts at 55% and not at 8%.
    """
    revenue = (gmv.groupby("month")["net_revenue"].sum()
               .reindex(months, fill_value=0.0))
    target = float(np.clip(profile.financials.gross_margin_pct, 0.45, 0.95))

    opex = profile.financials.opex_split
    n = len(months)
    rows = []
    for t, month in enumerate(months):
        rev = float(revenue.loc[month])
        # Processing cost is a share of GMV, not of the take, so the margin on
        # the take improves as the commission does — which is exactly why a
        # platform fights for take rate rather than for volume.
        cogs = rev * (1.0 - target) * float(rng.normal(1.0, 0.03))
        gross_profit = rev - cogs

        leverage = 1.0 + 0.22 * (1 - t / max(n - 1, 1))
        sales = rev * opex.get("sales", 0.12) * leverage * float(rng.normal(1.0, 0.04))
        marketing = rev * opex.get("marketing", 0.28) * leverage * float(rng.normal(1.0, 0.07))
        rnd = rev * opex.get("rnd", 0.18) * leverage * float(rng.normal(1.0, 0.03))
        ga = rev * opex.get("ga", 0.11) * leverage * float(rng.normal(1.0, 0.03))
        total_opex = sales + marketing + rnd + ga
        ebitda = gross_profit - total_opex

        rows.append({
            "month": month, "revenue": rev, "cogs": cogs,
            "gross_profit": gross_profit,
            "gross_margin_pct": gross_profit / rev if rev else 0.0,
            "sales_cost": sales, "marketing_cost": marketing, "rnd_cost": rnd,
            "ga_cost": ga, "total_opex": total_opex, "ebitda": ebitda,
            "ebitda_margin": ebitda / rev if rev else 0.0,
        })

    fin = pd.DataFrame(rows)
    receivables = (fin["revenue"].rolling(3, min_periods=1).mean()
                   * (DEBTOR_DAYS / 30.0))
    fin["capex"] = fin["revenue"] * 0.012
    fin["free_cash_flow"] = (fin["ebitda"] - fin["capex"]
                             - receivables.diff().fillna(0.0))
    fin["net_burn"] = -fin["free_cash_flow"]
    fin["cash"] = fin["free_cash_flow"].cumsum()
    fin["cash"] += profile.financials.cash - float(fin["cash"].iloc[-1])
    return fin


def _build_headcount(profile, financials, months, rng) -> pd.DataFrame:
    """The roster by function. Trust and safety is its own row for the same
    reason maintenance is in a plant: it is the first thing cut and the first
    thing regretted."""
    total = max(profile.size.headcount_total, 1)
    mix = {"engineering": 0.31, "operations": 0.19, "supply_growth": 0.14,
           "demand_marketing": 0.13, "trust_safety": 0.12, "ga": 0.11}
    # Relative pay per head; the absolute level comes from the P&L. Measured
    # before that change this generator paid its roster **101% of everything the
    # company spent** — more wages than it had money.
    weight = {"engineering": 1.45, "operations": 0.85, "supply_growth": 1.10,
              "demand_marketing": 1.05, "trust_safety": 0.72, "ga": 1.05}
    revenue = financials["revenue"].to_numpy()
    scale = revenue / max(revenue[-1], 1.0)
    budget = payroll_budget(financials, people_share("marketplace"))
    budget.index = list(months)
    rows = []
    for t, month in enumerate(months):
        weighted = {f: total * sh * (0.55 + 0.45 * scale[t]) * weight[f]
                    for f, sh in mix.items()}
        pool = sum(weighted.values()) or 1.0
        for function, share in mix.items():
            fte = max(total * share * (0.55 + 0.45 * scale[t]), 0.0)
            monthly_rate = 0.21 / 12.0
            rows.append({
                "month": month, "function": function, "fte": round(fte, 1),
                "cost": float(budget.loc[month]) * weighted[function] / pool,
                "leavers": int(rng.poisson(fte * monthly_rate)),
                "hires": int(rng.poisson(fte * monthly_rate * 1.3)),
            })
    return pd.DataFrame(rows)


def _build_marketing(financials, months, rng) -> pd.DataFrame:
    """Spend by channel, both sides of the market. Referral carries no spend."""
    budget = financials.set_index("month")["marketing_cost"]
    revenue = financials.set_index("month")["revenue"]
    scale = revenue / max(float(revenue.max()), 1.0)
    paid_share = sum(v for k, v in CHANNELS.items() if k not in UNPAID_CHANNELS)
    rows = []
    for month in months:
        for channel, share in CHANNELS.items():
            paid = channel not in UNPAID_CHANNELS
            spend = (float(budget.loc[month]) * share / paid_share
                     * float(rng.normal(1.0, 0.06))) if paid else 0.0
            rows.append({
                "month": month, "channel": channel, "spend": max(spend, 0.0),
                "leads": max(share * 1800.0 * float(scale.loc[month])
                             * float(rng.normal(1.0, 0.15)), 0.0),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------

@generator("marketplace")
def generate(profile: CompanyProfile,
             params: Optional[GeneratorParams] = None) -> GeneratedData:
    params = params or GeneratorParams()
    rng = volatile(np.random.default_rng(profile.seed), params.volatility)

    months, report_months = month_range(profile.history_months,
                                        end=params.history_end)
    segments = profile.market.segments
    if not segments:
        raise ValueError("marketplace generator requires at least one market segment")

    anomalies = (_plan_anomalies(profile, rng, len(months))
                 if params.inject_anomalies else [])
    growth = monthly_growth(profile)
    report_start = len(months) - profile.history_months

    def simulate(inner_rng, base_new, current_growth) -> Attempt:
        inner_rng = volatile(inner_rng, params.volatility)
        book = _simulate_market(profile, inner_rng, months, segments,
                                current_growth, anomalies, base_new,
                                amplitude=params.seasonality_amplitude)
        customers, units = book[0], book[5]
        if customers.empty or units.sum() <= 0:
            return Attempt(count=0, growth=None, payload=(book, inner_rng))
        value = pd.Series(units.sum(axis=1), index=list(months))
        return Attempt(
            count=int(customers["is_active"].sum()),
            growth=yoy_growth(value.rolling(12).sum().fillna(0.0), report_start),
            payload=(book, inner_rng),
        )

    book, rng = calibrate(
        simulate, seed=profile.seed,
        target_count=profile.market.customer_count,
        target_growth=profile.financials.growth_rate_yoy,
        growth=growth, base_new=260.0)

    customers, suppliers, demand, supply, matched, units = book
    if customers.empty or units.sum() <= 0:
        raise ReconciliationError("simulation produced no matched transactions")

    # Scale so the trailing year's **take** is the stated revenue. Solved on the
    # take rather than on GMV, because the take is what the profile means by
    # revenue — see the module docstring. GMV then falls out at whatever the
    # commission implies, which is the honest direction: nobody is asked their
    # GMV in the survey and everybody knows their revenue.
    trial = _build_gmv(months, matched, units, anomalies, 1.0)
    if trial.empty:
        raise ReconciliationError("simulation produced no GMV")
    by_month = (trial.groupby("month")["net_revenue"].sum()
                .reindex(months, fill_value=0.0))
    trailing = float(by_month.iloc[-12:].sum()) if len(months) >= 12 \
        else float(by_month.sum())
    if trailing <= 0:
        raise ReconciliationError("simulation produced non-positive revenue")
    scale = profile.financials.revenue / trailing

    gmv = _build_gmv(months, matched, units, anomalies, scale)
    liquidity = _build_liquidity(months, demand, supply, matched)
    suppliers["gross_merchandise_value"] *= scale
    customers["revenue"] *= scale

    financials = _build_financials(profile, gmv, months, rng)
    headcount = _build_headcount(profile, financials, months, rng)
    marketing = _build_marketing(financials, months, rng)

    # Category and buyer segment, the two cuts a platform reviews.
    buyer_revenue = pd.DataFrame([
        {"month": month, "buyer_segment": name, "revenue": float(value)}
        for month, row in gmv.groupby("month")["net_revenue"].sum().items()
        for name, value in (
            (s.name, row * s.share / sum(x.share for x in segments))
            for s in segments)
    ])
    segment_fin = segment_financials(financials, {
        "category": (gmv, "net_revenue", False),
        "buyer_segment": (buyer_revenue, "revenue", False),
    })

    tables = trim_warmup(
        {
            "monthly_financials": financials,
            "segment_financials": segment_fin,
            "gmv": gmv,
            "liquidity": liquidity,
            "suppliers": suppliers,
            "customers": customers,
            "headcount": headcount,
            "marketing": marketing,
        },
        cutoff=report_months[0],
        keep_full=("customers", "suppliers"),
    )

    checks = reconcile(tables, profile)
    return GeneratedData(tables=tables, anomalies=to_reported(anomalies),
                         checks=checks)


def reconcile(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[str]:
    """Hold generated platform data to both tiers, as the other four are."""
    return run_gate(tables, profile, source="synthetic",
                    archetype="marketplace").checks
