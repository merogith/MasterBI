"""The reported customer book must agree across KPIs and exhibits."""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kpi_maker.cli import load_profile
from kpi_maker.datagen import GENERATORS
from kpi_maker.insight.detectors import _status_breaches, customer_book
from kpi_maker.kpi.selection import select
from kpi_maker.metrics.engine import (
    MetricContext,
    _customer_count,
    compute,
    customer_mrr_matrix,
)
from kpi_maker.render.sections import finding_for_exhibit
from kpi_maker.spec.schema import RunSpec
from kpi_maker.viz.charts import cohort_heatmap

ROOT = Path(__file__).resolve().parents[1]


def test_opening_balances_and_churn_dust_do_not_change_customer_counts():
    months = pd.period_range("2026-01", periods=3, freq="M")
    tables = {
        "monthly_financials": pd.DataFrame({"month": months}),
        "customers": pd.DataFrame({
            "customer_id": [1, 2, 3, 4],
            "final_acv": [1560.0, 0.0, 120.0, 60.0],
            "is_active": [True, False, True, True],
        }),
        "mrr_movements": pd.DataFrame([
            {"month": months[i], "customer_id": cid, "delta_mrr": amount}
            for i, cid, amount in [(0, 1, 20), (1, 1, 50), (2, 1, -20),
                                   (0, 2, 0.1), (1, 2, 0.2), (2, 2, -0.3),
                                   (1, 4, 5)]
        ]),
    }
    matrix = customer_mrr_matrix(tables)
    np.testing.assert_allclose(matrix.sum(axis=1), [110.1, 165.3, 145.0])
    assert (matrix > 0).sum(axis=1).tolist() == [3, 4, 3]
    assert matrix.loc[months[-1], 2] == 0.0


@pytest.fixture(scope="module", params=["northwind_saas", "meridian_early", "atlas_enterprise"])
def subscription_sample(request):
    profile = load_profile(ROOT / "samples" / f"{request.param}.json")
    tables = GENERATORS["saas"](profile).tables
    return profile, tables, compute(select(profile), tables, profile)


def test_sample_customer_kpis_and_concentration_use_the_same_closing_book(subscription_sample):
    profile, tables, _ = subscription_sample
    matrix = customer_mrr_matrix(tables)
    np.testing.assert_allclose(matrix.sum(axis=1), tables["monthly_financials"]["mrr"], atol=1e-7)
    current = _customer_count(MetricContext(profile, tables)).iloc[-1]
    assert current == int(tables["customers"]["is_active"].sum())
    assert current == len(customer_book(tables))


def test_net_retention_cohort_chart_cannot_take_a_gross_retention_headline(subscription_sample):
    _, tables, results = subscription_sample
    chart = cohort_heatmap(tables)
    assert chart is not None
    grr = next(r for r in results if r.kpi.id == "grr")
    finding = _status_breaches([replace(grr, status="red")])[0]
    assert finding_for_exhibit(chart, [finding]) is None
    assert chart.about == ()
    assert chart.title == "Net revenue retention by acquisition cohort"


def test_cohort_retention_does_not_count_later_signups_as_expansion():
    months = pd.period_range("2026-01", periods=4, freq="M")
    tables = {
        "monthly_financials": pd.DataFrame({"month": months}),
        "customers": pd.DataFrame({
            "customer_id": [1, 2, 3], "acquired_month": months[:3],
            "final_acv": [120.0, 240.0, 360.0], "is_active": [True] * 3,
        }),
        "mrr_movements": pd.DataFrame({
            "month": months[:3], "customer_id": [1, 2, 3],
            "delta_mrr": [10.0, 20.0, 30.0],
        }),
    }
    chart = cohort_heatmap(tables)
    assert chart is not None
    assert list(chart.figure.data[0].z[0][:2]) == [100.0, 100.0]


@pytest.mark.parametrize("status,band", [("red", "red"), ("amber", "green")])
def test_near_threshold_prose_explains_equal_rounded_values(subscription_sample, status, band):
    _, _, results = subscription_sample
    nrr = next(r for r in results if r.kpi.id == "nrr")
    threshold = getattr(nrr.kpi.alert_bands, band)
    finding = _status_breaches([replace(nrr, current=threshold - 1e-6, status=status)])[0]
    assert f"just below the {band} threshold" in finding.statement


def test_recruiter_demo_pins_its_inputs():
    spec = RunSpec.model_validate_json((ROOT / "samples/specs/northwind_demo.json").read_text())
    assert spec.source.generator.history_end == "2026-10"
    assert spec.profile.seed == 20250806
    assert spec.ai.enabled is False
