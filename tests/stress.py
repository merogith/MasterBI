"""Adversarial test harness.

Runs the pipeline across the full configuration matrix and asserts invariants
that must hold for ANY company — a 4-person startup and a $5B platform alike.

This is not a unit-test suite. It is a "does the product lie?" suite: every
check here corresponds to a way the output could look plausible while being
wrong.

    python -m tests.stress            # full matrix
    python -m tests.stress --quick    # scale extremes only
"""
from __future__ import annotations

import argparse
import math
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.datagen.saas import calibration_tolerance, generate
from kpi_maker.insight.detectors import detect_all
from kpi_maker.kpi.schema import Perspective, Timing
from kpi_maker.kpi.selection import MIN_LEADING_SHARE, MIN_PER_PERSPECTIVE, select
from kpi_maker.metrics.engine import compute, facts_table
from kpi_maker.profile.schema import CompanyProfile

# Plausible bounds per KPI. A value outside these is not "unusual" — it is
# evidence of a bug, because no real company sits here.
BOUNDS: Dict[str, Tuple[float, float]] = {
    "nrr": (0.30, 2.50),
    "grr": (0.20, 1.001),
    "logo_churn_rate": (0.0, 1.20),
    "gross_margin_pct": (-0.50, 0.98),
    "expansion_share": (0.0, 1.0),
    "revenue_concentration_top10": (0.0, 1.0001),
    "win_rate": (0.0, 1.0001),
    "lead_to_mql_rate": (0.0, 1.0001),
    "mql_to_sql_rate": (0.0, 1.0001),
    "cac_payback_months": (0.0, 400.0),
    "ltv_cac_ratio": (0.0, 100.0),
    "magic_number": (-5.0, 20.0),
    "burn_multiple": (0.0, 200.0),
    "cash_runway_months": (0.0, 600.0),
    "arr_per_fte": (1_000.0, 5_000_000.0),
    "employee_attrition": (0.0, 2.0),
    "rnd_pct_revenue": (0.0, 2.0),
    "arpa": (0.0, 5_000_000.0),
    "sales_cycle_days": (1.0, 1000.0),
    "pipeline_coverage": (0.0, 50.0),
    "customer_count": (1.0, 5_000_000.0),
    "arr_growth_yoy": (-1.0, 20.0),
    "ebitda_margin": (-10.0, 1.0),
    "rule_of_40": (-10.0, 20.0),
    "blended_cac": (0.0, 10_000_000.0),
    # Best-in-class public SaaS FCF margin sits around 30-35%. Anything above
    # 45% means the working-capital model is inflating cash, not that the
    # company is exceptional.
    "fcf_margin": (-3.0, 0.45),
    "billings": (0.0, 5e10),
    "crpo_growth": (-1.0, 20.0),
    "quick_ratio": (0.0, 60.0),
    "sm_pct_revenue": (0.0, 3.0),
    "activation_rate": (0.0, 1.0001),
    "engagement_ratio": (0.0, 1.0001),
    "time_to_value_days": (0.0, 400.0),
    "quota_attainment": (0.0, 5.0),
    "rep_ramp_months": (0.5, 24.0),
    "enterprise_customers": (0.0, 1_000_000.0),
    "arr_per_rep": (0.0, 100_000_000.0),
    "pipeline_created": (0.0, 5e10),
}


@dataclass
class Case:
    name: str
    profile: CompanyProfile
    expect: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Issue:
    case: str
    severity: str      # fail | warn
    check: str
    detail: str


