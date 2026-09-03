"""Synthetic data generator for businesses that make physical things.

The fourth archetype: factories, food and drink, industrial. Both sectors that
reach it were approximating onto `ecommerce`, and both said in their own
taxonomy note exactly what was missing — *"what is missing is the capacity
ceiling, not the revenue shape"* for manufacturing, and *"yield, shelf life and
line efficiency are not simulated"* for food production. Measured before
writing anything, the visible half of that was the same as the consultancy's: a
factory's dashboard carried **average order value, category returns and buyer
mix**, because those are the exhibits the transactional tables draw.

**What this archetype has that the transactional one does not.**

* **A ceiling.** A shop can sell whatever it can buy; a plant can only make what
  its lines can run in the hours they are scheduled for. Demand above that does
  not become revenue, it becomes a stockout — and `output <= capacity` is
  physical, so it is Tier 1.
* **OEE, decomposed.** Overall equipment effectiveness is availability x
  performance x quality, and its entire value is that the three separate: the
  line was not running, it ran slowly, or what it made was scrap. Those are
  three different problems with three different owners, and a blended
  "efficiency" number names none of them. All three are columns; the identity
  asserts the product.
* **Stock that rolls forward.** What was made and not shipped is still in the
  building. A retailer's inventory table is a snapshot of cover; a plant's is a
  ledger, and it has to balance.

**Where the structure comes from.** Demand drives the schedule, the schedule is
capped by the lines, and stock absorbs the difference. Revenue is what shipped,
at the price it shipped at. One direction, so the identities in
`contract/identities.py` hold by construction — and yield is *not* a number the
generator writes down anywhere: scrap is a count of units, quality is derived
from it, and gross margin moves because scrapped units cost money and earn
none.

**Scheduling, not throttling.** When demand is below what a line could run, the
line is *scheduled for fewer hours* rather than run inefficiently. That is what
a plant actually does, and it matters for the metric: modelling a quiet month
as poor performance would put a sales problem into the plant manager's numbers.
Capacity utilisation — scheduled hours against nameplate — is the separate
question, and it has its own column.
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
    segment_financials,
    to_reported,
    trim_warmup,
    volatile,
    yoy_growth,
)

# Industrial demand is flatter than retail and dips where the plants shut: the
# European summer break and the turn of the year. Mean exactly 1.0.
PRODUCTION_SEASONALITY = np.array([
    0.94, 1.00, 1.08, 1.05, 1.06, 1.04,
    0.92, 0.84, 1.06, 1.08, 1.02, 0.91,
])

# One line per family, which is the simplification that keeps the capacity
# ceiling legible: "the line ran out of hours" is a sentence about a family.
# `scrap` is the baseline reject rate and `price` the relative unit price; the
# absolute price is solved at the end.
#
# There is deliberately no units-per-hour here. The first version carried one,
# and it made the capacity identity false in a way the gate caught immediately:
# a line's nameplate *output* is sized from the demand it has to serve, while
# `NAMEPLATE_HOURS x units_per_hour` is an unrelated absolute number, so
# "capacity" and "what the line can make" were two different quantities wearing
# one name. The ideal rate is derived from the nameplate instead, which is the
# only way the two can agree.
PRODUCT_FAMILIES: Dict[str, dict] = {
    "core":      {"share": 0.46, "scrap": 0.021, "price": 1.00},
    "premium":   {"share": 0.24, "scrap": 0.038, "price": 2.60},
    "volume":    {"share": 0.22, "scrap": 0.015, "price": 0.42},
    "specials":  {"share": 0.08, "scrap": 0.055, "price": 4.80},
}

# Cost as a multiple of what the same family's price would imply at the company
# blend. A premium line carries a better margin than a commodity one, which is
# most of why mix matters to a plant at all.
#
# The first version had *one* unit cost across every family, and the gate caught
# it on the first run: family prices span 0.42 to 4.80, so a month that happened
# to ship a lot of the cheap line reported a **negative gross margin** — a mix
# swing wearing the costume of a cost problem.
COST_TILT = {"core": 1.00, "premium": 0.88, "volume": 1.10, "specials": 0.80}

CHANNELS = {"distributor": 0.44, "direct": 0.31, "retail": 0.17, "export": 0.08}

#: Hours a line can physically run in a month at nameplate — three shifts, six
#: days, less planned maintenance. The denominator of capacity utilisation.
NAMEPLATE_HOURS = 480.0

#: Baseline availability and performance per line. Availability is unplanned
#: downtime against scheduled hours; performance is speed loss and minor stops.
BASE_AVAILABILITY = 0.88
BASE_PERFORMANCE = 0.91

#: Months of forward cover the plant builds ahead of demand. Finished goods are
#: the buffer between a schedule that changes weekly and a customer who does
#: not care.
TARGET_COVER_MONTHS = 1.3

#: How long after their last order an account still counts as active. A
#: manufacturer's customers order in batches, so the window is wider than a
#: consultancy's.
ACCOUNT_LAPSE_MONTHS = 9

#: Days of revenue in receivables. Industrial payment terms are long.
DEBTOR_DAYS = 68.0


def _plan_anomalies(profile, rng, n_total) -> List[Anomaly]:
    """Deliberate, documented events, chosen to be findable in this shape of data."""
    mid = WARMUP_MONTHS + int(0.45 * (n_total - WARMUP_MONTHS))
    return [
        Anomaly(
            kind="yield_loss", start_month=mid - 1, end_month=n_total,
            magnitude=2.4, segment="premium",
            description=(
                "Scrap on the premium line runs roughly 2.4x baseline and does "
                "not recover — consistent with a material change or a tooling "
                "problem. It shows in quality, in OEE and in gross margin, "
                "because scrapped units cost what they cost and earn nothing."),
        ),
        Anomaly(
            kind="downtime_episode", start_month=mid + 5, end_month=mid + 9,
            magnitude=0.78, segment="core",
            description=(
                "Availability on the core line falls ~22% for five months of "
                "unplanned downtime, so the plant loses output it had already "
                "scheduled the people and materials for."),
        ),
        Anomaly(
            kind="price_erosion", start_month=mid + 3, end_month=n_total,
            magnitude=0.93, segment="volume",
            description=(
                "Realised price on the volume family settles ~7% lower and does "
                "not recover, which is what competitive pressure looks like "
                "before it reaches the revenue line."),
        ),
    ]


def _active(anomaly: Optional[Anomaly], t: int) -> bool:
    return anomaly is not None and anomaly.start_month <= t <= anomaly.end_month


# --------------------------------------------------------------------------
# The order book
# --------------------------------------------------------------------------

def _simulate_demand(profile, rng, months, segments, growth, anomalies,
                     base_new=14.0, amplitude: float = 1.0):
    """Accounts won and lost, and the units they ask for each month.

    Demand is in units at a reference price, so the plant can be scheduled
    against it before anything has been priced. Money arrives later, once the
    level has been scaled to the profile.
    """
    n_months = len(months)
    curve = (np.ones(12) if profile.market.seasonality == "none"
             else apply_amplitude(PRODUCTION_SEASONALITY, amplitude))

    families = list(PRODUCT_FAMILIES)
    family_p = np.array([PRODUCT_FAMILIES[f]["share"] for f in families])
    family_p = family_p / family_p.sum()
    price = np.array([PRODUCT_FAMILIES[f]["price"] for f in families])
    channels = list(CHANNELS)
    channel_p = np.array(list(CHANNELS.values()))
    channel_p = channel_p / channel_p.sum()

    shares = np.array([s.share for s in segments], dtype=float)
    shares = shares / shares.sum()
    spend = np.array([max(s.avg_acv, 1.0) for s in segments], dtype=float)
    lapse = np.array([s.logo_churn_annual for s in segments], dtype=float)
    monthly_lapse = 1.0 - (1.0 - np.clip(lapse, 0.0, 0.95)) ** (1 / 12)

    seg = np.empty(0, dtype=np.int64)
    account_spend = np.empty(0, dtype=float)
    acquired = np.empty(0, dtype=np.int64)
    last_order = np.empty(0, dtype=np.int64)
    orders_total = np.zeros(0, dtype=np.int64)
    revenue_total = np.zeros(0, dtype=float)
    alive = np.empty(0, dtype=bool)

    # month x family x channel, in units at the reference price.
    demand = np.zeros((n_months, len(families), len(channels)))

    for t in range(n_months):
        seas = curve[months[t].month - 1]

        n_new = int(rng.poisson(max(base_new * ((1 + growth) ** t) * seas, 0.0)))
        if n_new:
            draws = rng.choice(len(segments), size=n_new, p=shares)
            seg = np.concatenate([seg, draws])
            account_spend = np.concatenate([account_spend, rng.lognormal(
                np.log(np.maximum(spend[draws], 1.0)), 0.50)])
            acquired = np.concatenate([acquired, np.full(n_new, t, dtype=np.int64)])
            last_order = np.concatenate([last_order, np.full(n_new, t, dtype=np.int64)])
            orders_total = np.concatenate([orders_total, np.zeros(n_new, dtype=np.int64)])
            revenue_total = np.concatenate([revenue_total, np.zeros(n_new)])
            alive = np.concatenate([alive, np.ones(n_new, dtype=bool)])

        who = np.flatnonzero(alive)
        if who.size == 0:
            continue

        # An account orders in most months but not all — batches, not a
        # subscription — so a month with no order is normal and is not a lapse
        # on its own.
        ordered = rng.random(who.size) < 0.72
        idle = who[~ordered]
        if idle.size:
            lapsed = rng.random(idle.size) < monthly_lapse[seg[idle]]
            alive[idle[lapsed]] = False

        buyers = who[ordered]
        if buyers.size == 0:
            continue

        value = (account_spend[buyers] / 12.0 * seas
                 * rng.lognormal(0.0, 0.30, size=buyers.size))
        family = rng.choice(len(families), size=buyers.size, p=family_p)
        channel = rng.choice(len(channels), size=buyers.size, p=channel_p)
        units = value / price[family]

        flat = family * len(channels) + channel
        demand[t] += np.bincount(flat, weights=units,
                                 minlength=len(families) * len(channels)
                                 ).reshape(len(families), len(channels))
        orders_total[buyers] += 1
        revenue_total[buyers] += value
        last_order[buyers] = t

    horizon = n_months - 1
    keep = np.flatnonzero(orders_total > 0)
    customers = pd.DataFrame({
        "customer_id": [f"A{i + 1:06d}" for i in keep],
        "segment": [segments[i].name for i in seg[keep]],
        "acquired_month": [months[i] for i in acquired[keep]],
        "last_order_month": [months[i] for i in last_order[keep]],
        "orders": orders_total[keep].astype(float),
        "revenue": revenue_total[keep],
        "is_active": alive[keep] & (last_order[keep] >= horizon - ACCOUNT_LAPSE_MONTHS),
    })
    return customers, demand


# --------------------------------------------------------------------------
# The plant
# --------------------------------------------------------------------------

def _run_the_plant(months, demand, rng, anomalies, nameplate_units):
    """Schedule the lines against demand, make what they can, ship what exists.

    Returns the production ledger, the stock ledger and what actually shipped —
    which is not what was asked for whenever a line ran out of hours, and that
    difference is the whole reason the archetype exists.
    """
    families = list(PRODUCT_FAMILIES)
    n_months, n_families, n_channels = demand.shape

    yield_loss = next((a for a in anomalies if a.kind == "yield_loss"), None)
    downtime = next((a for a in anomalies if a.kind == "downtime_episode"), None)

    made = np.zeros((n_months, n_families))
    scrapped = np.zeros((n_months, n_families))
    shipped = np.zeros((n_months, n_families, n_channels))
    rows: List[dict] = []
    stock_rows: List[dict] = []

    stock = np.zeros(n_families)
    # Availability and performance persist month to month for the same reason
    # utilisation does in the project archetype: a line does not get a new
    # personality every four weeks.
    availability = np.full(n_families, BASE_AVAILABILITY)
    performance = np.full(n_families, BASE_PERFORMANCE)

    for t in range(n_months):
        for fi, family in enumerate(families):
            spec = PRODUCT_FAMILIES[family]
            asked = float(demand[t, fi].sum())

            target_a = BASE_AVAILABILITY
            if downtime and downtime.segment == family and _active(downtime, t):
                target_a *= downtime.magnitude
            availability[fi] = float(np.clip(
                rng.normal(0.55 * availability[fi] + 0.45 * target_a, 0.015),
                0.35, 0.99))
            performance[fi] = float(np.clip(
                rng.normal(0.55 * performance[fi] + 0.45 * BASE_PERFORMANCE, 0.015),
                0.40, 0.99))

            scrap_rate = spec["scrap"]
            if yield_loss and yield_loss.segment == family and _active(yield_loss, t):
                scrap_rate *= yield_loss.magnitude
            scrap_rate = float(np.clip(rng.normal(scrap_rate, scrap_rate * 0.18),
                                       0.0, 0.45))
            quality = 1.0 - scrap_rate

            # What the schedule is aiming at: this month's demand plus enough
            # cover for next month, less what is already on the floor.
            want_good = max(asked * (1.0 + TARGET_COVER_MONTHS) - stock[fi], 0.0)
            want_total = want_good / max(quality, 1e-6)

            nameplate = float(nameplate_units[fi])
            ideal_rate = nameplate / NAMEPLATE_HOURS
            reachable = nameplate * availability[fi] * performance[fi]
            if want_total >= reachable:
                # Capacity-limited: run flat out and still come up short.
                planned_hours = NAMEPLATE_HOURS
                total_out = reachable
            else:
                # Demand-limited: schedule fewer hours rather than run the line
                # badly. The losses are properties of the line, not of the month.
                planned_hours = NAMEPLATE_HOURS * (want_total / max(reachable, 1e-9))
                total_out = want_total

            capacity_units = planned_hours * ideal_rate
            good = total_out * quality
            scrap = total_out - good

            opening = float(stock[fi])
            available_to_ship = opening + good
            per_channel = demand[t, fi]
            asked_total = float(per_channel.sum())
            if asked_total > available_to_ship and asked_total > 0:
                # Short. Ration proportionally rather than picking a favourite
                # channel, and let the stockout show up as revenue that did not
                # happen.
                per_channel = per_channel * (available_to_ship / asked_total)
            out = float(per_channel.sum())
            closing = available_to_ship - out

            made[t, fi] = good
            scrapped[t, fi] = scrap
            shipped[t, fi] = per_channel
            stock[fi] = closing

            # A line that was not scheduled has no OEE — availability,
            # performance and quality are all ratios of something that did not
            # happen, and emitting them puts a NaN into the one metric this
            # archetype exists for. The stock row below is still written: the
            # goods are on the floor whether or not the line ran.
            if total_out <= 0:
                stock_rows.append({
                    "month": months[t], "product_family": family,
                    "opening_units": opening, "units_produced": 0.0,
                    "units_shipped": out, "closing_units": closing,
                    "days_cover": closing / max(asked, 1e-9) * 30.0,
                })
                continue

            rows.append({
                "month": months[t], "line": f"line_{family}",
                "product_family": family,
                "nameplate_units": nameplate,
                "capacity_units": capacity_units,
                "planned_hours": planned_hours,
                "runtime_hours": planned_hours * availability[fi],
                "ideal_rate": ideal_rate,
                "units_produced": good,
                "units_scrapped": scrap,
                "availability": availability[fi],
                "performance": performance[fi],
                "quality": quality,
                "oee": availability[fi] * performance[fi] * quality,
            })
            stock_rows.append({
                "month": months[t], "product_family": family,
                "opening_units": opening,
                "units_produced": good,
                "units_shipped": out,
                "closing_units": closing,
                "days_cover": closing / max(asked, 1e-9) * 30.0,
            })

    return (pd.DataFrame(rows), pd.DataFrame(stock_rows), made, scrapped, shipped)


def _build_shipments(months, shipped, rng, anomalies, unit_price) -> pd.DataFrame:
    """Month x family x channel, priced. `units x price = gross revenue`, exactly."""
    families = list(PRODUCT_FAMILIES)
    channels = list(CHANNELS)
    erosion = next((a for a in anomalies if a.kind == "price_erosion"), None)

    rows: List[dict] = []
    for t, month in enumerate(months):
        for fi, family in enumerate(families):
            price = float(unit_price[fi])
            if erosion and erosion.segment == family and _active(erosion, t):
                price *= erosion.magnitude
            for ci, channel in enumerate(channels):
                units = float(shipped[t, fi, ci])
                if units <= 0:
                    continue
                # A distributor buys at a discount and an export order carries
                # freight; both land as a discount off the same list price, so
                # the identity stays one multiplication.
                gross = units * price
                discount = gross * float(np.clip(
                    rng.normal(0.09 if channel == "distributor" else 0.04, 0.02),
                    0.0, 0.4))
                returned = gross * float(np.clip(rng.normal(0.012, 0.006), 0.0, 0.2))
                rows.append({
                    "month": month, "product_family": family, "channel": channel,
                    "units_shipped": units, "unit_price": price,
                    "gross_revenue": gross, "discounts": discount,
                    "returns": returned,
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------

def _build_financials(profile, shipments, production, stock, months, rng,
                      unit_price) -> pd.DataFrame:
    """The P&L, with cost of sales on what shipped plus what was scrapped.

    **Sales, not production, and the first version got this wrong.** Costing
    everything the plant made expenses a stock build in the month it happens, so
    a factory that ran ahead of demand reported a *negative gross margin* — two
    months of it on the first run, which is what the gate's band caught. Good
    units are stock until they ship; that is the matching principle and it is
    also the only reading under which the margin means anything.

    Scrap is the exception and is expensed as it happens, which is the point: it
    never becomes sellable stock, so a yield problem is a margin problem in the
    month it occurs. A model that costs only what shipped would make scrap free.

    The unit cost itself is solved from the margin the user stated, because
    nobody is asked their bill of materials in the survey.
    """
    families = list(PRODUCT_FAMILIES)
    net = (shipments["gross_revenue"] - shipments["discounts"]
           - shipments["returns"])
    revenue = (shipments.assign(net=net).groupby("month")["net"].sum()
               .reindex(months, fill_value=0.0))

    # Per family, because a unit of the volume line and a unit of specials are
    # not the same cost. See COST_TILT.
    sold = (shipments.groupby(["month", "product_family"])["units_shipped"].sum()
            .unstack(fill_value=0.0).reindex(months, fill_value=0.0))
    scrapped = (production.groupby(["month", "product_family"])["units_scrapped"]
                .sum().unstack(fill_value=0.0).reindex(months, fill_value=0.0))
    closing = (stock.groupby(["month", "product_family"])["closing_units"].sum()
               .unstack(fill_value=0.0).reindex(months, fill_value=0.0))
    cost_base = pd.Series(0.0, index=list(months))
    stock_units = pd.Series(0.0, index=list(months))
    for i, family in enumerate(families):
        index = float(unit_price[i]) * COST_TILT[family]
        zero = pd.Series(0.0, index=list(months))
        units = sold.get(family, zero) + scrapped.get(family, zero)
        cost_base = cost_base + units * index
        stock_units = stock_units + closing.get(family, zero) * index

    target = float(np.clip(profile.financials.gross_margin_pct, 0.12, 0.75))
    if float(cost_base.sum()) <= 0:
        raise ReconciliationError("simulation produced no units")
    factor = float(revenue.sum()) * (1.0 - target) / float(cost_base.sum())

    opex = profile.financials.opex_split
    n = len(months)
    rows = []
    for t, month in enumerate(months):
        rev = float(revenue.loc[month])
        cogs = float(cost_base.loc[month]) * factor
        gross_profit = rev - cogs

        leverage = 1.0 + 0.16 * (1 - t / max(n - 1, 1))
        sales = rev * opex.get("sales", 0.07) * leverage * float(rng.normal(1.0, 0.04))
        marketing = rev * opex.get("marketing", 0.03) * leverage * float(rng.normal(1.0, 0.07))
        rnd = rev * opex.get("rnd", 0.03) * leverage * float(rng.normal(1.0, 0.03))
        ga = rev * opex.get("ga", 0.09) * leverage * float(rng.normal(1.0, 0.03))
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
    # A plant's cash sits in three places: the machines, the debtors and the
    # stock on the floor. All three are modelled, because a factory that grows
    # without funding its working capital is the commonest way a profitable
    # manufacturer runs out of money.
    # Against trailing revenue rather than the single month, which is how
    # DSO is actually computed and which stops one seasonal month
    # producing a working-capital movement larger than a month of sales.
    receivables = (fin["revenue"].rolling(3, min_periods=1).mean()
                   * (DEBTOR_DAYS / 30.0))
    # Finished goods at cost, read off the stock ledger rather than estimated
    # from a cover assumption. The first version multiplied the month's cost of
    # sales by the target cover, which swings with shipments rather than with
    # what is on the floor: free cash flow margin came out at **+112% one year
    # and -107% the next**, and the seasonal adjustment then quoted a third
    # number again, so one metric appeared on the same page as -2.3% and
    # -106.6%. The ledger is right there and it balances; the proxy was a
    # guess at a number that had already been counted.
    stock_value = (stock_units * factor).reset_index(drop=True)
    fin["capex"] = fin["revenue"] * 0.045
    fin["free_cash_flow"] = (fin["ebitda"] - fin["capex"]
                             - receivables.diff().fillna(0.0)
                             - stock_value.diff().fillna(0.0))
    fin["net_burn"] = -fin["free_cash_flow"]
    # Anchored so the LAST month is the balance the profile states, which is
    # what a user means by "we have this much cash" — `subscription.py` has
    # always read it that way and the other generators did not, so a stated
    # balance was silently the one from three years ago. On a plant funding a
    # growing working-capital base that is not a rounding difference: Orbis
    # opened its reported window at **-2.2M** against a stated 3.1M.
    fin["cash"] = fin["free_cash_flow"].cumsum()
    fin["cash"] += profile.financials.cash - float(fin["cash"].iloc[-1])
    return fin


def _build_headcount(profile, financials, months, rng) -> pd.DataFrame:
    """The roster by function, tracking output with a lag.

    A plant's people are mostly on the lines, and the support functions —
    maintenance and quality — are the two a cost programme cuts first and
    regrets, which is why they are their own rows rather than folded into
    operations.
    """
    total = max(profile.size.headcount_total, 1)
    mix = {"production": 0.52, "maintenance": 0.09, "quality": 0.07,
           "supply_chain": 0.11, "engineering": 0.08, "sales": 0.07, "ga": 0.06}
    revenue = financials["revenue"].to_numpy()
    scale = revenue / max(revenue[-1], 1.0)
    rows = []
    for t, month in enumerate(months):
        for function, share in mix.items():
            fte = max(total * share * (0.60 + 0.40 * scale[t]), 0.0)
            monthly_rate = 0.19 / 12.0        # shop-floor turnover runs higher
            rows.append({
                "month": month, "function": function, "fte": round(fte, 1),
                "cost": fte * float(rng.normal(4200, 220)),
                "leavers": int(rng.poisson(fte * monthly_rate)),
                "hires": int(rng.poisson(fte * monthly_rate * 1.15)),
            })
    return pd.DataFrame(rows)


def _build_marketing(financials, shipments, months, rng) -> pd.DataFrame:
    """Trade spend by route to market. The distributor channel carries most of it."""
    budget = financials.set_index("month")["marketing_cost"]
    units = shipments.groupby(["month", "channel"], as_index=False)[
        "units_shipped"].sum()
    rows = []
    for month, group in units.groupby("month"):
        spend_pool = float(budget.get(month, 0.0))
        total_units = float(group["units_shipped"].sum()) or 1.0
        for row in group.itertuples():
            weight = float(row.units_shipped) / total_units
            rows.append({
                "month": month, "channel": row.channel,
                "spend": spend_pool * weight * float(rng.normal(1.0, 0.06)),
                # Enquiries, at the grain a manufacturer counts them: quote
                # requests rather than web sessions.
                "leads": max(float(row.units_shipped) / 900.0
                             * float(rng.normal(1.0, 0.20)), 0.0),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------

@generator("production")
def generate(profile: CompanyProfile,
             params: Optional[GeneratorParams] = None) -> GeneratedData:
    params = params or GeneratorParams()
    rng = volatile(np.random.default_rng(profile.seed), params.volatility)

    months, report_months = month_range(profile.history_months,
                                        end=params.history_end)
    segments = profile.market.segments
    if not segments:
        raise ValueError("production generator requires at least one market segment")

    anomalies = (_plan_anomalies(profile, rng, len(months))
                 if params.inject_anomalies else [])
    growth = monthly_growth(profile)
    report_start = len(months) - profile.history_months
    families = list(PRODUCT_FAMILIES)

    def simulate(inner_rng, base_new, current_growth) -> Attempt:
        inner_rng = volatile(inner_rng, params.volatility)
        customers, demand = _simulate_demand(
            profile, inner_rng, months, segments, current_growth, anomalies,
            base_new, amplitude=params.seasonality_amplitude)
        if customers.empty or demand.sum() <= 0:
            return Attempt(count=0, growth=None,
                           payload=(customers, demand, inner_rng))
        price = np.array([PRODUCT_FAMILIES[f]["price"] for f in families])
        value = pd.Series((demand.sum(axis=2) * price[None, :]).sum(axis=1),
                          index=list(months))
        return Attempt(
            count=int(customers["is_active"].sum()),
            growth=yoy_growth(value.rolling(12).sum().fillna(0.0), report_start),
            payload=(customers, demand, inner_rng),
        )

    customers, demand, rng = calibrate(
        simulate, seed=profile.seed,
        target_count=profile.market.customer_count,
        target_growth=profile.financials.growth_rate_yoy,
        growth=growth, base_new=14.0)

    if customers.empty or demand.sum() <= 0:
        raise ReconciliationError("simulation produced no demand")

    # --- size the lines to the demand they have to serve --------------------
    #
    # Nameplate is set from the busiest twelve months rather than the average,
    # and the headroom is what makes the ceiling mean something: too much and
    # the plant never runs out, too little and every month is a stockout. At
    # 1.18 the busy months press against it and the quiet ones do not.
    peak = np.zeros(len(families))
    for fi in range(len(families)):
        monthly = demand[:, fi, :].sum(axis=1)
        window = monthly[report_start:] if len(monthly) > report_start else monthly
        peak[fi] = float(np.max(window)) if window.size else 0.0
    nameplate_units = np.maximum(peak * 1.18, 1.0)

    production, stock, made, scrapped, shipped = _run_the_plant(
        months, demand, rng, anomalies, nameplate_units)
    if shipped.sum() <= 0:
        raise ReconciliationError("simulation shipped nothing")

    # --- price, so trailing-year net revenue matches the profile ------------
    #
    # Solved on *net* revenue after discounts and returns, because that is what
    # the P&L and the calibration identity both read. Pricing on gross and
    # hoping the discounts come out right is a 6% miss that looks like noise.
    base_price = np.array([PRODUCT_FAMILIES[f]["price"] for f in families])
    trial = _build_shipments(months, shipped, np.random.default_rng(profile.seed),
                             anomalies, base_price)
    net = (trial["gross_revenue"] - trial["discounts"] - trial["returns"])
    by_month = (trial.assign(net=net).groupby("month")["net"].sum()
                .reindex(months, fill_value=0.0))
    trailing = float(by_month.iloc[-12:].sum()) if len(months) >= 12 \
        else float(by_month.sum())
    if trailing <= 0:
        raise ReconciliationError("simulation produced non-positive revenue")
    unit_price = base_price * (profile.financials.revenue / trailing)

    shipments = _build_shipments(months, shipped,
                                 np.random.default_rng(profile.seed),
                                 anomalies, unit_price)
    customers["revenue"] *= profile.financials.revenue / trailing

    financials = _build_financials(profile, shipments, production, stock, months,
                                   rng, unit_price)
    headcount = _build_headcount(profile, financials, months, rng)
    marketing = _build_marketing(financials, shipments, months, rng)

    # Two dimensions again: a plant asks "which family" and "which route to
    # market" as separate questions.
    segment_fin = segment_financials(financials, {
        "product_family": (shipments, "gross_revenue", False),
        "channel": (shipments, "gross_revenue", False),
    })

    tables = trim_warmup(
        {
            "monthly_financials": financials,
            "segment_financials": segment_fin,
            "production": production,
            "shipments": shipments,
            "inventory": stock,
            "customers": customers,
            "headcount": headcount,
            "marketing": marketing,
        },
        cutoff=report_months[0],
        keep_full=("customers",),
    )

    checks = reconcile(tables, profile)
    return GeneratedData(tables=tables, anomalies=to_reported(anomalies),
                         checks=checks)


def reconcile(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[str]:
    """Hold generated plant data to both tiers, as the other three are."""
    return run_gate(tables, profile, source="synthetic",
                    archetype="production").checks
