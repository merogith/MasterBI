"""One visual language across every chart (IBCS "Unify").

5.1 gave the engine a plan. 5.2 gives it a **notation**: actual solid, plan
dashed, prior dotted, defined once in `theme.SCENARIO_NOTATION` and drawn by
one helper, so a dash pattern cannot mean "plan" on one exhibit and something
else on the next.

Three things measured before anything was written, all of them wrong:

1. **`tickprefix="$"` was hardcoded** in three charts while every other figure
   on the page came from `_CURRENCY`. A €45M European SaaS company opened its
   board pack on an axis reading `$0 · $10M · $20M · $30M · $40M`. Nothing
   looked broken; the currency was simply wrong.
2. **`dash="dot"` already meant two things** — width-1 reference chrome
   (`add_hline` for an index-100 line or an alert band) and, on three charts,
   a second *measure*. Reclaiming it for "prior year" meant the second-measure
   use had to go.
3. And removing it exposed a third: **charts with two named series and no way
   to tell them apart.** `aov_conversion` carried the comment "Both are
   direct-labelled in the legend" with `showlegend=False` — a claim the code
   did not honour, on a chart with a left axis and a right axis.

Every test here was verified to fail with its fix reverted; the mutation is
named in each docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute  # noqa: E402
from kpi_maker.spec.schema import RunSpec  # noqa: E402
from kpi_maker.viz import charts as C  # noqa: E402
from kpi_maker.viz.theme import (  # noqa: E402
    SCENARIO_NOTATION,
    SCENARIO_TOKEN,
)

#: **Derived, not listed, and the first version of this file listed them.**
#: The hand-written tuple happened to omit `atlas_enterprise` — the €45M SaaS
#: company the hardcoded-dollar bug was found on — so the currency test stayed
#: green with the bug restored. A sample list that does not contain the case
#: is a test of nothing, and this is the fourth hardcoded shape in this
#: program to be replaced by the property it stood in for.
SAMPLES = tuple(sorted(p.stem for p in (ROOT / "samples").glob("*.json")
                       if p.name != "gallery.json"))


def _build(sample: str, spec: RunSpec = None):
    """Every exhibit this sample produces, with its currency set."""
    profile = load_profile(ROOT / "samples" / f"{sample}.json")
    spec = spec or RunSpec(profile=profile)
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)
    results = compute(select(profile), tables, profile, plan_spec=spec.plan)
    C.set_currency(profile.identity.currency)
    out = {}
    for entry in sorted(C.CHARTS.values(), key=lambda e: e.order):
        args = {"results": results, "tables": tables}
        try:
            built = entry.fn(*[args[t] for t in entry.takes])
        except Exception:                                 # noqa: BLE001
            continue
        if built is not None:
            out[built.id] = built
    return profile, out


@pytest.fixture(scope="module")
def exhibits():
    return {s: _build(s) for s in SAMPLES}


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------

def test_no_axis_states_a_currency_this_company_does_not_use(exhibits):
    """The bug that opened 5.2, on the company it was measured on.

    Mutation: `fig.update_yaxes(tickprefix="$", tickformat="~s")` back in any
    of the three charts, and the euro company's ARR axis reads in dollars
    while its tiles, PDF and workbook read in euros.
    """
    from kpi_maker.fmt import CURRENCY_SYMBOL

    others = set(CURRENCY_SYMBOL.values())
    for sample, (profile, built) in exhibits.items():
        mine = CURRENCY_SYMBOL[profile.identity.currency]
        wrong = others - {mine}
        for chart_id, spec in built.items():
            for axis in ("xaxis", "yaxis", "xaxis2", "yaxis2"):
                prefix = getattr(getattr(spec.figure.layout, axis, None),
                                 "tickprefix", None)
                assert prefix not in wrong, (
                    f"{sample}/{chart_id}: {axis} is prefixed {prefix!r} on a "
                    f"{profile.identity.currency} company")


def test_a_money_axis_uses_this_runs_symbol(exhibits):
    """And the positive half: where a currency axis exists, it is ours."""
    from kpi_maker.fmt import CURRENCY_SYMBOL

    seen = 0
    for sample, (profile, built) in exhibits.items():
        mine = CURRENCY_SYMBOL[profile.identity.currency]
        for spec in built.values():
            for axis in ("xaxis", "yaxis"):
                prefix = getattr(getattr(spec.figure.layout, axis, None),
                                 "tickprefix", None)
                if prefix:
                    assert prefix == mine, (sample, prefix, mine)
                    seen += 1
    assert seen, "no chart sets a currency prefix at all any more"


# --------------------------------------------------------------------------
# The notation itself
# --------------------------------------------------------------------------

def test_the_three_scenarios_are_distinguishable_without_colour():
    """A greyscale print and a colour-vision deficiency see the same chart.

    The same rule `STATUS_GLYPH` enforces for RAG. Actual must also be the
    only solid line and the heaviest, because it is what happened and it
    should read first.

    Mutation: give plan and prior the same dash, or make actual dashed.
    """
    dashes = {k: v["dash"] for k, v in SCENARIO_NOTATION.items()}
    assert len(set(dashes.values())) == len(dashes), dashes
    assert dashes["actual"] == "solid"
    assert [k for k, v in dashes.items() if v == "solid"] == ["actual"]

    widths = {k: v["width"] for k, v in SCENARIO_NOTATION.items()}
    assert widths["actual"] == max(widths.values())
    assert SCENARIO_NOTATION["prior"]["opacity"] < \
        SCENARIO_NOTATION["actual"]["opacity"]


def test_forecast_has_no_notation_because_nothing_draws_one():
    """IBCS names four scenarios and this engine produces three.

    A vocabulary for something no chart can draw is the dead-spec-field
    pattern in a stylesheet — 5.1 left `forecast` out of `PlanSpec` for the
    same reason, and the two must not drift apart.

    Mutation: add a `forecast` entry here without a producer.
    """
    assert set(SCENARIO_NOTATION) == {"actual", "plan", "prior"}
    assert set(SCENARIO_TOKEN) == set(SCENARIO_NOTATION)


def test_plan_and_prior_do_not_consume_categorical_series_slots():
    """`MAX_CATEGORICAL_SERIES` is 3, and a chart showing one metric three
    ways is showing one subject, not three."""
    from kpi_maker.viz.theme import MAX_CATEGORICAL_SERIES

    assert MAX_CATEGORICAL_SERIES == 3
    assert SCENARIO_TOKEN["plan"] == SCENARIO_TOKEN["prior"] == "deemphasis"
    assert SCENARIO_TOKEN["actual"].startswith("series_")


def test_dash_is_reserved_for_scenarios_and_width_one_chrome():
    """Reclaiming the vocabulary is the point of "Unify".

    Before 5.2, three charts drew a *second measure* dotted while dot was
    about to mean "prior year". Any dashed stroke left in a builder must now
    be either a scenario (drawn through `add_scenario`) or reference chrome,
    and chrome is identifiable: width 1.

    Mutation: restore `dash="dot"` on any second-measure series.
    """
    import re

    source = (ROOT / "kpi_maker" / "viz" / "charts.py").read_text(encoding="utf-8")
    # Only the executable lines; the module's own prose discusses the history.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith(("#", "*", '"')))
    for match in re.finditer(r"line=dict\([^)]*dash=\"[a-z]+\"[^)]*\)", code):
        clause = match.group(0)
        assert "width=1," in clause or "width=1)" in clause, (
            f"a dashed stroke that is not width-1 chrome: {clause}. "
            f"Scenarios go through add_scenario.")


# --------------------------------------------------------------------------
# The exhibit that draws it
# --------------------------------------------------------------------------

def test_the_plan_exhibit_appears_only_on_a_run_that_has_a_plan():
    """An exhibit is a stronger claim than a table cell, so 5.1's rule binds
    harder here: no plan, no chart.

    **The builder is called directly, not through the fixture**, and the first
    version of this test was not. `_build` swallows exceptions — it has to,
    because a SaaS exhibit asked for a retailer's tables raises `KeyError` and
    that is the normal cross-archetype path — so a mutation that made the
    builder *crash* also made the chart absent, and the test passed while
    asserting nothing about the rule.

    Mutation: fall back to the target when no plan is stated.
    """
    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    spec = RunSpec(profile=profile)
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)
    results = compute(select(profile), tables, profile, plan_spec=spec.plan)
    assert any(r.target is not None for r in results), \
        "the premise: targets exist, so falling back to one would draw a line"
    assert C.plan_vs_actual(results) is None

    planned_spec = RunSpec.model_validate_json(
        (ROOT / "samples" / "specs" / "kestrel_retail.json").read_text(
            encoding="utf-8"))
    planned = compute(select(profile), tables, profile,
                      plan_spec=planned_spec.plan)
    assert C.plan_vs_actual(planned) is not None


def test_the_plan_exhibit_draws_the_shared_notation():
    """The whole reason the notation is not just a stylesheet.

    Mutation: hand-roll the traces in the builder instead of calling
    `add_scenario`, and the dash patterns drift from the definition.
    """
    spec = RunSpec.model_validate_json(
        (ROOT / "samples" / "specs" / "kestrel_retail.json").read_text(
            encoding="utf-8"))
    _, built = _build("kestrel_retail", spec)
    fig = built["plan_vs_actual"].figure

    by_name = {t.name: t for t in fig.data}
    assert {"Actual", "Plan"} <= set(by_name), sorted(by_name)
    for name, scenario in (("Actual", "actual"), ("Plan", "plan"),
                           ("Prior year", "prior")):
        if name not in by_name:
            continue
        assert by_name[name].line.dash == SCENARIO_NOTATION[scenario]["dash"]
        assert by_name[name].line.width == SCENARIO_NOTATION[scenario]["width"]

    assert fig.layout.showlegend, "three lines and no legend"
    # Every trace maps to a token, or it does not restyle in dark mode.
    assert set(built["plan_vs_actual"].trace_tokens) == set(range(len(fig.data)))


def test_a_derived_path_is_never_labelled_plan_on_a_chart():
    """A legend entry reading "Plan" is a stronger claim than the scorecard's
    badge, because nothing else on the exhibit qualifies it.

    Mutation: use the notation's own label unconditionally.
    """
    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    spec = RunSpec(profile=profile)
    spec.plan.derive_from_target = True
    _, built = _build("kestrel_retail", spec)

    chart_spec = built["plan_vs_actual"]
    names = {t.name for t in chart_spec.figure.data}
    assert "Plan" not in names, names
    assert "Target path" in names, names
    assert "not a stated budget" in chart_spec.note.lower()


# --------------------------------------------------------------------------
# The rule that keeps coming back
# --------------------------------------------------------------------------

def test_every_multi_series_chart_says_which_line_is_which(exhibits):
    """Two named lines need a legend **or** direct labels — this has now been
    fixed three separate times on three separate charts.

    4.2b turned the legend on for the OEE exhibit and the utilisation pair.
    5.2 found `aov_conversion` carrying the comment "Both are direct-labelled
    in the legend" with `showlegend=False`, and `revenue_orders` relying on a
    dash that had to be reclaimed. A rule enforced chart by chart is how the
    next chart arrives without it, so this checks all of them.

    Direct labelling is a legitimate alternative and is not a defect:
    `indexed_growth` annotates each line at its right-hand end *because* its
    aqua slot carries a sub-3:1 contrast warning, which is a better answer
    than a legend. The rule is that the reader can tell, not that a legend
    exists.

    Mutation: `showlegend=False` on `aov_conversion` or `revenue_orders`.
    """
    unlabelled = []
    for sample, (_, built) in exhibits.items():
        for chart_id, spec in built.items():
            named = [t for t in spec.figure.data
                     if getattr(t, "name", None)
                     and t.type in ("scatter", "scattergl")]
            if len(named) < 2:
                continue
            annotations = {str(a.text) for a in (spec.figure.layout.annotations or ())}
            direct = all(any(t.name in text for text in annotations)
                         for t in named)
            if not spec.figure.layout.showlegend and not direct:
                unlabelled.append(
                    (sample, chart_id, [t.name for t in named]))
    assert not unlabelled, unlabelled