def _profile(name: str, **overrides) -> CompanyProfile:
    """A valid baseline profile with targeted overrides."""
    base: Dict[str, Any] = {
        "identity": {"name": name, "country": "US", "currency": "USD"},
        "industry": {"internal_sector": "saas.general"},
        "business_model": {
            "type": "saas", "customer_type": "B2B",
            "revenue_model": ["subscription"], "sales_motion": "inside",
        },
        "size": {
            "headcount_total": 85,
            "headcount_by_function": {"engineering": 30, "sales": 20,
                                      "customer_success": 14, "ga": 12, "marketing": 9},
            "revenue_band": "10-50M", "stage": "growth", "age_years": 7,
        },
        "market": {
            "geographies": {"US": 1.0},
            "segments": [
                {"name": "SMB", "share": 0.55, "avg_acv": 5500,
                 "logo_churn_annual": 0.28, "expansion_annual": 0.05},
                {"name": "Mid-Market", "share": 0.33, "avg_acv": 21000,
                 "logo_churn_annual": 0.14, "expansion_annual": 0.11},
                {"name": "Enterprise", "share": 0.12, "avg_acv": 72000,
                 "logo_churn_annual": 0.07, "expansion_annual": 0.18},
            ],
            "customer_count": 645, "concentration_top10_pct": 0.22,
            "seasonality": "b2b_software",
        },
        "financials": {
            "revenue": 12_000_000, "gross_margin_pct": 0.72,
            "opex_split": {"sales": 0.22, "marketing": 0.14, "rnd": 0.20, "ga": 0.12},
            "cash": 6_500_000,
        },
        "org_culture": {"data_maturity": "bi_tool", "kpi_experience": "medium"},
        "intent": {"primary_objective": "growth", "audience": "board"},
        "data_availability": {"has": ["billing", "crm", "gl", "hris",
                                      "marketing_automation", "product_analytics"],
                              "missing": ["survey", "support_desk"]},
        "seed": 4242, "history_months": 36,
    }
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return CompanyProfile(**base)


def _segments(blended_target: float, mix=(0.55, 0.33, 0.12),
              mult=(1.0, 3.8, 13.0), churn=(0.28, 0.14, 0.07),
              exp=(0.05, 0.11, 0.18)) -> Tuple[List[dict], float]:
    """Segments whose blended ACV hits a target, so revenue reconciles."""
    base = blended_target / sum(s * m for s, m in zip(mix, mult))
    names = ["SMB", "Mid-Market", "Enterprise"]
    segs = [{"name": n, "share": s, "avg_acv": round(base * m, 2),
             "logo_churn_annual": c, "expansion_annual": e}
            for n, s, m, c, e in zip(names, mix, mult, churn, exp)]
    return segs, sum(s["share"] * s["avg_acv"] for s in segs)


