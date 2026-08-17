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
from kpi_maker.metrics.engine import compute  # noqa: E402
from kpi_maker.metrics.targets import (  # noqa: E402
    TARGET_NAMES,
    resolve_target,
    validate_rule,
)


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
    assert resolve_target(kpi, 0.40) == pytest.approx(0.60)
    # And it is the rule doing the work, not a coincidence with the median.
    assert resolve_target(kpi, 0.40) != kpi.benchmark.p50


def test_spacing_no_longer_decides_whether_a_rule_runs(with_rule):
    """`max(benchmark_p50, current*1.1)` — one space removed — silently became
    the benchmark median under the old lookup."""
    bench = {"p25": 0.1, "p50": 0.5, "p75": 0.9, "source": "test"}
    spaced = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
                  benchmark=bench)
    tight = _kpi(with_rule, target_rule="MAX(benchmark_p50,current*1.1)",
                 benchmark=bench)
    assert resolve_target(spaced, 1.0) == resolve_target(tight, 1.0) == 1.1


def test_every_named_scalar_a_rule_may_use_actually_resolves(with_rule):
    kpi = _kpi(with_rule,
               benchmark={"p25": 1.0, "p50": 2.0, "p75": 3.0, "source": "test"},
               alert_bands={"green": 5.0, "red": 1.0}, direction="higher_is_better")
    for name in TARGET_NAMES:
        target = resolve_target(_kpi({**kpi.model_dump(), "target_rule": name}),
                                current=7.0, prior_year=6.0, prior_month=6.5)
        assert target is not None, f"{name} did not resolve"


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
    assert resolve_target(kpi, 2.0) == 4.0


def test_a_typed_target_still_beats_the_rule(with_rule):
    kpi = _kpi(with_rule, target_rule="MAX(benchmark_p50, current * 1.1)",
               target_override=0.42,
               benchmark={"p25": 1.0, "p50": 2.0, "p75": 3.0, "source": "test"})
    assert resolve_target(kpi, 1.0) == 0.42


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
