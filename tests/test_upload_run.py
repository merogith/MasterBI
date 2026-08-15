"""A spreadsheet, and whether the product does anything with it.

Mode 3's engine half had three dead ends between "the user uploaded a P&L" and
"a number appears on a dashboard". None of them raised. Measured on a clean,
24-month, correctly-formatted P&L export before any of this was fixed: the run
**completed, wrote nine artifacts, and computed zero of eighteen KPIs.** Every
one reported "needs the monthly_financials table, which this run does not
have", of a file that was nothing but monthly financials.

1. **The table was keyed by the file's stem.** `finance_export_2025.csv` became
   a table called `finance_export_2025`, which no metric has ever heard of.
   `ingest/shapes.py` and `ingest/mapping.py` — five shapes and a three-signal
   confidence scorer, both good — were never called by `ingest/pipeline.py`.
2. **Every column arrived as text.** `readers.py` loads with `dtype=str` on
   purpose, because 1.234,56 read as Anglo is a plausible number wrong by three
   orders of magnitude. The profiler detects the convention and suggests the
   cast; nothing applied it, so `revenue - cogs` raised on two strings.
3. **The contract wants columns no export carries.** `gross_profit`,
   `total_opex` and `ebitda` are Tier 1 *identities* — the gate would reject any
   value but the derived one — and half the cross-sector pack reads them.

Same file after: **nine of nineteen**, and the ten that do not compute say so
because the data genuinely is not there.

The last test is the one that is easy to get wrong and expensive to get wrong:
the detected mapping has to survive a warm re-run that rebuilds `model` while
reusing `source`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.contract import run_gate  # noqa: E402
from kpi_maker.ingest.pipeline import (  # noqa: E402
    build_from_uploads,
    derive_pl_columns,
    plan_uploads,
)
from kpi_maker.pipeline.runner import execute  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402
from kpi_maker.spec.schema import CalculatedColumn, RunSpec  # noqa: E402

SAMPLE = ROOT / "samples" / "kestrel_retail.json"

# A P&L as an accounting package exports one: the accountant's own column
# names, no derived lines, and every number a string because that is what a CSV
# is. Deliberately *not* named for the table it becomes — a file called
# `monthly_financials.csv` would prove nothing.
PL_EXPORT = """\
Period,Total Revenue,Cost of Sales,Sales Expense,Marketing,R&D,Admin,Bank Balance
2024-01,412000,168000,52000,38000,61000,44000,1850000
2024-02,428000,172000,53000,39000,62000,44500,1810000
2024-03,441000,176000,55000,41000,63000,45000,1795000
2024-04,455000,181000,56000,42000,64000,45500,1780000
2024-05,470000,187000,58000,44000,65000,46000,1772000
2024-06,486000,193000,60000,45000,66000,46500,1768000
2024-07,502000,199000,62000,47000,67000,47000,1766000
2024-08,519000,206000,64000,48000,68000,47500,1770000
2024-09,537000,213000,66000,50000,69000,48000,1778000
2024-10,556000,220000,68000,52000,70000,48500,1790000
2024-11,575000,228000,71000,54000,71000,49000,1806000
2024-12,595000,236000,73000,56000,72000,49500,1826000
2025-01,608000,241000,75000,57000,73000,50000,1845000
2025-02,622000,246000,77000,58000,74000,50500,1866000
2025-03,637000,252000,79000,60000,75000,51000,1890000
2025-04,652000,258000,81000,61000,76000,51500,1917000
2025-05,668000,264000,83000,63000,77000,52000,1947000
2025-06,684000,271000,85000,64000,78000,52500,1980000
2025-07,701000,277000,87000,66000,79000,53000,2016000
2025-08,718000,284000,89000,68000,80000,53500,2055000
2025-09,736000,291000,92000,69000,81000,54000,2097000
2025-10,754000,298000,94000,71000,82000,54500,2142000
2025-11,773000,306000,96000,73000,83000,55000,2190000
2025-12,792000,313000,99000,75000,84000,55500,2241000
"""


@pytest.fixture(scope="module")
def export(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("uploads") / "finance_export_2025.csv"
    path.write_text(PL_EXPORT, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def retailer():
    """A sector on the cross-sector pack, which is what a P&L alone can serve.

    `logistics` rather than `ecommerce`: the e-commerce scorecard is mostly
    orders and traffic, so a P&L-only run there is narrow for a reason that has
    nothing to do with what is being tested here.
    """
    profile = load_profile(SAMPLE)
    profile.business_model.type = BusinessModel.logistics
    return profile


def _canonical(export: Path) -> pd.DataFrame:
    """The financials table as the `model` stage sees it — mapped, not derived.

    Renaming deliberately happens after `clean`, so `plan_uploads` returns the
    user's own column names and the mapping it proposes alongside them.
    """
    from kpi_maker.ingest.pipeline import apply_mapping, detected_mapping
    tables, plans, _detail = plan_uploads([export])
    return apply_mapping(tables, detected_mapping(plans))["monthly_financials"]


def _run(tmp_path: Path, export: Path, profile, **model) -> tuple:
    """Execute a real upload run and return (result, computed kpi ids)."""
    out = tmp_path / "run"
    out.mkdir(parents=True, exist_ok=True)
    uploads = out.parent / "_uploads"
    uploads.mkdir(exist_ok=True)
    (uploads / export.name).write_text(export.read_text(), encoding="utf-8")

    spec = RunSpec(profile=profile)
    spec.source.kind = type(spec.source.kind)("upload")
    spec.source.uploads = [export.name]
    for key, value in model.items():
        setattr(spec.model, key, value)

    result = execute(spec, out, artifacts=["dashboard"])
    computed = {m.kpi.id for m in result.values["metrics"] if m.computed}
    return result, computed


def test_a_pl_export_becomes_the_financials_table(export):
    """The file's contents decide the table, not its name."""
    tables, plans, _detail = plan_uploads([export])

    assert list(tables) == ["monthly_financials"], \
        f"the P&L did not become the financials table: {list(tables)}"
    plan = plans[0]
    assert plan.shape == "pnl_export"
    assert plan.confidence >= 0.75, "a P&L this clean should match confidently"
    assert plan.mapping["revenue"] == "Total Revenue"
    assert plan.mapping["cogs"] == "Cost of Sales"


