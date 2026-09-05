"""Actual against plan — the scenario this codebase never had.

IBCS notation is built on actual / plan / prior / forecast. **Two of the four
did not exist anywhere in the engine before 5.1**, verified rather than
assumed: `MetricResult` carried a single `target` scalar, and the only classes
with "Plan" in the name were `StagePlan` and `UploadPlan`, both about pipeline
scheduling. So there was no variance-to-plan at all, which is the thing
management reporting is most often asked for.

What landed, and what these tests pin:

* `spec.plan` — monthly figures per KPI, round-tripping through JSON.
* `MetricResult.plan` / `.plan_basis` / `.plan_current` / `.vs_plan`.
* Two sources, kept apart all the way to the reader: **stated** (the user's
  own budget) and **derived** (built from the KPI's target rule, and labelled
  every time it renders).
* **No plan means no variance.** Not a zero, not the target repeated twelve
  times. A variance against a fabricated budget reads as performance against
  a commitment nobody made, which is worse than no variance at all.

Forecast is deliberately still absent: nothing produces one, and a slot with
no producer is the dead-spec-field pattern 0.3 was spent removing.

Every test here was verified to fail with its fix reverted; the mutation is
named in each docstring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute  # noqa: E402
from kpi_maker.metrics.plan import derived_plan, stated_plan  # noqa: E402
from kpi_maker.spec.schema import PATCHABLE_SECTIONS, RunSpec  # noqa: E402

SPEC_SAMPLE = ROOT / "samples" / "specs" / "kestrel_retail.json"


@pytest.fixture(scope="module")
def retailer():
    """The sample that carries a real stated budget, computed once."""
    spec = RunSpec.model_validate_json(SPEC_SAMPLE.read_text(encoding="utf-8"))
    profile = spec.profile
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)
    kpi_set = select(profile)
    return spec, {r.kpi.id: r
                  for r in compute(kpi_set, tables, profile, plan_spec=spec.plan)}


@pytest.fixture(scope="module")
def unplanned():
    """The same company with no plan block — the pre-5.1 behaviour."""
    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    spec = RunSpec(profile=profile)
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)
    return {r.kpi.id: r
            for r in compute(select(profile), tables, profile,
                             plan_spec=spec.plan)}


# --------------------------------------------------------------------------
# No plan means no variance
# --------------------------------------------------------------------------

def test_a_run_without_a_plan_has_no_variance_anywhere(unplanned):
    """The single most important property in this item.

    Mutation: fall back to the target in `plan.resolve` when nothing is
    stated, which is the tempting shortcut and would put a variance against a
    number nobody committed to on every row of every board pack ever produced.
    """
    assert unplanned, "the fixture computed nothing"
    for result in unplanned.values():
        assert result.plan is None, result.kpi.id
        assert result.plan_basis == "", result.kpi.id
        assert result.plan_current is None, result.kpi.id
        assert result.vs_plan is None, result.kpi.id


def test_the_planned_kpis_are_exactly_the_ones_the_budget_names(retailer):
    """A budget set on six lines does not imply one on the other fourteen.

    **Asserted as an exact set, and the first version was not.** It checked
    only that unplanned KPIs had no variance, which stayed green against the
    fabricate-from-target mutation: the KPIs that carry no target still had
    none, so the weaker claim held while the engine invented budgets for
    everything else. The set is the claim.

    Mutation: fall back to the target in `plan.resolve`.
    """
    spec, results = retailer
    planned = {k for k, r in results.items() if r.plan_basis}
    expected = set(spec.plan.values) & set(results)
    assert planned == expected, sorted(planned ^ expected)

    for kpi_id in set(results) - planned:
        assert results[kpi_id].plan is None, kpi_id
        assert results[kpi_id].vs_plan is None, kpi_id


# --------------------------------------------------------------------------
# A stated plan
# --------------------------------------------------------------------------

def test_the_sample_budget_produces_a_real_variance(retailer):
    """The end-to-end fact, on the sample that carries a board-set budget.

    Mutation: drop `plan_spec=` from the `metrics` stage's `compute` call and
    every one of these goes to None.
    """
    spec, results = retailer
    assert spec.plan.values, "the sample spec carries no plan"
    assert spec.plan.source, "a plan with no provenance is a number somebody typed"

    planned = {k: r for k, r in results.items() if r.plan_basis}
    assert len(planned) >= 5, sorted(planned)
    for kpi_id, r in planned.items():
        assert r.plan_basis == "stated", kpi_id
        assert r.plan_current is not None, kpi_id
        assert r.vs_plan == pytest.approx(r.current - r.plan_current), kpi_id


def test_the_variance_is_read_at_the_month_the_actual_is_read_at(retailer):
    """Not "the last plan value there is".

    A budget running to year end compared against an actual eight months
    behind it would produce a variance that is a reading of the calendar — the
    same positional-versus-period mistake `_year_ago` was fixed for in 0.2,
    where `series.iloc[-13]` was wrong on any gapped index.

    Mutation: `plan.dropna().iloc[-1]` in `plan_current`.
    """
    _, results = retailer
    r = next(x for x in results.values() if x.plan_basis == "stated")
    latest = r.series.dropna().index[-1]
    assert r.plan_current == pytest.approx(float(r.plan.loc[latest]))

    # And a plan that extends past the actual must not be read from its end.
    extended = r.plan.copy()
    future = pd.Series({latest + 1: 999999.0, latest + 2: 999999.0})
    extended = pd.concat([extended, future])
    stretched = type(r)(**{**r.__dict__, "plan": extended})
    assert stretched.plan_current == pytest.approx(float(r.plan.loc[latest]))


def test_a_month_the_user_did_not_state_stays_empty():
    """A budget set for half a year is a real thing; interpolating the other
    half would put figures on the page nobody approved.

    Mutation: `.interpolate()` or `.ffill()` on the reindexed series.
    """
    index = pd.period_range("2025-01", "2025-12", freq="M")
    plan = stated_plan({"2025-07": 10.0, "2025-08": 12.0}, index)
    assert plan is not None
    assert plan.notna().sum() == 2
    assert plan.dropna().index.tolist() == [pd.Period("2025-07"),
                                            pd.Period("2025-08")]


def test_a_malformed_month_does_not_take_the_rest_of_the_plan_with_it():
    """One typo is the user's; losing their whole budget over it is ours."""
    index = pd.period_range("2025-01", "2025-06", freq="M")
    plan = stated_plan({"2025-03": 5.0, "not-a-month": 9.0}, index)
    assert plan is not None and plan.dropna().tolist() == [5.0]
    assert stated_plan({"nope": 1.0}, index) is None
    assert stated_plan({}, index) is None


