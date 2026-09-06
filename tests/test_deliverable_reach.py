"""Does a board pack present what the run computed, or a list somebody typed?

5.4's brief said `DIAGNOSTIC_EXHIBITS = ("arr_bridge", "cohort_heatmap")` is
"hardcoded SaaS". Measuring first showed the reach was the whole deck, and the
numbers are the item:

    sample                charts built    in the deck    slides
    northwind_saas             12              9           15
    kestrel_retail              8              1            7
    halberd_consulting          7              1            7
    lumen_exchange              7              1            7
    orbis_works                 7              1            7

`deck.EXHIBIT_PLAN` was nine subscription chart ids, so a factory that
computes an OEE trend, capacity headroom, scrap by family, a decomposition,
small multiples and a customer Pareto presented **one** chart — the benchmark
bars — on the artifact you stand up in front of a board. And the report's
`diagnostic` section rendered a paragraph about an ARR bridge and cohort
retention with no exhibits under it at all.

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
from kpi_maker.insight.detectors import detect_all  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute, dimensions  # noqa: E402
from kpi_maker.render.deck import exhibit_plan  # noqa: E402
from kpi_maker.render.sections import (  # noqa: E402
    REGISTRY,
    SectionContext,
    diagnostic_exhibits,
)
from kpi_maker.spec.schema import RunSpec  # noqa: E402
from kpi_maker.viz.charts import build_all  # noqa: E402

SAMPLES = ("northwind_saas", "kestrel_retail", "halberd_consulting",
           "orbis_works", "lumen_exchange")


@pytest.fixture(scope="module")
def runs():
    out = {}
    for sample in SAMPLES:
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=profile)
        generated = GENERATORS[spec.resolve_archetype()](profile)
        tables = dict(generated.tables)
        kpi_set = select(profile)
        results = compute(kpi_set, tables, profile, by=dimensions(tables))
        specs = build_all(results, tables, currency=profile.identity.currency)
        findings = detect_all(results, tables, profile)
        out[sample] = (profile, kpi_set, results, findings, specs)
    return out


def _context(sample, runs) -> SectionContext:
    profile, kpi_set, results, findings, specs = runs[sample]
    return SectionContext(
        profile=profile, kpi_set=kpi_set, results=results, findings=findings,
        specs=specs,
        # The images a real run would have exported: every spec it built.
        images={s.id: b"png" for s in specs},
    )


def test_every_chart_a_run_builds_reaches_the_deck(runs):
    """A chart that is computed, rendered to PNG, shipped in the artifact
    bundle and then left out of the presentation is the "computed and
    rendered nowhere" gap one step further along.

    Mutation: put the hardcoded nine-id `EXHIBIT_PLAN` back and iterate that.
    """
    for sample in SAMPLES:
        _, _, _, _, specs = runs[sample]
        planned = {spec_id for spec_id, _, _, _ in exhibit_plan(specs)}
        built = {s.id for s in specs}
        assert planned == built, (
            f"{sample}: {sorted(built - planned)} were built and not presented")


def test_the_subscription_deck_was_not_whole_either(runs):
    """**The hardcoded list had been forgotten on the archetype it was written
    for.** northwind gained three exhibits — `decomposition`,
    `segment_multiples` and `customer_pareto`, every chart 5.3 added — none of
    which anyone thought to add to a central registry of what to present.

    A place to list things is a place to forget them, which is why the run's
    own charts are the plan now.

    Mutation: restore the nine-id list.
    """
    _, _, _, _, specs = runs["northwind_saas"]
    planned = {spec_id for spec_id, _, _, _ in exhibit_plan(specs)}
    for late in ("decomposition", "segment_multiples", "customer_pareto"):
        assert late in planned, late


def test_a_chart_with_no_hint_still_lands_in_a_section_with_a_kicker(runs):
    """The derived half has to produce a usable row, not just an id: the deck
    slide needs a section that exists and a kicker to print.

    **The fallback needs a constructed case**, and the first version of this
    test did not have one: every chart the samples build declares a tab that
    `_KICKER` knows, so blanking the default changed nothing and the test was
    grading the shipped tab names rather than the rule. A chart added later
    with a new tab is exactly what the default is for.

    Mutation: return an empty kicker for an unknown tab, or a section name no
    renderer knows.
    """
    from types import SimpleNamespace

    from kpi_maker.render.deck import EXHIBIT_SECTIONS

    for sample in SAMPLES:
        _, _, _, _, specs = runs[sample]
        for spec_id, _kpi, kicker, section in exhibit_plan(specs):
            assert kicker and kicker.strip(), f"{sample}/{spec_id}: no kicker"
            assert section in EXHIBIT_SECTIONS, f"{sample}/{spec_id}: {section}"

    # A chart whose tab nobody has mapped yet still has to be presentable.
    stranger = SimpleNamespace(id="a_new_exhibit", tab="a_tab_nobody_mapped")
    (_, _, kicker, section), = exhibit_plan([stranger])
    assert kicker and kicker.strip(), "an unmapped tab yields no kicker"
    assert section in EXHIBIT_SECTIONS, section


def test_the_diagnostic_section_shows_what_this_run_drew(runs):
    """Four of five archetypes shipped a section headed "Diagnostic" whose
    whole content was a paragraph about an ARR bridge and cohort retention,
    followed by nothing — prose in another business model's vocabulary
    explaining two charts the reader cannot see.

    Every archetype builds a decomposition of its most senior sliceable KPI,
    so there is always something to put there.

    Mutation: return the two subscription exhibits unconditionally.
    """
    saas = diagnostic_exhibits(_context("northwind_saas", runs))
    assert {e.id for e in saas} == {"arr_bridge", "cohort_heatmap"}

    for sample in ("kestrel_retail", "halberd_consulting", "orbis_works",
                   "lumen_exchange"):
        drawn = diagnostic_exhibits(_context(sample, runs))
        assert drawn, f"{sample}: the Diagnostic section is empty"
        _, _, _, _, specs = runs[sample]
        built = {s.id for s in specs}
        for exhibit in drawn:
            assert exhibit.id in built, (
                f"{sample}: Diagnostic promises {exhibit.id}, which this run "
                f"did not draw")


def test_the_deep_dives_skip_exactly_what_the_diagnostic_took(runs):
    """`DIAGNOSTIC_EXHIBITS` was a module constant, so `_deep_dives` skipped
    two subscription ids it never had — and once the diagnostic section
    started taking the decomposition, that same chart would have appeared in
    both sections of the same report.

    One function decides ownership and both sections ask it.

    Mutation: skip `DIAGNOSTIC_EXHIBITS` in `_deep_dives` again.
    """
    for sample in SAMPLES:
        ctx = _context(sample, runs)
        owned = {e.id for e in diagnostic_exhibits(ctx)}
        deep = REGISTRY["deep_dives"].build(ctx)
        shown = [e.id for e in deep.exhibits]
        assert len(shown) == len(set(shown)), f"{sample}: {shown}"
        assert not (owned & set(shown)), (
            f"{sample}: {sorted(owned & set(shown))} is in both sections")
