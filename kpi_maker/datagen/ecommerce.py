"""Synthetic data generator for e-commerce and D2C businesses.

The second archetype, and the one that decides whether the seams P4 opened are
real. It follows the same design rules as `subscription.py` — sample a small
number of primitives and derive the rest through identities, warm up before
the reported window, add structure rather than noise, scale to the profile at
the end — but it shares none of the subscription vocabulary.

**What the profile means here.** The same `CompanyProfile` describes a retailer,
read differently: a segment's `avg_acv` is what a customer spends in a year,
`logo_churn_annual` is the rate at which buyers lapse, and `customer_count` is
the active buyer base. Nothing in the profile schema needed to change, which is
the useful discovery — it was already a description of a business rather than a
description of a SaaS business.

**Where the structure comes from.** Orders drive everything. Revenue is derived
from them, the P&L from revenue, traffic backwards from orders through the
funnel, and inventory from units sold. That single direction is what makes the
identities in `contract/identities.py` hold by construction rather than by
being asserted twice.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract.gate import ReconciliationError, run_gate
from ..profile.schema import CompanyProfile
from ..spec.schema import GeneratorParams
from .base import (WARMUP_MONTHS, Anomaly, Attempt, GeneratedData,
                   apply_amplitude, calibrate, generator, month_range,
                   monthly_growth, to_reported, trim_warmup, volatile,
                   yoy_growth)

# Retail seasonality, which is nothing like B2B software's. The year is built
# around the fourth quarter: Black Friday and Christmas carry it, January is
# the hangover, and there is a small back-to-school lift in late summer.
RETAIL_SEASONALITY = np.array([
    0.72, 0.74, 0.88, 0.90, 0.95, 0.92,   # Jan-Jun
    0.88, 1.02, 0.98, 1.10, 1.65, 1.86,   # Jul-Dec
])

CHANNELS = {
    "organic": 0.28,
    "paid_search": 0.24,
    "paid_social": 0.20,
    "email": 0.16,
    "marketplace": 0.12,
}

CATEGORIES = {
    "apparel": 0.38,
    "accessories": 0.24,
    "footwear": 0.22,
    "home": 0.16,
}

# Return rates differ enough by category that a blended number hides the
# problem — which is the point of carrying the dimension at all.
CATEGORY_RETURN_RATE = {
    "apparel": 0.24, "accessories": 0.08, "footwear": 0.19, "home": 0.06,
}

# Sessions per order, by channel. Marketplace traffic converts far better than
# paid social because the intent is different, and a blended conversion rate
# says nothing about where to spend.
SESSIONS_PER_ORDER = {
    "organic": 38.0, "paid_search": 30.0, "paid_social": 62.0,
    "email": 18.0, "marketplace": 14.0,
}


def _plan_anomalies(profile, rng, n_total) -> List[Anomaly]:
    """Deliberate, documented events, chosen to be findable in this shape of data."""
    mid = WARMUP_MONTHS + int(0.45 * (n_total - WARMUP_MONTHS))
    worst = max(CATEGORY_RETURN_RATE, key=CATEGORY_RETURN_RATE.get)
    return [
        Anomaly(
            kind="return_spike", start_month=mid, end_month=mid + 5,
            magnitude=1.7, segment=worst,
            description=(
                f"Returns in {worst} run ~70% above baseline for five months — "
                f"consistent with a sizing change or a supplier switch."),
        ),
        Anomaly(
            kind="paid_efficiency_decay", start_month=mid + 4, end_month=n_total,
            magnitude=1.5,
            description=(
                "Paid social needs ~50% more sessions per order and does not "
                "recover, dragging blended acquisition cost."),
        ),
        Anomaly(
            kind="margin_compression", start_month=mid - 8, end_month=n_total,
            magnitude=0.04,
            description=(
                "Landed product cost rises faster than price, compressing gross "
                "margin by ~4 points over the period."),
        ),
    ]


# --------------------------------------------------------------------------
# The book of buyers
# --------------------------------------------------------------------------

def _simulate_book(profile, rng, months, segments, growth, anomalies,
                   base_new=180.0, amplitude: float = 1.0):
    """Month-by-month acquisition, repeat purchase and lapse.

    A buyer is not a subscriber: nothing renews, and "active" means bought
    recently rather than not-yet-cancelled. So the loop draws orders per
    customer per month instead of carrying an MRR balance forward.
    """
    seasonality = (apply_amplitude(RETAIL_SEASONALITY, amplitude)
                   if profile.market.seasonality != "none" else np.ones(12))

    shares = np.array([s.share for s in segments], dtype=float)
    shares = shares / shares.sum()
    lapse = np.array([s.logo_churn_annual for s in segments], dtype=float)
    monthly_lapse = 1.0 - (1.0 - np.clip(lapse, 0.0, 0.95)) ** (1 / 12)
    annual_spend = np.array([s.avg_acv for s in segments], dtype=float)

    return_spike = next((a for a in anomalies if a.kind == "return_spike"), None)
    decay = next((a for a in anomalies if a.kind == "paid_efficiency_decay"), None)

    channel_names = list(CHANNELS)
    channel_p = np.array(list(CHANNELS.values()))
    category_names = list(CATEGORIES)
    category_p = np.array(list(CATEGORIES.values()))

    # Buyers are held as parallel arrays rather than a dict of dicts. The
    # subscription generator can afford a Python loop per customer because a
    # SaaS book is a few hundred logos; a retailer's is tens of thousands, and
    # the same shape of code took 126 seconds against the subscription
    # generator's 3.6. Everything below is per MONTH, vectorised across
    # whoever is active in it.
    n_channels, n_categories = len(channel_names), len(category_names)
    category_rate = np.array([CATEGORY_RETURN_RATE[c] for c in category_names])
    sessions_per_channel = np.array([SESSIONS_PER_ORDER[c] for c in channel_names])
    spike_category = (category_names.index(return_spike.segment)
                      if return_spike and return_spike.segment in category_names
                      else -1)
    social = channel_names.index("paid_social") if "paid_social" in channel_names else -1

    seg = np.empty(0, dtype=np.int64)          # segment index per buyer
    spend = np.empty(0, dtype=float)           # their annual spend
    n_orders_total = np.zeros(0, dtype=np.int64)
    revenue_total = np.zeros(0, dtype=float)
    acquired_at = np.empty(0, dtype=np.int64)  # month index
    last_order_at = np.empty(0, dtype=np.int64)
    alive = np.empty(0, dtype=bool)

    aov_hint = max(_aov_hint(profile), 1.0)
    # month x channel x category, flattened. Accumulated with bincount, which
    # is what makes the aggregation cheap enough to do every month.
    cells = len(months) * n_channels * n_categories
    acc_orders = np.zeros(cells)
    acc_units = np.zeros(cells)
    acc_gross = np.zeros(cells)
    acc_discount = np.zeros(cells)
    acc_returns = np.zeros(cells)
    acc_sessions = np.zeros(cells)
    buyer_rows: List[dict] = []

    for t, month in enumerate(months):
        seas = seasonality[month.month - 1]

        # --- acquisition -------------------------------------------------
        n_new = int(rng.poisson(max(base_new * ((1 + growth) ** t) * seas, 0.0)))
        if n_new:
            draws = rng.choice(len(segments), size=n_new, p=shares)
            seg = np.concatenate([seg, draws])
            spend = np.concatenate([spend, rng.lognormal(
                np.log(np.maximum(annual_spend[draws], 1.0)), 0.55)])
            n_orders_total = np.concatenate([n_orders_total, np.zeros(n_new, dtype=np.int64)])
            revenue_total = np.concatenate([revenue_total, np.zeros(n_new)])
            acquired_at = np.concatenate([acquired_at, np.full(n_new, t, dtype=np.int64)])
            last_order_at = np.concatenate([last_order_at, np.full(n_new, t, dtype=np.int64)])
            alive = np.concatenate([alive, np.ones(n_new, dtype=bool)])

        who = np.flatnonzero(alive)
        if who.size == 0:
            buyer_rows.append({"month": month, "new_buyers": 0.0,
                               "repeat_buyers": 0.0, "active_buyers": 0.0})
            continue

        # --- purchase ------------------------------------------------------
        base_orders = spend[who] / 12.0 / aov_hint
        expected = base_orders * seas
        # A new buyer's first order lands in the month they were acquired,
        # which is what makes acquisition and revenue tie together.
        expected = np.where(acquired_at[who] == t, np.maximum(expected, 1.0), expected)
        counts = rng.poisson(np.maximum(expected, 0.0))

        bought = counts > 0
        idle = who[~bought]
        if idle.size:
            # Lapse only among those who did not buy — someone who ordered
            # this month has not gone anywhere.
            lapsed = rng.random(idle.size) < monthly_lapse[seg[idle]]
            alive[idle[lapsed]] = False

        buyers = who[bought]
        counts = counts[bought]
        if buyers.size == 0:
            buyer_rows.append({"month": month, "new_buyers": 0.0,
                               "repeat_buyers": 0.0, "active_buyers": 0.0})
            continue

        first_time = n_orders_total[buyers] == 0
        buyer_rows.append({
            "month": month,
            "new_buyers": float(first_time.sum()),
            "repeat_buyers": float((~first_time).sum()),
            "active_buyers": float(buyers.size),
        })

        n = buyers.size
        channel = rng.choice(n_channels, size=n, p=channel_p)
        category = rng.choice(n_categories, size=n, p=category_p)

        unit_price = spend[buyers] / 12.0 / np.maximum(base_orders[bought], 0.5)
        units = np.maximum(rng.poisson(2.2, size=n), 1)
        gross = unit_price * units * rng.lognormal(0.0, 0.25, size=n)
        discount = gross * np.clip(rng.normal(0.11, 0.04, size=n), 0.0, 0.6)

        rate = category_rate[category].copy()
        if spike_category >= 0 and return_spike.start_month <= t <= return_spike.end_month:
            rate[category == spike_category] *= return_spike.magnitude
        returned = (gross - discount) * np.clip(
            rng.normal(rate, rate * 0.25), 0.0, 0.9)

        sessions_per = sessions_per_channel[channel].copy()
        if decay and social >= 0 and decay.start_month <= t <= decay.end_month:
            sessions_per[channel == social] *= decay.magnitude

        net = (gross - discount - returned) * counts
        n_orders_total[buyers] += counts
        revenue_total[buyers] += net
        last_order_at[buyers] = t

        flat = (t * n_channels + channel) * n_categories + category
        acc_orders += np.bincount(flat, weights=counts.astype(float), minlength=cells)
        acc_units += np.bincount(flat, weights=(units * counts).astype(float), minlength=cells)
        acc_gross += np.bincount(flat, weights=gross * counts, minlength=cells)
        acc_discount += np.bincount(flat, weights=discount * counts, minlength=cells)
        acc_returns += np.bincount(flat, weights=returned * counts, minlength=cells)
        acc_sessions += np.bincount(flat, weights=sessions_per * counts, minlength=cells)

    # --- assemble -----------------------------------------------------------
    used = np.flatnonzero(acc_orders > 0)
    month_index = used // (n_channels * n_categories)
    remainder = used % (n_channels * n_categories)
    orders = pd.DataFrame({
        "month": [months[i] for i in month_index],
        "channel": [channel_names[i] for i in remainder // n_categories],
        "category": [category_names[i] for i in remainder % n_categories],
        "orders": acc_orders[used],
        "units": acc_units[used],
        "gross_revenue": acc_gross[used],
        "discounts": acc_discount[used],
        "returns": acc_returns[used],
        "sessions": acc_sessions[used],
    })

    ever_bought = n_orders_total > 0
    keep = np.flatnonzero(ever_bought)
    customers = pd.DataFrame({
        "customer_id": [f"B{i + 1:06d}" for i in keep],
        "segment": [segments[i].name for i in seg[keep]],
        "acquired_month": [months[i] for i in acquired_at[keep]],
        "last_order_month": [months[i] for i in last_order_at[keep]],
        "orders": n_orders_total[keep].astype(float),
        "revenue": revenue_total[keep],
        "is_active": alive[keep],
    })
    buyers_table = pd.DataFrame(buyer_rows)
    return customers, buyers_table, orders


def _aov_hint(profile) -> float:
    """A first guess at average order value, used only to size order counts.

    Actual AOV falls out of the simulation; this just stops a £2,000-a-year
    customer being modelled as one order a year or as fifty.
    """
    blended = sum(s.share * s.avg_acv for s in profile.market.segments)
    return max(blended / 8.0, 15.0)


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------

def _build_financials(profile, orders, months, rng) -> pd.DataFrame:
    """The P&L, derived from orders. Every line ties back to net revenue."""
    net = (orders["gross_revenue"] - orders["discounts"] - orders["returns"])
    by_month = (orders.assign(net=net).groupby("month")["net"].sum()
                .reindex(months, fill_value=0.0))

    gm_target = profile.financials.gross_margin_pct
    opex = profile.financials.opex_split
    n = len(months)
    rows = []
    for t, month in enumerate(months):
        revenue = float(by_month.loc[month])
        # Margin compression: product cost was better in the past.
        gm = gm_target + 0.04 * (1 - t / max(n - 1, 1))
        cogs = revenue * (1 - gm)
        gross_profit = revenue - cogs

        leverage = 1.0 + 0.20 * (1 - t / max(n - 1, 1))
        sales = revenue * opex.get("sales", 0.08) * leverage * float(rng.normal(1.0, 0.04))
        marketing = revenue * opex.get("marketing", 0.16) * leverage * float(rng.normal(1.0, 0.07))
        rnd = revenue * opex.get("rnd", 0.03) * leverage * float(rng.normal(1.0, 0.03))
        ga = revenue * opex.get("ga", 0.11) * leverage * float(rng.normal(1.0, 0.03))
        total_opex = sales + marketing + rnd + ga
        ebitda = gross_profit - total_opex

        rows.append({
            "month": month, "revenue": revenue, "cogs": cogs,
            "gross_profit": gross_profit,
            "gross_margin_pct": gm if revenue else 0.0,
            "sales_cost": sales, "marketing_cost": marketing, "rnd_cost": rnd,
            "ga_cost": ga, "total_opex": total_opex, "ebitda": ebitda,
            "ebitda_margin": ebitda / revenue if revenue else 0.0,
        })

    fin = pd.DataFrame(rows)

    # Working capital, which is where a retailer's cash actually goes: stock is
    # bought before it is sold, so a growing retailer burns cash while trading
    # profitably. Modelling it is the difference between an honest cash line
    # and one that flatters the business.
    fin["capex"] = fin["revenue"] * 0.015
    inventory_build = fin["cogs"].diff().fillna(0.0) * 0.8
    fin["free_cash_flow"] = fin["ebitda"] - fin["capex"] - inventory_build
    fin["net_burn"] = -fin["free_cash_flow"]
    fin["cash"] = profile.financials.cash + fin["free_cash_flow"].cumsum()
    return fin


def _build_traffic(orders, months, rng) -> pd.DataFrame:
    """Sessions, carts and checkouts — derived backwards from orders.

    Deriving the funnel from its outcome rather than sampling it independently
    is what makes `checkouts <= add_to_carts <= sessions` hold by construction.
    Sampling all three would need the identity asserted twice and would break
    the first time somebody changed one of them.
    """
    grouped = orders.groupby(["month", "channel"], as_index=False).agg(
        orders=("orders", "sum"), sessions=("sessions", "sum"))
    rows = []
    for row in grouped.itertuples():
        sessions = max(float(row.sessions), float(row.orders))
        # Checkouts exceed orders because some are abandoned at payment; carts
        # exceed checkouts by more, because most abandonment happens earlier.
        checkouts = row.orders * float(np.clip(rng.normal(1.28, 0.06), 1.02, 2.0))
        checkouts = min(checkouts, sessions)
        carts = checkouts * float(np.clip(rng.normal(2.35, 0.18), 1.05, 5.0))
        carts = min(carts, sessions)
        rows.append({
            "month": row.month, "channel": row.channel,
            "sessions": sessions, "add_to_carts": carts, "checkouts": checkouts,
            "orders": float(row.orders),
        })
    return pd.DataFrame(rows)


def _build_inventory(orders, months, rng) -> pd.DataFrame:
    """Stock on hand by category, sized off what actually sold."""
    grouped = orders.groupby(["month", "category"], as_index=False).agg(
        units_sold=("units", "sum"),
        cogs=("gross_revenue", "sum"))
    rows = []
    for row in grouped.itertuples():
        cover = float(np.clip(rng.normal(2.6, 0.5), 0.6, 8.0))   # months of cover
        on_hand = row.units_sold * cover
        rows.append({
            "month": row.month, "category": row.category,
            "units_sold": float(row.units_sold),
            "units_on_hand": float(on_hand),
            "stockout_days": float(np.clip(rng.normal(1.4, 1.2), 0.0, 28.0)),
        })
    return pd.DataFrame(rows)


def _build_headcount(profile, financials, months, rng) -> pd.DataFrame:
    """Headcount by function, tracking revenue with a lag."""
    total = max(profile.size.headcount_total, 1)
    mix = {"operations": 0.34, "marketing": 0.18, "merchandising": 0.16,
           "technology": 0.14, "customer_service": 0.12, "ga": 0.06}
    revenue = financials["revenue"].to_numpy()
    scale = revenue / max(revenue[-1], 1.0)
    rows = []
    for t, month in enumerate(months):
        for function, share in mix.items():
            fte = max(total * share * (0.55 + 0.45 * scale[t]), 0.0)
            monthly_rate = 0.18 / 12.0
            rows.append({
                "month": month, "function": function,
                "fte": round(fte, 1),
                "cost": fte * float(rng.normal(5800, 300)),
                "leavers": int(rng.poisson(fte * monthly_rate)),
                "hires": int(rng.poisson(fte * monthly_rate * 1.25)),
            })
    return pd.DataFrame(rows)


def _build_marketing(orders, financials, months, rng) -> pd.DataFrame:
    """Spend and sessions by channel. Organic gets no spend, which is the point."""
    sessions = orders.groupby(["month", "channel"], as_index=False)["sessions"].sum()
    spend_by_month = financials.set_index("month")["marketing_cost"]
    paid = {c for c in CHANNELS if c != "organic"}
    rows = []
    for month, group in sessions.groupby("month"):
        budget = float(spend_by_month.get(month, 0.0))
        paid_rows = group[group["channel"].isin(paid)]
        total_paid_sessions = float(paid_rows["sessions"].sum()) or 1.0
        for row in group.itertuples():
            share = (float(row.sessions) / total_paid_sessions
                     if row.channel in paid else 0.0)
            rows.append({
                "month": month, "channel": row.channel,
                "spend": budget * share * float(rng.normal(1.0, 0.06)),
                "leads": float(row.sessions),
                "sessions": float(row.sessions),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------

@generator("ecommerce")
def generate(profile: CompanyProfile,
             params: Optional[GeneratorParams] = None) -> GeneratedData:
    params = params or GeneratorParams()
    rng = volatile(np.random.default_rng(profile.seed), params.volatility)

    months, report_months = month_range(profile.history_months)
    segments = profile.market.segments
    if not segments:
        raise ValueError("e-commerce generator requires at least one market segment")

    anomalies = (_plan_anomalies(profile, rng, len(months))
                 if params.inject_anomalies else [])
    growth = monthly_growth(profile)
    report_start = len(months) - profile.history_months

    def simulate(inner_rng, base_new, current_growth) -> Attempt:
        inner_rng = volatile(inner_rng, params.volatility)
        customers, buyers, orders = _simulate_book(
            profile, inner_rng, months, segments, current_growth, anomalies,
            base_new, amplitude=params.seasonality_amplitude)
        if orders.empty:
            return Attempt(count=0, growth=None,
                           payload=(customers, buyers, orders, inner_rng))
        net = orders["gross_revenue"] - orders["discounts"] - orders["returns"]
        monthly = (orders.assign(net=net).groupby("month")["net"].sum()
                   .reindex(months, fill_value=0.0))
        return Attempt(
            count=int(customers["is_active"].sum()),
            growth=yoy_growth(monthly.rolling(12).sum().fillna(0.0), report_start),
            payload=(customers, buyers, orders, inner_rng),
        )

    customers, buyers, orders, rng = calibrate(
        simulate, seed=profile.seed,
        target_count=profile.market.customer_count,
        target_growth=profile.financials.growth_rate_yoy,
        growth=growth, base_new=180.0)

    if orders.empty:
        raise ReconciliationError("simulation produced no orders")

    # Scale the simulated book to the stated revenue. Net revenue is linear in
    # order value, so scaling the three money columns together preserves every
    # identity between them while hitting the user's number exactly.
    # The trailing TWELVE months, matching what the calibration identity
    # measures. Scaling against eleven and checking against twelve is a 5%
    # error that looks like noise and is not — the gate caught it immediately.
    last_year = report_months[-12] if len(report_months) >= 12 else report_months[0]
    recent = orders[orders["month"] >= last_year]
    ending_year = float((recent["gross_revenue"] - recent["discounts"]
                         - recent["returns"]).sum())
    if ending_year <= 0:
        raise ReconciliationError("simulation produced non-positive revenue")
    scale = profile.financials.revenue / ending_year
    for col in ("gross_revenue", "discounts", "returns"):
        orders[col] *= scale
    customers["revenue"] *= scale

    financials = _build_financials(profile, orders, months, rng)
    traffic = _build_traffic(orders, months, rng)
    inventory = _build_inventory(orders, months, rng)
    headcount = _build_headcount(profile, financials, months, rng)
    marketing = _build_marketing(orders, financials, months, rng)

    tables = trim_warmup(
        {
            "monthly_financials": financials,
            "orders": orders.drop(columns=["sessions"]),
            "traffic": traffic,
            "inventory": inventory,
            "customers": customers,
            "buyers": buyers,
            "headcount": headcount,
            "marketing": marketing,
        },
        cutoff=report_months[0],
        # Cohort analysis needs buyers acquired before the reported window.
        keep_full=("customers",),
    )

    checks = reconcile(tables, profile)
    return GeneratedData(tables=tables, anomalies=to_reported(anomalies),
                         checks=checks)


def reconcile(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[str]:
    """Hold generated retail data to both tiers, as the subscription one is."""
    return run_gate(tables, profile, source="synthetic",
                    archetype="ecommerce").checks