# --------------------------------------------------------------------------
# A derived plan, and what it is allowed to claim
# --------------------------------------------------------------------------

def test_a_derived_plan_keeps_last_years_shape_rather_than_drawing_a_line():
    """The design decision this function exists for.

    A seasonal business budgeted flat posts a variance every month that is
    purely the calendar — the artefact 3.4b spent an item removing from the
    detectors, and worse here, because a plan variance reads as a management
    failure rather than as a detector's noise.

    Mutation: `np.linspace(current, target, len(index))`, which correlates
    with the calendar rather than with the business and is flat by
    construction.
    """
    index = pd.period_range("2024-01", "2025-12", freq="M")
    seasonal = pd.Series(
        [100, 90, 95, 100, 105, 110, 105, 100, 110, 130, 180, 240] * 2,
        index=index, dtype=float)
    plan = derived_plan(seasonal, target=264.0)
    assert plan is not None

    covered = plan.dropna()
    prior = seasonal.shift(12).reindex(covered.index)
    assert covered.corr(prior) == pytest.approx(1.0, abs=1e-9), "shape was lost"
    assert covered.max() / covered.min() > 2.0, "the plan came out flat"


def test_a_derived_plan_lands_on_the_target_in_the_final_month():
    """The target is a level for the last month, so the scale is set there.

    Mutation: scale by the annual sum instead, and the plan lands somewhere
    the target never named.
    """
    index = pd.period_range("2024-01", "2025-12", freq="M")
    actual = pd.Series(range(100, 124), index=index, dtype=float)
    plan = derived_plan(actual, target=200.0)
    assert plan is not None
    assert float(plan.dropna().iloc[-1]) == pytest.approx(200.0)


def test_a_derived_plan_refuses_without_a_full_prior_year():
    """With fewer than twelve months there is no shape to borrow, and the
    fallback would be the straight line the function exists to avoid.

    **The gappy series is the case that matters, and the first version of this
    test missed it.** A short run is already refused by the "is last year's
    same month present?" check, so shortening the series left the length guard
    unexercised and the mutation green. What the guard is actually for is a
    series that *spans* a year while barely covering it — five quarterly
    readings, say — where the anchor month exists and there is still no
    monthly shape to borrow.

    Mutation: drop `if len(clean) < 12` and the sparse case silently produces
    a plan built from five points.
    """
    # Spans thirteen months, anchor month present, five observations.
    sparse = pd.Series(
        [10.0, 12.0, 14.0, 16.0, 18.0],
        index=pd.PeriodIndex(["2024-01", "2024-04", "2024-07", "2024-10",
                              "2025-01"], freq="M"))
    assert sparse.index[-1] - 12 in sparse.index, "the premise of this test"
    assert derived_plan(sparse, target=20.0) is None

    # And the plainly-too-short case, which the anchor check catches.
    short = pd.Series(range(9), index=pd.period_range("2025-01", periods=9,
                                                      freq="M"), dtype=float)
    assert derived_plan(short, target=20.0) is None

    # No target, nothing to scale to.
    assert derived_plan(pd.Series(range(24), dtype=float,
                                  index=pd.period_range("2024-01", periods=24,
                                                        freq="M")),
                        target=None) is None


