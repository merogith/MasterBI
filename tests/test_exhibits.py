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
