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


CHECKS: List[Check] = []


def check(name: str, tier: Tier, requires: tuple = ()):
    def wrap(fn):
        CHECKS.append(Check(name=name, tier=tier, fn=fn, requires=requires))
        return fn
    return wrap


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


@check("arr = mrr x 12", Tier.structural, FIN)
def _arr(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(fin["arr"], fin["mrr"] * 12.0, rtol=1e-9))


@check("billings = revenue + change in deferred revenue", Tier.structural, FIN)
def _billings(t, p):
    fin = t["monthly_financials"]
    # From the second reported month onward: the first row's deferred-revenue
    # movement was computed against a warm-up month that trimming removed, so
    # it cannot be re-derived from this frame. The value is right; its input is
    # no longer here.
    return _ok(np.allclose(
        fin["billings"].iloc[1:],
        (fin["revenue"] + fin["deferred_revenue"].diff()).iloc[1:], rtol=1e-9))


@check("free cash flow = ebitda + deferred revenue movement - capex", Tier.structural, FIN)
def _fcf(t, p):
    fin = t["monthly_financials"]
    return _ok(np.allclose(
        fin["free_cash_flow"].iloc[1:],
        (fin["ebitda"] + fin["deferred_revenue"].diff() - fin["capex"]).iloc[1:],
        rtol=1e-9))


@check("cRPO never exceeds RPO", Tier.structural, FIN)
def _crpo(t, p):
    fin = t["monthly_financials"]
    return _ok((fin["crpo"] <= fin["rpo"] + 1e-6).all())


@check("deferred revenue never negative", Tier.structural, FIN)
def _deferred(t, p):
    return _ok((t["monthly_financials"]["deferred_revenue"] >= 0).all())


@check("MRR never negative", Tier.structural, FIN)
def _mrr(t, p):
    return _ok((t["monthly_financials"]["mrr"] >= 0).all())


@check("no negative customer ACV", Tier.structural, ("customers",))
def _acv(t, p):
    return _ok((t["customers"]["final_acv"] >= -1e-6).all())


@check("daily actives never exceed monthly actives", Tier.structural, ("product_usage",))
def _dau(t, p):
    usage = t["product_usage"]
    return _ok((usage["dau"] <= usage["mau"] + 1e-6).all())


@check("activated accounts never exceed new accounts", Tier.structural, ("product_usage",))
def _activated(t, p):
    usage = t["product_usage"]
    return _ok((usage["activated_accounts"] <= usage["new_accounts"]).all())


@check("ramping + productive reps = total reps", Tier.structural, ("sales_capacity",))
def _reps(t, p):
    cap = t["sales_capacity"]
    return _ok((cap["reps_ramping"] + cap["reps_productive"] == cap["reps_total"]).all())


@check("movement signs match movement type", Tier.structural, ("mrr_movements",))
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


@check("churned customers are not active", Tier.structural, ("mrr_movements", "customers"))
def _churned(t, p):
    churned = set(t["mrr_movements"].loc[
        t["mrr_movements"]["movement_type"] == "churn", "customer_id"])
    active = set(t["customers"].loc[t["customers"]["is_active"], "customer_id"])
    overlap = churned & active
    return _ok(not overlap, f"{len(overlap)} customers both churned and active")


# --------------------------------------------------------------------------
# Tier 2 — calibration. Does the data match the profile it claims to describe?
# --------------------------------------------------------------------------

@check("ending ARR matches profile revenue", Tier.calibration, FIN)
def _arr_vs_profile(t, p):
    target = p.financials.revenue
    if target <= 0:
        return CheckResult(True, "no stated revenue", skipped=True)
    ending = float(t["monthly_financials"]["arr"].iloc[-1])
    drift = abs(ending - target) / target
    return _ok(drift < 0.02,
               f"ending ARR {ending:,.0f} vs profile {target:,.0f} ({drift:.1%} drift)")


@check("active customer count matches profile", Tier.calibration, ("customers",))
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


@check("blended ACV matches segment mix", Tier.calibration, ("customers",))
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


@check("gross margin within [0.3, 0.95]", Tier.calibration, FIN)
def _margin_band(t, p):
    fin = t["monthly_financials"]
    inside = fin["gross_margin_pct"].between(0.30, 0.95)
    if bool(inside.all()):
        return _ok(True)
    worst = fin.loc[~inside, "gross_margin_pct"]
    return _ok(False, f"{len(worst)} months outside the band "
                      f"(min {worst.min():.1%}, max {worst.max():.1%})")


def growth_note(tables: Dict[str, pd.DataFrame], profile) -> Optional[str]:
    """Did we deliver the growth the user stated?

    Never a gate in either direction. The customer-count target and a steeply
    declining book are jointly unsatisfiable, so a turnaround company
    legitimately lands short — but the user must not be handed a number that
    contradicts what they told us without being told.
    """
    stated = profile.financials.growth_rate_yoy
    fin = tables.get("monthly_financials")
    if stated is None or fin is None or len(fin) < 13 or fin["arr"].iloc[-13] <= 0:
        return None
    achieved = float(fin["arr"].iloc[-1] / fin["arr"].iloc[-13] - 1.0)
    if abs(achieved - stated) > max(0.10, abs(stated) * 0.35):
        return (f"NOTE: stated growth {stated:+.0%} vs modelled {achieved:+.0%} — "
                f"the customer-count target constrains how far the book can "
                f"decline; treat the trajectory as indicative")
    return f"modelled growth {achieved:+.0%} matches stated {stated:+.0%}: pass"
