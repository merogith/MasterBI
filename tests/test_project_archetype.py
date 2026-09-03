"""The third archetype: firms that sell their people's time.

Four sectors — professional services, agencies, architecture and engineering,
and construction — reached a generator by approximating onto `ecommerce`, and
the approximation was louder than the note admitted. Measured before writing
anything: a consultancy's dashboard carried **average order value, category
returns and buyer mix**, because those are the exhibits the transactional
archetype's tables draw. The taxonomy's own reason said "project fees behave
like orders", which is true of the revenue line and of nothing else a services
firm manages itself by.

What this archetype adds is three things the other two cannot express — a
stock of sold work, a capacity ceiling in hours, and realisation — and the
tests below are organised around them plus the four defects that reading a real
run's output turned up.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.contract.gate import run_gate  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.spec.schema import GeneratorParams  # noqa: E402

SAMPLE = ROOT / "samples" / "halberd_consulting.json"

#: The four sectors the project archetype exists for. `construction` is
#: deliberately still an approximation — staged completion is modelled and
#: retentions, variations and a materials-heavy cost base are not — so it is
#: listed here as reaching the archetype without claiming to be exact.
PROJECT_SECTORS = ("services", "agency", "engineering", "construction")


@pytest.fixture(scope="module")
def data():
    return GENERATORS["project"](load_profile(SAMPLE))


@pytest.fixture(scope="module")
def profile():
    return load_profile(SAMPLE)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_the_project_shaped_sectors_reach_the_project_generator() -> None:
    for sector in PROJECT_SECTORS:
        assert sectors.resolve_archetype(sector).value == "project"
    assert "project" in GENERATORS


def test_construction_still_says_what_it_does_not_simulate() -> None:
    """An archetype that fits three sectors exactly does not fit the fourth.

    Retentions and variations are real money on a building contract and are not
    in this model, so `construction` keeps a `why` and keeps warning. Dropping
    it to claim a fourth exact sector would be the kind of quiet overclaim 0.1
    exists to prevent.
    """
    exact = [s for s in PROJECT_SECTORS if sectors.resolve_archetype(s).exact]
    assert sorted(exact) == ["agency", "engineering", "services"]

    note = sectors.resolve_archetype("construction").note
    assert note and "retentions" in note and "variations" in note


def test_every_project_sector_generates_and_reconciles() -> None:
    from kpi_maker.survey import build_profile

    for sector in PROJECT_SECTORS:
        data = GENERATORS["project"](build_profile({"business_model": sector}))
        assert set(data.tables) >= {"timesheets", "backlog", "projects",
                                    "customers", "monthly_financials",
                                    "segment_financials", "headcount",
                                    "marketing"}


def test_the_vacuity_guard_is_satisfied_by_real_checks(data, profile) -> None:
    """A generated archetype nothing checked is worse than no gate at all.

    `contract/gate.py::_guard_against_vacuity` refuses exactly that, and the
    reason it can is that the universal P&L identities do not count toward
    coverage. So this asserts the *project* identities ran, not merely that the
    gate returned.
    """
    result = run_gate(dict(data.tables), profile, source="synthetic",
                      archetype="project")
    ran = {line.split(":")[0] for line in result.checks}
    for name in ("fee revenue = billable hours x standard rate x realisation",
                 "billable hours never exceed available hours",
                 "backlog rolls forward",
                 "recognised revenue never exceeds contract value"):
        assert name in ran, f"{name!r} did not run: {sorted(ran)}"


# --------------------------------------------------------------------------
# The three things this archetype can say and the others cannot
# --------------------------------------------------------------------------

def test_fee_revenue_is_hours_times_rate_times_realisation(data) -> None:
    """The plan's `hours x rate = revenue`, with the term that makes it true.

    A firm earns the fee it agreed over however many hours the job took;
    `hours x rate` is what it hoped to earn. Realisation is derived from the
    other three rather than sampled, so this is arithmetic rather than a
    modelling assumption — and a gap means one of the four was rewritten
    without the others.
    """
    ts = data.tables["timesheets"]
    expected = ts["billable_hours"] * ts["standard_rate"] * ts["realisation"]
    assert np.allclose(ts["fee_revenue"], expected, rtol=1e-6, atol=1e-6)


def test_utilisation_never_exceeds_one(data) -> None:
    ts = data.tables["timesheets"]
    assert (ts["billable_hours"] <= ts["available_hours"] + 1e-6).all()
    blended = ts["billable_hours"].sum() / ts["available_hours"].sum()
    assert 0.40 <= blended <= 0.95


def test_utilisation_carries_over_rather_than_being_redrawn(data) -> None:
    """It was an independent draw per line per month, and it looked like one.

    On screen that produced a 74-86-74% sawtooth — a firm rebuilding its bench
    every four weeks. The walk is an AR(1) toward each line's own target, so the
    month-on-month step is small while the level and the planted bench episode
    both survive.
    """
    ts = data.tables["timesheets"]
    monthly = ts.groupby("month").apply(
        lambda g: g["billable_hours"].sum() / g["available_hours"].sum(),
        include_groups=False)
    steps = monthly.diff().abs().dropna()
    assert steps.mean() < 0.03, f"month-on-month utilisation moves {steps.mean():.3f}"
    # ...and it still moves: a constant would pass the line above.
    assert monthly.max() - monthly.min() > 0.03


def test_the_backlog_rolls_forward_and_never_goes_negative(data) -> None:
    b = data.tables["backlog"]
    assert np.allclose(
        b["closing_backlog"],
        b["opening_backlog"] + b["bookings"] - b["revenue_recognised"],
        rtol=1e-6, atol=1e-6)
    assert (b["closing_backlog"] >= -1e-6).all()
    # A backlog that is always zero would satisfy both lines above.
    assert b["closing_backlog"].iloc[-1] > 0


def test_a_completed_engagement_recognises_its_whole_contract_value(data) -> None:
    """Percentage of completion has to reach 100% when the work stops.

    Recognition is `contract_value x hours_this_month / total_hours`, and the
    final month takes the residual rather than its share, so the sum is exact
    rather than nearly exact — a rounding gap here is a Tier 1 failure, not a
    rounding gap.
    """
    pr = data.tables["projects"]
    done = pr[~pr["is_active"].astype(bool)]
    live = pr[pr["is_active"].astype(bool)]
    assert not done.empty and not live.empty

    assert np.allclose(done["recognised_revenue"], done["contract_value"],
                       rtol=1e-9, atol=1e-6)
    # An engagement still running must NOT have recognised everything, or
    # "complete" means nothing.
    assert (live["recognised_revenue"] < live["contract_value"] - 1e-6).all()


def test_both_operational_tables_tie_to_the_profit_and_loss(data) -> None:
    fin = data.tables["monthly_financials"].set_index("month")["revenue"]
    fees = data.tables["timesheets"].groupby("month")["fee_revenue"].sum()
    delivered = data.tables["backlog"].set_index("month")["revenue_recognised"]
    for series in (fees, delivered):
        joined = series.reindex(fin.index).fillna(0.0)
        assert np.allclose(joined.to_numpy(), fin.to_numpy(),
                           rtol=1e-6, atol=1e-6)


def test_realisation_falls_where_the_engagements_overran(data) -> None:
    """The one story only this archetype can tell.

    Scope creep is planted as *hours*, not as margin: the fee is fixed and the
    job takes 34% longer, so realisation falls out of the arithmetic. Nothing
    plants a realisation number anywhere.
    """
    # Asserted at the mechanism first, and that is not fussiness. The earlier
    # version of this test only compared realisation at the start of the window
    # against the end, and it survived deleting the scope-creep anomaly
    # entirely: the *booking slowdown* leaves the last year dominated by older,
    # longer-running engagements and moves realisation -0.059 on its own, past
    # the 0.05 the test asked for. A test that any of three anomalies can
    # satisfy is not a test of the one it names.
    creep = next(a for a in data.anomalies if a.kind == "scope_creep")
    months = sorted(data.tables["timesheets"]["month"].unique())
    window = (months[max(creep.start_month, 0)],
              months[min(creep.end_month, len(months) - 1)])

    pr = data.tables["projects"]
    line = pr[(pr["service_line"] == creep.segment)
              & ~pr["is_active"].astype(bool)]
    inside = line[line["won_month"].between(*window)]
    outside = line[~line["won_month"].between(*window)]
    assert not inside.empty and not outside.empty

    def overrun(frame):
        return float((frame["actual_hours"] / frame["budget_hours"]).mean())

    assert overrun(inside) > overrun(outside) * 1.20, (
        f"inside {overrun(inside):.2f} vs outside {overrun(outside):.2f}")

    # And it reaches realisation, which is the number a partner reads. The
    # threshold is above what the other two anomalies produce between them.
    ts = data.tables["timesheets"]
    billed = ts[ts["service_line"] == creep.segment]
    first = billed[billed["month"] <= billed["month"].min() + 11]
    last = billed[billed["month"] >= billed["month"].max() - 11]

    def realised(frame):
        return (frame["fee_revenue"].sum()
                / (frame["billable_hours"] * frame["standard_rate"]).sum())

    assert realised(last) < realised(first) - 0.10

    quiet = GENERATORS["project"](
        load_profile(SAMPLE), GeneratorParams(inject_anomalies=False))
    qts = quiet.tables["timesheets"]
    qcreep = qts[qts["service_line"] == creep.segment]
    qfirst = qcreep[qcreep["month"] <= qcreep["month"].min() + 11]
    qlast = qcreep[qcreep["month"] >= qcreep["month"].max() - 11]
    assert abs(realised(qlast) - realised(qfirst)) < 0.05


def test_hours_follow_the_roster_and_money_follows_the_revenue(data, profile) -> None:
    """Two scalings, applied for different reasons, and both have to land.

    A single factor cannot satisfy both: the profile states a headcount *and* a
    revenue, and the ratio between them is a charge-out rate nobody was asked
    for. So hours are scaled to the roster, money to the revenue, and the rate
    is solved last.
    """
    fin = data.tables["monthly_financials"]
    assert abs(fin["revenue"].iloc[-12:].sum() / profile.financials.revenue - 1) < 0.02

    roster = data.tables["headcount"].groupby("month")["fte"].sum()
    assert abs(roster.iloc[-1] / profile.size.headcount_total - 1) < 0.10

    # And the rate that falls out is a charge-out rate rather than a number.
    ts = data.tables["timesheets"]
    rates = ts.groupby("role")["standard_rate"].first()
    assert (rates > 20).all() and (rates < 1500).all(), rates.to_dict()
    assert rates["partner"] > rates["analyst"] * 2


# --------------------------------------------------------------------------
# Four defects the archetype's first real run exposed
# --------------------------------------------------------------------------

def test_the_gross_margin_band_is_the_archetypes_own() -> None:
    """It asserted [0.30, 0.95] for every business, which is a software band.

    A distributor at 18% and a contractor at 12% would have been rejected as
    corrupt data. Invisible while there were two archetypes and the second
    shipped its own sample profile rather than going through the survey.
    """
    from kpi_maker.contract.identities import CHECKS

    band = next(c for c in CHECKS
                if c.name == "gross margin is plausible for the archetype")
    fin = pd.DataFrame({
        "month": pd.period_range("2024-01", periods=12, freq="M"),
        "gross_margin_pct": [0.22] * 12,
    })

    class _P:
        class business_model:
            class type:
                value = "services"

    assert band.fn({"monthly_financials": fin}, _P).passed

    class _Saas(_P):
        class business_model:
            class type:
                value = "saas"

    assert not band.fn({"monthly_financials": fin}, _Saas).passed


def test_the_financial_priors_branch_on_the_archetype() -> None:
    """72% gross margin and 0.7% EBITDA for a 110-person consultancy.

    Nothing was wrong with the generator — it honours the profile, and the
    profile was a venture-funded software company wearing a services label.
    """
    from kpi_maker.survey import build_profile

    services = build_profile({"business_model": "services"})
    saas = build_profile({"business_model": "saas"})

    assert services.financials.gross_margin_pct < 0.50
    assert sum(services.financials.opex_split.values()) < 0.45
    # The subscription path is deliberately untouched: it falls through to the
    # stage tables, so nothing that ran before this moves.
    assert saas.financials.gross_margin_pct == pytest.approx(0.72)
    assert sum(saas.financials.opex_split.values()) == pytest.approx(0.68)

    # A plausible operating margin is the point of both, so assert the result
    # rather than the inputs.
    implied = services.financials.gross_margin_pct - sum(
        services.financials.opex_split.values())
    assert 0.0 < implied < 0.20

    assert "archetype=project" in services.provenance[
        "financials.gross_margin_pct"]


def test_a_target_band_metric_is_never_reported_as_top_quartile() -> None:
    """R&D intensity of 2.0% shipped as "top quartile ... a defensible strength".

    `vs_benchmark` had two branches, higher-is-better and everything else, so a
    `target_band` KPI fell into the lower-is-better one and the product
    congratulated a firm for spending almost nothing on research — on the one
    metric whose own definition says both extremes are bad.
    """
    from kpi_maker.kpi.schema import Direction
    from kpi_maker.kpi.selection import load_library

    banded = [k for k in load_library(include_user=False)
              if k.direction == Direction.target_band
              and k.benchmark is not None and k.benchmark.p25 is not None]
    assert banded, "no target_band KPI carries a band to test"

    for kpi in banded:
        lo = min(kpi.benchmark.p25, kpi.benchmark.p75)
        hi = max(kpi.benchmark.p25, kpi.benchmark.p75)
        assert kpi.vs_benchmark((lo + hi) / 2) == "in_band"
        assert kpi.vs_benchmark(lo * 0.1) == "outside_band"
        assert kpi.vs_benchmark(hi * 10) == "outside_band"


def test_a_target_band_breach_names_the_range_and_the_right_side() -> None:
    """It quoted alert bands the metric does not have and does not use.

    Shipped as *"stands at 2.0%, above the green threshold of —, but not yet at
    the red threshold of —"*: no numbers, a comparison that was never made, and
    the direction backwards, since the value was below the band.
    """
    from kpi_maker.insight.detectors import _status_breaches
    from kpi_maker.kpi.schema import Direction
    from kpi_maker.kpi.selection import load_library
    from kpi_maker.metrics.engine import MetricResult

    kpi = next(k for k in load_library(include_user=False)
               if k.direction == Direction.target_band
               and k.benchmark is not None and k.benchmark.p25 is not None)
    lo = min(kpi.benchmark.p25, kpi.benchmark.p75)

    result = MetricResult(
        kpi=kpi, computed=True, current=lo * 0.4, prior_year=lo * 0.4,
        prior_month=None, series=None, target=None,
        status=kpi.status(lo * 0.4), benchmark_position=None,
        reason="", tables_used=[], basis="measured")
    findings = _status_breaches([result])
    assert findings, "a value well outside the band should breach"
    statement = findings[0].statement
    assert "below" in statement
    assert "green threshold" not in statement and "—" not in statement


def test_employee_attrition_is_a_rolling_year_not_one_month(data) -> None:
    """It read 0.0%, top quartile, because nobody resigned in December.

    The record sheet's own `pitfalls` said annualising a single month is noisy
    in a small team. A caveat on a record sheet does not reach the reader of a
    RAG chip, so the definition had to change instead.
    """
    from kpi_maker.kpi.selection import load_library

    kpi = next(k for k in load_library(["general"], include_user=False)
               if k.id == "attrition_rate")
    expression = kpi.compute.expression or ""
    assert "TTM(" in expression and "ROLLING(" in expression

    hc = data.tables["headcount"]
    quiet = hc.groupby("month")["leavers"].sum()
    assert (quiet == 0).any(), "no zero-leaver month in this dataset to test"

    from kpi_maker.kpi.schema import KPISet
    from kpi_maker.metrics.engine import compute

    tables = dict(data.tables)
    result = next(r for r in compute(KPISet(north_star=kpi.id, kpis=[kpi]),
                                     tables, load_profile(SAMPLE))
                  if r.kpi.id == "attrition_rate")
    assert result.computed and result.current > 0.01


# --------------------------------------------------------------------------
# Exhibits
# --------------------------------------------------------------------------

def test_the_project_exhibits_build_and_the_others_stand_down(data) -> None:
    """An archetype nobody can look at is an archetype nobody will use.

    Moving four sectors off `ecommerce` removed the four transactional exhibits
    they had been borrowing, which would have left a consultancy's dashboard
    with one chart.
    """
    from kpi_maker.viz.charts import CHARTS

    tables = dict(data.tables)
    built = []
    for eid in ("utilisation_realisation", "backlog_cover", "service_line_margin"):
        spec = CHARTS[eid].fn(tables)
        assert spec is not None, f"{eid} drew nothing on project tables"
        built.append(spec)
    assert {s.tab for s in built} == {"overview", "growth", "people"}

    # And they stand down where their tables do not exist, the same way the
    # transactional ones do on a subscription run.
    saas = GENERATORS["saas"](load_profile(ROOT / "samples" / "northwind_saas.json"))
    for eid in ("utilisation_realisation", "backlog_cover", "service_line_margin"):
        assert CHARTS[eid].fn(dict(saas.tables)) is None


def test_horizontal_exhibits_leave_room_for_their_own_labels() -> None:
    """`automargin` was set in the PNG exporter and nowhere else.

    Its comment in `viz/export.py` says the benchmark exhibit "lost the start of
    every KPI name" — correct, and fixed for one of the two consumers. The
    dashboard renders `spec.figure` directly, so on screen that chart showed one
    character per bar: %, R, n, y, %, y. Owned by `_base_layout` now, which
    every builder calls, so both consumers inherit it.
    """
    from kpi_maker.viz.charts import CHARTS

    saas = GENERATORS["saas"](load_profile(ROOT / "samples" / "northwind_saas.json"))
    from kpi_maker.kpi.selection import select
    from kpi_maker.metrics.engine import compute

    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    results = compute(select(profile), dict(saas.tables), profile)
    spec = CHARTS["benchmark_position"].fn(results)
    assert spec is not None
    assert spec.figure.layout.yaxis.automargin is True
    assert spec.figure.layout.xaxis.automargin is True


def test_the_benchmark_chart_signs_a_target_band_by_distance_from_the_band(
        data, profile) -> None:
    """"Positive is better on every metric" was false for target_band KPIs.

    The chart negated the gap for `lower_is_better` and left everything else
    reading as higher-is-better, so being 90% *above* the R&D median drew as the
    best bar on the exhibit. Measured from the band instead: zero inside it,
    negative by how far outside, whichever side.
    """
    from kpi_maker.kpi.schema import Direction
    from kpi_maker.kpi.selection import select
    from kpi_maker.metrics.engine import compute
    from kpi_maker.viz.charts import CHARTS

    results = compute(select(profile), dict(data.tables), profile)
    banded = [r for r in results
              if r.kpi.direction == Direction.target_band and r.computed
              and r.kpi.benchmark is not None and r.kpi.benchmark.p25 is not None]
    assert banded, "the consulting run should compute a target_band KPI"

    # Three placements of the same KPI, because only the ABOVE case separates
    # the fix from the bug. The first version of this test asserted only that
    # the run's own banded metrics drew non-positive — and every one of them
    # happened to sit *below* its band, where the old median-distance reading
    # also comes out negative. It passed with the fix deleted.
    def bar_for(result, value):
        moved = result.__class__(**{**result.__dict__, "current": value})
        spec = CHARTS["benchmark_position"].fn([moved])
        assert spec is not None
        return float(spec.figure.data[0].x[0])

    for r in banded:
        lo = min(r.kpi.benchmark.p25, r.kpi.benchmark.p75)
        hi = max(r.kpi.benchmark.p25, r.kpi.benchmark.p75)
        assert bar_for(r, (lo + hi) / 2) == pytest.approx(0.0, abs=1e-9)
        assert bar_for(r, lo * 0.2) < -0.1
        assert bar_for(r, hi * 5) < -0.1, (
            f"{r.kpi.id} well above its band drew as better than the cohort")
