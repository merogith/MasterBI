"""A target rule was a lookup table of four exact strings.

`_resolve_target` matched these, character for character:

    max(benchmark_p50, current * 1.1)
    min(current * 0.85, 12)
    max(1.10, current + 0.05)
    min(current * 0.85, benchmark_p50)

Anything else — a fifth rule, one of these with different spacing, `1.10`
written where the table says `1.1` — fell through to the benchmark median with
no error raised, nothing logged and nothing on screen. Measured across the
library as it stands: **4 KPIs of 80 carry a rule and all four are in the
set**, so this was a trap rather than a live bug. Phase 4 plans ~600 record
sheets across ~20 packs, at which point a pack author writes a fifth rule, gets
a plausible-looking target, and never learns their rule was ignored.

The rules are now evaluated in the sandbox that already exists for formulas —
parsed with `ast`, walked against an explicit node whitelist, calling only
registered functions — over a small named scope. The library's four are
rewritten in the sandbox's own vocabulary (`MAX`, not `max`) rather than the
sandbox growing lowercase aliases for one caller.

Two failure modes, deliberately different: a rule that cannot parse or names
something that does not exist is an **authoring error** and raises where the
pack loads; a good rule that this particular run cannot feed — it wants a
benchmark and none is published — is **data**, and falls back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.formula.errors import FormulaError  # noqa: E402
from kpi_maker.kpi.schema import KPI  # noqa: E402
from kpi_maker.kpi.selection import load_library, select  # noqa: E402
from kpi_maker.metrics.engine import compute, facts_table  # noqa: E402
from kpi_maker.metrics.targets import (  # noqa: E402
    TARGET_NAMES,
    resolve_target,
    validate_rule,
)
from kpi_maker.spec.schema import RunSpec  # noqa: E402


@pytest.fixture(scope="module")
def library():
    return load_library(None, include_user=False)


@pytest.fixture(scope="module")
def with_rule(library):
    kpi = next(k for k in library if k.target_rule)
    return kpi.model_dump()


def _kpi(base, **changes) -> KPI:
    return KPI.model_validate({**base, **changes})


# --------------------------------------------------------------------------
# The grammar
# --------------------------------------------------------------------------

def test_a_rule_the_lookup_table_never_had_is_evaluated(with_rule):
    """The whole point: a fifth rule works, rather than being ignored."""
    kpi = _kpi(with_rule, target_rule="MAX(benchmark_p50 * 1.2, current + 0.02)",
               benchmark={"p25": 0.1, "p50": 0.5, "p75": 0.9, "source": "test"})
    assert resolve_target(kpi, 0.40).value == pytest.approx(0.60)
    # And it is the rule doing the work, not a coincidence with the median.
    assert resolve_target(kpi, 0.40).value != kpi.benchmark.p50
    assert resolve_target(kpi, 0.40).basis == "rule"


def test_spacing_no_longer_decides_whether_a_rule_runs(with_rule):
    """`max(benchmark_p50, current*1.1)` — one space removed — silently became
    the benchmark median under the old lookup."""
    bench = {"p25": 0.1, "p50": 0.5, "p75": 0.9, "source": "test"}
    spaced = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
                  benchmark=bench)
    tight = _kpi(with_rule, target_rule="MAX(benchmark_p50,current*1.1)",
                 benchmark=bench)
    assert resolve_target(spaced, 1.0) == resolve_target(tight, 1.0)
    assert resolve_target(spaced, 1.0) == (1.1, "rule")


def test_every_named_scalar_a_rule_may_use_actually_resolves(with_rule):
    kpi = _kpi(with_rule,
               benchmark={"p25": 1.0, "p50": 2.0, "p75": 3.0, "source": "test"},
               alert_bands={"green": 5.0, "red": 1.0}, direction="higher_is_better")
    for name in TARGET_NAMES:
        target = resolve_target(_kpi({**kpi.model_dump(), "target_rule": name}),
                                current=7.0, prior_year=6.0, prior_month=6.5)
        assert target.value is not None, f"{name} did not resolve"


def test_a_rule_that_cannot_parse_fails_where_the_pack_loads(with_rule):
    """Not at compute time, and not silently: a broken rule must never reach a
    run, the same way a broken `applies_when` does not."""
    for bad in ("MAX(current,", "current +", "MAX(nrr, 1)", "max(current, 1)",
                "__import__('os')", "current.__class__"):
        with pytest.raises(ValueError):
            _kpi(with_rule, target_rule=bad)


def test_the_sandbox_is_the_sandbox(with_rule):
    """A target rule gets no more reach than a formula does. `SUM` is a series
    function and there is no series here; an attribute is not a value."""
    for reach in ("SUM(monthly_financials.revenue)", "TTM(current)",
                  "current.real", "[current]", "current > benchmark_p50"):
        with pytest.raises(FormulaError):
            validate_rule(reach)


def test_a_good_rule_a_run_cannot_feed_falls_back_rather_than_raising(with_rule):
    """"This KPI has no published benchmark" is data, not an authoring error,
    and a run must not die on it."""
    kpi = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
               benchmark=None, alert_bands={"green": 4.0, "red": 1.0},
               direction="higher_is_better")
    # No benchmark, so the rule cannot be evaluated — the green band is the
    # last stated intent available.
    assert resolve_target(kpi, 2.0) == (4.0, "band")


def test_a_typed_target_still_beats_the_rule(with_rule):
    kpi = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
               target_override=0.42,
               benchmark={"p25": 1.0, "p50": 2.0, "p75": 3.0, "source": "test"})
    assert resolve_target(kpi, 1.0) == (0.42, "override")


# --------------------------------------------------------------------------
# Against the library and a real run
# --------------------------------------------------------------------------

def test_every_rule_in_the_library_is_evaluable(library):
    """The drift check. A rule that no longer parses used to be indetectable —
    it just stopped applying."""
    ruled = [k for k in library if k.target_rule]
    assert ruled, "no KPI carries a target rule, so this asserts nothing"
    for kpi in ruled:
        validate_rule(kpi.target_rule)


def test_the_rules_survived_the_move_with_the_same_numbers():
    """The four library rules were rewritten into the sandbox's vocabulary, so
    the targets they produce must be the ones they produced before."""
    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    tables = dict(GENERATORS["saas"](profile).tables)
    results = {r.kpi.id: r for r in compute(select(profile), tables, profile)}

    nrr = results["nrr"]
    assert nrr.kpi.target_rule == "MAX(1.10, current + 0.05)"
    assert nrr.target == pytest.approx(max(1.10, nrr.current + 0.05))

    payback = results["cac_payback_months"]
    assert payback.target == pytest.approx(min(payback.current * 0.85, 12))

    growth = results["arr_growth_yoy"]
    assert growth.target == pytest.approx(
        max(growth.kpi.benchmark.p50, growth.current * 1.1))


def test_no_pack_writes_a_rule_in_the_old_lowercase_form():
    """One formula language, one spelling. The sandbox's functions are
    uppercase everywhere else, and a lowercase `max` now raises rather than
    being quietly matched by a lookup table."""
    offenders = []
    for path in (ROOT / "kpi_maker" / "kpi" / "library").glob("*.yaml"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "target_rule:" in line and ("max(" in line or "min(" in line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"lowercase target rules would now fail to load: {offenders}"


# --------------------------------------------------------------------------
# 5.3d — where the target came from
# --------------------------------------------------------------------------
#
# `resolve_target` returned a bare float, so the four fallbacks were
# indistinguishable by the time anything rendered one. This module's own
# docstring had claimed since 3.6 that it "falls back and says which fallback
# it took"; it did not. Every test below was verified to fail with its fix
# reverted; the mutation is named in each docstring.


def test_each_fallback_names_itself(with_rule):
    """The four sources, told apart.

    Mutation: return the value without the basis (a bare float), which is what
    shipped for two phases.
    """
    bench = {"p25": 1.0, "p50": 2.0, "p75": 3.0, "source": "test"}
    bands = {"green": 4.0, "red": 1.0}

    typed = _kpi(with_rule, target_override=0.42, benchmark=bench)
    assert resolve_target(typed, 1.0).basis == "override"

    ruled = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
                 benchmark=bench, target_override=None)
    assert resolve_target(ruled, 1.0).basis == "rule"

    # No rule, so the cohort median stands in — the overwhelmingly common case.
    plain = _kpi(with_rule, target_rule=None, target_override=None,
                 benchmark=bench)
    assert resolve_target(plain, 1.0) == (2.0, "benchmark")

    # No rule and no published benchmark: the record sheet's green threshold.
    banded = _kpi(with_rule, target_rule=None, target_override=None,
                  benchmark=None, alert_bands=bands,
                  direction="higher_is_better")
    assert resolve_target(banded, 1.0) == (4.0, "band")


def test_a_rule_that_this_run_cannot_feed_reports_the_fallback_it_took(with_rule):
    """The distinction the module is built around: a rule that does not parse
    is an authoring error, and a good rule this run cannot feed is *data*. The
    second case must not claim the rule produced the number.

    Mutation: report `"rule"` whenever a rule is present rather than when it
    evaluated.
    """
    kpi = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
               target_override=None, benchmark=None,
               alert_bands={"green": 4.0, "red": 1.0},
               direction="higher_is_better")
    resolved = resolve_target(kpi, 2.0)
    assert resolved.value == 4.0
    assert resolved.basis == "band", "a rule that did not run claimed credit"


def test_a_kpi_with_nothing_to_aim_at_has_no_basis_either(with_rule):
    """None and None, never a basis beside a missing number.

    Mutation: return `Target(None, "benchmark")` on the empty path.
    """
    kpi = _kpi(with_rule, target_rule=None, target_override=None,
               benchmark=None, alert_bands=None)
    assert resolve_target(kpi, 1.0) == (None, None)


def test_the_basis_travels_to_the_facts_table():
    """It has to reach a renderer to be worth having: the dashboard, the web
    scorecard and the record sheet all read the facts row, and the badge that
    keeps "Target" from being read as a commitment is driven by this column.

    Mutation: drop `target_basis` from `facts_table`'s row.
    """
    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    tables = dict(GENERATORS["saas"](profile).tables)
    results = compute(select(profile), tables, profile)
    frame = facts_table(results)

    assert "target_basis" in frame.columns
    stated = frame[frame["target"].notna()]
    assert not stated.empty
    assert stated["target_basis"].notna().all()
    assert set(stated["target_basis"]) <= {"override", "rule", "benchmark",
                                           "band"}
    # And a row with no target carries no basis, so the two cannot disagree.
    blank = frame[frame["target"].isna()]
    assert blank["target_basis"].isna().all()


def test_most_targets_are_the_cohort_median_and_the_run_says_so():
    """**The measurement this item turned on**, pinned so it cannot quietly
    stop being true. 109 of 123 targets across the seven samples are the peer
    cohort's median, which is why the scorecard prints the same figure in the
    Target and Cohort Median columns on nearly every row — and why the
    dashboard states that once in the panel subtitle rather than badging it
    nineteen times.

    Asserted as the property rather than the number: if authored targets
    arrive with Phase 4's packs the share moves, and a hardcoded 109 would go
    red for the right reason and read as a regression. What must stay true is
    that the run can *tell*, and that the stand-in case is named.

    Mutation: make `resolve_target` report every fallback as `"rule"`.
    """
    from collections import Counter

    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    tables = dict(GENERATORS["saas"](profile).tables)
    results = compute(select(profile), tables, profile)

    seen = Counter(r.target_basis for r in results
                   if r.computed and r.target is not None)
    assert seen, "no targets resolved at all"
    assert len(seen) > 1, (
        f"every target on this run reports the same source ({seen}), which "
        f"means the basis is not being distinguished")
    assert seen["benchmark"] > seen["rule"], seen


def test_the_dashboard_states_the_stand_in_once_and_badges_the_exception():
    """`_basis_badge` and `_plan_badge` label the untrustworthy case and stay
    quiet on the trustworthy one. That rule does not transfer here, because
    the untrustworthy case is the *common* one: badging it fired on 18 of 19
    rows and became the loudest thing in the table — the "invisible through
    repetition" failure those two docstrings warn about, which is how the
    first version of this looked on screen.

    So the majority goes in the panel subtitle once, and the badge marks the
    minority.

    Mutations: badge the `benchmark` rows too, or drop the subtitle sentence.
    """
    from kpi_maker.render.dashboard import _target_badge, _target_note

    # The archetype is resolved, not named: `orbis_works` is `production`,
    # and hardcoding "ecommerce" made the gate reject a factory's order value
    # against a retail band. A test that names an archetype is asserting the
    # sample's taxonomy entry by accident.
    profile = load_profile(ROOT / "samples" / "orbis_works.json")
    spec = RunSpec(profile=profile)
    tables = dict(GENERATORS[spec.resolve_archetype()](profile).tables)
    results = compute(select(profile), tables, profile)

    with_target = [r for r in results if r.computed and r.target is not None]
    stand_ins = [r for r in with_target if r.target_basis == "benchmark"]
    assert len(stand_ins) > len(with_target) / 2, "premise: most are stand-ins"

    assert all(_target_badge(r) == "" for r in stand_ins), (
        "the cohort-median rows are badged, which on this run is most of them")
    stated = [r for r in with_target if r.target_basis != "benchmark"]
    assert stated, "premise: this run has at least one stated target"
    assert all(_target_badge(r) for r in stated)

    note = _target_note(results)
    assert str(len(stand_ins)) in note and "cohort" in note, note
    # A KPI with no target at all must not be badged as though it had one.
    assert all(_target_badge(r) == "" for r in results if r.target is None)