def build_cases(quick: bool = False) -> List[Case]:
    cases: List[Case] = []

    # ---- Scale extremes ---------------------------------------------------
    # A four-person startup. Tiny counts break anything that assumes a
    # meaningful population (cohorts, top-10 concentration, percentiles).
    segs, blended = _segments(9_000, mix=(0.85, 0.13, 0.02))
    cases.append(Case("micro_startup_4_people", _profile(
        "Tiny Startup",
        size={"headcount_total": 4,
              "headcount_by_function": {"engineering": 2, "sales": 1, "ga": 1},
              "revenue_band": "under_1m", "stage": "early", "age_years": 1},
        market={"segments": segs, "customer_count": max(1, round(220_000 / blended)),
                "geographies": {"US": 1.0}, "concentration_top10_pct": 0.65},
        financials={"revenue": 220_000, "gross_margin_pct": 0.62,
                    "opex_split": {"sales": 0.30, "marketing": 0.20,
                                   "rnd": 0.40, "ga": 0.20},
                    "cash": 180_000},
        intent={"primary_objective": "cash_liquidity", "audience": "investor"},
        org_culture={"data_maturity": "none", "kpi_experience": "low"},
        history_months=24, seed=11,
    )))

    # A mega-cap platform. Big numbers break formatting, percentile logic and
    # anything quadratic in customer count.
    segs, blended = _segments(140_000, mix=(0.05, 0.25, 0.70))
    cases.append(Case("mega_platform_5bn", _profile(
        "Mega Platform",
        size={"headcount_total": 21_000,
              "headcount_by_function": {"engineering": 7000, "sales": 6000,
                                        "customer_success": 3500, "ga": 2500,
                                        "marketing": 2000},
              "revenue_band": "50m_plus", "stage": "mature", "age_years": 24},
        market={"segments": segs, "customer_count": round(5_000_000_000 / blended),
                "geographies": {"US": 0.55, "GB": 0.18, "DE": 0.15, "AU": 0.12},
                "concentration_top10_pct": 0.08},
        financials={"revenue": 5_000_000_000, "gross_margin_pct": 0.81,
                    "opex_split": {"sales": 0.16, "marketing": 0.09,
                                   "rnd": 0.18, "ga": 0.08},
                    "cash": 3_800_000_000},
        intent={"primary_objective": "margin_expansion", "audience": "investor"},
        org_culture={"data_maturity": "data_team", "kpi_experience": "high"},
        seed=22,
    )))

    # Single-customer edge: concentration = 100%, cohorts of size 1.
    segs, blended = _segments(400_000, mix=(0.0001, 0.0001, 0.9998))
    cases.append(Case("single_whale_customer", _profile(
        "One Big Client",
        size={"headcount_total": 9,
              "headcount_by_function": {"engineering": 5, "sales": 2, "ga": 2},
              "revenue_band": "under_1m", "stage": "early"},
        market={"segments": segs, "customer_count": 3,
                "geographies": {"US": 1.0}, "concentration_top10_pct": 1.0},
        financials={"revenue": 1_200_000, "gross_margin_pct": 0.70,
                    "opex_split": {"sales": 0.15, "marketing": 0.05,
                                   "rnd": 0.35, "ga": 0.15},
                    "cash": 500_000},
        intent={"primary_objective": "retention", "audience": "exec"},
        history_months=24, seed=33,
    )))

    if quick:
        return cases

    # ---- Motion / model variants -----------------------------------------
    cases.append(Case("self_serve_no_pipeline", _profile(
        "Self Serve Co",
        business_model={"type": "saas", "customer_type": "B2B",
                        "revenue_model": ["subscription"],
                        "sales_motion": "self_serve"},
        seed=44,
    ), expect={"absent_kpis": ["win_rate", "pipeline_coverage", "sales_cycle_days"]}))

    segs, blended = _segments(140)
    cases.append(Case("b2c_prosumer", _profile(
        "Consumer App",
        business_model={"type": "saas", "customer_type": "B2C",
                        "revenue_model": ["subscription"],
                        "sales_motion": "self_serve"},
        market={"segments": segs, "customer_count": round(9_000_000 / blended),
                "geographies": {"US": 1.0}, "concentration_top10_pct": 0.01,
                "seasonality": "retail_q4"},
        financials={"revenue": 9_000_000, "gross_margin_pct": 0.86,
                    "opex_split": {"sales": 0.05, "marketing": 0.30,
                                   "rnd": 0.22, "ga": 0.10},
                    "cash": 4_000_000},
        seed=55,
    )))

    # ---- Stage sweep ------------------------------------------------------
    for stage, cash in [("early", 900_000), ("growth", 6_500_000),
                        ("established", 9_000_000), ("mature", 20_000_000),
                        ("turnaround", 1_200_000)]:
        cases.append(Case(f"stage_{stage}", _profile(
            f"Stage {stage.title()}",
            size={"headcount_total": 85,
                  "headcount_by_function": {"engineering": 30, "sales": 20,
                                            "customer_success": 14, "ga": 12,
                                            "marketing": 9},
                  "revenue_band": "10-50M", "stage": stage},
            financials={"revenue": 12_000_000, "gross_margin_pct": 0.72,
                        "opex_split": {"sales": 0.22, "marketing": 0.14,
                                       "rnd": 0.20, "ga": 0.12},
                        "cash": cash},
            seed=hash(stage) % 9999,
        )))

    # ---- Objective x audience sweep --------------------------------------
    objectives = ["growth", "margin_expansion", "retention", "cash_liquidity",
                  "fundraise", "cost_reduction", "exit_prep", "quality"]
    audiences = ["board", "exec", "investor", "bank", "department_head",
                 "operational_team"]
    for i, obj in enumerate(objectives):
        cases.append(Case(f"objective_{obj}", _profile(
            f"Objective {obj}",
            intent={"primary_objective": obj, "audience": audiences[i % len(audiences)]},
            seed=1000 + i,
        )))

    # ---- KPI literacy / data maturity ------------------------------------
    for literacy in ["low", "medium", "high"]:
        cases.append(Case(f"literacy_{literacy}", _profile(
            f"Literacy {literacy}",
            org_culture={"data_maturity": "spreadsheets", "kpi_experience": literacy},
            seed=2000 + len(literacy),
        )))

    cases.append(Case("no_marketing_data", _profile(
        "Blind Marketing",
        data_availability={"has": ["billing", "crm", "gl"],
                           "missing": ["survey", "support_desk",
                                       "marketing_automation", "hris"]},
        seed=3001,
    )))

    # ---- Short history ----------------------------------------------------
    cases.append(Case("short_history_13mo", _profile(
        "Young Data", history_months=13, seed=4001,
    )))

    return cases


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------