def test_the_numbers_arrive_as_numbers(export):
    """`readers.py` loads everything as text on purpose; somebody has to finish.

    The suggestion to cast existed and was correct. It was shown to a user who
    had no way to know the entire run depended on their acting on it.
    """
    tables, plans, _detail = plan_uploads([export])
    frame = tables["monthly_financials"]

    numeric = [c for c in frame.columns
               if pd.api.types.is_numeric_dtype(frame[c])]
    assert len(numeric) >= 6, f"still text: {list(frame.dtypes.items())}"
    assert plans[0].read_fixes, "the type fixes were applied but not reported"


def test_an_explicit_file_name_beats_detection(tmp_path, export):
    """`monthly_financials.csv` has already answered the question."""
    named = tmp_path / "headcount.csv"
    named.write_text("month,function,fte,cost\n2025-01,Sales,4,42000\n",
                     encoding="utf-8")
    _tables, plans, _detail = plan_uploads([named])
    assert plans[0].table == "headcount"
    assert "named for the table" in plans[0].note


def test_an_assignment_beats_everything(export):
    """Detection is a proposal. A caller who has said otherwise has said so."""
    _tables, plans, _detail = plan_uploads(
        [export], assignments={export.name: "marketing"})
    assert plans[0].table == "marketing"


def test_two_files_never_silently_become_one(tmp_path, export):
    """Both P&Ls land, and the second says why it kept its own name."""
    second = tmp_path / "finance_export_2024.csv"
    second.write_text(PL_EXPORT, encoding="utf-8")

    tables, plans, _detail = plan_uploads([export, second])
    assert len(tables) == 2, "one upload overwrote the other"
    assert "already took that table" in plans[1].note


