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


# --------------------------------------------------------------------------
# 5.4b — message-driven titles, exec-summary basis, contents page
# --------------------------------------------------------------------------


def test_one_matcher_finds_the_finding_an_exhibit_is_about(runs):
    """**There were two rules and both were mostly missing.** `deck.py`
    matched a hand-written KPI id per chart; `_deep_dives` matched by
    spelling — `finding.id.endswith(spec.id) or spec.id in finding.id`.

    Measured, the spelling rule found a finding for **3 of 12** exhibits on
    the subscription run and **0 of 7** on the marketplace, and it missed
    `decomposition` on every archetype: "decomp_net_revenue_channel" neither
    ends with nor contains "decomposition". The PDF printed exhibits with no
    observation while the sentence about them sat in `findings.json`.

    `ChartSpec.about` names the KPI ids an exhibit draws, so the match is
    exact. Across the samples this went from 6 of 41 charts to 25.

    Mutation: drop the `about` branch and leave only the spelling fallback.
    """
    from kpi_maker.render.sections import finding_for_exhibit

    total = matched = 0
    for sample in SAMPLES:
        _, _, _, findings, specs = runs[sample]
        used: set = set()
        hits = [s for s in specs if finding_for_exhibit(s, findings, used)]
        total += len(specs)
        matched += len(hits)
    assert matched >= 20, f"only {matched} of {total} exhibits found a finding"


def test_a_finding_headlines_at_most_one_exhibit(runs):
    """The decomposition and the small multiples are about the same KPI by
    construction — one splits its move, the other draws its segments — so a
    real dashboard showed both under the identical headline, "direct drove
    most of the move in Revenue", twice in a row. Two adjacent cards saying
    the same thing is worse than the descriptive titles they replaced.

    Mutation: drop the `used` set, or stop passing it.
    """
    from kpi_maker.render.sections import finding_for_exhibit

    for sample in SAMPLES:
        _, _, _, findings, specs = runs[sample]
        used: set = set()
        got = [f.id for s in specs
               if (f := finding_for_exhibit(s, findings, used))]
        assert len(got) == len(set(got)), f"{sample}: {got}"


def test_a_sliced_exhibit_only_takes_a_finding_about_its_own_cut(runs):
    """A headline naming something the reader cannot find below it is worse
    than a descriptive one. Matching on the KPI alone put "premium drove most
    of the move in Revenue" — a product family — over a factory's *channel*
    small multiples.

    Mutation: drop the `slice_of` check.
    """
    from kpi_maker.render.sections import finding_for_exhibit

    for sample in SAMPLES:
        _, _, _, findings, specs = runs[sample]
        for spec in specs:
            cut = getattr(spec, "dimension", "")
            if not cut:
                continue
            found = finding_for_exhibit(spec, findings)
            if found is not None:
                assert found.id.endswith(f"_{cut}"), (
                    f"{sample}/{spec.id} draws {cut} and is headlined by "
                    f"{found.id}")


def test_no_exhibit_claims_a_kpi_that_does_not_exist():
    """An `about` naming a KPI id nothing defines never matches, and never
    says so — the silent-no-match shape of 4.3b's `serves_objectives`, which
    had eight entries worth nothing at the heaviest scoring weight.

    Checked against every pack, because a chart is archetype-specific and its
    KPI lives in that archetype's pack rather than the one a test happens to
    load.

    Mutation: misspell any id in any `about=`.
    """
    import re

    library = set()
    for path in (ROOT / "kpi_maker/kpi/library").glob("*.yaml"):
        library |= set(re.findall(r"^- id: ([a-z0-9_]+)", path.read_text(
            encoding="utf-8"), re.M))
    assert len(library) > 100, len(library)

    source = (ROOT / "kpi_maker/viz/charts.py").read_text(encoding="utf-8")
    named = set(re.findall(r'about=\(([^)]*)\)', source))
    ids = {i for group in named for i in re.findall(r'"([a-z0-9_]+)"', group)}
    assert ids, "no exhibit declares what it is about"
    assert not (ids - library), f"exhibits name unknown KPIs: {sorted(ids - library)}"


def test_the_exec_summary_says_when_a_statement_is_not_measured():
    """A reader who takes one page away should not have to go to the
    scorecard to learn that the number in the top bullet came from the
    generator filling a gap in their upload.

    Quiet when everything was measured, on `_basis_badge`'s reasoning — which
    is why this is constructed rather than measured off a sample: every
    figure in a synthetic run is `measured`, so the feature is invisible
    there and a sample-based test would assert nothing.

    The **weakest** basis wins: a bullet standing on one measured KPI and one
    the generator supplied is not a measured statement, and reporting the
    better of the two is the direction that misleads.

    Mutation: return the first basis rather than the weakest, or drop the
    field.
    """
    from kpi_maker.insight.detectors import Finding
    from kpi_maker.render.sections import _finding_basis

    finding = Finding(id="x", severity="high", title="t", statement="s",
                      kpi_ids=["a", "b"])
    assert _finding_basis(finding, {"a": "measured", "b": "measured"}) == ""
    assert _finding_basis(finding, {"a": "measured", "b": "modelled"}) == "modelled"
    assert _finding_basis(finding, {"a": "mixed", "b": "measured"}) == "mixed"
    assert _finding_basis(finding, {"a": "mixed", "b": "modelled"}) == "modelled"
    # A KPI the run never computed contributes nothing rather than "".
    assert _finding_basis(finding, {}) == ""


def test_the_report_has_a_contents_page(tmp_path):
    """A sixteen-page board pack with no way in: nine numbered sections and
    no table of contents anywhere, so a reader wanting the benchmarks thumbed
    through the deep dives.

    Asserted on a rendered PDF rather than on the code, because the page
    numbers are the part that can be wrong — a contents page pointing at the
    wrong pages is worse than none, and `insert_toc_placeholder`'s two-pass
    rendering is what makes them right.

    Mutations: remove the placeholder; render the ToC before the cover; drop
    the `_page_untouched` reuse and a blank page returns.
    """
    import re

    import pypdf

    from kpi_maker.cli import run_pipeline

    profile = load_profile(ROOT / "samples" / "orbis_works.json")
    run_pipeline(profile, tmp_path, quiet=True)
    reader = pypdf.PdfReader(str(tmp_path / "report.pdf"))

    contents = reader.pages[1].extract_text() or ""
    assert "Contents" in contents, contents[:200]

    listed = dict(re.findall(r"(\d+)\.\s+([A-Za-z][A-Za-z \-]+?)\s*\.{3,}\s*(\d+)",
                             contents) and
                  [(m[1].strip(), int(m[2])) for m in
                   re.findall(r"(\d+)\.\s+([A-Za-z][A-Za-z \-]+?)\s*\.{3,}\s*(\d+)",
                              contents)])
    assert len(listed) >= 6, listed

    # Every number points at the page whose heading it names — the thing a
    # hand-built contents page gets wrong.
    for title, page in listed.items():
        text = reader.pages[page - 1].extract_text() or ""
        assert title.lower() in text.lower(), (
            f"contents sends {title!r} to page {page}, which does not carry it")

    # And no blank page between the contents and the first section.
    third = (reader.pages[2].extract_text() or "")
    assert len(third.strip()) > 120, f"page 3 looks blank: {third!r}"
