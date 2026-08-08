"""Driver-based synthetic data generator for subscription businesses.

Design rules (see ARCHITECTURE.md §3):

  * Sample a SMALL number of primitives; DERIVE everything else through
    accounting identities. Never sample two numbers that must agree.
  * Simulate a warm-up period before the reported window, so cohorts have real
    history and month 1 is not an artificial cliff.
  * Add structure, not noise: trend + seasonality + deliberate anomalies. A
    dataset with nothing wrong in it produces a report with nothing to say.
  * Scale to the profile at the end. Because MRR is linear in ACV, simulating
    the SHAPE and then scaling to the stated revenue keeps every relationship
    intact while hitting the user's number exactly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..contract.gate import ReconciliationError, run_gate
from ..profile.schema import CompanyProfile, Stage

WARMUP_MONTHS = 24

# Monthly growth in new-logo acquisition, by company stage.
STAGE_GROWTH = {
    Stage.pre_revenue: 0.08,
    Stage.early: 0.055,
    Stage.growth: 0.030,
    Stage.established: 0.012,
    Stage.mature: 0.004,
    Stage.turnaround: -0.005,
}

# B2B software seasonality: Q4 push, summer trough, January restart.
B2B_SEASONALITY = np.array([
    0.85, 0.95, 1.10, 1.00, 1.05, 1.15,   # Jan-Jun
    0.80, 0.75, 1.10, 1.05, 1.10, 1.35,   # Jul-Dec
])

MARKETING_CHANNELS = {
    "paid_search": 0.30,
    "content_seo": 0.15,
    "events": 0.20,
    "outbound": 0.25,
    "partner": 0.10,
}


def _monthly_growth(profile) -> float:
    """Monthly acquisition growth. A stated growth rate always beats the stage prior.

    Stage is a coarse proxy — it puts every self-described "growth stage"
    company on the same 3%/month curve. When the user actually tells us their
    growth rate, using the prior instead would mean asking a question and then
    ignoring the answer.
    """
    stated = profile.financials.growth_rate_yoy
    if stated is not None:
        # Annual -> monthly, guarding the domain: -100% annual has no monthly
        # equivalent, and runaway rates make the simulation explode.
        return float(np.clip((1.0 + max(stated, -0.90)) ** (1 / 12) - 1.0, -0.05, 0.20))
    return STAGE_GROWTH.get(profile.size.stage, 0.02)


@dataclass
class Anomaly:
    """A deliberate, documented event the insight engine is meant to find."""
    kind: str
    start_month: int
    end_month: int
    magnitude: float
    description: str
    segment: str = ""


@dataclass
class GeneratedData:
    tables: Dict[str, pd.DataFrame]
    anomalies: List[Anomaly] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> pd.DataFrame:
        return self.tables[key]


# `ReconciliationError` is imported above from `contract.gate`, where it now
# lives alongside the identities it reports on. Re-exported from here because
# `datagen` has always been where callers import it from.


def generate(profile: CompanyProfile) -> GeneratedData:
    rng = np.random.default_rng(profile.seed)
    n_report = profile.history_months
    n_total = n_report + WARMUP_MONTHS

    months = pd.period_range(end=pd.Period("2025-12", freq="M"), periods=n_total, freq="M")
    report_months = months[WARMUP_MONTHS:]

    segments = profile.market.segments
    if not segments:
        raise ValueError("SaaS generator requires at least one market segment")

    growth = _monthly_growth(profile)

    anomalies = _plan_anomalies(profile, rng, n_total, segments)

    # Calibrate acquisition VOLUME to the profile's customer count before
    # scaling ACV to the profile's revenue. Without this, the ACV scaling alone
    # absorbs the whole error and silently invents a book with the wrong shape
    # — e.g. twice the customers at a third of the stated deal size.
    customers, movements, rng = _calibrated_book(
        profile, months, segments, growth, anomalies
    )

    # --- Scale the simulated book to the profile's stated revenue -----------
    ending_mrr = movements.loc[movements["month"] <= months[-1], "delta_mrr"].sum()
    target_mrr = profile.financials.revenue / 12.0
    if ending_mrr <= 0:
        raise ReconciliationError("simulation produced non-positive ending MRR")
    scale = target_mrr / ending_mrr
    movements["delta_mrr"] *= scale
    for col in ("initial_acv", "final_acv"):
        customers[col] *= scale

    financials = _build_financials(profile, movements, months, rng)
    headcount = _build_headcount(profile, financials, months, rng)
    pipeline_tbl, marketing = _build_gtm(profile, rng, months, movements, financials)

    product_usage = _build_product_usage(profile, rng, months, movements, customers)
    sales_capacity = _build_sales_capacity(profile, rng, months, movements, headcount)

    tables = {
        "customers": customers,
        "mrr_movements": movements,
        "monthly_financials": financials,
        "headcount": headcount,
        "pipeline": pipeline_tbl,
        "marketing": marketing,
        "product_usage": product_usage,
        "sales_capacity": sales_capacity,
    }

    # Trim the warm-up out of every time-series table; `customers` keeps its
    # full history because cohort analysis needs the earlier acquisitions.
    cutoff = report_months[0]
    for name in ("mrr_movements", "monthly_financials", "headcount", "pipeline",
                 "marketing", "product_usage", "sales_capacity"):
        tables[name] = tables[name][tables[name]["month"] >= cutoff].reset_index(drop=True)

    checks = reconcile(tables, profile)
    reported = [
        Anomaly(a.kind, a.start_month - WARMUP_MONTHS, a.end_month - WARMUP_MONTHS,
                a.magnitude, a.description, a.segment)
        for a in anomalies if a.end_month >= WARMUP_MONTHS
    ]
    return GeneratedData(tables=tables, anomalies=reported, checks=checks)


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------

def _plan_anomalies(profile, rng, n_total, segments) -> List[Anomaly]:
    """Two or three planted events so the narrative has something real to find."""
    worst_segment = max(segments, key=lambda s: s.logo_churn_annual).name
    mid = WARMUP_MONTHS + int(0.45 * (n_total - WARMUP_MONTHS))
    return [
        Anomaly(
            kind="churn_spike",
            start_month=mid,
            end_month=mid + 4,
            magnitude=2.1,
            segment=worst_segment,
            description=(
                f"Churn in the {worst_segment} segment runs ~2x baseline for four "
                "months — consistent with a pricing change or a competitor entry."
            ),
        ),
        Anomaly(
            kind="cac_inflation",
            start_month=mid + 6,
            end_month=n_total,
            magnitude=1.55,
            description=(
                "Paid-search cost per lead inflates ~55% and does not recover, "
                "dragging blended CAC and CAC payback."
            ),
        ),
        Anomaly(
            kind="margin_compression",
            start_month=mid - 8,
            end_month=n_total,
            magnitude=0.045,
            description=(
                "Hosting and support costs grow faster than revenue, compressing "
                "gross margin by ~4.5 points over the period."
            ),
        ),
    ]


def calibration_tolerance(target: int) -> float:
    """Relative tolerance that stays achievable at small customer counts.

    Acquisition is a Poisson process, so the noise floor on the surviving
    population is roughly sqrt(N). For a 4-person startup with 24 customers one
    logo is already 4% of the book and a fixed 8% target is unreachable — the
    calibrator would spin and then fail the reconciliation gate. Allow the
    larger of 8% or three customers, which is tight for big books and merely
    honest for small ones.
    """
    if target <= 0:
        return 1.0
    return max(0.08, 3.0 / target, 1.0 / math.sqrt(target))


def _measured_arr_growth(movements, months, report_start: int) -> Optional[float]:
    """Year-on-year ARR growth implied by a simulated book.

    Measured on the REPORTED window, not the warm-up, because that is the
    window the user will see and judge the number against.
    """
    if report_start < 0 or len(months) - report_start < 13:
        return None
    monthly = movements.groupby("month")["delta_mrr"].sum().reindex(months, fill_value=0.0)
    mrr = monthly.cumsum()
    end = float(mrr.iloc[-1])
    prior = float(mrr.iloc[-13])
    if prior <= 0:
        return None
    return end / prior - 1.0


def _calibrated_book(profile, months, segments, growth, anomalies,
                     max_attempts: int = 10):
    """Solve for the acquisition rate that lands on the profile's customer count.

    Re-simulating a few times beats deriving the rate in closed form, because
    churn, seasonality and Poisson noise all feed back into the surviving
    population. We keep the BEST attempt rather than the last: the final
    iteration is not necessarily the closest, and on a small book the search
    can oscillate around the target.
    """
    target = profile.market.customer_count
    tolerance = calibration_tolerance(target)
    base_new = 12.0
    best: Optional[Tuple[float, Any, Any, Any]] = None
    rng = np.random.default_rng(profile.seed)

    # Two knobs, two targets. `base_new` sets the LEVEL of the book (how many
    # customers survive to the end) and `growth` sets its SHAPE (how fast ARR
    # compounds). Calibrating only the level is what let a company that told us
    # it was flat come out growing 26% — the acquisition rate was flat, but a
    # young book keeps accumulating regardless. Both are corrected together.
    target_growth = profile.financials.growth_rate_yoy
    report_start = len(months) - profile.history_months

    # Small books are cheap to simulate and noisy to hit, so they get more
    # attempts; a 50,000-customer book is the opposite on both counts.
    if target and target < 300:
        max_attempts = 24
    elif target_growth is not None:
        # Fitting two targets needs more iterations than fitting one.
        max_attempts = max(max_attempts, 18)

    for attempt in range(max_attempts):
        # The SEED IS HELD CONSTANT across attempts. Re-seeding each pass would
        # redraw the Poisson noise every time, so the search could never tell a
        # better `base_new` from a luckier draw and would wander instead of
        # converging. Fixing the noise makes the objective deterministic in
        # base_new, which is what lets the damped step below actually work.
        rng = np.random.default_rng(profile.seed)
        customers, movements = _simulate_book(
            profile, rng, months, segments, growth, anomalies, base_new
        )
        if target <= 0:
            return customers, movements, rng

        active = int(customers["is_active"].sum())
        if active == 0:
            base_new = max(base_new * 2, 1.0)
            continue

        error = abs(active - target) / target

        growth_error = 0.0
        if target_growth is not None:
            measured = _measured_arr_growth(movements, months, report_start)
            if measured is not None:
                growth_error = abs(measured - target_growth) / max(abs(target_growth), 0.10)
                # Nudge the monthly acquisition-growth knob toward closing the
                # gap in ANNUAL ARR growth. The step has to be big enough to
                # cross zero within the attempt budget: a business that reports
                # itself as flat or shrinking needs NEGATIVE acquisition growth,
                # and a timid step never gets there from a positive start.
                gap = float(np.clip(target_growth - measured, -1.5, 1.5))
                growth = float(np.clip(growth + 1.1 * gap / 12.0, -0.09, 0.20))

        combined = error + 0.5 * growth_error
        if best is None or combined < best[0]:
            best = (combined, customers, movements, rng)
        if error <= tolerance and growth_error <= 0.15:
            return customers, movements, rng

        # Damped correction. An undamped ratio step overshoots on small books
        # because the same multiplier also changes how many churn away.
        ratio = target / active
        base_new *= 1.0 + 0.75 * (ratio - 1.0)
        base_new = max(base_new, 1e-4)

    if best is None:
        raise ReconciliationError(
            f"could not simulate any surviving customers for target {target}"
        )
    return best[1], best[2], best[3]


def _simulate_book(profile, rng, months, segments, growth, anomalies, base_new=12.0):
    """Month-by-month customer acquisition, expansion, contraction and churn."""
    n_total = len(months)
    seasonality = B2B_SEASONALITY if profile.market.seasonality != "none" else np.ones(12)

    churn_spikes = {a.segment: a for a in anomalies if a.kind == "churn_spike"}
    shares = np.array([s.share for s in segments], dtype=float)
    shares = shares / shares.sum()

    customer_rows = []
    movement_rows = []
    active: Dict[int, dict] = {}
    next_id = 1

    countries = list(profile.market.geographies.keys()) or ["US"]
    country_p = np.array(list(profile.market.geographies.values()) or [1.0])
    country_p = country_p / country_p.sum()

    for t, month in enumerate(months):
        seas = seasonality[month.month - 1]
        expected_new = base_new * ((1 + growth) ** t) * seas
        # No lower floor on the rate. A 0.1/month floor compounded over the
        # 60-month simulation guaranteed ~6 acquisitions no matter how small
        # the calibrator set base_new, which made books of 1-5 customers
        # impossible to hit. Poisson(0) is legitimately 0.
        n_new = int(rng.poisson(max(expected_new, 0.0)))

        # --- acquisition ---
        seg_draw = rng.choice(len(segments), size=n_new, p=shares)
        for idx in seg_draw:
            seg = segments[idx]
            # Lognormal ACV: deal sizes are right-skewed in every real book.
            acv = float(rng.lognormal(mean=np.log(seg.avg_acv), sigma=0.45))
            cid = next_id
            next_id += 1
            active[cid] = {"segment": seg.name, "acv": acv, "acquired": t}
            customer_rows.append({
                "customer_id": cid,
                "segment": seg.name,
                "country": rng.choice(countries, p=country_p),
                "acquired_month": month,
                "acquired_index": t,
                "churn_month": pd.NaT,
                "initial_acv": acv,
                "final_acv": acv,
            })
            movement_rows.append({
                "month": month, "customer_id": cid, "segment": seg.name,
                "movement_type": "new", "delta_mrr": acv / 12.0,
            })

        # --- expansion / contraction / churn on the existing book ---
        seg_by_name = {s.name: s for s in segments}
        for cid in list(active.keys()):
            rec = active[cid]
            seg = seg_by_name[rec["segment"]]

            monthly_churn = 1 - (1 - seg.logo_churn_annual) ** (1 / 12)
            spike = churn_spikes.get(seg.name)
            if spike and spike.start_month <= t <= spike.end_month:
                monthly_churn *= spike.magnitude

            if rng.random() < monthly_churn:
                movement_rows.append({
                    "month": month, "customer_id": cid, "segment": seg.name,
                    "movement_type": "churn", "delta_mrr": -rec["acv"] / 12.0,
                })
                active.pop(cid)
                continue

            # Expansion calibrated so the annual rate lands near the segment's
            # stated expansion: p * uplift * 12 ~= expansion_annual.
            if seg.expansion_annual > 0:
                p_expand = min(0.9, seg.expansion_annual / 0.12 / 12)
                if rng.random() < p_expand:
                    uplift = rec["acv"] * float(rng.uniform(0.06, 0.18))
                    rec["acv"] += uplift
                    movement_rows.append({
                        "month": month, "customer_id": cid, "segment": seg.name,
                        "movement_type": "expansion", "delta_mrr": uplift / 12.0,
                    })
            if rng.random() < 0.006:
                cut = rec["acv"] * float(rng.uniform(0.05, 0.20))
                rec["acv"] -= cut
                movement_rows.append({
                    "month": month, "customer_id": cid, "segment": seg.name,
                    "movement_type": "contraction", "delta_mrr": -cut / 12.0,
                })

    customers = pd.DataFrame(customer_rows)
    movements = pd.DataFrame(movement_rows)

    # Stamp churn dates and final ACVs back onto the customer table.
    churned = movements[movements["movement_type"] == "churn"].set_index("customer_id")["month"]
    customers["churn_month"] = customers["customer_id"].map(churned)
    final_acv = (
        movements.groupby("customer_id")["delta_mrr"].sum() * 12.0
    ).clip(lower=0.0)
    customers["final_acv"] = customers["customer_id"].map(final_acv).fillna(0.0)
    customers["is_active"] = customers["churn_month"].isna()

    return customers, movements


def _build_financials(profile, movements, months, rng) -> pd.DataFrame:
    """Derive the P&L from the MRR book. Every line ties back to revenue."""
    mrr_by_month = movements.groupby("month")["delta_mrr"].sum().reindex(months, fill_value=0.0)
    mrr = mrr_by_month.cumsum()

    rows = []
    gm_target = profile.financials.gross_margin_pct
    n = len(months)
    opex = profile.financials.opex_split

    for t, month in enumerate(months):
        m = float(mrr.loc[month])
        revenue = m
        # Margin compression anomaly: gross margin was better in the past.
        gm = gm_target + 0.045 * (1 - t / max(n - 1, 1))
        cogs = revenue * (1 - gm)
        gross_profit = revenue - cogs

        # Opex ratios improve slowly with scale (operating leverage), with noise.
        leverage = 1.0 + 0.25 * (1 - t / max(n - 1, 1))
        noise = float(rng.normal(1.0, 0.04))
        sales = revenue * opex.get("sales", 0.22) * leverage * noise
        marketing = revenue * opex.get("marketing", 0.14) * leverage * float(rng.normal(1.0, 0.06))
        rnd = revenue * opex.get("rnd", 0.20) * leverage * float(rng.normal(1.0, 0.03))
        ga = revenue * opex.get("ga", 0.12) * leverage * float(rng.normal(1.0, 0.03))
        total_opex = sales + marketing + rnd + ga
        ebitda = gross_profit - total_opex

        rows.append({
            "month": month, "mrr": m, "arr": m * 12.0, "revenue": revenue,
            "cogs": cogs, "gross_profit": gross_profit, "gross_margin_pct": gm,
            "sales_cost": sales, "marketing_cost": marketing, "rnd_cost": rnd,
            "ga_cost": ga, "total_opex": total_opex, "ebitda": ebitda,
            "ebitda_margin": ebitda / revenue if revenue else 0.0,
        })

    df = pd.DataFrame(rows)

    # --- Contracted / forward revenue -------------------------------------
    # Billings = revenue recognised + the change in deferred revenue. Rather
    # than sample billings (which would drift from revenue), we DERIVE deferred
    # revenue from the contract structure and let the identity define billings.
    # That way billings, deferred revenue and RPO can never disagree.
    prepay = profile.business_model.annual_prepay_share
    contract_months = profile.business_model.avg_contract_months

    # An annually-prepaid book carries, on average, half a year of unrecognised
    # revenue; a monthly book carries almost none.
    #
    # The billed-in-advance horizon is capped at 12 months even on multi-year
    # contracts, because multi-year deals are almost universally invoiced
    # annually rather than in full up front. Without the cap a 24-month
    # contract produced ~10 months of deferred revenue, and the resulting
    # working-capital swing pushed FCF margin above 50% — a number no real
    # SaaS business posts.
    billed_ahead_months = min(contract_months, 12.0)
    deferred_months = prepay * (billed_ahead_months / 2.0)
    df["deferred_revenue"] = df["revenue"] * deferred_months
    df["billings"] = df["revenue"] + df["deferred_revenue"].diff().fillna(0.0)

    # RPO is the whole contracted book not yet recognised; cRPO is the part due
    # in the next twelve months (the ASC 606 disclosure investors actually use).
    df["rpo"] = df["arr"] * (contract_months / 12.0) * 0.85
    df["crpo"] = np.minimum(df["rpo"], df["arr"] * 0.92)

    # --- Cash --------------------------------------------------------------
    df["net_burn"] = -df["ebitda"]
    df["cash"] = -df["net_burn"].cumsum()
    df["cash"] += profile.financials.cash - df["cash"].iloc[-1]

    # Free cash flow. Prepaid billings are collected ahead of recognition, so
    # FCF runs ahead of EBITDA for a growing annual-prepay business — this is
    # exactly why FCF margin and operating margin diverge in real filings.
    capex = df["revenue"] * 0.025
    working_capital_release = df["deferred_revenue"].diff().fillna(0.0)
    df["capex"] = capex
    df["free_cash_flow"] = df["ebitda"] + working_capital_release - capex
    return df


def _build_headcount(profile, financials, months, rng) -> pd.DataFrame:
    """Headcount grows sub-linearly with revenue — that gap IS operating leverage."""
    end_revenue = financials["revenue"].iloc[-1]
    # Sales attrition in SaaS runs well above engineering; a blended number
    # would hide the segment that actually needs attention.
    base_attrition = {"sales": 0.26, "customer_success": 0.20, "marketing": 0.17,
                      "engineering": 0.12, "ga": 0.10}
    rows = []
    for _, fin in financials.iterrows():
        ratio = (fin["revenue"] / end_revenue) ** 0.8 if end_revenue else 0.0
        for function, end_fte in profile.size.headcount_by_function.items():
            fte = max(1, int(round(end_fte * ratio)))
            monthly_rate = base_attrition.get(function, 0.15) / 12
            rows.append({
                "month": fin["month"], "function": function, "fte": fte,
                "leavers": int(rng.poisson(fte * monthly_rate)),
                # Function-weighted cost: engineering and sales cost more per head.
                "cost": fte * {"engineering": 11000, "sales": 9500, "marketing": 8000,
                               "customer_success": 7000, "ga": 7500}.get(function, 8000),
            })
    return pd.DataFrame(rows)


def _build_gtm(profile, rng, months, movements, financials):
    """Back out the funnel from won deals — never simulate it independently."""
    new_by_month = (
        movements[movements["movement_type"] == "new"]
        .groupby("month").size().reindex(months, fill_value=0)
    )
    new_arr = (
        movements[movements["movement_type"] == "new"]
        .groupby("month")["delta_mrr"].sum().reindex(months, fill_value=0.0) * 12.0
    )

    pipeline_rows, marketing_rows = [], []
    cac_anomaly_start = len(months) - int(0.35 * len(months))

    for t, month in enumerate(months):
        won = int(new_by_month.loc[month])
        win_rate = float(np.clip(rng.normal(0.22, 0.03), 0.08, 0.45))
        closed = max(won, int(round(won / win_rate))) if won else int(rng.integers(1, 4))
        lost = max(closed - won, 0)
        avg_deal = float(new_arr.loc[month] / won) if won else 0.0
        cycle = float(np.clip(rng.normal(74, 9), 30, 160))

        # Open pipeline is roughly one sales cycle of forward coverage.
        coverage = float(np.clip(rng.normal(3.1, 0.45), 1.2, 6.0))
        quota = float(new_arr.loc[month]) * 1.15
        pipeline_rows.append({
            "month": month, "opps_won": won, "opps_lost": lost, "opps_closed": closed,
            "win_rate": won / closed if closed else 0.0,
            "avg_deal_size": avg_deal, "sales_cycle_days": cycle,
            "open_pipeline_value": quota * coverage, "quota": quota,
            "pipeline_coverage": coverage,
        })

        # Marketing spend splits the S&M line across channels; the funnel is
        # sized from the opportunities we know were created.
        spend_total = float(financials.loc[financials["month"] == month, "marketing_cost"].iloc[0])
        sqls = max(closed, 1)
        mqls = int(sqls / np.clip(rng.normal(0.28, 0.04), 0.1, 0.6))
        leads = int(mqls / np.clip(rng.normal(0.22, 0.03), 0.08, 0.5))
        for channel, share in MARKETING_CHANNELS.items():
            spend = spend_total * share * float(rng.normal(1.0, 0.08))
            if channel == "paid_search" and t >= cac_anomaly_start:
                spend *= 1.55   # planted CAC inflation
            marketing_rows.append({
                "month": month, "channel": channel, "spend": spend,
                "leads": int(leads * share), "mqls": int(mqls * share),
                "sqls": int(sqls * share),
            })

    return pd.DataFrame(pipeline_rows), pd.DataFrame(marketing_rows)


def _build_product_usage(profile, rng, months, movements, customers) -> pd.DataFrame:
    """Product engagement — the leading half of the scorecard.

    Every metric here moves BEFORE revenue does, which is the whole point: a
    scorecard of financial outcomes is a post-mortem. Engagement is modelled as
    a function of the book so it cannot drift away from the customer count.
    """
    matrix = movements.pivot_table(index="month", columns="customer_id",
                                   values="delta_mrr", aggfunc="sum",
                                   fill_value=0.0).cumsum().clip(lower=0.0)
    active_by_month = (matrix > 0).sum(axis=1).reindex(months).ffill().fillna(0)

    new_by_month = (
        movements[movements["movement_type"] == "new"]
        .groupby("month").size().reindex(months, fill_value=0)
    )

    # Self-serve products are used daily; field-sold enterprise software is
    # used weekly at best. That difference is the single biggest driver of
    # DAU/MAU, so it comes from the sales motion rather than a free parameter.
    motion = profile.business_model.sales_motion.value
    base_engagement = {"self_serve": 0.46, "inside": 0.34,
                       "field": 0.26, "channel": 0.22, "tender": 0.18}.get(motion, 0.30)

    rows = []
    n = len(months)
    for t, month in enumerate(months):
        accounts = float(active_by_month.loc[month]) if month in active_by_month.index else 0.0
        seats_per_account = float(np.clip(rng.normal(9.0, 1.4), 2.0, 60.0))
        mau = accounts * seats_per_account * float(np.clip(rng.normal(0.72, 0.05), 0.3, 1.0))
        # Engagement drifts down slowly as the book broadens beyond early
        # enthusiasts — a real and commonly-missed pattern.
        drift = 1.0 - 0.12 * (t / max(n - 1, 1))
        dau = mau * float(np.clip(rng.normal(base_engagement * drift, 0.02), 0.05, 0.95))

        new_accounts = int(new_by_month.loc[month]) if month in new_by_month.index else 0
        activation = float(np.clip(rng.normal(0.62, 0.05), 0.1, 0.98))
        rows.append({
            "month": month,
            "active_accounts": round(accounts),
            "mau": round(mau),
            "dau": round(dau),
            "new_accounts": new_accounts,
            "activated_accounts": int(round(new_accounts * activation)),
            "median_time_to_value_days": float(np.clip(rng.normal(21, 4), 1, 120)),
            "weekly_active_pct": float(np.clip(rng.normal(0.55, 0.04), 0.05, 0.99)),
        })
    return pd.DataFrame(rows)


def _build_sales_capacity(profile, rng, months, movements, headcount) -> pd.DataFrame:
    """Rep capacity, ramp and quota attainment.

    Quota attainment is a leading indicator of next quarter's bookings and one
    of the first things a CRO is asked for. Derived from actual new ARR so it
    reconciles with the bookings the rest of the model already produced.
    """
    new_arr = (
        movements[movements["movement_type"] == "new"]
        .groupby("month")["delta_mrr"].sum().reindex(months, fill_value=0.0) * 12.0
    )
    sales_fte = (
        headcount[headcount["function"] == "sales"]
        .groupby("month")["fte"].sum().reindex(months).ffill().fillna(1)
    )

    rows = []
    for month in months:
        reps = max(1, int(sales_fte.loc[month]))
        # Roughly a third of a growing team is still ramping at any moment.
        ramping = int(round(reps * float(np.clip(rng.normal(0.28, 0.06), 0.0, 0.6))))
        productive = max(1, reps - ramping)
        booked = float(new_arr.loc[month])
        per_rep = booked / productive if productive else 0.0
        # Quota is set from a plan, so attainment is bookings against plan.
        quota_per_rep = max(per_rep * float(np.clip(rng.normal(1.12, 0.08), 0.6, 2.0)), 1.0)
        rows.append({
            "month": month,
            "reps_total": reps,
            "reps_ramping": ramping,
            "reps_productive": productive,
            "new_arr_booked": booked,
            "arr_per_productive_rep": per_rep,
            "quota_per_rep": quota_per_rep,
            "quota_attainment": per_rep / quota_per_rep if quota_per_rep else 0.0,
            "avg_ramp_months": float(np.clip(rng.normal(4.6, 0.6), 1.0, 12.0)),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Reconciliation gate — nothing renders until these pass
# --------------------------------------------------------------------------

def reconcile(tables: Dict[str, pd.DataFrame], profile: CompanyProfile) -> List[str]:
    """Assert the accounting identities. Failure here means the data is fiction.

    The identities themselves now live in `kpi_maker/contract/`, because they
    were never really about synthetic data — uploaded data has to satisfy the
    structural ones too. Generated data is held to BOTH tiers: matching the
    profile is this generator's whole job, so a calibration miss is a bug here
    even though it is only a warning for a file someone uploaded.
    """
    result = run_gate(tables, profile, source="synthetic")
    return result.checks