def test_the_derived_pl_columns_satisfy_the_gate(export, retailer):
    """The three derived columns are Tier 1 identities, so the gate is the test.

    Asserting `gross_profit == revenue - cogs` here would just restate the
    arithmetic in a second place. Running the contract gate over the result
    checks it against the definition the project actually enforces.
    """
    frame, added = derive_pl_columns(_canonical(export))
    assert set(added) == {"gross_profit", "total_opex", "ebitda", "gross_margin_pct"}

    result = run_gate({"monthly_financials": frame}, retailer, source="upload")
    failed = [c for c in result.checks if "FAIL" in c]
    assert not failed, failed
    assert any("gross_profit = revenue - cogs: pass" in c for c in result.checks), \
        "the identity did not run, so this test proved nothing"


def test_a_users_own_column_is_never_overwritten(export):
    """Their ledger is the record; ours would be a restatement of it."""
    frame = _canonical(export)
    frame["ebitda"] = 1.0

    out, added = derive_pl_columns(frame)
    assert "ebitda" not in added
    assert (out["ebitda"] == 1.0).all()


def test_capex_and_cash_flow_are_not_invented(export):
    """The generator models these; a real ledger has to supply them.

    Deriving `capex` as a share of revenue is a fair simulation and would be a
    fabricated number on someone's accounts. A KPI that needs it must go
    without, and say so.
    """
    frame, _added = derive_pl_columns(_canonical(export))
    for column in ("capex", "free_cash_flow", "net_burn"):
        assert column not in frame.columns, f"{column} was invented"


def test_the_upload_actually_produces_numbers(tmp_path, export, retailer):
    """The whole point, end to end: a spreadsheet in, a scorecard out.

    Zero before this work — a completed run, nine artifacts, and every KPI
    reporting a missing table.
    """
    result, computed = _run(tmp_path, export, retailer)

    assert len(computed) >= 8, \
        f"only {len(computed)} KPIs computed from a complete P&L: {sorted(computed)}"
    assert {"revenue_ttm", "gross_margin", "operating_margin"} <= computed

    facts = result.values["metrics"]
    revenue = next(m for m in facts if m.kpi.id == "revenue_ttm")
    assert revenue.current == pytest.approx(8_345_000, rel=1e-6), \
        "trailing revenue does not match the file"


def test_what_is_missing_is_reported_as_missing(tmp_path, export, retailer):
    """The KPIs that cannot compute must fail for the true reason."""
    _result, computed = _run(tmp_path, export, retailer)
    assert "workforce_size" not in computed, \
        "a headcount metric computed from a file with no headcount in it"


def test_the_mapping_survives_a_warm_partial_rerun(tmp_path, export, retailer):
    """`model` rebuilt, `source` reused — and the mapping still has to be there.

    This is why the plans travel on the stage's cached output rather than on
    `RunContext`. A context side channel is set only when its stage runs, so
    reusing `source` would hand `model` an empty mapping, the columns would stay
    raw, and the run would quietly degrade to the zero-KPI state this whole file
    exists to prevent. Nothing would raise — which is exactly how the original
    bug survived.
    """
    result, cold = _run(tmp_path, export, retailer)
    assert "model" in result.ran

    warm, hot = _run(tmp_path, export, retailer, calculated_columns=[
        CalculatedColumn(table="monthly_financials", name="headroom",
                         expression="revenue - cogs")])

    assert "source" in warm.skipped, "the reuse this test is about did not happen"
    assert "model" in warm.ran, "model did not rebuild, so nothing was tested"
    assert hot == cold, \
        f"the warm re-run lost {sorted(cold - hot)} and gained {sorted(hot - cold)}"


def test_generated_runs_carry_no_upload_plans(retailer):
    """The new field must stay empty for the path that has no files."""
    from kpi_maker.datagen import GENERATORS
    data = GENERATORS["ecommerce"](retailer)
    assert data.upload_plans == []


def test_build_from_uploads_explains_each_file(export, retailer):
    """`checks` reaches the methodology appendix, so it has to say what happened."""
    spec = RunSpec(profile=retailer)
    data, origins, plans = build_from_uploads([export], retailer, spec)

    assert origins["monthly_financials"] == "measured"
    assert any(export.name in line and "monthly_financials" in line
               for line in data.checks), data.checks
    assert data.upload_plans == plans


