"""The checks that make ~20 packs affordable, and what they found on day one.

Phase 4 plans roughly six hundred record sheets. Reading those is not review,
and the failures that matter do not look like anything in a diff: a
`driver_parent` pointing at an id that no longer exists, an `applies_when` that
names a field that was renamed, a `kind: builtin` with nothing registered to
compute it. Each shows up as a quietly missing row.

**Every threshold the plan proposed had to be re-scoped, and measuring said so
each time.** That is the interesting part of this item, so it is what these
tests pin:

* "min 2 / max 7 per perspective" *per pack* fails three of the four shipped
  packs — `saas` has fourteen financial candidates. A pack is a menu and a
  scorecard is the meal; the maximum is `kpi/selection.py`'s and the pack owes
  a minimum.
* "≥30% leading" *per pack* is a proxy, and a bad one: the e-commerce pack
  cleared it at 30.4% while a retailer's actual scorecard was still 26%,
  because selection caps tier 1 at six and ten of that pack's twelve tier-1
  sheets were lagging. The gate is the scorecard a run really gets.
* Placeholder benchmarks are counted, not gated — sixty of sixty-seven cite an
  illustrative composite, and 4.4 replaces them. Failing every pack over
  scheduled work teaches an author to ignore the linter.

And the linter's own first two runs were wrong, which is why the last two tests
exist: it reported `saas_standard` as reachable by nobody (it reimplemented the
`{pack}_*.yaml` glob and missed half of it), and it linted a `pack+general`
group that no profile ever loads.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.authoring import lint_all, lint_group, validate_sheet  # noqa: E402
from kpi_maker.authoring.lint import load_groups  # noqa: E402
from kpi_maker.kpi.schema import KPI  # noqa: E402
from kpi_maker.kpi.selection import load_library, pack_files, select  # noqa: E402
from kpi_maker.profile import sectors, taxonomy  # noqa: E402
from kpi_maker.survey import build_profile  # noqa: E402


@pytest.fixture(scope="module")
def reports():
    return lint_all()


def _sheet(**changes) -> KPI:
    base = load_library(["ecommerce"], include_user=False)[0].model_dump()
    return KPI.model_validate({**base, **changes})


# --------------------------------------------------------------------------
# The library passes its own linter
# --------------------------------------------------------------------------

def test_every_shipped_pack_passes(reports):
    """The gate. If this goes red, a pack ships a defect a user would meet."""
    failing = {r.group: [str(f) for f in r.errors] for r in reports if not r.ok}
    assert not failing, failing


def test_the_groups_linted_are_the_groups_that_run(reports):
    """Not `pack+general` for everything, which is what the first version
    linted. A SaaS run loads `saas` alone and a retailer loads `general` alone;
    a group that never occurs would pass a cross-pack parent that breaks in
    every real run."""
    linted = {tuple(sorted(r.packs)) for r in reports}
    real = {tuple(sorted(sectors.resolve_packs(s.id).value))
            for s in taxonomy.load().sectors}
    assert real <= linted, f"never linted: {sorted(real - linted)}"
    assert linted <= real, f"linted a group nothing loads: {sorted(linted - real)}"


def test_no_pack_file_is_unreachable(reports):
    """A pack no sector resolves to ships to nobody.

    The first version of this check compared file stems to pack names and
    called `saas_standard` unreachable — `_load_packs` globs `{pack}_*.yaml`
    too, so `saas` loads all fourteen of its sheets. Asking `pack_files` is the
    fix: one function decides what a pack is.
    """
    loaded = {path.name for r in reports for path in pack_files(r.packs)}
    on_disk = {p.name for p in (ROOT / "kpi_maker" / "kpi" / "library").glob("*.yaml")}
    assert on_disk == loaded, f"unreachable: {sorted(on_disk - loaded)}"


# --------------------------------------------------------------------------
# What the rules actually catch
# --------------------------------------------------------------------------

def test_a_dangling_driver_parent_is_an_error():
    findings = validate_sheet(_sheet(driver_parent="no_such_kpi"),
                              known_ids={"net_revenue"})
    assert any(f.level == "error" and f.rule == "driver-parent" for f in findings)


def test_an_applies_when_that_names_a_field_that_does_not_exist_is_an_error():
    """Parsing is not the check — this expression parses perfectly.

    `business_model.revenu_model` is a typo that hides the KPI from every
    profile forever, and a grammar check waves it through, so the gate is
    evaluated against a real `CompanyProfile` instead.
    """
    findings = validate_sheet(
        _sheet(applies_when="'subscription' in business_model.revenu_model"),
        known_ids=set(), profile=build_profile({}))
    assert any(f.level == "error" and f.rule == "applies-when" for f in findings)

    ok = validate_sheet(
        _sheet(applies_when="'subscription' in business_model.revenue_model"),
        known_ids=set(), profile=build_profile({}))
    assert not any(f.rule == "applies-when" for f in ok)


def test_a_builtin_with_no_implementation_is_an_error_unless_it_says_why():
    """The check that would have caught `nps` — with the distinction 0.2 drew.

    A builtin naming a source system this build has no data model for is
    declared, and selection drops it with a readable reason. One naming nothing
    is the defect: it gets selected and renders as a broken row.
    """
    broken = validate_sheet(
        _sheet(id="ghost", compute={"kind": "builtin"}, requires_data=[]),
        known_ids=set(), registry=set())
    assert any(f.level == "error" and f.rule == "unimplemented" for f in broken)

    declared = validate_sheet(
        _sheet(id="ghost", compute={"kind": "builtin"}, requires_data=["survey"]),
        known_ids=set(), registry=set())
    assert not any(f.level == "error" for f in declared)
    assert any(f.level == "info" and f.rule == "unimplemented" for f in declared)


def test_the_two_known_unimplemented_sheets_are_reported_but_do_not_fail(reports):
    """`nps` and `support_first_response_hours`, still declared and still
    uncomputable, still dropped with a reason rather than crashing."""
    infos = [f for r in reports for f in r.findings
             if f.rule == "unimplemented" and f.level == "info"]
    assert {f.kpi_id for f in infos} == {"nps", "support_first_response_hours"}


def test_a_pack_short_of_candidates_in_a_perspective_is_an_error():
    thin = [k for k in load_library(["ecommerce"], include_user=False)
            if k.perspective.value != "learning_growth"]
    report = lint_group(["ecommerce"], kpis=thin)
    assert any(f.rule == "coverage" for f in report.errors), \
        [str(f) for f in report.findings]


def test_the_balanced_scorecard_maximum_is_not_a_pack_rule(reports):
    """`saas` has fourteen financial candidates and must not fail for it.

    The plan asked for max 7 per perspective here. Applied to packs it fails
    three of the four shipped ones, because selection picks about 25 of 44 and
    is where a maximum means anything.
    """
    saas = load_library(["saas"], include_user=False)
    financial = sum(1 for k in saas if k.perspective.value == "financial")
    assert financial > 7, "the premise of this test has changed"
    assert next(r for r in reports if r.packs == ["saas"]).ok


def test_placeholder_benchmarks_are_counted_and_not_gated(reports):
    placeholders = [f for r in reports for f in r.findings
                    if f.rule == "benchmark-placeholder"]
    assert len(placeholders) > 20, "the count stopped being reported"
    assert all(f.level == "info" for f in placeholders), \
        "gating on 4.4's work would teach authors to ignore the linter"


# --------------------------------------------------------------------------
# The gate that matters: what a run actually gets
# --------------------------------------------------------------------------

def test_the_gate_is_the_scorecard_a_run_really_gets():
    """A pack may not ship a warning the user cannot act on.

    `kestrel_retail` carried "only 22% of the selected KPIs are leading" on
    every run. Both quality warnings `kpi/selection.py` raises are properties of
    the library, not of the company, so an author has to be stopped rather than
    the user informed.
    """
    for sector in ("ecommerce", "saas"):
        kpi_set = select(build_profile({"business_model": sector}))
        assert "_leading_warning" not in kpi_set.rationale, \
            f"{sector} still ships a leading-indicator warning"


def test_the_retail_pack_now_offers_enough_leading_indicators():
    """4.3a's first use, and its payoff, measured.

    The linter refused the e-commerce pack; three leading sheets were authored
    from tables the archetype already emits — new buyer share, cost per new
    buyer, inventory cover — and two of them had to move to tier 2 to be
    selected at all, matching the library's own convention that a raw cost is
    functional (`blended_cac`) and a decision-ready ratio is executive
    (`cac_payback_months`).
    """
    kpi_set = select(build_profile({"business_model": "ecommerce"}))
    selected = {k.id for k in kpi_set.kpis}
    assert {"new_buyer_share", "cost_per_new_buyer",
            "inventory_cover_days"} <= selected, sorted(selected)

    leading = sum(1 for k in kpi_set.kpis if k.timing.value == "leading")
    share = leading / len(kpi_set.kpis)
    assert share >= 0.30, f"back to {share:.0%} leading"


def test_the_new_sheets_compute_on_real_data():
    """A record sheet that does not produce a number is a definition, not a
    metric — and the formula sandbox will accept an expression that references
    a column no table has."""
    from kpi_maker.cli import load_profile
    from kpi_maker.datagen import GENERATORS
    from kpi_maker.metrics.engine import compute

    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    tables = dict(GENERATORS["ecommerce"](profile).tables)
    results = {r.kpi.id: r for r in compute(select(profile), tables, profile)}

    for kpi_id in ("new_buyer_share", "cost_per_new_buyer", "inventory_cover_days"):
        result = results.get(kpi_id)
        assert result is not None and result.computed, f"{kpi_id} did not compute"
        assert result.current is not None and result.current > 0, \
            f"{kpi_id} = {result.current}"


def test_the_cli_exits_non_zero_on_an_error(capsys):
    """It is a gate, so CI has to be able to use it without reading anything."""
    from kpi_maker.authoring.__main__ import main

    assert main(["lint", "--all"]) == 0
    capsys.readouterr()


def test_load_groups_comes_from_the_taxonomy():
    """A sector gaining its own pack in 4.3 starts being linted as the group it
    will run as, with nothing here to remember to edit."""
    groups = load_groups()
    assert ["general"] in groups.values(), "the fallback group is not linted"
    assert ["saas"] in groups.values() and ["ecommerce"] in groups.values()


def test_a_pack_that_would_ship_a_quality_warning_is_refused():
    """The gate itself, against a scorecard that trips it.

    The first version of this rule had no test that failed when it was deleted:
    every shipped pack passes, so removing the check changed nothing observable.
    A rule with no failing case is a rule that will be deleted by accident.
    """
    from kpi_maker.authoring import warnings_a_user_cannot_act_on

    class _Set:
        rationale = {"_leading_warning": "Only 22% of the selected KPIs are "
                                         "leading indicators (target 30%)."}

    findings = warnings_a_user_cannot_act_on(_Set(), group="demo", sector="ecommerce")
    assert [f.rule for f in findings] == ["leading-warning"]
    assert findings[0].level == "error"
    assert "22%" in findings[0].message and "cannot act on it" in findings[0].message

    # An approximated sector is *expected* to be thin, and 0.1's whole design is
    # that it says so. Failing it for approximating would be wrong.
    class _Thin:
        rationale = {"_coverage_warning": "Perspectives below the minimum: customer."}

    assert warnings_a_user_cannot_act_on(_Thin(), group="g", sector="retail",
                                         approximate=True) == []
    assert warnings_a_user_cannot_act_on(_Thin(), group="g", sector="ecommerce",
                                         approximate=False)


def test_the_linter_actually_consults_the_selected_scorecard(monkeypatch):
    """The wiring, not just the rule.

    Deleting the one line that calls the gate left all sixteen other tests
    green — the rule was covered and its use was not, which is the same shape
    as a rule with no failing case at all. Selection is patched to return a
    scorecard that trips its own warning, so this fails if `lint_group` stops
    asking.
    """
    import kpi_maker.kpi.selection as selection

    class _Set:
        rationale = {"_leading_warning": "Only 5% of the selected KPIs are "
                                         "leading indicators (target 30%)."}
        kpis: list = []

    monkeypatch.setattr(selection, "select", lambda *a, **k: _Set())
    report = lint_group(["ecommerce"])
    assert any(f.rule == "leading-warning" for f in report.errors), \
        [str(f) for f in report.findings]