def test_derivation_is_off_unless_the_run_asks_for_it(unplanned):
    """A path to a target is not a budget, so nobody gets one by default.

    Mutation: `derive_from_target: bool = True` in `PlanSpec`.
    """
    assert RunSpec(profile=load_profile(
        ROOT / "samples" / "kestrel_retail.json")).plan.derive_from_target is False
    assert all(r.plan_basis == "" for r in unplanned.values())


def test_a_derived_plan_is_labelled_as_derived_everywhere_it_appears():
    """A plan this engine drew and one a board approved are not
    interchangeable, and the reader has to be able to tell.

    Mutation: return `"stated"` from the derivation branch of `plan.resolve`,
    or drop the badge from the renderer.
    """
    from kpi_maker.render.dashboard import _plan_badge

    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    spec = RunSpec(profile=profile)
    spec.plan.derive_from_target = True
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)
    results = [r for r in compute(select(profile), tables, profile,
                                  plan_spec=spec.plan) if r.plan_basis]
    assert results, "derivation produced nothing to label"
    assert {r.plan_basis for r in results} == {"derived"}
    assert "Derived" in _plan_badge(results[0])

    # And a stated plan is quiet, or the distinction is lost to repetition.
    stated = type(results[0])(**{**results[0].__dict__, "plan_basis": "stated"})
    assert _plan_badge(stated) == ""


# --------------------------------------------------------------------------
# The spec contract
# --------------------------------------------------------------------------

def test_the_plan_is_in_the_metrics_stage_cache_key():
    """0.2's side-channel lesson, applied to a new section.

    A stage's cache key is the spec sections it declares in `reads`. Without
    `plan` there, a spec whose only change is a new budget is served the
    previous run's metrics and reports variance against a plan nobody set.

    Mutation: remove `"plan"` from the `metrics` stage's `reads`.

    Asserted by **running the pipeline**, not by reading the declaration. A
    declaration test would pass against a `reads` tuple that named the section
    while something else short-circuited the rebuild, and 0.6 recorded that a
    green unit suite proves the seams you thought to test while a live run
    proves the feature.
    """
    import kpi_maker.pipeline.stages  # noqa: F401  - registers the stages
    from kpi_maker.pipeline.graph import STAGES

    assert "plan" in STAGES["metrics"].reads, STAGES["metrics"].reads


def test_the_plan_round_trips_through_json():
    """A spec is data. A plan that survives one save and not the next is not."""
    spec = RunSpec.model_validate_json(SPEC_SAMPLE.read_text(encoding="utf-8"))
    again = RunSpec.model_validate_json(spec.model_dump_json())
    assert again.plan.values == spec.plan.values
    assert again.plan.label == spec.plan.label
    assert again.plan.source == spec.plan.source


def test_the_plan_is_patchable_but_the_profile_still_is_not():
    """A budget is exactly the kind of thing a user edits in the Studio. Who
    the company is remains the one section no automated patch may touch."""
    assert "plan" in PATCHABLE_SECTIONS
    assert "profile" not in PATCHABLE_SECTIONS


# --------------------------------------------------------------------------
# The rendered variance
# --------------------------------------------------------------------------

def test_variance_is_coloured_by_whether_it_is_good_not_by_its_sign(retailer):
    """A cost metric beating its budget is *negative*.

    Colouring on the sign puts a red chip on the best cost month of the year,
    which is the mistake 4.2a found in `vs_benchmark` — two branches where the
    metric's own `direction` needed three.

    Mutation: `good = value > 0` unconditionally.
    """
    from kpi_maker.render.dashboard import _variance_cell

    _, results = retailer
    from kpi_maker.kpi.schema import Direction

    r = next(x for x in results.values()
             if x.plan_basis and x.kpi.direction == Direction.higher_is_better
             and x.vs_plan and x.vs_plan > 0)

    assert "variance-good" in _variance_cell(r, "GBP")

    flipped = type(r)(**{**r.__dict__})
    flipped.kpi = r.kpi.model_copy(update={"direction": Direction.lower_is_better})
    assert "variance-bad" in _variance_cell(flipped, "GBP")

    # `target_band` is the third case: both extremes are bad, so the number is
    # shown without a judgement rather than with a guessed one.
    banded = type(r)(**{**r.__dict__})
    banded.kpi = r.kpi.model_copy(update={"direction": Direction.target_band})
    cell = _variance_cell(banded, "GBP")
    assert "variance-good" not in cell and "variance-bad" not in cell


