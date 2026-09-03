"""Synthetic data generator for firms that sell their people's time.

The third archetype: agencies, consultancies, engineering practices. Four
sectors reached it by approximating onto `ecommerce`, and the approximation was
more visible than the note admitted — a consultancy's dashboard carried
*average order value*, *category returns* and *buyer mix*, because those are
the exhibits the transactional archetype's tables draw. The taxonomy said
"project fees behave like orders", which is true of the revenue line and of
nothing else a services firm manages itself by.

**What this archetype has that neither of the others does.**

* **A stock of sold work.** The backlog is the first number a services board
  asks for, and book-to-bill is the leading indicator the P&L cannot show:
  revenue is what was delivered, bookings are what was won, and a firm can
  report a record month while its future empties out.
* **A capacity ceiling measured in hours.** Utilisation is not a ratio someone
  chooses; it is billable hours over the hours the roster could have offered.
  The identity `billable <= available` is physical, which is why it is Tier 1.
* **Realisation.** A firm earns the fee it agreed, over however many hours the
  job actually took. `hours x rate` is what it *hoped* to earn. The gap is
  where a services business quietly loses its margin, and it is invisible in
  any table the other two archetypes emit.

**Where the structure comes from.** Engagements drive everything. Hours are
spread across an engagement's months, revenue is recognised by percentage of
completion on those hours, the backlog is what has been sold and not yet
recognised, the roster is the capacity those hours consumed, and the P&L
follows the roster. One direction, so the identities in
`contract/identities.py` hold by construction rather than by assertion — and
realisation is *derived* from the other three terms rather than sampled, which
is what makes `fee = hours x rate x realisation` an arithmetic fact rather than
a modelling assumption.

**Three scalings, applied in order, and the reason there are three.** The
simulation runs in arbitrary units; the profile then pins three separate
things that a single scale factor cannot satisfy at once.

1. *Hours* are scaled so the delivery roster the work implies matches the
   headcount the user stated. Billable and available scale together, so
   utilisation is untouched.
2. *Money* is scaled so trailing-year revenue matches the stated revenue.
3. *The standard rate* is then solved so blended realisation lands where a
   services firm's does. It is the free parameter — nobody states a charge-out
   rate in the survey — so solving for it is what keeps realisation meaningful
   after the first two scalings have moved hours and money independently.
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

# Delivery capacity, which is a calendar fact rather than a demand one: August
# and December are short months in every professional-services firm in the
# northern hemisphere, and the dip is in the hours available, not in the work
# won. Mean exactly 1.0, so amplitude scales the shape without moving the level.
DELIVERY_SEASONALITY = np.array([
    0.98, 1.03, 1.10, 1.04, 1.03, 1.02,   # Jan-Jun
    0.86, 0.78, 1.06, 1.10, 1.06, 0.94,   # Jul-Dec
])

# Bookings follow budget cycles instead, and they peak where delivery does not:
# work is won in the new year and against year-end budget, and signed off by
# nobody in August. Two curves rather than one because the phase difference is
# the point — a firm can be winning while it is not delivering.
BOOKING_SEASONALITY = np.array([
    1.12, 1.08, 1.05, 0.98, 0.96, 0.94,
    0.82, 0.80, 1.02, 1.08, 1.10, 1.05,
])

# Service lines, with the shape of work each one is. `months` is the mean
# engagement length and `realisation` the fee a line expects to hold before any
# overrun — a fixed-price implementation concedes more than a time-and-
# materials managed service, which is why the two cannot share one number.
SERVICE_LINES: Dict[str, dict] = {
    "advisory":        {"share": 0.16, "months": 3.0,  "realisation": 0.97,
                        "utilisation": 0.60},
    "implementation":  {"share": 0.44, "months": 8.0,  "realisation": 0.90,
                        "utilisation": 0.79},
    "managed_service": {"share": 0.26, "months": 14.0, "realisation": 0.95,
                        "utilisation": 0.85},
    "design":          {"share": 0.14, "months": 4.5,  "realisation": 0.88,
                        "utilisation": 0.73},
}

# Charge-out multiple by role, against a consultant at 1.0. The absolute rate is
# solved for at the end; only these ratios are modelled, because they are the
# part a firm actually sets.
ROLE_RATE = {"partner": 2.60, "manager": 1.60, "consultant": 1.00, "analyst": 0.62}

# Who does the work, by line. An advisory engagement is partner-heavy and a
# managed service is not, and that mix is most of why two lines with the same
# revenue have different margins.
LINE_ROLE_MIX: Dict[str, Dict[str, float]] = {
    "advisory":        {"partner": 0.22, "manager": 0.30, "consultant": 0.36, "analyst": 0.12},
    "implementation":  {"partner": 0.06, "manager": 0.18, "consultant": 0.48, "analyst": 0.28},
    "managed_service": {"partner": 0.03, "manager": 0.12, "consultant": 0.40, "analyst": 0.45},
    "design":          {"partner": 0.08, "manager": 0.20, "consultant": 0.46, "analyst": 0.26},
}

#: Hours one full-time equivalent can offer in a month, before holiday and
#: training. The denominator of utilisation, and the bridge between the hours
#: table and the headcount roster.
HOURS_PER_FTE_MONTH = 152.0

#: Share of the roster that delivers. The rest sell, market and administer, and
#: they are not available for client work, so they are outside utilisation.
DELIVERY_SHARE = 0.72

#: Blended realisation the rate solve targets. Below 1.0 always: some of every
#: firm's hours are written off, and a model in which none are would make the
#: single most useful metric in the archetype a constant.
TARGET_REALISATION = 0.93

#: How long after their last engagement ends a client still counts as active.
#: Two quarters, which is the span a partner would recognise: a client nobody
#: has billed since the half-year is not a client, whatever the CRM says. It is
#: also what makes the stated client count reachable — a nine-month window
#: counts everyone mid-engagement *and* everyone recently finished, so the book
#: comes out systematically larger than `clients x annual spend = revenue`
#: implies, and the calibration identity fails on the arithmetic rather than on
#: anything the simulation did wrong.
CLIENT_LAPSE_MONTHS = 6

#: Share of new engagements that come from a client the firm already has. Over
#: half, which is the defining economic fact of a services business and the
#: reason client concentration is a risk worth a detector.
REPEAT_SHARE = 0.55

#: Days of revenue sitting in receivables and unbilled work in progress. A
#: services firm's working capital is its debtors, not its stock, so the cash
#: line is built from this rather than from an inventory build.
DEBTOR_DAYS = 62.0


def _plan_anomalies(profile, rng, n_total) -> List[Anomaly]:
    """Deliberate, documented events, chosen to be findable in this shape of data."""
    mid = WARMUP_MONTHS + int(0.45 * (n_total - WARMUP_MONTHS))
    return [
        Anomaly(
            kind="scope_creep", start_month=mid - 2, end_month=mid + 7,
            magnitude=1.34, segment="implementation",
            description=(
                "Implementation engagements won over ten months run ~34% over "
                "their budgeted hours. The fee is fixed, so the overrun shows "
                "up as realisation and gross margin rather than as cost."),
        ),
        Anomaly(
            kind="bench_time", start_month=mid + 5, end_month=mid + 10,
            magnitude=0.86, segment="advisory",
            description=(
                "Advisory utilisation falls ~14% for six months as a large "
                "programme ends and the team is not immediately redeployed."),
        ),
        Anomaly(
            kind="booking_slowdown", start_month=mid + 8, end_month=n_total,
            magnitude=0.58, description=(
                "New engagements won fall to ~58% of trend and do not recover, "
                "so book-to-bill drops below 1.0 and the backlog erodes while "
                "the revenue line still looks healthy."),
        ),
    ]


def _active(anomaly: Optional[Anomaly], t: int) -> bool:
    return anomaly is not None and anomaly.start_month <= t <= anomaly.end_month


# --------------------------------------------------------------------------
# The book of engagements
# --------------------------------------------------------------------------

def _simulate_book(profile, rng, months, segments, growth, anomalies,
                   base_new=9.0, amplitude: float = 1.0):
    """Engagements won, hours delivered, and revenue recognised against them.

    Returns the raw arrays in arbitrary units. Everything is scaled to the
    profile afterwards; doing it here would mean re-deriving the schedules on
    every calibration attempt for no gain.
    """
    n_months = len(months)
    flat = profile.market.seasonality == "none"
    delivery_curve = (np.ones(12) if flat
                      else apply_amplitude(DELIVERY_SEASONALITY, amplitude))
    booking_curve = (np.ones(12) if flat
                     else apply_amplitude(BOOKING_SEASONALITY, amplitude))

    line_names = list(SERVICE_LINES)
    line_p = np.array([SERVICE_LINES[k]["share"] for k in line_names])
    line_p = line_p / line_p.sum()
    role_names = list(ROLE_RATE)
    # month x line x role, and month x line, accumulated as the loop goes.
    hours = np.zeros((n_months, len(line_names), len(role_names)))
    recognised = np.zeros((n_months, len(line_names)))
    bookings = np.zeros(n_months)

    seg_shares = np.array([s.share for s in segments], dtype=float)
    seg_shares = seg_shares / seg_shares.sum()
    seg_spend = np.array([max(s.avg_acv, 1.0) for s in segments], dtype=float)
    n_segments = len(segments)
    recognised_by_segment = np.zeros((n_months, n_segments))

    creep = next((a for a in anomalies if a.kind == "scope_creep"), None)
    slowdown = next((a for a in anomalies if a.kind == "booking_slowdown"), None)

    role_mix = {line: np.array([LINE_ROLE_MIX[line][r] for r in role_names])
                for line in line_names}
    # What one hour of this line is worth at standard rate, relative to a
    # consultant hour. Budget hours are divided by it, so a partner-heavy
    # advisory job buys fewer hours for the same fee than an analyst-heavy
    # managed service does.
    #
    # The first version left this out, and the consequence was the opposite of
    # what it looks like: advisory, the line with the *highest* target
    # realisation, measured the lowest at 0.68 — because it was sold at the
    # same price per hour as everything else and then staffed by partners.
    # Realisation is derived from the fee against the hours, so mispricing the
    # hours misprices the metric the archetype exists to expose.
    rate_index = {line: float(np.dot(role_mix[line],
                                     [ROLE_RATE[r] for r in role_names]))
                  for line in line_names}

    client_segment: List[int] = []
    client_acquired: List[int] = []
    client_last: List[int] = []
    client_projects: List[int] = []
    client_revenue: List[float] = []
    project_rows: List[dict] = []

    for t in range(n_months):
        calendar = months[t].month - 1
        expected = base_new * ((1 + growth) ** t) * booking_curve[calendar]
        if _active(slowdown, t):
            expected *= slowdown.magnitude
        for _ in range(int(rng.poisson(max(expected, 0.0)))):
            line = line_names[int(rng.choice(len(line_names), p=line_p))]
            spec = SERVICE_LINES[line]

            # Repeat work goes to a client the firm is *currently* serving, not
            # to any name it has ever invoiced. Drawing from the whole history
            # instead — which the first version did — quietly reactivates
            # clients that lapsed two years ago, so the active book grows with
            # the length of the simulation rather than with the work won, and
            # the calibrator cannot land on a stated client count: it measured
            # 89 active at nine wins a month and 137 at twelve, straddling the
            # target with nothing in between.
            recent = [i for i, last in enumerate(client_last)
                      if last >= t - CLIENT_LAPSE_MONTHS]
            if recent and rng.random() < REPEAT_SHARE:
                client = int(recent[int(rng.integers(0, len(recent)))])
                seg_index = client_segment[client]
            else:
                seg_index = int(rng.choice(n_segments, p=seg_shares))
                client_segment.append(seg_index)
                client_acquired.append(t)
                client_last.append(t)
                client_projects.append(0)
                client_revenue.append(0.0)
                client = len(client_segment) - 1

            duration = int(np.clip(round(float(rng.lognormal(
                np.log(spec["months"]), 0.40))), 1, 30))
            # Contract value in arbitrary units, proportional to what a client
            # of this segment spends in a year over the engagement's length. The
            # level is meaningless and the *ratios* are not: a large-segment
            # client's work has to be larger than a small one's, or the
            # concentration detector has nothing true to find.
            contract_value = float(seg_spend[seg_index]) * duration / 12.0 \
                * float(rng.lognormal(0.0, 0.35))
            budget_hours = contract_value / (spec["realisation"] * rate_index[line])

            overrun = float(rng.lognormal(np.log(1.05), 0.18))
            if creep and creep.segment == line and _active(creep, t):
                overrun *= creep.magnitude
            planned_hours = budget_hours * overrun

            # The schedule: how the hours land across the engagement's months.
            # Weighted by delivery capacity, so a job running through August
            # genuinely takes longer in elapsed time.
            span = np.arange(t, min(t + duration, n_months))
            full = t + duration <= n_months
            weights = np.array([delivery_curve[months[i].month - 1]
                                * float(rng.lognormal(0.0, 0.16)) for i in span])
            planned_span = np.arange(t, t + duration)
            planned_weights_total = float(np.sum([
                delivery_curve[months[min(i, n_months - 1)].month - 1]
                for i in planned_span]))
            if weights.sum() <= 0 or planned_weights_total <= 0:
                continue

            if full:
                # Exact: the last month takes the residual, so a completed
                # engagement recognises its contract value to the cent. A
                # rounding gap here is a Tier 1 failure, not a rounding gap.
                share = weights / weights.sum()
                month_revenue = contract_value * share
                month_revenue[-1] = contract_value - float(month_revenue[:-1].sum())
                month_hours = planned_hours * share
            else:
                # Still running at the horizon. Recognise only the fraction of
                # the plan that has actually been delivered.
                delivered = weights.sum() / (weights.sum() + max(
                    duration - len(span), 0) * float(np.mean(weights)))
                share = weights / weights.sum() * delivered
                month_revenue = contract_value * share
                month_hours = planned_hours * share

            mix = role_mix[line]
            line_index = line_names.index(line)
            hours[span, line_index, :] += month_hours[:, None] * mix[None, :]
            recognised[span, line_index] += month_revenue
            recognised_by_segment[span, seg_index] += month_revenue
            bookings[t] += contract_value

            client_projects[client] += 1
            client_revenue[client] += float(month_revenue.sum())
            client_last[client] = max(client_last[client],
                                      min(t + duration - 1, n_months - 1))

            project_rows.append({
                "project_id": f"E{len(project_rows) + 1:06d}",
                "customer_id": f"C{client + 1:06d}",
                "service_line": line,
                "segment": segments[seg_index].name,
                "won_month": months[t],
                "start_month": months[span[0]],
                "end_month": months[min(t + duration - 1, n_months - 1)],
                "contract_value": contract_value,
                "budget_hours": budget_hours,
                "actual_hours": float(month_hours.sum()),
                "recognised_revenue": float(month_revenue.sum()),
                "is_active": not full,
            })

    horizon = n_months - 1
    customers = pd.DataFrame({
        "customer_id": [f"C{i + 1:06d}" for i in range(len(client_segment))],
        "segment": [segments[i].name for i in client_segment],
        "acquired_month": [months[i] for i in client_acquired],
        "last_project_month": [months[i] for i in client_last],
        "projects": [float(n) for n in client_projects],
        "revenue": client_revenue,
        "is_active": [last >= horizon - CLIENT_LAPSE_MONTHS
                      for last in client_last],
    })
    projects = pd.DataFrame(project_rows)
    return customers, projects, hours, recognised, recognised_by_segment, bookings


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------

def _build_timesheets(months, hours, recognised, rng, anomalies,
                      base_rate: float) -> pd.DataFrame:
    """Month x line x role, with utilisation and realisation both derived.

    Utilisation is a *draw* — how well the firm kept its people busy is an
    input, and available hours follow from it, which is what makes
    `billable <= available` hold by construction rather than by a clamp that
    would quietly rewrite the bad months.

    Realisation is the opposite: it is computed from the fee actually
    recognised against the hours actually worked at the rate actually charged,
    so it cannot be set and cannot drift from the other three.
    """
    line_names = list(SERVICE_LINES)
    role_names = list(ROLE_RATE)
    rate = np.array([ROLE_RATE[r] for r in role_names]) * base_rate
    bench = next((a for a in anomalies if a.kind == "bench_time"), None)

    # Utilisation carries over month to month rather than being redrawn from
    # scratch. A firm does not rebuild its bench every four weeks, and an
    # independent draw per month produced a 74-86-74% sawtooth on screen that no
    # services business has ever reported. An AR(1) walk toward the line's own
    # target keeps the level and the anomaly and loses the noise.
    previous: Dict[str, float] = {}

    rows: List[dict] = []
    for t, month in enumerate(months):
        for li, line in enumerate(line_names):
            worked = hours[t, li, :]
            if worked.sum() <= 0:
                continue
            # The month's fee, spread across roles in proportion to what each
            # role's hours were worth at standard rate. Any other allocation
            # would make realisation differ by role for no reason a firm could
            # explain.
            standard_value = worked * rate
            if standard_value.sum() <= 0:
                continue
            fee = recognised[t, li] * standard_value / standard_value.sum()

            target = SERVICE_LINES[line]["utilisation"]
            if bench and bench.segment == line and _active(bench, t):
                target *= bench.magnitude
            anchor = previous.get(line, target)
            utilisation = float(np.clip(
                rng.normal(0.62 * anchor + 0.38 * target, 0.018), 0.30, 0.95))
            previous[line] = utilisation

            for ri, role in enumerate(role_names):
                if worked[ri] <= 0:
                    continue
                billable = float(worked[ri])
                rows.append({
                    "month": month,
                    "service_line": line,
                    "role": role,
                    "billable_hours": billable,
                    "available_hours": billable / utilisation,
                    "standard_rate": float(rate[ri]),
                    "realisation": float(fee[ri] / (billable * rate[ri])),
                    "fee_revenue": float(fee[ri]),
                })
    return pd.DataFrame(rows)


def _build_backlog(months, bookings, recognised) -> pd.DataFrame:
    """Sold work not yet delivered, rolled forward month by month.

    Opening plus what was won minus what was delivered, which is the whole
    definition and the whole identity. Computed cumulatively rather than
    re-derived per row so the roll-forward closes exactly.
    """
    by_month = recognised.sum(axis=1)
    rows = []
    opening = 0.0
    for t, month in enumerate(months):
        won = float(bookings[t])
        delivered = float(by_month[t])
        closing = opening + won - delivered
        rows.append({
            "month": month,
            "opening_backlog": opening,
            "bookings": won,
            "revenue_recognised": delivered,
            "closing_backlog": closing,
        })
        opening = closing
    return pd.DataFrame(rows)


def _build_financials(profile, timesheets, months, rng) -> pd.DataFrame:
    """The P&L, built from the roster rather than from a margin assumption.

    Cost of delivery is the hours worked at a cost rate, so gross margin is an
    *outcome* of realisation and role mix. That is the difference between a
    services P&L and a resale one, and it is what makes the scope-creep anomaly
    show up as margin compression without anyone planting margin compression:
    the same hours earn less fee, and the cost of those hours does not move.

    The cost rate itself is solved from the margin the user stated, because
    nobody is asked their salary cost in the survey. So the level comes from
    the profile and the *shape* comes from the data.
    """
    revenue = (timesheets.groupby("month")["fee_revenue"].sum()
               .reindex(months, fill_value=0.0))
    standard = (timesheets.assign(
        v=timesheets["billable_hours"] * timesheets["standard_rate"])
        .groupby("month")["v"].sum().reindex(months, fill_value=0.0))

    target = float(np.clip(profile.financials.gross_margin_pct, 0.32, 0.90))
    total_revenue = float(revenue.sum())
    total_standard = float(standard.sum())
    if total_standard <= 0:
        raise ReconciliationError("simulation produced no billable hours")
    # One factor, applied to every hour: cogs = hours x rate x cost_factor.
    cost_factor = total_revenue * (1.0 - target) / total_standard

    opex = profile.financials.opex_split
    n = len(months)
    rows = []
    for t, month in enumerate(months):
        rev = float(revenue.loc[month])
        cogs = float(standard.loc[month]) * cost_factor
        gross_profit = rev - cogs

        leverage = 1.0 + 0.18 * (1 - t / max(n - 1, 1))
        sales = rev * opex.get("sales", 0.10) * leverage * float(rng.normal(1.0, 0.04))
        marketing = rev * opex.get("marketing", 0.05) * leverage * float(rng.normal(1.0, 0.07))
        rnd = rev * opex.get("rnd", 0.02) * leverage * float(rng.normal(1.0, 0.03))
        ga = rev * opex.get("ga", 0.13) * leverage * float(rng.normal(1.0, 0.03))
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
    # Working capital is debtors and unbilled work, not stock. A services firm
    # that grows fast funds its clients' payment terms, which is where the cash
    # goes and why a profitable agency can still run out of it.
    # Against trailing revenue rather than one month, which is how DSO is
    # actually computed — a single seasonal month otherwise produces a
    # working-capital movement larger than the month itself.
    receivables = (fin["revenue"].rolling(3, min_periods=1).mean()
                   * (DEBTOR_DAYS / 30.0))
    fin["capex"] = fin["revenue"] * 0.008
    fin["free_cash_flow"] = (fin["ebitda"] - fin["capex"]
                             - receivables.diff().fillna(0.0))
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


def _build_headcount(profile, timesheets, financials, months, rng) -> pd.DataFrame:
    """The roster, with delivery derived from capacity and overhead from the profile.

    **A decision worth stating.** Delivery headcount is *not* taken from the
    profile: it is the available hours the work required, divided by what one
    person can offer. Forcing it to the stated total instead would mean either
    inventing a utilisation that closes the gap — the one number in this
    archetype nobody should be handed — or capping the work at the roster and
    quietly deleting revenue the user told us they earned. A firm with more work
    than people hires or subcontracts; a model that cannot say so is not a model
    of a services firm.

    The stated headcount governs the part it can: the overhead roster is sized
    as whatever is left over, floored so that a profile understating its people
    still gets a plausible back office rather than none.
    """
    available = (timesheets.groupby(["month", "role"])["available_hours"].sum()
                 .unstack(fill_value=0.0).reindex(months, fill_value=0.0))
    delivery_fte = available / HOURS_PER_FTE_MONTH

    # Cost of delivery is exactly the P&L's cost of sales, allocated across
    # roles by what each was worth at standard rate — so the roster and the
    # gross margin cannot tell different stories.
    standard = (timesheets.assign(
        v=timesheets["billable_hours"] * timesheets["standard_rate"])
        .groupby(["month", "role"])["v"].sum()
        .unstack(fill_value=0.0).reindex(months, fill_value=0.0))
    cogs = financials.set_index("month")["cogs"]

    ending_delivery = float(delivery_fte.iloc[-1].sum())
    overhead_total = max(profile.size.headcount_total - ending_delivery,
                         ending_delivery * 0.22)
    overhead_mix = {"sales": 0.34, "marketing": 0.19, "ga": 0.47}
    revenue = financials.set_index("month")["revenue"]
    trajectory = (revenue / max(float(revenue.iloc[-1]), 1.0)).clip(0.0, 2.0)
    opex_people = {"sales": "sales_cost", "marketing": "marketing_cost",
                   "ga": "ga_cost"}
    opex = financials.set_index("month")

    rows = []
    for month in months:
        row_standard = float(standard.loc[month].sum())
        for role in delivery_fte.columns:
            fte = float(delivery_fte.loc[month, role])
            if fte <= 0:
                continue
            share = (float(standard.loc[month, role]) / row_standard
                     if row_standard > 0 else 0.0)
            rows.append(_person_row(month, role, fte,
                                    float(cogs.loc[month]) * share, rng))
        scale = 0.55 + 0.45 * float(trajectory.loc[month])
        for function, share in overhead_mix.items():
            fte = overhead_total * share * scale
            # Most of a back-office line is the people in it; the remainder is
            # tools, premises and everything else that lands in the same
            # bucket. Kept below the line so the roster never costs more than
            # the P&L says the function did.
            cost = float(opex.loc[month, opex_people[function]]) * 0.72
            rows.append(_person_row(month, function, fte, cost, rng))
    return pd.DataFrame(rows)


def _person_row(month, function, fte, cost, rng) -> dict:
    monthly_rate = 0.16 / 12.0          # ~16% annual attrition
    return {
        "month": month, "function": function, "fte": round(fte, 1),
        "cost": float(cost),
        "leavers": int(rng.poisson(max(fte, 0.0) * monthly_rate)),
        "hires": int(rng.poisson(max(fte, 0.0) * monthly_rate * 1.2)),
    }


# Business development, not demand generation. A services firm's pipeline comes
# from people it already knows, which is why referral carries no spend and is
# still the largest source — and why a firm that stops attending its industry's
# events finds out about it two quarters later.
BD_CHANNELS = {"referral": 0.38, "events": 0.24, "content": 0.22, "outbound": 0.16}
PAID_BD_CHANNELS = {"events", "content", "outbound"}


def _build_marketing(profile, financials, months, rng) -> pd.DataFrame:
    """Business-development spend and qualified enquiries, by channel."""
    budget = financials.set_index("month")["marketing_cost"]
    revenue = financials.set_index("month")["revenue"]
    scale = revenue / max(float(revenue.max()), 1.0)
    paid_share = sum(v for k, v in BD_CHANNELS.items() if k in PAID_BD_CHANNELS)
    rows = []
    for month in months:
        for channel, share in BD_CHANNELS.items():
            paid = channel in PAID_BD_CHANNELS
            spend = (float(budget.loc[month]) * share / paid_share
                     * float(rng.normal(1.0, 0.06))) if paid else 0.0
            enquiries = max(share * 42.0 * float(scale.loc[month])
                            * float(rng.normal(1.0, 0.18)), 0.0)
            rows.append({"month": month, "channel": channel,
                         "spend": max(spend, 0.0), "leads": enquiries})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------

@generator("project")
def generate(profile: CompanyProfile,
             params: Optional[GeneratorParams] = None) -> GeneratedData:
    params = params or GeneratorParams()
    rng = volatile(np.random.default_rng(profile.seed), params.volatility)

    months, report_months = month_range(profile.history_months,
                                        end=params.history_end)
    segments = profile.market.segments
    if not segments:
        raise ValueError("project generator requires at least one market segment")

    anomalies = (_plan_anomalies(profile, rng, len(months))
                 if params.inject_anomalies else [])
    growth = monthly_growth(profile)
    report_start = len(months) - profile.history_months

    def simulate(inner_rng, base_new, current_growth) -> Attempt:
        inner_rng = volatile(inner_rng, params.volatility)
        book = _simulate_book(profile, inner_rng, months, segments,
                              current_growth, anomalies, base_new,
                              amplitude=params.seasonality_amplitude)
        customers, projects = book[0], book[1]
        recognised = book[3]
        if projects.empty:
            return Attempt(count=0, growth=None, payload=(book, inner_rng))
        monthly = pd.Series(recognised.sum(axis=1), index=list(months))
        return Attempt(
            count=int(customers["is_active"].sum()),
            growth=yoy_growth(monthly.rolling(12).sum().fillna(0.0), report_start),
            payload=(book, inner_rng),
        )

    book, rng = calibrate(
        simulate, seed=profile.seed,
        target_count=profile.market.customer_count,
        target_growth=profile.financials.growth_rate_yoy,
        growth=growth, base_new=9.0)

    (customers, projects, hours, recognised, recognised_by_segment,
     bookings) = book
    if projects.empty:
        raise ReconciliationError("simulation produced no engagements")

    # --- 1. hours, so the roster matches the stated headcount ---------------
    ending = hours[-12:].sum() / 12.0
    if ending <= 0:
        raise ReconciliationError("simulation produced no delivered hours")
    blended_utilisation = float(np.mean(
        [SERVICE_LINES[line]["utilisation"] for line in SERVICE_LINES]))
    target_hours = (max(profile.size.headcount_total, 1) * DELIVERY_SHARE
                    * HOURS_PER_FTE_MONTH * blended_utilisation)
    hours *= target_hours / ending

    # --- 2. money, so trailing-year revenue matches the stated revenue ------
    trailing = float(recognised[-12:].sum()) if len(months) >= 12 \
        else float(recognised.sum())
    if trailing <= 0:
        raise ReconciliationError("simulation produced non-positive revenue")
    money = profile.financials.revenue / trailing
    recognised = recognised * money
    recognised_by_segment = recognised_by_segment * money
    bookings = bookings * money
    for column in ("contract_value", "recognised_revenue"):
        projects[column] *= money
    customers["revenue"] *= money

    # Hours moved and money moved, by different factors and for different
    # reasons, so neither of them is the charge-out rate. Solve for it.
    #
    # Anchored on the **first** reported year rather than on the whole window,
    # which is not a detail. Anchoring on the average puts the pre-anomaly
    # period above 100% realisation so that the scope-creep months can drag the
    # mean back to target — a firm billing more than its own standard rate for
    # a year, and a deterioration that reads on the chart as a return to
    # normal. Anchoring on the baseline makes the anomaly what it is: a fall
    # from a plausible starting level.
    role_weights = np.array([ROLE_RATE[r] for r in ROLE_RATE])[None, None, :]
    first = report_start + 12
    standard_hours = float((hours[report_start:first] * role_weights).sum())
    earned = float(recognised[report_start:first].sum())
    if standard_hours <= 0 or earned <= 0:
        standard_hours = float((hours * role_weights).sum())
        earned = float(recognised.sum())
    base_rate = earned / (standard_hours * TARGET_REALISATION)
    # Hours are not scaled again, so the budget/actual pair on an engagement
    # keeps the ratio that produced its realisation.
    hour_scale = target_hours / ending
    for column in ("budget_hours", "actual_hours"):
        projects[column] *= hour_scale

    timesheets = _build_timesheets(months, hours, recognised, rng, anomalies,
                                   base_rate)
    if timesheets.empty:
        raise ReconciliationError("simulation produced no timesheet lines")
    backlog = _build_backlog(months, bookings, recognised)
    financials = _build_financials(profile, timesheets, months, rng)
    headcount = _build_headcount(profile, timesheets, financials, months, rng)
    marketing = _build_marketing(profile, financials, months, rng)

    # Two dimensions, as e-commerce has two: a partner asks "which service line"
    # and "which kind of client" as separate questions.
    line_revenue = pd.DataFrame([
        {"month": month, "service_line": line, "revenue": recognised[t, li]}
        for t, month in enumerate(months)
        for li, line in enumerate(SERVICE_LINES)
    ])
    client_revenue = pd.DataFrame([
        {"month": month, "client_segment": segments[si].name,
         "revenue": recognised_by_segment[t, si]}
        for t, month in enumerate(months)
        for si in range(len(segments))
    ])
    segment_fin = segment_financials(financials, {
        "service_line": (line_revenue, "revenue", False),
        "client_segment": (client_revenue, "revenue", False),
    })

    tables = trim_warmup(
        {
            "monthly_financials": financials,
            "segment_financials": segment_fin,
            "timesheets": timesheets,
            "backlog": backlog,
            "projects": projects,
            "customers": customers,
            "headcount": headcount,
            "marketing": marketing,
        },
        cutoff=report_months[0],
        # Both are entity grain, and an engagement won before the reported
        # window is still the engagement being delivered inside it.
        keep_full=("customers", "projects"),
    )

    checks = reconcile(tables, profile)
    return GeneratedData(tables=tables, anomalies=to_reported(anomalies),
                         checks=checks)


def reconcile(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[str]:
    """Hold generated services data to both tiers, as the other two are."""
    return run_gate(tables, profile, source="synthetic",
                    archetype="project").checks
