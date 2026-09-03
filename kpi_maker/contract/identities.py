"""The accounting identities, in two tiers.

`datagen/saas.py:reconcile` ran these as a 120-line function of inline asserts
where every failure was fatal. That is right for generated data and wrong for
uploaded data, because the checks are making two different kinds of claim:

**Tier 1 — structural.** "Is this data internally contradictory?" Definitional
arithmetic (`gross_profit = revenue - cogs`) and physical impossibility (daily
actives above monthly actives). Violate one and the numbers are fiction
whatever produced them, so these are fatal for every source.

**Tier 2 — calibration.** "Does this data match what you told us about
yourself?" Ending ARR against the profile's stated revenue, customer count
against the stated count, blended ACV against the segment mix. For synthetic
data, hitting the profile is the generator's entire job, so a miss is a bug and
stays fatal. For an upload the data is the truth and the profile is the guess —
a mismatch is a finding worth reporting, not a reason to refuse to render.

Without that split, every honest upload fails the gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd


class Tier(int, Enum):
    structural = 1     # internally contradictory — fatal for any source
    calibration = 2    # disagrees with the profile — fatal only for synthetic
    informational = 3  # never fatal; reports its own sentence either way


@dataclass
class CheckResult:
    passed: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class Check:
    name: str
    tier: Tier
    fn: Callable[[Dict[str, pd.DataFrame], object], CheckResult]
    requires: tuple = ()          # tables that must be present, else skip
    # Which archetypes this identity is *about*. None means universal — a P&L
    # identity holds whatever the business sells. Naming them matters because
    # `requires` alone cannot distinguish "this upload is missing a table" from
    # "this sector does not have that table at all", and the gate needs to tell
    # those apart to know whether it checked anything.
    archetypes: Optional[tuple] = None

    def applies_to(self, archetype: Optional[str]) -> bool:
        return (archetype is None or self.archetypes is None
                or archetype in self.archetypes)

    def run(self, tables: Dict[str, pd.DataFrame], profile) -> CheckResult:
        for table in self.requires:
            if table not in tables or tables[table] is None or tables[table].empty:
                return CheckResult(True, f"no {table} table", skipped=True)
        try:
            return self.fn(tables, profile)
        except KeyError as exc:
            # A missing column is a contract violation in its own right, but it
            # is the schema's job to say so precisely; here it just means this
            # identity could not be evaluated.
            return CheckResult(True, f"column {exc} absent", skipped=True)
        except (TypeError, ValueError) as exc:
            # Almost always a numeric column still holding text — an upload
            # that has not been cast yet. That is a real problem (no metric can
            # be computed from it either), so it fails rather than skips, but
            # it fails with the fix rather than as a TypeError from deep inside
            # a comparison.
            return CheckResult(
                False,
                f"could not be evaluated ({exc}). A numeric column is probably "
                f"still stored as text — add a 'cast' cleaning step for it.")


CHECKS: List[Check] = []


def check(name: str, tier: Tier, requires: tuple = (),
          archetypes: Optional[tuple] = None):
    def wrap(fn):
        CHECKS.append(Check(name=name, tier=tier, fn=fn, requires=requires,
                            archetypes=archetypes))
        return fn
    return wrap


SUBSCRIPTION = ("saas",)
ECOMMERCE = ("ecommerce",)
PROJECT = ("project",)
PRODUCTION = ("production",)
MARKETPLACE = ("marketplace",)


def _ok(condition: bool, detail: str = "") -> CheckResult:
    return CheckResult(bool(condition), detail)


FIN = ("monthly_financials",)


# --------------------------------------------------------------------------
# Tier 1 — structural. Definitional arithmetic and physical impossibility.
# --------------------------------------------------------------------------

@check("gross_profit = revenue - cogs", Tier.structural, FIN)
def _gross_profit(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(fin["gross_profit"], fin["revenue"] - fin["cogs"], rtol=1e-9))


@check("ebitda = gross_profit - total_opex", Tier.structural, FIN)
def _ebitda(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(fin["ebitda"], fin["gross_profit"] - fin["total_opex"], rtol=1e-9))


@check("total_opex = sum of opex lines", Tier.structural, FIN)
def _opex(t, p):
    fin = t["monthly_financials"]
    lines = ["sales_cost", "marketing_cost", "rnd_cost", "ga_cost"]
    return _ok(np.allclose(fin["total_opex"], fin[lines].sum(axis=1), rtol=1e-9))


@check("segment revenue sums to company revenue", Tier.structural,
       ("monthly_financials", "segment_financials"))
def _segment_revenue(t, p):
    """The whole point of splitting by share rather than by simulated level.

    A per-segment figure that does not add back to the company's is not a
    decomposition, it is a second, quieter set of numbers — and a reader who
    sums the segments and gets a different total has caught the product lying.
    Checked per dimension, because a run can be sliced more than one way and a
    residual in one cut must not be hidden by another that reconciles.
    """
    fin = t["monthly_financials"].set_index("month")["revenue"]
    seg = t["segment_financials"]
    for dimension, part in seg.groupby("dimension"):
        totals = part.groupby("month")["revenue"].sum()
        expected = fin.reindex(totals.index)
        if not np.allclose(totals.to_numpy(), expected.to_numpy(), rtol=1e-9):
            worst = float((totals - expected).abs().max())
            return _ok(False, f"{dimension}: off by up to {worst:,.2f}")
        shares = part.groupby("month")["share"].sum()
        if not np.allclose(shares.to_numpy(), 1.0, atol=1e-9):
            return _ok(False, f"{dimension}: shares do not sum to 1.0")
    return _ok(True)


@check("arr = mrr x 12", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _arr(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(fin["arr"], fin["mrr"] * 12.0, rtol=1e-9))


@check("billings = revenue + change in deferred revenue", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _billings(t, p):
    fin = t["monthly_financials"]
    # From the second reported month onward: the first row's deferred-revenue
    # movement was computed against a warm-up month that trimming removed, so
    # it cannot be re-derived from this frame. The value is right; its input is
    # no longer here.
    return _ok(np.allclose(
        fin["billings"].iloc[1:],
        (fin["revenue"] + fin["deferred_revenue"].diff()).iloc[1:], rtol=1e-9))


@check("free cash flow = ebitda + deferred revenue movement - capex", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _fcf(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(
        fin["free_cash_flow"].iloc[1:],
        (fin["ebitda"] + fin["deferred_revenue"].diff() - fin["capex"]).iloc[1:],
        rtol=1e-9))


@check("cRPO never exceeds RPO", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _crpo(t, p):
    fin = t["monthly_financials"]
    return _ok((fin["crpo"] <= fin["rpo"] + 1e-6).all())


@check("deferred revenue never negative", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _deferred(t, p):
    return _ok((t["monthly_financials"]["deferred_revenue"] >= 0).all())


@check("MRR never negative", Tier.structural, FIN, archetypes=SUBSCRIPTION)
def _mrr(t, p):
    return _ok((t["monthly_financials"]["mrr"] >= 0).all())


@check("no negative customer ACV", Tier.structural, ("customers",), archetypes=SUBSCRIPTION)
def _acv(t, p):
    return _ok((t["customers"]["final_acv"] >= -1e-6).all())


@check("daily actives never exceed monthly actives", Tier.structural, ("product_usage",), archetypes=SUBSCRIPTION)
def _dau(t, p):
    usage = t["product_usage"]
    return _ok((usage["dau"] <= usage["mau"] + 1e-6).all())


@check("activated accounts never exceed new accounts", Tier.structural, ("product_usage",), archetypes=SUBSCRIPTION)
def _activated(t, p):
    usage = t["product_usage"]
    return _ok((usage["activated_accounts"] <= usage["new_accounts"]).all())


@check("ramping + productive reps = total reps", Tier.structural, ("sales_capacity",), archetypes=SUBSCRIPTION)
def _reps(t, p):
    cap = t["sales_capacity"]
    return _ok((cap["reps_ramping"] + cap["reps_productive"] == cap["reps_total"]).all())


@check("movement signs match movement type", Tier.structural, ("mrr_movements",), archetypes=SUBSCRIPTION)
def _signs(t, p):
    mov = t["mrr_movements"]
    positive = {"new", "expansion", "reactivation"}
    bad = mov[
        (mov["movement_type"].isin(positive) & (mov["delta_mrr"] < 0))
        | (~mov["movement_type"].isin(positive) & (mov["delta_mrr"] > 0))
    ]
    if bad.empty:
        return _ok(True)
    # The commonest cause by far is an export that stores magnitudes and leaves
    # the sign to the type column. That is a fixable convention, not corrupt
    # data, so the message names the fix.
    return _ok(False, f"{len(bad)} row(s) signed against their type — if your "
                      f"export stores amounts as positive magnitudes, add the "
                      f"'apply_sign' cleaning step with negative_when = "
                      f"churn, contraction")


@check("churned customers are not active", Tier.structural, ("mrr_movements", "customers"), archetypes=SUBSCRIPTION)
def _churned(t, p):
    churned = set(t["mrr_movements"].loc[
        t["mrr_movements"]["movement_type"] == "churn", "customer_id"])
    active = set(t["customers"].loc[t["customers"]["is_active"], "customer_id"])
    overlap = churned & active
    return _ok(not overlap, f"{len(overlap)} customers both churned and active")


# --------------------------------------------------------------------------
# Tier 2 — calibration. Does the data match the profile it claims to describe?
# --------------------------------------------------------------------------

@check("ending ARR matches profile revenue", Tier.calibration, FIN, archetypes=SUBSCRIPTION)
def _arr_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    ending = float(t["monthly_financials"]["arr"].iloc[-1])
    drift = abs(ending - target) / target
    return _ok(drift < 0.02,
               f"ending ARR {ending:,.0f} vs profile {target:,.0f} ({drift:.1%} drift)")


@check("active customer count matches profile", Tier.calibration, ("customers",), archetypes=SUBSCRIPTION)
def _customers_vs_profile(t, p):
    target = p.market.customer_count
    if target <= 0:
        return CheckResult(True, "no stated customer count", skipped=True)
    from ..datagen.saas import calibration_tolerance
    active = int(t["customers"]["is_active"].sum())
    drift = abs(active - target) / target
    # Looser than the calibrator's own target, or a run that legitimately
    # converged to the noise floor still fails here.
    gate = calibration_tolerance(target) * 2.0
    return _ok(drift <= gate,
               f"{active} active vs profile {target} ({drift:.1%} drift, gate {gate:.1%})")


@check("blended ACV matches segment mix", Tier.calibration, ("customers",), archetypes=SUBSCRIPTION)
def _acv_vs_profile(t, p):
    expected = sum(s.share * s.avg_acv for s in p.market.segments)
    if expected <= 0:
        return CheckResult(True, "no segment mix", skipped=True)
    cust = t["customers"]
    active = cust.loc[cust["is_active"], "final_acv"]
    if active.empty:
        return CheckResult(True, "no active customers", skipped=True)
    actual = float(active.mean())
    drift = abs(actual - expected) / expected
    # ACVs are drawn lognormally, so the sample mean's own error scales with
    # 1/sqrt(N). On a three-customer book a 40% gate is a coin flip.
    gate = max(0.40, 1.6 / math.sqrt(max(len(active), 1)))
    return _ok(drift <= gate,
               f"blended ACV {actual:,.0f} vs expected {expected:,.0f} "
               f"({drift:.1%} drift, gate {gate:.1%})")


#: What "a plausible gross margin" means, which is not one number. The check
#: below asserted [0.30, 0.95] for every business, and that band is a
#: subscription band: it would have failed a distributor at 18% and a
#: contractor at 12% as corrupt data. It stayed invisible while there were two
#: archetypes and the second one shipped its own sample profile.
#:
#: Widened rather than deleted, and narrowed where the archetype allows it —
#: 30% is not a floor for retail and it is far too low a floor for software.
#: The unknown case is the widest, because an upload nobody has classified is
#: exactly when this check knows least.
MARGIN_BANDS = {
    "saas": (0.45, 0.95),
    "ecommerce": (0.15, 0.80),
    "project": (0.20, 0.75),
    "production": (0.08, 0.65),
    # On the **take**, not on GMV. See the note in `schemas.py`: a
    # marketplace that reported GMV as revenue would show a 4% margin and
    # be twenty times its real size.
    "marketplace": (0.55, 0.95),
}
UNKNOWN_MARGIN_BAND = (0.05, 0.98)


@check("gross margin is plausible for the archetype", Tier.calibration, FIN)
def _margin_band(t, p):
    from ..profile import sectors

    archetype = sectors.resolve_archetype(p.business_model.type.value).value
    low, high = MARGIN_BANDS.get(archetype, UNKNOWN_MARGIN_BAND)
    fin = t["monthly_financials"]
    inside = fin["gross_margin_pct"].between(low, high)
    if bool(inside.all()):
        return _ok(True)
    worst = fin.loc[~inside, "gross_margin_pct"]
    return _ok(False, f"{len(worst)} months outside the {low:.0%}-{high:.0%} "
                      f"band a {archetype!r} business is held to "
                      f"(min {worst.min():.1%}, max {worst.max():.1%})")


# --------------------------------------------------------------------------
# E-commerce
# --------------------------------------------------------------------------
#
# Most of these hold by construction rather than by luck: the generator derives
# revenue from orders and the funnel backwards from orders, so the arithmetic
# has one direction. They are asserted anyway, because "it holds by
# construction" is a claim about code that someone will change.

ORD = ("orders",)


@check("net revenue = gross - discounts - returns", Tier.structural, ORD,
       archetypes=ECOMMERCE)
def _net_revenue(t, p):
    o = t["orders"]
    net = o["gross_revenue"] - o["discounts"] - o["returns"]
    return _ok((net >= -1e-6).all(),
               f"{int((net < -1e-6).sum())} row(s) where discounts and returns "
               f"exceed gross revenue")


@check("order revenue ties to the P&L", Tier.structural, ("orders", "monthly_financials"),
       archetypes=ECOMMERCE)
def _orders_tie(t, p):
    o = t["orders"]
    net = (o["gross_revenue"] - o["discounts"] - o["returns"])
    by_month = o.assign(net=net).groupby("month")["net"].sum()
    fin = t["monthly_financials"].set_index("month")["revenue"]
    joined = by_month.reindex(fin.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), fin.to_numpy(), rtol=1e-6, atol=1e-6),
               "the order lines and the revenue line disagree — one of them was "
               "changed without the other")


@check("checkouts never exceed carts, carts never exceed sessions",
       Tier.structural, ("traffic",), archetypes=ECOMMERCE)
def _funnel_order(t, p):
    tr = t["traffic"]
    bad_carts = int((tr["add_to_carts"] > tr["sessions"] + 1e-6).sum())
    bad_checkouts = int((tr["checkouts"] > tr["add_to_carts"] + 1e-6).sum())
    return _ok(not (bad_carts or bad_checkouts),
               f"{bad_carts} row(s) with more carts than sessions, "
               f"{bad_checkouts} with more checkouts than carts")


@check("orders never exceed checkouts", Tier.structural, ("traffic",),
       archetypes=ECOMMERCE)
def _orders_within_funnel(t, p):
    tr = t["traffic"]
    return _ok((tr["orders"] <= tr["checkouts"] + 1e-6).all(),
               f"{int((tr['orders'] > tr['checkouts'] + 1e-6).sum())} row(s) with "
               f"more orders than checkouts")


@check("units and order counts never negative", Tier.structural, ORD,
       archetypes=ECOMMERCE)
def _counts_non_negative(t, p):
    o = t["orders"]
    return _ok((o["orders"] >= 0).all() and (o["units"] >= 0).all())


@check("stock on hand never negative", Tier.structural, ("inventory",),
       archetypes=ECOMMERCE)
def _stock_non_negative(t, p):
    return _ok((t["inventory"]["units_on_hand"] >= -1e-6).all())


@check("units sold reconcile between orders and inventory", Tier.structural,
       ("orders", "inventory"), archetypes=ECOMMERCE)
def _units_tie(t, p):
    sold = t["orders"].groupby("month")["units"].sum()
    stocked = t["inventory"].groupby("month")["units_sold"].sum()
    joined = stocked.reindex(sold.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), sold.to_numpy(), rtol=1e-6, atol=1e-6),
               "the order lines and the inventory movement disagree on units")


@check("new + repeat buyers = active buyers", Tier.structural, ("buyers",),
       archetypes=ECOMMERCE)
def _buyer_split(t, p):
    b = t["buyers"]
    return _ok(np.allclose(b["new_buyers"] + b["repeat_buyers"],
                           b["active_buyers"], rtol=1e-9, atol=1e-9),
               "the buyer split does not add up to the buyer count")


@check("buyers never exceed orders", Tier.structural, ("buyers", "orders"),
       archetypes=ECOMMERCE)
def _buyers_vs_orders(t, p):
    b = t["buyers"].set_index("month")["active_buyers"]
    o = t["orders"].groupby("month")["orders"].sum()
    joined = b.reindex(o.index).fillna(0.0)
    return _ok((joined <= o + 1e-6).all(),
               f"{int((joined > o + 1e-6).sum())} month(s) with more buyers than "
               f"orders, which would mean someone bought without ordering")


@check("ending revenue matches profile revenue", Tier.calibration, ORD,
       archetypes=ECOMMERCE)
def _revenue_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    o = t["orders"]
    net = (o["gross_revenue"] - o["discounts"] - o["returns"])
    by_month = o.assign(net=net).groupby("month")["net"].sum().sort_index()
    if len(by_month) < 12:
        return CheckResult(True, "less than a year of orders", skipped=True)
    trailing = float(by_month.iloc[-12:].sum())
    drift = abs(trailing - target) / target
    return _ok(drift < 0.02,
               f"trailing-year revenue {trailing:,.0f} vs profile {target:,.0f} "
               f"({drift:.1%} drift)")


@check("active buyer count matches profile", Tier.calibration, ("customers",),
       archetypes=ECOMMERCE)
def _buyers_vs_profile(t, p):
    target = p.market.customer_count
    if target <= 0:
        return CheckResult(True, "no stated customer count", skipped=True)
    from ..datagen.base import calibration_tolerance
    active = int(t["customers"]["is_active"].sum())
    drift = abs(active - target) / target
    gate = calibration_tolerance(target) * 2.0
    return _ok(drift <= gate,
               f"{active} active buyers vs profile {target} "
               f"({drift:.1%} drift, gate {gate:.1%})")


@check("average order value is plausible", Tier.calibration, ORD,
       archetypes=ECOMMERCE)
def _aov_band(t, p):
    o = t["orders"]
    orders = float(o["orders"].sum())
    if orders <= 0:
        return CheckResult(True, "no orders", skipped=True)
    net = float((o["gross_revenue"] - o["discounts"] - o["returns"]).sum())
    aov = net / orders
    # A band rather than a point: AOV is an outcome of the simulation, not an
    # input, and the profile does not state one. What would be wrong is an AOV
    # of 40p or of six thousand pounds, either of which means the order-count
    # sizing has come adrift from the spend.
    return _ok(2.0 <= aov <= 5000.0,
               f"average order value {aov:,.2f} is outside a plausible retail band")


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------
#
# The plan asked for two: `hours x rate = revenue` and `utilisation <= 1`. Both
# are here, and the first needed a third term before it was true of anything. A
# services firm does not earn the standard rate — it earns the fee it agreed,
# over however many hours the job actually took. Realisation is the ratio
# between those two, and writing the identity as `hours x rate = revenue`
# without it would either be false on every fixed-fee engagement or would force
# the generator to pretend no job ever overruns. So realisation is a *derived*
# column: the generator recognises revenue by percentage of completion and this
# check asserts the arithmetic closes.
#
# The other three are what makes a backlog a backlog rather than a number
# someone typed: it rolls forward, it never goes negative, and the revenue that
# leaves it is the revenue in the P&L.

TIME = ("timesheets",)


@check("fee revenue = billable hours x standard rate x realisation",
       Tier.structural, TIME, archetypes=PROJECT)
def _fee_arithmetic(t, p):
    ts = t["timesheets"]
    expected = ts["billable_hours"] * ts["standard_rate"] * ts["realisation"]
    if np.allclose(ts["fee_revenue"], expected, rtol=1e-6, atol=1e-6):
        return _ok(True)
    worst = float((ts["fee_revenue"] - expected).abs().max())
    return _ok(False, f"off by up to {worst:,.2f} — realisation is derived from "
                      f"the other three, so a gap means one of them was "
                      f"rewritten without it")


@check("billable hours never exceed available hours", Tier.structural, TIME,
       archetypes=PROJECT)
def _utilisation_ceiling(t, p):
    ts = t["timesheets"]
    over = int((ts["billable_hours"] > ts["available_hours"] + 1e-6).sum())
    return _ok(not over,
               f"{over} row(s) bill more hours than the people were available "
               f"for, which is a utilisation above 100%")


@check("timesheet fee revenue ties to the P&L", Tier.structural,
       ("timesheets", "monthly_financials"), archetypes=PROJECT)
def _fees_tie(t, p):
    fees = t["timesheets"].groupby("month")["fee_revenue"].sum()
    fin = t["monthly_financials"].set_index("month")["revenue"]
    joined = fees.reindex(fin.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), fin.to_numpy(), rtol=1e-6, atol=1e-6),
               "the timesheet lines and the revenue line disagree — one of them "
               "was changed without the other")


@check("backlog rolls forward", Tier.structural, ("backlog",), archetypes=PROJECT)
def _backlog_rollforward(t, p):
    b = t["backlog"]
    expected = b["opening_backlog"] + b["bookings"] - b["revenue_recognised"]
    if np.allclose(b["closing_backlog"], expected, rtol=1e-6, atol=1e-6):
        return _ok(True)
    worst = float((b["closing_backlog"] - expected).abs().max())
    return _ok(False, f"closing backlog is off by up to {worst:,.2f} from "
                      f"opening + bookings - revenue recognised")


@check("backlog never negative", Tier.structural, ("backlog",), archetypes=PROJECT)
def _backlog_non_negative(t, p):
    b = t["backlog"]
    return _ok((b["closing_backlog"] >= -1e-6).all(),
               "sold work cannot go below zero — more revenue was recognised "
               "than was ever booked")


@check("recognised backlog revenue ties to the P&L", Tier.structural,
       ("backlog", "monthly_financials"), archetypes=PROJECT)
def _backlog_ties(t, p):
    b = t["backlog"].set_index("month")["revenue_recognised"]
    fin = t["monthly_financials"].set_index("month")["revenue"]
    joined = b.reindex(fin.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), fin.to_numpy(), rtol=1e-6, atol=1e-6),
               "the backlog movement and the revenue line disagree about what "
               "was delivered")


@check("recognised revenue never exceeds contract value", Tier.structural,
       ("projects",), archetypes=PROJECT)
def _recognition_ceiling(t, p):
    pr = t["projects"]
    over = pr[pr["recognised_revenue"] > pr["contract_value"] * (1 + 1e-9) + 1e-6]
    return _ok(over.empty,
               f"{len(over)} engagement(s) recognise more than they were sold "
               f"for, which is revenue nobody agreed to pay")


@check("a completed engagement has recognised its whole contract value",
       Tier.structural, ("projects",), archetypes=PROJECT)
def _completed_fully_recognised(t, p):
    pr = t["projects"]
    done = pr[~pr["is_active"].astype(bool)]
    if done.empty:
        return CheckResult(True, "no completed engagements", skipped=True)
    gap = (done["contract_value"] - done["recognised_revenue"]).abs()
    worst = float(gap.max())
    return _ok(worst <= max(1e-6, float(done["contract_value"].max()) * 1e-9),
               f"{int((gap > 1e-6).sum())} closed engagement(s) left revenue "
               f"unrecognised, worst {worst:,.2f} — percentage of completion "
               f"has to reach 100% when the work stops")


@check("trailing-year fee revenue matches profile revenue", Tier.calibration,
       TIME, archetypes=PROJECT)
def _fees_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    by_month = t["timesheets"].groupby("month")["fee_revenue"].sum().sort_index()
    if len(by_month) < 12:
        return CheckResult(True, "less than a year of timesheets", skipped=True)
    trailing = float(by_month.iloc[-12:].sum())
    drift = abs(trailing - target) / target
    return _ok(drift < 0.02,
               f"trailing-year fees {trailing:,.0f} vs profile {target:,.0f} "
               f"({drift:.1%} drift)")


@check("active client count matches profile", Tier.calibration, ("customers",),
       archetypes=PROJECT)
def _clients_vs_profile(t, p):
    target = p.market.customer_count
    if target <= 0:
        return CheckResult(True, "no stated customer count", skipped=True)
    from ..datagen.base import calibration_tolerance
    active = int(t["customers"]["is_active"].sum())
    drift = abs(active - target) / target
    gate = calibration_tolerance(target) * 2.0
    return _ok(drift <= gate,
               f"{active} active clients vs profile {target} "
               f"({drift:.1%} drift, gate {gate:.1%})")


@check("blended utilisation is plausible", Tier.calibration, TIME,
       archetypes=PROJECT)
def _utilisation_band(t, p):
    ts = t["timesheets"]
    available = float(ts["available_hours"].sum())
    if available <= 0:
        return CheckResult(True, "no available hours", skipped=True)
    utilisation = float(ts["billable_hours"].sum()) / available
    # A band rather than a point, for the same reason as e-commerce's AOV: the
    # profile states no utilisation, so this is an outcome. What would be wrong
    # is 20% (a firm that cannot pay its people) or 98% (nobody sells, trains or
    # takes leave) — either means the hours have come adrift from the roster.
    return _ok(0.40 <= utilisation <= 0.95,
               f"blended utilisation {utilisation:.1%} is outside a plausible "
               f"professional-services band")


# --------------------------------------------------------------------------
# Production
# --------------------------------------------------------------------------
#
# The plan asked for three: `units x price = revenue`, `output <= capacity`, and
# `OEE = availability x performance x quality`. All three are here as written,
# which is the first time that has happened — a plant's arithmetic is genuinely
# this clean, because every term is a count of something physical.
#
# The fourth is the one the plan did not name and a factory could not do
# without: stock rolls forward. What was made and not shipped is still in the
# building, and a model where it is not has quietly invented or destroyed goods.

MAKE = ("production",)
SHIP = ("shipments",)


@check("gross revenue = units shipped x unit price", Tier.structural, SHIP,
       archetypes=PRODUCTION)
def _units_times_price(t, p):
    s = t["shipments"]
    expected = s["units_shipped"] * s["unit_price"]
    if np.allclose(s["gross_revenue"], expected, rtol=1e-6, atol=1e-6):
        return _ok(True)
    worst = float((s["gross_revenue"] - expected).abs().max())
    return _ok(False, f"off by up to {worst:,.2f}")


@check("output never exceeds the capacity that was scheduled", Tier.structural,
       MAKE, archetypes=PRODUCTION)
def _output_within_capacity(t, p):
    m = t["production"]
    total = m["units_produced"] + m["units_scrapped"]
    over = int((total > m["capacity_units"] * (1 + 1e-9) + 1e-6).sum())
    return _ok(not over,
               f"{over} line-month(s) made more than the line could have made "
               f"in the hours it was scheduled for")


@check("scheduled capacity never exceeds the line's nameplate", Tier.structural,
       MAKE, archetypes=PRODUCTION)
def _capacity_within_nameplate(t, p):
    m = t["production"]
    over = int((m["capacity_units"] > m["nameplate_units"] * (1 + 1e-9) + 1e-6).sum())
    return _ok(not over,
               f"{over} line-month(s) scheduled beyond what the line physically "
               f"runs — capacity is a ceiling, not a target")


@check("OEE = availability x performance x quality", Tier.structural, MAKE,
       archetypes=PRODUCTION)
def _oee(t, p):
    m = t["production"]
    expected = m["availability"] * m["performance"] * m["quality"]
    if np.allclose(m["oee"], expected, rtol=1e-9, atol=1e-9):
        return _ok(True)
    worst = float((m["oee"] - expected).abs().max())
    return _ok(False, f"off by up to {worst:.6f} — the whole value of OEE is "
                      f"that it decomposes, so a total that is not the product "
                      f"of its three losses is a different number wearing the "
                      f"same name")


@check("runtime never exceeds planned hours", Tier.structural, MAKE,
       archetypes=PRODUCTION)
def _runtime_within_plan(t, p):
    m = t["production"]
    return _ok((m["runtime_hours"] <= m["planned_hours"] + 1e-6).all(),
               f"{int((m['runtime_hours'] > m['planned_hours'] + 1e-6).sum())} "
               f"line-month(s) ran longer than they were scheduled for")


@check("quality is good units over everything made", Tier.structural, MAKE,
       archetypes=PRODUCTION)
def _quality_definition(t, p):
    m = t["production"]
    total = m["units_produced"] + m["units_scrapped"]
    live = m[total > 0]
    if live.empty:
        return CheckResult(True, "no output", skipped=True)
    made = live["units_produced"] + live["units_scrapped"]
    return _ok(np.allclose(live["quality"], live["units_produced"] / made,
                           rtol=1e-6, atol=1e-6),
               "the quality rate and the scrap count disagree")


@check("stock rolls forward", Tier.structural, ("inventory",),
       archetypes=PRODUCTION)
def _stock_rollforward(t, p):
    inv = t["inventory"]
    expected = (inv["opening_units"] + inv["units_produced"]
                - inv["units_shipped"])
    if not np.allclose(inv["closing_units"], expected, rtol=1e-6, atol=1e-6):
        worst = float((inv["closing_units"] - expected).abs().max())
        return _ok(False, f"closing stock is off by up to {worst:,.1f} units "
                          f"from opening + made - shipped")
    return _ok((inv["closing_units"] >= -1e-6).all(),
               "stock went negative — more was shipped than was ever made")


@check("what was made and what was shipped reconcile across the two tables",
       Tier.structural, ("production", "inventory", "shipments"),
       archetypes=PRODUCTION)
def _made_and_shipped_tie(t, p):
    made = t["production"].groupby("month")["units_produced"].sum()
    booked = t["inventory"].groupby("month")["units_produced"].sum()
    shipped = t["shipments"].groupby("month")["units_shipped"].sum()
    stocked = t["inventory"].groupby("month")["units_shipped"].sum()
    for left, right, what in ((made, booked, "made"), (shipped, stocked, "shipped")):
        joined = right.reindex(left.index).fillna(0.0)
        if not np.allclose(joined.to_numpy(), left.to_numpy(),
                           rtol=1e-6, atol=1e-6):
            return _ok(False, f"the stock ledger and the {what} lines disagree "
                              f"on units")
    return _ok(True)


@check("shipment revenue ties to the P&L", Tier.structural,
       ("shipments", "monthly_financials"), archetypes=PRODUCTION)
def _shipments_tie(t, p):
    s = t["shipments"]
    net = s["gross_revenue"] - s["discounts"] - s["returns"]
    by_month = s.assign(net=net).groupby("month")["net"].sum()
    fin = t["monthly_financials"].set_index("month")["revenue"]
    joined = by_month.reindex(fin.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), fin.to_numpy(), rtol=1e-6, atol=1e-6),
               "the shipment lines and the revenue line disagree — one of them "
               "was changed without the other")


@check("trailing-year shipped revenue matches profile revenue", Tier.calibration,
       SHIP, archetypes=PRODUCTION)
def _shipped_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    s = t["shipments"]
    net = s["gross_revenue"] - s["discounts"] - s["returns"]
    by_month = s.assign(net=net).groupby("month")["net"].sum().sort_index()
    if len(by_month) < 12:
        return CheckResult(True, "less than a year of shipments", skipped=True)
    trailing = float(by_month.iloc[-12:].sum())
    drift = abs(trailing - target) / target
    return _ok(drift < 0.02,
               f"trailing-year shipped revenue {trailing:,.0f} vs profile "
               f"{target:,.0f} ({drift:.1%} drift)")


@check("active customer count matches profile", Tier.calibration, ("customers",),
       archetypes=PRODUCTION)
def _accounts_vs_profile(t, p):
    target = p.market.customer_count
    if target <= 0:
        return CheckResult(True, "no stated customer count", skipped=True)
    from ..datagen.base import calibration_tolerance
    active = int(t["customers"]["is_active"].sum())
    drift = abs(active - target) / target
    gate = calibration_tolerance(target) * 2.0
    return _ok(drift <= gate,
               f"{active} active accounts vs profile {target} "
               f"({drift:.1%} drift, gate {gate:.1%})")


@check("OEE is plausible", Tier.calibration, MAKE, archetypes=PRODUCTION)
def _oee_band(t, p):
    m = t["production"]
    weight = m["capacity_units"]
    if float(weight.sum()) <= 0:
        return CheckResult(True, "no scheduled capacity", skipped=True)
    blended = float((m["oee"] * weight).sum() / weight.sum())
    # A band, like AOV and utilisation before it: nobody states an OEE in the
    # survey, so this is an outcome. World-class is around 85% and discrete
    # manufacturing averages near 60%; below 25% the plant would not be open,
    # and above 95% the losses have been modelled away.
    return _ok(0.25 <= blended <= 0.95,
               f"blended OEE {blended:.1%} is outside a plausible band")


# --------------------------------------------------------------------------
# Marketplace
# --------------------------------------------------------------------------
#
# The plan asked for two — `GMV x take_rate = revenue` and
# `fills <= min(supply, demand)` — and both are here as written. The second is
# the more interesting: it is the only identity in this file that is about a
# *market* rather than about a business, and it is what makes a liquidity
# problem visible as something other than weak demand.

GMV = ("gmv",)
LIQUIDITY = ("liquidity",)


@check("net revenue = GMV x take rate", Tier.structural, GMV,
       archetypes=MARKETPLACE)
def _take(t, p):
    g = t["gmv"]
    expected = g["gross_merchandise_value"] * g["take_rate"]
    if np.allclose(g["net_revenue"], expected, rtol=1e-6, atol=1e-6):
        return _ok(True)
    worst = float((g["net_revenue"] - expected).abs().max())
    return _ok(False, f"off by up to {worst:,.2f}")


@check("matches never exceed either side of the market", Tier.structural,
       LIQUIDITY, archetypes=MARKETPLACE)
def _fills_within_both_sides(t, p):
    liq = t["liquidity"]
    over_supply = int((liq["matches"] > liq["supply_listings"] + 1e-6).sum())
    over_demand = int((liq["matches"] > liq["demand_requests"] + 1e-6).sum())
    return _ok(not (over_supply or over_demand),
               f"{over_supply} month(s) matched more than was listed and "
               f"{over_demand} more than was asked for — a match needs both "
               f"sides, and a model where it does not is not a market")


@check("match rate is matches over demand", Tier.structural, LIQUIDITY,
       archetypes=MARKETPLACE)
def _match_rate_definition(t, p):
    liq = t["liquidity"]
    live = liq[liq["demand_requests"] > 0]
    if live.empty:
        return CheckResult(True, "no demand", skipped=True)
    return _ok(np.allclose(live["match_rate"],
                           live["matches"] / live["demand_requests"],
                           rtol=1e-6, atol=1e-6),
               "the match rate and the two counts disagree")


@check("matched transactions tie to the GMV lines", Tier.structural,
       ("liquidity", "gmv"), archetypes=MARKETPLACE)
def _matches_tie(t, p):
    matched = t["liquidity"].groupby("month")["matches"].sum()
    orders = t["gmv"].groupby("month")["orders"].sum()
    joined = orders.reindex(matched.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), matched.to_numpy(),
                           rtol=1e-6, atol=1e-6),
               "a match is a transaction, and the two tables disagree on how "
               "many there were")


@check("net revenue ties to the P&L", Tier.structural,
       ("gmv", "monthly_financials"), archetypes=MARKETPLACE)
def _net_revenue_ties(t, p):
    by_month = t["gmv"].groupby("month")["net_revenue"].sum()
    fin = t["monthly_financials"].set_index("month")["revenue"]
    joined = by_month.reindex(fin.index).fillna(0.0)
    return _ok(np.allclose(joined.to_numpy(), fin.to_numpy(), rtol=1e-6, atol=1e-6),
               "the take and the revenue line disagree. If they are apart by "
               "roughly the take rate, GMV is being reported as revenue")


@check("net revenue never exceeds the value that passed through",
       Tier.structural, GMV, archetypes=MARKETPLACE)
def _take_within_gmv(t, p):
    """The one mistake this archetype's whole design exists to prevent.

    A platform that reports GMV where it means the take is twenty times its real
    size at a fifth of its real margin, and the two columns sit next to each
    other. Swap them and this is what fires.
    """
    g = t["gmv"]
    over = int((g["net_revenue"] > g["gross_merchandise_value"] + 1e-6).sum())
    return _ok(not over,
               f"{over} row(s) keep more than passed through them — GMV and the "
               f"take have probably been swapped")


@check("a supplier's history is coherent", Tier.structural, ("suppliers",),
       archetypes=MARKETPLACE)
def _supplier_history(t, p):
    """Entity-grain, so it is deliberately *not* compared against the monthly
    tables.

    The first version of this checked that seller GMV summed to no more than the
    platform's, and it failed on the first run for a reason worth keeping: the
    supplier book is `keep_full` — it carries the warm-up, because a seller who
    joined before the reported window is still the seller trading inside it —
    while `gmv` is trimmed to the window. Sixty months of one against thirty-six
    of the other is not a contradiction in the data, it is a comparison that
    cannot be made. Any identity spanning an entity table and a monthly one has
    the same problem.
    """
    s = t["suppliers"]
    backwards = int((s["last_active_month"] < s["joined_month"]).sum())
    idle = s[s["is_active"].astype(bool) & (s["listings"] <= 0)]
    return _ok(not backwards and idle.empty,
               f"{backwards} seller(s) last active before they joined, "
               f"{len(idle)} active with nothing listed")


@check("trailing-year take matches profile revenue", Tier.calibration, GMV,
       archetypes=MARKETPLACE)
def _take_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    by_month = t["gmv"].groupby("month")["net_revenue"].sum().sort_index()
    if len(by_month) < 12:
        return CheckResult(True, "less than a year of GMV", skipped=True)
    trailing = float(by_month.iloc[-12:].sum())
    drift = abs(trailing - target) / target
    return _ok(drift < 0.02,
               f"trailing-year take {trailing:,.0f} vs profile {target:,.0f} "
               f"({drift:.1%} drift)")


@check("active buyer count matches profile", Tier.calibration, ("customers",),
       archetypes=MARKETPLACE)
def _buyers_vs_profile_marketplace(t, p):
    target = p.market.customer_count
    if target <= 0:
        return CheckResult(True, "no stated customer count", skipped=True)
    from ..datagen.base import calibration_tolerance
    active = int(t["customers"]["is_active"].sum())
    drift = abs(active - target) / target
    gate = calibration_tolerance(target) * 2.0
    return _ok(drift <= gate,
               f"{active} active buyers vs profile {target} "
               f"({drift:.1%} drift, gate {gate:.1%})")


@check("take rate is plausible", Tier.calibration, GMV, archetypes=MARKETPLACE)
def _take_rate_band(t, p):
    g = t["gmv"]
    gmv = float(g["gross_merchandise_value"].sum())
    if gmv <= 0:
        return CheckResult(True, "no GMV", skipped=True)
    blended = float(g["net_revenue"].sum()) / gmv
    # A band, like AOV, utilisation and OEE before it. Nobody states a take rate
    # in the survey, so it is an outcome: below 1% the platform is not a
    # business and above 40% it is a retailer pretending to be a platform.
    return _ok(0.01 <= blended <= 0.40,
               f"blended take rate {blended:.1%} is outside a plausible band")


def growth_note(tables: Dict[str, pd.DataFrame], profile) -> Optional[str]:
    """Did we deliver the growth the user stated?

    Never a gate in either direction. The customer-count target and a steeply
    declining book are jointly unsatisfiable, so a turnaround company
    legitimately lands short — but the user must not be handed a number that
    contradicts what they told us without being told.
    """
    stated = profile.financials.growth_rate_yoy
    fin = tables.get("monthly_financials")
    if stated is None or fin is None or len(fin) < 13:
        return None

    # Growth is measured on whatever the archetype's top line is. A
    # subscription business compounds ARR, which is already annualised; a
    # retailer has no such column, so the comparable figure is trailing-twelve
    # revenue. Reading `arr` unconditionally is what this used to do, and it
    # raised a KeyError the first time a non-subscription archetype reached it.
    if "arr" in fin.columns:
        series = fin["arr"]
    elif len(fin) >= 24:
        series = fin["revenue"].rolling(12).sum()
    else:
        return None

    if pd.isna(series.iloc[-13]) or series.iloc[-13] <= 0:
        return None
    achieved = float(series.iloc[-1] / series.iloc[-13] - 1.0)
    if abs(achieved - stated) > max(0.10, abs(stated) * 0.35):
        return (f"NOTE: stated growth {stated:+.0%} vs modelled {achieved:+.0%} — "
                f"the customer-count target constrains how far the book can "
                f"decline; treat the trajectory as indicative")
    return f"modelled growth {achieved:+.0%} matches stated {stated:+.0%}: pass"