def check_case(case: Case) -> List[Issue]:
    issues: List[Issue] = []
    p = case.profile

    def fail(check: str, detail: str) -> None:
        issues.append(Issue(case.name, "fail", check, detail))

    def warn(check: str, detail: str) -> None:
        issues.append(Issue(case.name, "warn", check, detail))

    # --- Selection ---------------------------------------------------------
    kpi_set = select(p)
    if not kpi_set.kpis:
        fail("selection", "no KPIs selected")
        return issues

    for perspective in Perspective:
        n = len(kpi_set.by_perspective(perspective))
        if n and n < MIN_PER_PERSPECTIVE:
            warn("bsc_coverage",
                 f"{perspective.value} has only {n} KPI(s)")
    covered = sum(1 for x in Perspective if kpi_set.by_perspective(x))
    if covered < 4:
        warn("bsc_coverage", f"only {covered}/4 perspectives represented")

    if kpi_set.leading_share < MIN_LEADING_SHARE:
        warn("leading_share",
             f"{kpi_set.leading_share:.0%} leading (target {MIN_LEADING_SHARE:.0%})")

    for kid in case.expect.get("absent_kpis", []):
        if kpi_set.by_id(kid) is not None:
            fail("applicability", f"{kid} selected but should not apply")

    # --- Data --------------------------------------------------------------
    data = generate(p)
    tables = data.tables

    for name, df in tables.items():
        if df.empty:
            fail("data", f"table {name} is empty")
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                values = df[col].astype(float).to_numpy()
                if np.isinf(values).any():
                    fail("data", f"{name}.{col} contains infinity")

    fin = tables["monthly_financials"]
    if (fin["revenue"] < 0).any():
        fail("data", "negative revenue")
    if (fin["mrr"] < 0).any():
        fail("data", "negative MRR")

    cust = tables["customers"]
    active = int(cust["is_active"].sum())
    if active < 1:
        fail("data", "no active customers at period end")
    if p.market.customer_count > 0:
        drift = abs(active - p.market.customer_count) / p.market.customer_count
        # Same noise-aware gate the generator uses — a fixed 15% is unreachable
        # on a 24-customer book and would flag valid output.
        gate = calibration_tolerance(p.market.customer_count) * 2.0
        if drift > gate:
            fail("data", f"active customers {active} vs profile "
                         f"{p.market.customer_count} ({drift:.0%} drift, gate {gate:.0%})")

    # --- Metrics -----------------------------------------------------------
    results = compute(kpi_set, tables, p)
    facts = facts_table(results)

    not_computed = [r.kpi.id for r in results if not r.computed]
    # Some are legitimately not computable (a profitable company has no burn
    # multiple). Only flag when MOST of the set fails.
    if len(not_computed) > max(3, 0.35 * len(results)):
        warn("metrics", f"{len(not_computed)}/{len(results)} not computed: "
                        f"{', '.join(not_computed[:6])}")

    for r in results:
        if not r.computed or r.current is None:
            continue
        v = float(r.current)
        if math.isnan(v) or math.isinf(v):
            fail("metric_value", f"{r.kpi.id} is {v}")
            continue
        lo, hi = BOUNDS.get(r.kpi.id, (-1e15, 1e15))
        if not (lo <= v <= hi):
            fail("metric_bounds",
                 f"{r.kpi.id}={v:,.4f} outside plausible [{lo:,.2f}, {hi:,.2f}]")
        if r.series is not None:
            s = r.series.replace([np.inf, -np.inf], np.nan).dropna()
            if len(s) and (s.abs() > 1e13).any():
                fail("metric_series", f"{r.kpi.id} series has absurd magnitude")

    # Cross-metric consistency
    by_id = {r.kpi.id: r for r in results if r.computed and r.current is not None}
    if "nrr" in by_id and "grr" in by_id:
        if by_id["grr"].current > by_id["nrr"].current + 1e-6:
            fail("consistency", f"GRR ({by_id['grr'].current:.3f}) > "
                                f"NRR ({by_id['nrr'].current:.3f})")
    if "arr" in by_id:
        ending = float(fin["arr"].iloc[-1])
        if abs(by_id["arr"].current - ending) > 1:
            fail("consistency", "ARR metric does not match the financials table")
        drift = abs(ending - p.financials.revenue) / max(p.financials.revenue, 1)
        if drift > 0.02:
            fail("consistency", f"ending ARR {ending:,.0f} vs profile revenue "
                                f"{p.financials.revenue:,.0f}")
    if "customer_count" in by_id and "arpa" in by_id:
        implied = by_id["customer_count"].current * by_id["arpa"].current
        actual = float(fin["mrr"].iloc[-1])
        if actual > 0 and abs(implied - actual) / actual > 0.02:
            fail("consistency", "customers x ARPA does not reconcile to MRR")

    # --- Findings ----------------------------------------------------------
    findings = detect_all(results, tables, p)
    fact_numbers = set()
    for r in results:
        if r.current is not None:
            fact_numbers.add(round(float(r.current), 4))
    for f in findings:
        if not f.statement.strip():
            fail("findings", f"{f.id} has an empty statement")
        if "nan" in f.statement.lower() or "inf" in f.statement.lower().split():
            fail("findings", f"{f.id} statement leaks a non-finite value")
        for value in f.evidence.values():
            if value is None:
                continue
            try:
                fv = float(value)
            except (TypeError, ValueError):
                continue
            if math.isnan(fv) or math.isinf(fv):
                fail("findings", f"{f.id} evidence contains {fv}")

    return issues


def run(quick: bool = False) -> int:
    cases = build_cases(quick)
    print(f"Running {len(cases)} cases\n")
    all_issues: List[Issue] = []
    crashed: List[Tuple[str, str]] = []

    for case in cases:
        try:
            issues = check_case(case)
        except Exception as exc:                            # noqa: BLE001
            crashed.append((case.name, f"{type(exc).__name__}: {exc}"))
            print(f"  CRASH  {case.name}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=4)
            continue
        fails = [i for i in issues if i.severity == "fail"]
        warns = [i for i in issues if i.severity == "warn"]
        mark = "FAIL " if fails else ("warn " if warns else "ok   ")
        print(f"  {mark}  {case.name}"
              f"{f'  ({len(fails)} fail, {len(warns)} warn)' if issues else ''}")
        for i in issues:
            print(f"           [{i.severity}] {i.check}: {i.detail}")
        all_issues.extend(issues)

    fails = [i for i in all_issues if i.severity == "fail"]
    warns = [i for i in all_issues if i.severity == "warn"]
    print(f"\n{'=' * 70}")
    print(f"cases: {len(cases)}  crashed: {len(crashed)}  "
          f"fails: {len(fails)}  warns: {len(warns)}")
    return 1 if (fails or crashed) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.quick))