def test_a_variance_too_small_for_the_units_is_shown_as_a_percentage(retailer):
    """Found by looking at the retailer's board pack.

    Average order value missed its budget by 41p on a £22 line and the cell
    read **"+£0"** — a real miss presented as exactly on plan, because a
    variance is a small number by construction and the level's own precision
    rounds it away. Same defect as 4.2a's band breach quoting "the green
    threshold of —".

    Mutation: delete the `_rounds_to_nothing` branch.
    """
    from kpi_maker.render.dashboard import _variance_cell

    _, results = retailer
    aov = results["aov"]
    assert aov.vs_plan is not None and abs(aov.vs_plan) < 1.0
    cell = _variance_cell(aov, "GBP")
    assert "£0" not in cell, cell
    assert "%" in cell, cell


def test_the_scorecard_grows_plan_columns_only_when_there_is_a_plan(retailer,
                                                                   unplanned):
    """Two columns of em-dashes would be worse than the feature's absence.

    Mutation: render the columns unconditionally.
    """
    from kpi_maker.kpi.selection import select as _select
    from kpi_maker.render.dashboard import _scorecard_table

    spec, results = retailer
    kpi_set = _select(spec.profile)
    with_plan = _scorecard_table(list(results.values()), kpi_set, "GBP")
    assert "vs plan" in with_plan and "Plan</th>" in with_plan

    without = _scorecard_table(list(unplanned.values()), kpi_set, "GBP")
    assert "vs plan" not in without and "Plan</th>" not in without
    assert 'colspan="7"' in without and 'colspan="9"' in with_plan


# --------------------------------------------------------------------------
# The directory contract this item nearly broke
# --------------------------------------------------------------------------

def test_every_file_in_samples_is_a_profile_and_specs_live_below_it():
    """An implicit contract, made explicit because 5.1 nearly broke it twice.

    `samples/*.json` is loaded as a `CompanyProfile` by `tests/test_packaging`,
    by `api/server.py`'s gallery, and by CI's "every sample produces every
    artifact" loop. Dropping a RunSpec in there failed the first and would
    have failed the third — and the existing guard was a hardcoded
    `if sample.name == "gallery.json": continue`, so the fix was heading for a
    second special case. Run specs live in `samples/specs/` instead, and this
    says so once for all three readers.
    """
    from kpi_maker.profile.schema import CompanyProfile

    for path in sorted((ROOT / "samples").glob("*.json")):
        if path.name == "gallery.json":
            continue
        CompanyProfile.model_validate_json(path.read_text(encoding="utf-8"))

    specs = sorted((ROOT / "samples" / "specs").glob("*.json"))
    assert specs, "the plan sample is gone"
    for path in specs:
        spec = RunSpec.model_validate_json(path.read_text(encoding="utf-8"))
        assert spec.plan.values, f"{path.name} is a spec with nothing to show"
        # A spec sample must name a profile that exists on its own, or the two
        # drift and nobody notices which company the budget belongs to.
        sibling = ROOT / "samples" / path.name
        assert sibling.exists(), sibling
        assert spec.profile.identity.name == json.loads(
            sibling.read_text(encoding="utf-8"))["identity"]["name"]


def test_editing_only_the_plan_rebuilds_only_what_depends_on_it(tmp_path):
    """The behaviour the declaration above stands for, measured on a real run.

    Cold: seven stages. Warm with the same spec: seven reused, nothing run.
    Change one KPI's budget and nothing else, and exactly `metrics` and the
    artifact below it rebuild while `source` — the expensive one — is reused.

    Mutation: remove `"plan"` from the `metrics` stage's `reads`, and the run
    with the new budget reports variance from the previous budget's cache.
    """
    from kpi_maker.pipeline.runner import execute

    spec = RunSpec.model_validate_json(SPEC_SAMPLE.read_text(encoding="utf-8"))
    out = tmp_path / "run"

    cold = execute(spec, out, artifacts=["facts_csv"], say=lambda *_: None)
    assert cold.ran and not cold.skipped

    warm = execute(spec, out, artifacts=["facts_csv"], say=lambda *_: None)
    assert not warm.ran, sorted(warm.ran)

    edited = spec.model_copy(deep=True)
    kpi_id = next(iter(edited.plan.values))
    edited.plan.values[kpi_id] = {m: v * 1.5
                                  for m, v in edited.plan.values[kpi_id].items()}
    after = execute(edited, out, artifacts=["facts_csv"], say=lambda *_: None)
    assert "metrics" in after.ran, sorted(after.ran)
    assert "source" in after.skipped, "the expensive stage should be reused"