# --------------------------------------------------------------------------
# The funnel's server side. Called as functions rather than over HTTP, the way
# `tests/test_progress.py` drives the API, so the suite still needs no client.
# --------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch, export):
    """The server module with its upload directory pointed somewhere throwaway."""
    from kpi_maker.api import server

    uploads = tmp_path / "_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / export.name).write_text(export.read_text(), encoding="utf-8")
    monkeypatch.setattr(server, "UPLOADS_DIR", uploads)
    return server


def test_the_gate_answers_before_a_run_exists(api, export):
    """The quality report used to need a `run_id`, so it could only speak after
    the fact — a gate you walk through and are then told about."""
    report = api.ingest_quality({"uploads": [export.name],
                                 "answers": {"business_model": "logistics"}})

    assert report["can_run"] is True
    assert report["tables_present"] == ["monthly_financials"]
    assert report["kpis_available"] > 0
    assert report["plans"][0]["table"] == "monthly_financials"


def test_the_preview_does_not_report_problems_the_run_then_fixes(api, export):
    """A gate that disagrees with the run is worse than no gate.

    Found on the live server: the preview read the tables through
    `plan_uploads` but not through `derive_pl_columns`, so it reported
    `gross_profit`, `gross_margin_pct` and `total_opex` missing — three
    problems the run itself resolved a second later.
    """
    report = api.ingest_quality({"uploads": [export.name], "answers": {}})
    for column in ("gross_profit", "gross_margin_pct", "total_opex"):
        assert not any(column in problem for problem in report["schema_problems"]), \
            f"the gate warned about {column}, which the run derives"


def test_derive_runs_before_the_survey_it_shortens(api, export):
    """The orphan route, and why it was one.

    It took a `run_id` and read that run's tables, so it could only answer
    after a run existed — which is after the survey it exists to shorten. That
    is a circle, and it is why nothing ever called it.
    """
    derived = api.ingest_derive({"uploads": [export.name]})

    assert derived["values"]["financials.revenue"] == pytest.approx(8_345_000)
    assert derived["values"]["history_months"] == 24
    assert derived["provenance"]["financials.revenue"].startswith("ingested:")

    total = len(api.survey_json()["questions"])
    assert 0 < len(derived["remaining_questions"]) < total, \
        "the shortened survey is not shorter than the full one"
    assert "revenue_band" not in derived["remaining_questions"], \
        "the file already answered revenue and was asked for it anyway"


def test_a_measured_revenue_keeps_the_customer_book_consistent():
    """Measured figures go into the build, not over the top of it.

    `build_profile` *solves* customer count from revenue so the profile's
    cross-block validator passes by construction. Patching revenue in
    afterwards breaks the equation it had just satisfied, and a clean 12-month
    export was rejected: "312 customers x 24,000 blended ACV = 7,488,000, but
    financials.revenue is 1,270,200 (490% apart)".
    """
    from kpi_maker.survey import build_profile

    profile = build_profile({"business_model": "logistics"},
                            measured={"financials.revenue": 1_270_200.0,
                                      "history_months": 12})

    assert profile.financials.revenue == pytest.approx(1_270_200.0)
    assert profile.history_months == 12
    book = sum(s.share * s.avg_acv for s in profile.market.segments) \
        * profile.market.customer_count
    assert book == pytest.approx(1_270_200.0, rel=0.05), \
        "revenue and the customer book disagree, which the validator forbids"


def test_a_measured_customer_count_moves_the_acv_not_the_revenue():
    """When both sides are measured, the assumption nobody made has to yield."""
    from kpi_maker.survey import build_profile

    profile = build_profile({}, measured={"financials.revenue": 4_000_000.0,
                                          "market.customer_count": 200})

    assert profile.market.customer_count == 200
    assert profile.financials.revenue == pytest.approx(4_000_000.0)
    blended = sum(s.share * s.avg_acv for s in profile.market.segments)
    assert blended == pytest.approx(20_000, rel=0.05), \
        "the segment ACVs did not move to meet two measured numbers"


def test_an_unknown_measured_path_is_ignored_rather_than_fatal():
    """`derive` may learn to report something the profile has no field for."""
    from kpi_maker.survey import build_profile

    profile = build_profile({}, measured={"nothing.like.this": 1,
                                          "history_months": 18})
    assert profile.history_months == 18
