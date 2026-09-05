"""The decomposition exhibit 3.4 deferred to Phase 5.

3.4 built `insight/decompose.py` and left the hard part solved: it computes
every bar of a waterfall, **and it computes whether the bars are entitled to
be a waterfall at all.** Measured before this item: `decompose` was read by
`detectors.py` and by nothing else — no chart drew it, which is the same
"computed and rendered nowhere" gap 4.3b closed for the archetype tables.

The distinction is the whole item. A waterfall asserts that the parts sum to
the whole:

* `kind == "contribution"` — measured to add up on this run's own numbers, so
  the waterfall is arithmetic.
* `kind == "dispersion"` — they do not, and a waterfall would be the lie 3.4
  spent an item refusing to tell in prose. Levels against the blend is what
  the numbers support.

Every test here was verified to fail with its fix reverted; the mutation is
named in each docstring.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.insight.decompose import decompose, worth_reporting  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute, dimensions  # noqa: E402
from kpi_maker.spec.schema import RunSpec  # noqa: E402
from kpi_maker.viz import charts as C  # noqa: E402

SAMPLES = ("northwind_saas", "kestrel_retail", "halberd_consulting",
           "orbis_works", "lumen_exchange")


@pytest.fixture(scope="module")
def runs():
    """Segmented results for every archetype, computed once."""
    out = {}
    for sample in SAMPLES:
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=profile)
        tables = dict(GENERATORS[spec.resolve_archetype()](
            profile, spec.source.generator).tables)
        results = compute(select(profile), tables, profile,
                          by=dimensions(tables))
        out[sample] = (profile, results)
    return out


def _built(profile, results):
    C.set_currency(profile.identity.currency)
    return C.decomposition(results)


# --------------------------------------------------------------------------
# It draws at all, on every archetype
# --------------------------------------------------------------------------

def test_every_archetype_gets_a_decomposition_exhibit(runs):
    """3.4's numbers reached a chart on all five, not just the one it was
    written against.

    Mutation: return None unconditionally.
    """
    for sample, (profile, results) in runs.items():
        spec = _built(profile, results)
        assert spec is not None, sample
        assert spec.figure.data, sample
        assert spec.title and spec.subtitle, sample


def test_the_exhibit_is_absent_when_there_is_nothing_to_decompose(runs):
    """A run with no segment dimension has no decomposition, and an exhibit
    is a stronger claim than a table cell.
    """
    profile, results = runs["northwind_saas"]
    stripped = []
    for r in results:
        copy = type(r)(**{**r.__dict__})
        copy.by_segment = {}
        stripped.append(copy)
    assert _built(profile, stripped) is None


def test_a_move_spread_evenly_across_segments_draws_nothing():
    """`worth_reporting` is the guard, and the first version of this test did
    not exercise it — it stripped `by_segment` entirely, so no candidate was
    produced at all and removing the guard changed nothing.

    Naming the largest of four near-identical contributors as *the* cause is
    picking a scapegoat out of noise, which is what the guard refuses.

    Mutation: drop `and worth_reporting(candidate)` from the builder.
    """
    import pandas as pd

    profile, results = _synthetic_segments(
        # Four segments, each contributing a quarter of the move: additive,
        # so a contribution — and no leader worth naming.
        {"a": (100.0, 125.0), "b": (100.0, 125.0),
         "c": (100.0, 125.0), "d": (100.0, 125.0)})
    found = decompose(results[0], "segment")
    assert found is not None and found.kind == "contribution", found
    assert not worth_reporting(found), "the premise: no segment owns the move"
    assert _built(profile, results) is None


def _synthetic_segments(spec: dict):
    """A single result whose segments are stated outright.

    The samples cannot produce every shape this exhibit has to handle — a
    perfectly even split, or a segment that did not exist a year ago — so
    those two are constructed rather than hoped for.
    """
    import pandas as pd

    index = pd.period_range("2024-01", periods=13, freq="M")
    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    base = compute(select(profile), dict(GENERATORS["saas"](
        profile, RunSpec(profile=profile).source.generator).tables), profile)
    template = next(r for r in base if r.computed and r.kpi.unit == "currency")

    by_segment = {}
    for name, (start, end) in spec.items():
        if start is None:
            values = [None] * 12 + [end]
        else:
            values = list(pd.Series([start, end]).reindex(
                range(13)).interpolate(limit_direction="both"))
            values = [start + (end - start) * i / 12 for i in range(13)]
        by_segment[name] = pd.Series(values, index=index, dtype="float64")

    blended = sum(s.fillna(0.0) for s in by_segment.values())
    result = type(template)(**{**template.__dict__})
    result.series = blended
    result.current = float(blended.iloc[-1])
    result.by_segment = {"segment": by_segment}
    return profile, [result]


def test_a_contribution_covers_every_segment_so_no_remainder_is_possible():
    """Why this exhibit needs no "all other" bar — asserted, not assumed.

    The first version drew one defensively. Constructing the case it was for
    — a segment that launched inside the window, with no reading a year ago —
    showed it can never fire: `is_additive` must hold at **both** ends for
    the kind to be `contribution`, and it returns False the moment a segment
    is missing a reading, which is the same condition that makes `decompose`
    drop that part. The result comes back as a *dispersion* instead, so there
    is no short waterfall to patch up.

    Dead defensive code is what this repo removes rather than keeps, and the
    invariant that replaced it is the one that makes the waterfall sound.
    """
    profile, results = _synthetic_segments(
        {"established": (100.0, 400.0), "launched": (None, 200.0)})
    found = decompose(results[0], "segment")
    assert found is not None
    assert found.kind == "dispersion", (
        "a segment missing an endpoint must not yield a contribution")

    # And on real data, every contribution's parts cover every segment.
    for sample in SAMPLES:
        p = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=p)
        tables = dict(GENERATORS[spec.resolve_archetype()](
            p, spec.source.generator).tables)
        for r in compute(select(p), tables, p, by=dimensions(tables)):
            for dimension in r.dimensions:
                d = decompose(r, dimension)
                if d is None or d.kind != "contribution":
                    continue
                assert len(d.parts) == len(r.by_segment[dimension]), \
                    (sample, r.kpi.id, dimension)
                assert sum(x.change for x in d.parts) == pytest.approx(
                    d.total_change, rel=1e-6), (sample, r.kpi.id)


# --------------------------------------------------------------------------
# A waterfall only when the parts really sum
# --------------------------------------------------------------------------

def test_a_contribution_waterfall_actually_adds_up(runs):
    """The claim a waterfall makes, checked against the bars it draws.

    Mutation: draw the parts without the opening bar, or drop the "All other"
    remainder — either makes the closing total disagree with the sum, which
    is exactly the fiction `is_additive` exists to prevent.
    """
    import plotly.graph_objects as go

    checked = 0
    for sample, (profile, results) in runs.items():
        spec = _built(profile, results)
        trace = spec.figure.data[0]
        if not isinstance(trace, go.Waterfall):
            continue
        checked += 1
        values = [float(v) for v in trace.y]
        measures = list(trace.measure)
        assert measures[0] == "absolute" and measures[-1] == "total", sample
        # Every intermediate bar is a step, and the closing label is the sum.
        assert set(measures[1:-1]) == {"relative"}, sample
        assert trace.text[-1], f"{sample}: the closing total is unlabelled"
        # The bars must reconstruct the metric's own current value.
        result = next(r for r in results if r.kpi.name in spec.title)
        assert sum(values) == pytest.approx(result.current, rel=1e-6), sample
    assert checked, "no sample produced a waterfall at all"


def test_a_dispersion_is_not_drawn_as_a_waterfall(runs):
    """The distinction the whole exhibit turns on.

    A rate does not sum across segments — 3.4 measured `nrr` by segment as
    dispersion for exactly this reason — so a waterfall of it would assert an
    arithmetic that is not there.

    Mutation: drop the `found.kind == "contribution"` branch and draw a
    waterfall unconditionally.
    """
    import plotly.graph_objects as go

    seen = 0
    for sample, (profile, results) in runs.items():
        for r in results:
            for dimension in r.dimensions:
                found = decompose(r, dimension)
                if found is None or not worth_reporting(found):
                    continue
                if found.kind != "dispersion":
                    continue
                spec = _built(profile, [r])
                if spec is None:
                    continue
                seen += 1
                assert not isinstance(spec.figure.data[0], go.Waterfall), \
                    f"{sample}/{r.kpi.id}: dispersion drawn as a waterfall"
                assert isinstance(spec.figure.data[0], go.Bar)
                # The blend has to be on the chart, not only in the subtitle.
                assert spec.figure.layout.shapes, r.kpi.id
                assert "not a waterfall" in spec.note.lower()
                break
    assert seen, "no dispersion decomposition was produced to check"


def test_the_dispersion_bars_are_ordered_so_the_outlier_is_findable(runs):
    """A spread chart in arbitrary order is a list, not a chart.

    Mutation: drop the `sorted(...)`.
    """
    import plotly.graph_objects as go

    for sample, (profile, results) in runs.items():
        for r in results:
            for dimension in r.dimensions:
                found = decompose(r, dimension)
                if (found is None or not worth_reporting(found)
                        or found.kind != "dispersion"):
                    continue
                spec = _built(profile, [r])
                if spec is None or not isinstance(spec.figure.data[0], go.Bar):
                    continue
                values = [float(v) for v in spec.figure.data[0].x]
                assert values == sorted(values), (sample, r.kpi.id, values)
                return
    pytest.skip("no dispersion decomposition on these samples")


# --------------------------------------------------------------------------
# What it says about itself
# --------------------------------------------------------------------------

def test_the_note_says_which_kind_it_is(runs):
    """A reader must be able to tell a summing waterfall from a spread, and
    the two make different promises.

    Mutation: give both branches the same note.
    """
    import plotly.graph_objects as go

    notes = {}
    for _sample, (profile, results) in runs.items():
        spec = _built(profile, results)
        kind = ("contribution" if isinstance(spec.figure.data[0], go.Waterfall)
                else "dispersion")
        notes.setdefault(kind, set()).add(spec.note)
    for kind, texts in notes.items():
        assert all(t.strip() for t in texts), kind
    if len(notes) == 2:
        assert notes["contribution"] != notes["dispersion"]


def test_both_waterfalls_label_their_closing_total(runs):
    """Found by looking: the opening bar carried a figure and the closing one
    — the number the whole exhibit builds to — was blank, so the reader was
    asked to squint at an axis. `arr_bridge` had the convention first and
    `decomposition` copied it before it was noticed; both are fixed.

    Mutation: `... if m != "total" else ""` in either builder.
    """
    profile, results = runs["northwind_saas"]
    C.set_currency(profile.identity.currency)
    spec = RunSpec(profile=profile)
    tables = dict(GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator).tables)

    bridge = C.arr_bridge(tables)
    assert bridge is not None
    trace = bridge.figure.data[0]
    assert trace.text[-1].strip(), "arr_bridge's closing total is unlabelled"

    built = _built(profile, results)
    if built.figure.data[0].type == "waterfall":
        assert built.figure.data[0].text[-1].strip()


# --------------------------------------------------------------------------
# Anomalies on the time axis (5.3b)
# --------------------------------------------------------------------------

def _anomaly(kind="thing", start=2, end=5, segment=""):
    from kpi_maker.datagen.base import Anomaly

    return Anomaly(kind=kind, start_month=start, end_month=end,
                   magnitude=0.2, description="planted", segment=segment)


def _month_figure(months=12):
    import plotly.graph_objects as go

    labels = [f"2025-{m:02d}" for m in range(1, 13)][:months]
    fig = go.Figure(go.Scatter(x=labels, y=list(range(len(labels))),
                               mode="lines", name="thing"))
    return fig, labels


def test_only_a_figure_whose_x_axis_is_months_gets_marked():
    """Derived from the figure rather than declared on `ChartSpec`.

    A `time_axis` flag would be one more thing twenty-odd builders have to
    remember and one more thing that can be wrong; whether the x values are
    `YYYY-MM` is a fact the figure already carries.

    Mutation: mark unconditionally, and every bar chart grows vertical rules
    through its categories.
    """
    import plotly.graph_objects as go

    fig, _ = _month_figure()
    assert C.mark_anomalies(fig, [_anomaly()]) == 1

    categorical = go.Figure(go.Bar(x=["apparel", "home", "toys"], y=[1, 2, 3]))
    assert C.mark_anomalies(categorical, [_anomaly()]) == 0
    assert not (categorical.layout.shapes or ())


def test_an_anomaly_running_past_the_window_is_marked_ongoing():
    """Measured on the samples before writing anything: **`end_month`
    routinely runs past the last reported month** — 36 on a 36-month window,
    26 on a 25-month one — because an anomaly still running when history ends
    is planted with an open end.

    Indexing it naively raises `IndexError`. Clamping it silently would say
    the problem is over. It is clamped *and* labelled.

    Mutation: drop the `ongoing` flag, or index without clamping.
    """
    fig, labels = _month_figure(months=12)
    assert C.mark_anomalies(fig, [_anomaly(kind="decay", start=6, end=99)]) == 1
    texts = [str(a.text) for a in fig.layout.annotations]
    assert any("ongoing" in t for t in texts), texts

    bounded = _month_figure()[0]
    C.mark_anomalies(bounded, [_anomaly(kind="spike", start=2, end=5)])
    assert not any("ongoing" in str(a.text) for a in bounded.layout.annotations)


def test_an_anomaly_wholly_outside_the_window_is_not_marked():
    """`to_reported` drops the ones that ended before the window; one that
    starts after it ends is the mirror case and must not draw a rule at the
    right-hand edge.

    The behaviour is asserted, not a particular guard — and that distinction
    mattered: the first version named "drop the `start > last` check" as its
    mutation and stayed green under it, because clamping such an event leaves
    `end < start` and the next line catches it anyway. Two guards where one
    decides, so the redundant one is gone.

    Mutation: `if end < start` -> `if False`.
    """
    fig, _ = _month_figure(months=12)
    assert C.mark_anomalies(fig, [_anomaly(start=40, end=50)]) == 0
    assert not (fig.layout.shapes or ())
    # And the mirror: an event that ended before the window opened.
    before = _month_figure(months=12)[0]
    assert C.mark_anomalies(before, [_anomaly(start=-9, end=-3)]) == 0


def test_the_marker_does_not_restyle_the_data_it_points_at():
    """**The first version shaded each anomaly's span**, and looking at it is
    the whole reason this is a rule and not a band: three events tinted about
    seventy per cent of a three-year ARR chart amber, and the tints
    *compounded* where events overlapped — so the darkest region was wherever
    the most bands coincided rather than wherever anything mattered.

    An annotation that restyles the data it points at, and whose emphasis is
    an artefact of overlap, is worse than no annotation.

    Mutation: `add_vrect(...)` instead of the line shape.
    """
    fig, _ = _month_figure()
    C.mark_anomalies(fig, [_anomaly(start=1, end=9), _anomaly(start=3, end=8)])
    shapes = fig.layout.shapes or ()
    assert shapes, "nothing was marked"
    assert all(s.type == "line" for s in shapes), [s.type for s in shapes]
    assert all(s.fillcolor in (None, "") for s in shapes)


def test_the_planted_events_reach_a_real_run(runs):
    """The gap this closes: the generator plants each anomaly with a written
    description, the appendix prints the prose, and until 5.3b nothing marked
    the months it referred to.

    Mutation: drop `anomalies=` from the `visualise` stage.
    """
    from kpi_maker.viz.charts import build_all

    for sample, (profile, results) in runs.items():
        spec = RunSpec(profile=profile)
        generated = GENERATORS[spec.resolve_archetype()](
            profile, spec.source.generator)
        assert generated.anomalies, sample
        tables = dict(generated.tables)

        plain = build_all(results, tables, currency=profile.identity.currency)
        marked = build_all(results, tables, currency=profile.identity.currency,
                           anomalies=generated.anomalies)
        added = sum(len(b.figure.layout.shapes or ()) - len(a.figure.layout.shapes or ())
                    for a, b in zip(plain, marked))
        assert added > 0, f"{sample}: no exhibit was marked at all"


def test_the_visualise_stage_declares_the_source_it_reads():
    """The anomalies live on the `source` stage's output, so `visualise` has
    to depend on it — otherwise a warm re-run that reused `visualise` while
    `source` rebuilt would show the previous run's events. The same cache-key
    discipline 5.1 applied to the plan section.

    Mutation: remove `"source"` from the stage's `needs`.
    """
    import kpi_maker.pipeline.stages  # noqa: F401  - registers the stages
    from kpi_maker.pipeline.graph import STAGES

    assert "source" in STAGES["visualise"].needs, STAGES["visualise"].needs


# --------------------------------------------------------------------------
# 5.3c — small multiples over `by_segment`
# --------------------------------------------------------------------------
#
# The last of Phase 3's "computed and rendered nowhere" outputs. 3.2 built
# `MetricResult.by_segment` and closed by naming its consumers; the waterfall
# arrived in 5.3a and this is the other one. Every test below was verified to
# fail with its fix reverted; the mutation is named in each docstring.


def _grid(profile, results):
    C.set_currency(profile.identity.currency)
    return C.segment_multiples(results)


def test_every_sample_gets_a_grid_of_at_least_two_panels(runs):
    """An exhibit nobody can produce is one nobody will look at. All five
    archetypes emit `segment_financials`, so all five have a cut to draw.

    Mutation: `if best is None: return None` -> build anyway.
    """
    for sample, (profile, results) in runs.items():
        spec = _grid(profile, results)
        assert spec is not None, f"{sample}: no small multiples at all"
        panels = sum(1 for k in spec.figure.layout if k.startswith("xaxis"))
        assert panels >= 2, f"{sample}: {panels} panel(s) is not a comparison"


def test_a_cut_with_one_level_is_not_a_comparison(runs):
    """A one-panel "small multiple" is a chart whose whole argument — this
    panel against that one — is missing.

    **Two earlier versions of this test were green under their own
    mutation**, and both were the premise trap this repo keeps hitting.
    Relaxing the minimum to 1 changes nothing on any sample, because the
    widest cut wins and every sample has one with several levels — that
    version was grading the samples, not the rule. Cutting each dimension
    down to a single level does not reach the guard either, because
    `MetricResult.dimensions` already drops a cut with one level, so the
    builder never sees it.

    The case the guard is actually for is narrower than either: a cut with
    two levels where only one of them has enough history to draw. That is a
    segment launched inside the reported window, and it has to be
    constructed.

    Mutation: `if len(usable) < 2: continue` -> `< 1`.
    """
    profile, results = runs["kestrel_retail"]
    narrowed = []
    for r in results:
        clone = copy.copy(r)
        cut = {}
        for dim, levels in (r.by_segment or {}).items():
            names = list(levels)[:2]
            if len(names) < 2:
                continue
            # The second segment launched three months ago: present, real,
            # and too short to put beside a three-year panel.
            cut[dim] = {names[0]: levels[names[0]],
                        names[1]: levels[names[1]].iloc[-3:]}
        clone.by_segment = cut
        narrowed.append(clone)
    assert any(c.dimensions for c in narrowed), "the builder sees no cut"
    assert C.segment_multiples(narrowed) is None


def test_the_widest_cut_of_the_most_senior_kpi_wins(runs):
    """More panels means the blended figure was hiding more, so the widest
    cut wins; tier orders the KPIs so the exhibit is about something a board
    reads. Measured: northwind draws NRR by segment — the sample's own stated
    story, "churn concentrated in one segment the blended number hides".

    Mutation: `key = (-len(usable), dimension)` -> `(len(usable), dimension)`,
    and the narrowest cut wins instead.
    """
    profile, results = runs["kestrel_retail"]
    spec = _grid(profile, results)
    panels = sum(1 for k in spec.figure.layout if k.startswith("xaxis"))

    # The retailer's channel cut has five levels and its category cut four,
    # so a narrowest-first rule would draw four panels rather than five.
    by_id = {r.kpi.id: r for r in results}
    widest = max(
        (len({n for n, s in (r.by_segment.get(d) or {}).items()
              if s is not None and s.dropna().size >= 6})
         for r in results if r.computed and r.series is not None
         for d in r.dimensions),
        default=0)
    assert panels == widest, (panels, widest)
    assert by_id  # the run really did compute something to choose among


def test_the_company_line_appears_only_when_it_is_comparable_to_the_parts(runs):
    """**Looking at the first version is why this rule exists.** For an
    additive metric — net revenue by channel — the company total is by
    definition larger than every segment, so putting it on the shared scale
    pushed all five channels into the bottom fifth of their panels and they
    read as five identical flat lines. The exhibit exists to make differences
    visible and it was hiding them.

    Whether the parts sum to the whole is not guessed from the unit: it is
    `decompose.is_additive`, measured against this run's own numbers and
    reused rather than reimplemented.

    Mutations: `show_blend = True` unconditionally (the retailer flattens),
    and `show_blend = False` unconditionally (NRR loses the reference that is
    the most useful thing on the chart).
    """
    def blend_traces(spec):
        return [t for t in spec.figure.data if t.name == "Company"]

    additive = _grid(*runs["kestrel_retail"])
    assert additive.title.startswith("Net Revenue by channel"), additive.title
    assert not blend_traces(additive), "an additive metric drew the blend"
    assert "left off" in additive.note

    ratio = _grid(*runs["northwind_saas"])
    assert "Retention" in ratio.title, ratio.title
    panels = sum(1 for k in ratio.figure.layout if k.startswith("xaxis"))
    assert len(blend_traces(ratio)) == panels, "the blend is not in every panel"
    assert "company as a whole" in ratio.note


def test_the_panels_share_one_y_scale(runs):
    """Panels on independent scales look alike whatever the data does, which
    is the failure mode of every small-multiple grid drawn without thinking.
    Sharing the scale is what makes "this one is different" visible at all —
    and it is what makes the additivity rule above necessary.

    Mutation: `shared_yaxes=True` -> `False`.
    """
    for sample, (profile, results) in runs.items():
        spec = _grid(profile, results)
        others = [k for k in spec.figure.layout
                  if k.startswith("yaxis") and k != "yaxis"]
        assert others, sample
        for key in others:
            assert spec.figure.layout[key].matches == "y", f"{sample}: {key}"


def test_every_panel_carries_the_same_axis_chrome(runs):
    """`update_layout(xaxis=..., yaxis=...)` styles **axis 1 only**, so
    `_base_layout`'s chrome stopped at the first panel: panels 2..n kept
    Plotly's default black `zeroline`, and the consultancy's revenue grid drew
    a heavy rule under three of its four panels and not the fourth. Found by
    looking.

    Mutation: `_base_layout` back to `update_layout(xaxis=..., yaxis=...)`.
    """
    for sample, (profile, results) in runs.items():
        fig = _grid(profile, results).figure
        for key in [k for k in fig.layout if k.startswith("yaxis")]:
            assert fig.layout[key].zeroline is False, f"{sample}: {key}"
        for key in [k for k in fig.layout if k.startswith("xaxis")]:
            assert fig.layout[key].showgrid is False, f"{sample}: {key}"
            assert fig.layout[key].showticklabels is False, f"{sample}: {key}"


def test_the_panel_titles_have_room_above_the_plot(runs):
    """`_base_layout`'s 8px top margin is right for a chart whose top edge is
    its plotting area and wrong for a grid, where the panel titles live above
    it — at 8px they were sheared off by the dashboard card and ran into the
    exhibit's own subtitle. Invisible in the PNG, where the figure has the
    page to itself.

    Mutation: drop the `margin=dict(..., t=26, ...)` override.
    """
    for sample, (profile, results) in runs.items():
        fig = _grid(profile, results).figure
        titles = [a for a in fig.layout.annotations if a.yref == "paper"]
        assert titles, sample
        assert fig.layout.margin.t > 20, f"{sample}: t={fig.layout.margin.t}"


def test_a_grid_of_panels_is_not_anomaly_marked(runs):
    """Every shape in `mark_anomalies` is anchored to `xref="x"`, which on a
    subplot figure is panel 1 and nothing else — so the first version put all
    three of the retailer's events into the "email" panel and none into the
    other four. Drawing the rule in every panel fixes the placement and
    leaves the harder half: `return spike · apparel` is wider than a 240px
    panel, a categorical axis autoranges to fit an annotation, and panel 1's
    range came out roughly doubled with its series squeezed into the left
    half of its own panel.

    Both halves are asserted together, so this cannot pass by the anomalies
    simply being absent from the run.

    Mutation: remove the multi-axis guard.
    """
    profile, results = runs["kestrel_retail"]
    spec = RunSpec(profile=profile)
    generated = GENERATORS[spec.resolve_archetype()](
        profile, spec.source.generator)
    anomalies = generated.anomalies
    assert anomalies

    grid = _grid(profile, results)
    assert C.mark_anomalies(grid.figure, anomalies) == 0
    assert not (grid.figure.layout.shapes or ())

    # The same events, the same run, on an ordinary single-panel time axis.
    single = C.revenue_and_orders(dict(generated.tables))
    assert C.mark_anomalies(single.figure, anomalies) > 0


def test_the_subtitle_names_the_two_ends_of_the_spread(runs):
    """A grid without a stated finding leaves the reader to do the ranking.
    The subtitle names the highest and lowest segment at the last reported
    month, in the run's own currency — which is also what says the exhibit
    read the numbers rather than only drew them.

    Mutation: `low = min(latest, key=latest.get)` -> `max`, so the subtitle
    compares a segment against itself.
    """
    for sample, (profile, results) in runs.items():
        spec = _grid(profile, results)
        assert " against " in spec.subtitle, f"{sample}: {spec.subtitle}"
        high, low = spec.subtitle.split(" against ")
        assert high.strip() and low.strip()
        assert high.split()[0] != low.split()[0], f"{sample}: {spec.subtitle}"


def test_a_run_with_nothing_sliceable_draws_no_grid(runs):
    """An exhibit is a stronger claim than a table cell. With no segmented
    series there is nothing to compare, and a one-panel grid would be a chart
    whose whole argument is missing.

    Called directly rather than through `build_all`, which swallows the
    `KeyError` a cross-archetype exhibit raises — a mutation that made the
    builder *crash* would otherwise look like a chart correctly absent. The
    same trap 5.2's "no plan, no chart" test fell into.

    Mutation: `if best is None: return None` -> build anyway.
    """
    profile, results = runs["northwind_saas"]
    stripped = []
    for r in results:
        clone = copy.copy(r)
        clone.by_segment = {}
        stripped.append(clone)
    assert C.segment_multiples(stripped) is None
