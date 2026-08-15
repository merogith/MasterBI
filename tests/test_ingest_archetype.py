"""Uploading a spreadsheet as a retailer, and being answered as one.

Mode 3 has an engine half and a UI half. This file is the engine half: three
places where ingestion had a SaaS assumption baked in so deep that a
non-subscription business got a wrong answer with no error anywhere.

Every one of them is the same shape — a function that already knew how to be
archetype-aware, called by something that never told it which archetype — and
every one of them is silent, which is why they survived eight sectors shipping:

1. **Gap filling ran `datagen.saas.generate` whatever the sector.** A retailer
   who ticked "fill the gaps" got synthetic *subscription* tables — MRR
   movements for a shop — labelled `MODELLED`, which makes an invented number
   look like a deliberate one rather than a wrong one.
2. **`validate_schemas` was called with no archetype**, so an upload was judged
   against the union. Its own docstring names the consequence: "a retailer held
   to the union would be asked for `mrr`". Measured before the fix — four
   fabricated problems on a clean e-commerce dataset, including `final_acv`.
3. **The quality report's table map was hand-written SaaS ids**, so the "supply
   this and unlock N KPIs" number — the one thing on that screen that motivates
   anyone to go and find another file — read **zero** for every table a
   non-SaaS company could supply, and the missing-tables list was a to-do list
   of subscription tables no retailer will ever have.

The fourth test is the one that keeps the fix: the map is now derived from the
metrics engine by `tools/gen_table_kpis.py`, and this asserts the committed copy
still matches what the engine does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.contract.schemas import validate_schemas  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.ingest.pipeline import MODELLED, fill_missing_tables  # noqa: E402
from kpi_maker.ingest.quality import build_report  # noqa: E402
from kpi_maker.ingest.table_kpis import TABLE_KPIS  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402

RETAIL_SAMPLE = ROOT / "samples" / "kestrel_retail.json"
SAAS_SAMPLE = ROOT / "samples" / "northwind_saas.json"


@pytest.fixture(scope="module")
def retailer():
    """A retail profile — an *approximated* sector, simulated by `ecommerce`.

    Deliberately `retail` rather than `ecommerce`: it exercises the resolution
    step as well as the lookup, and it is the sector the report was worst at.
    """
    profile = load_profile(RETAIL_SAMPLE)
    profile.business_model.type = BusinessModel.retail
    return profile


@pytest.fixture(scope="module")
def retail_tables(retailer):
    return dict(GENERATORS["ecommerce"](retailer).tables)


def test_gap_filling_follows_the_profiles_own_archetype(retailer):
    """Ask a retailer's run to fill both an e-commerce table and a SaaS one.

    With the bug, exactly the wrong one arrives: `datagen.saas` has no `orders`
    so the table the business actually needs is skipped, and it does have
    `mrr_movements`, so a shop is handed a subscription movements table.
    """
    filled, origins = fill_missing_tables(
        {}, retailer, fill=["orders", "mrr_movements"])

    assert "orders" in filled and not filled["orders"].empty, \
        "the transactional archetype's own table was not filled"
    assert origins["orders"] == MODELLED

    assert "mrr_movements" not in filled, \
        "a retailer was given synthetic subscription movements"


def test_a_supplied_table_is_never_overwritten_by_the_filler(retailer, retail_tables):
    """The opt-in stays opt-in per table — `fill` names gaps, not replacements."""
    mine = retail_tables["orders"].head(3)
    filled, origins = fill_missing_tables(
        {"orders": mine}, retailer, fill=["orders"])

    assert len(filled["orders"]) == 3
    assert origins["orders"] != MODELLED


def test_a_retailer_is_not_asked_for_columns_only_saas_has(retailer, retail_tables):
    """The report must judge the upload against the schemas for *this* business.

    The second assertion is what proves the first is not vacuous: the union
    genuinely does invent these problems, so scoping is what removes them.
    """
    tables = {name: retail_tables[name] for name in ("monthly_financials", "customers")}

    report = build_report(tables, retailer)
    invented = [p for p in report.schema_problems
                if any(c in p for c in ("final_acv", "initial_acv", "mrr", "arr"))]
    assert not invented, f"a retailer was told their data was wrong: {invented}"

    _, union_problems = validate_schemas(tables)
    assert union_problems, \
        "the union no longer objects, so this test would pass without the fix"


def test_the_missing_list_is_this_archetypes_tables(retailer, retail_tables):
    """"What else could you send us" has to be answerable by the business asked.

    A retailer's missing list used to be `mrr_movements`, `pipeline`,
    `product_usage`, `sales_capacity` — four files a shop will never have — and
    to omit every table it would actually have.
    """
    report = build_report(
        {"monthly_financials": retail_tables["monthly_financials"]}, retailer)
    missing = {entry["table"] for entry in report.tables_missing}

    assert {"orders", "traffic", "inventory", "buyers"} <= missing
    assert not missing & {"mrr_movements", "pipeline", "sales_capacity"}, \
        "a retailer was asked to supply subscription tables"


def test_a_non_saas_upload_is_told_what_a_table_would_unlock(retailer, retail_tables):
    """The number that motivates the whole screen, on a profile that is not SaaS.

    Every one of these read 0 before the fix. Where the map has nothing to say
    the answer must be `None` — "no opinion" — and never 0, which reads as
    "don't bother" and was the wrong answer rather than a missing one.
    """
    report = build_report(
        {"monthly_financials": retail_tables["monthly_financials"]}, retailer)
    counts = {e["table"]: e["unlocks_kpis"] for e in report.tables_missing}

    assert counts.get("headcount"), \
        "a retailer supplying a headcount roster was told it unlocks nothing"
    assert counts.get("marketing"), \
        "a retailer supplying marketing spend was told it unlocks nothing"
    assert all(c is None or c > 0 for c in counts.values()), \
        f"a zero unlock count is a claim, not an absence: {counts}"


def test_the_two_halves_of_the_report_agree(retailer, retail_tables):
    """"0 KPIs blocked" and "orders unlocks 6" cannot both be on one screen.

    Found by reading the real report from the running server rather than from a
    test: four tables missing, thirteen KPIs named against them, and `blocked`
    printed as zero underneath. `_kpi_counts` counted a KPI available if *any*
    present table listed it, so the P&L alone made everything look reachable.
    """
    supplied = ("monthly_financials", "customers", "buyers", "marketing")
    report = build_report({n: retail_tables[n] for n in supplied}, retailer)

    named = set()
    for entry in report.tables_missing:
        named.update(entry["example_kpis"])

    assert report.kpis_blocked >= 1, \
        "tables are missing and KPIs are named against them, so some are blocked"
    assert report.kpis_blocked >= len(named & _selected_ids(retailer)), \
        "fewer KPIs reported blocked than the missing tables individually name"
    assert report.kpis_available + report.kpis_blocked == len(_selected_ids(retailer))


def _selected_ids(profile):
    from kpi_maker.kpi.selection import select
    return {k.id for k in select(profile).kpis}


def test_the_saas_report_did_not_regress(retail_tables):
    """The path that already worked still works — the map is generated now."""
    profile = load_profile(SAAS_SAMPLE)
    tables = dict(GENERATORS["saas"](profile).tables)
    report = build_report({"monthly_financials": tables["monthly_financials"]},
                          profile)
    counts = {e["table"]: e["unlocks_kpis"] for e in report.tables_missing}

    assert counts.get("mrr_movements", 0) >= 5, \
        "the movements table backs most of the retention scorecard"
    assert report.kpis_available > 0 and report.can_run


def test_the_generated_table_map_matches_the_engine():
    """The committed map must still be what the metrics engine actually does.

    This is the check that stops the map rotting again. It re-derives it — take
    each table away, see which KPIs stop computing — and compares. Costs one
    synthetic dataset per archetype, which is why the map is generated and
    committed rather than computed on the request that renders a report.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    from tools.gen_table_kpis import derive

    derived = derive()
    assert derived == TABLE_KPIS, (
        "kpi_maker/ingest/table_kpis.py is stale — run "
        "`python tools/gen_table_kpis.py`")
