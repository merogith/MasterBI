"""Ranking findings, and saying which part of the business moved the number.

Four things, and the first two are the ones a reader notices.

**Ordering was `(severity, id)`** — alphabetical by detector id inside the band
that matters, which is to say arbitrary. A reader takes the top three findings
seriously and skims the rest, so the biggest miss and the smallest one sat
wherever their ids happened to fall.

**"Churn is up" was all the engine could say.** `driver_decomposition` turns
that into "apparel accounts for 43% of the move", and it could not be written
before 3.2 gave every KPI a per-segment series and 3.3 gave the KPIs a graph.

**An e-commerce run got three working detectors** of eight. Five of ten now,
and the two that will never apply say why instead of failing: an ARR bridge is
a statement about a subscription book, and a retailer does not have one.
"Needs the mrr_movements table" invited someone to go and find a file that does
not exist for their business.

**Concentration was authored, not computed.** `atlas_enterprise`'s whole story
is customer concentration and it was written into the sample's profile. HHI on
`segment_financials` computes it for any archetype that emits one.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.insight.decompose import (  # noqa: E402
    decompose,
    is_additive,
    worth_reporting,
)
from kpi_maker.insight.detectors import (  # noqa: E402
    DETECTOR_NAMES,
    Finding,
    _concentration,
    detect_all,
)
from kpi_maker.insight.ranking import magnitude, rank_all, recency, score  # noqa: E402
from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.metrics.engine import compute, dimensions  # noqa: E402
from kpi_maker.spec.schema import RunSpec  # noqa: E402

ARCHETYPES = {
    "saas": ROOT / "samples" / "northwind_saas.json",
    "ecommerce": ROOT / "samples" / "kestrel_retail.json",
}


@pytest.fixture(scope="module", params=sorted(ARCHETYPES))
def run(request):
    archetype = request.param
    profile = load_profile(ARCHETYPES[archetype])
    tables = dict(GENERATORS[archetype](profile).tables)
    results = compute(select(profile), tables, profile, by=dimensions(tables))
    return archetype, profile, tables, results


def _finding(fid: str, severity: str = "medium", **evidence) -> Finding:
    return Finding(id=fid, severity=severity, title=fid, statement="",
                   evidence=evidence, kpi_ids=[fid])


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def test_severity_still_dominates_magnitude():
    """A large "medium" must never outrank a small "critical".

    Severity is the detector's own judgement, made knowing the metric. The
    score sorts *within* it rather than against it, and a scorer that could
    invert the bands would be worse than no scorer.
    """
    big_medium = _finding("m", "medium", current=100.0, threshold=1.0)
    small_critical = _finding("c", "critical", current=1.01, threshold=1.0)
    ordered = rank_all([big_medium, small_critical])
    assert [f.id for f in ordered] == ["c", "m"]


def test_a_bigger_miss_outranks_a_smaller_one_in_the_same_band():
    """The thing arbitrary ordering could not do."""
    small = _finding("aaa_small", "high", current=1.05, threshold=1.0)
    large = _finding("zzz_large", "high", current=2.00, threshold=1.0)
    # `aaa` sorts first alphabetically, which is exactly how the old ordering
    # would have put the smaller miss on top.
    assert [f.id for f in rank_all([small, large])] == ["zzz_large", "aaa_small"]


def test_a_pinned_kpi_is_lifted():
    a = _finding("a", "medium", current=1.1, threshold=1.0)
    b = _finding("b", "medium", current=1.1, threshold=1.0)
    assert [f.id for f in rank_all([a, b], watched={"b"})] == ["b", "a"]


def test_an_old_event_fades():
    recent_event = _finding("recent", "high", current=2.0, threshold=1.0)
    old_event = _finding("old", "high", current=2.0, threshold=1.0)
    old_event.months_ago = 24
    assert [f.id for f in rank_all([recent_event, old_event])] == ["recent", "old"]
    assert recency(None) == 1.0, \
        "a finding about the current state must not be penalised for saying so"


def test_magnitude_needs_both_numbers_and_never_guesses():
    assert magnitude({}) == 0.0
    assert magnitude({"current": 5.0}) == 0.0, \
        "a magnitude was invented from one number"
    assert magnitude({"current": 2.0, "threshold": 1.0}) == pytest.approx(1.0)
    # Clamped: a metric that moved forty-fold must not own the list forever.
    assert magnitude({"current": 40.0, "threshold": 1.0}) == 1.0


def test_the_order_is_stable_between_runs():
    """`tests/spine.py` compares artifacts byte for byte, so an unstable sort
    would make every re-run look like a change."""
    findings = [_finding(f"f{i}", "high", current=1.0, threshold=1.0)
                for i in range(8)]
    first = [f.id for f in rank_all(list(findings))]
    second = [f.id for f in rank_all(list(reversed(findings)))]
    assert first == second


def test_every_finding_a_real_run_produces_carries_a_score(run):
    _archetype, profile, tables, results = run
    findings = detect_all(results, tables, profile)
    assert findings
    assert all(f.score > 0 for f in findings)
    scores = [(f.rank, -f.score) for f in findings]
    assert scores == sorted(scores), "the run's findings are not in ranked order"


# --------------------------------------------------------------------------
# Decomposition
# --------------------------------------------------------------------------

@dataclass
class _Kpi:
    id: str = "k"
    name: str = "K"
    unit: str = "count"

    class _Direction:
        value = "higher_is_better"
    direction = _Direction()

    class _Tier:
        value = 1
    tier = _Tier()


@dataclass
class _Result:
    kpi: _Kpi = field(default_factory=_Kpi)
    series: Optional[pd.Series] = None
    current: Optional[float] = None
    computed: bool = True
    by_segment: Dict[str, Dict[str, pd.Series]] = field(default_factory=dict)

    @property
    def dimensions(self) -> List[str]:
        return [d for d, levels in self.by_segment.items() if len(levels) > 1]

    @property
    def segmented(self) -> bool:
        return bool(self.dimensions)


def _series(values) -> pd.Series:
    return pd.Series(values, index=pd.period_range("2023-01", periods=len(values),
                                                   freq="M"))


def test_additivity_is_measured_not_assumed():
    """A metric's unit cannot say whether it sums: a "currency" figure may be a
    total or an average, and treating an average as a total produces a
    contribution sentence that is quietly wrong."""
    assert is_additive(10.0, {"a": 6.0, "b": 4.0})
    assert not is_additive(10.0, {"a": 6.0, "b": 6.0})
    assert not is_additive(10.0, {"a": 6.0, "b": None}), \
        "a missing part must not be treated as zero"
    assert not is_additive(None, {"a": 1.0})


def test_parts_that_sum_produce_a_contribution_claim():
    months = 13
    result = _Result(
        series=_series([100.0 + i * 10 for i in range(months)]),
        current=220.0,
        by_segment={"segment": {
            "big": _series([60.0 + i * 9 for i in range(months)]),
            "small": _series([40.0 + i for i in range(months)]),
        }})
    found = decompose(result, "segment")
    assert found is not None and found.kind == "contribution"
    assert found.leader.segment == "big"
    assert found.leader.share_of_move == pytest.approx(0.9, rel=1e-6)
    assert worth_reporting(found)


def test_parts_that_do_not_sum_produce_a_dispersion_claim_only():
    """A rate. No contribution arithmetic is attempted, and `share_of_move`
    stays zero rather than carrying a number nobody may use."""
    months = 13
    result = _Result(
        series=_series([1.00 + i * 0.01 for i in range(months)]),
        current=1.12,
        by_segment={"segment": {
            "good": _series([1.20 + i * 0.01 for i in range(months)]),
            "bad": _series([0.80 + i * 0.01 for i in range(months)]),
        }})
    found = decompose(result, "segment")
    assert found is not None and found.kind == "dispersion"
    assert all(part.share_of_move == 0.0 for part in found.parts)


def test_a_move_spread_evenly_is_not_a_cause():
    """Naming the largest of five near-identical contributors as *the* cause
    would be picking a scapegoat out of noise."""
    months = 13
    even = {name: _series([20.0 + i * 2 for i in range(months)])
            for name in ("a", "b", "c", "d", "e")}
    result = _Result(series=_series([100.0 + i * 10 for i in range(months)]),
                     current=220.0, by_segment={"segment": even})
    found = decompose(result, "segment")
    assert found is not None
    assert not worth_reporting(found), \
        "one of five equal contributors was named as the cause"


def test_a_move_too_small_to_discuss_is_skipped():
    months = 13
    flat = _Result(
        series=_series([100.0 + i * 0.01 for i in range(months)]),
        current=100.12,
        by_segment={"segment": {
            "a": _series([60.0] * months), "b": _series([40.0] * months)}})
    assert decompose(flat, "segment") is None


def test_the_detector_says_which_part_moved_the_number(run):
    archetype, profile, tables, results = run
    findings = detect_all(results, tables, profile)
    decomps = [f for f in findings if f.id.startswith("decomp_")]
    assert decomps, f"{archetype} produced no decomposition at all"

    prose = " ".join(f.statement for f in decomps)
    assert "accounts for" in prose or "blended" in prose, prose[:300]


def test_a_dispersion_is_judged_against_the_blend_not_its_own_move(run):
    """NRR at 81% against a 103% blend is a problem however the segment moved.

    Using one severity test for both kinds of finding labelled exactly that
    case `positive`.
    """
    archetype, profile, tables, results = run
    if archetype != "saas":
        pytest.skip("the retention story belongs to the subscription sample")

    findings = {f.id: f for f in detect_all(results, tables, profile)}
    nrr = findings.get("decomp_nrr_segment")
    assert nrr is not None, "NRR was not decomposed"
    assert nrr.severity in ("high", "critical", "medium"), \
        f"a segment far below the blend was reported as {nrr.severity}"


# --------------------------------------------------------------------------
# Concentration
# --------------------------------------------------------------------------

def test_concentration_is_computed_rather_than_authored(run):
    """`atlas_enterprise`'s whole story is concentration and it used to be
    authored into the sample's profile.

    **This no longer asserts that every archetype fires it**, and that change
    is the item: the detector used to be unable to say no, so "it fired" was
    not evidence of anything. It is silent on the retailer now, correctly —
    a near-even four-way category split is not a concentration risk.

    Mutation: gate on the raw HHI again, and the retailer fires too.
    """
    _archetype, profile, tables, results = run
    findings = [f for f in detect_all(results, tables, profile)
                if f.id.startswith("concentration_")]
    for finding in findings:
        uneven = finding.evidence["current"]
        assert 0.0 <= uneven <= 1.0
        # The raw index is still quoted, because it is the standard measure;
        # what changed is which of the two decides.
        assert 0.0 < finding.evidence["hhi"] <= 1.0
        assert finding.evidence["hhi"] > uneven, (
            "the corrected index must sit below the raw one")
        assert "Herfindahl" in finding.statement
        # The even share is what makes the claim checkable by eye.
        assert finding.evidence["top_share"] > finding.evidence["even_share"]


def test_the_concentrated_sample_fires_and_the_diversified_one_does_not():
    """The discrimination the raw index could not do.

    Measured across the seven samples: the finding fired on **11 of 11**
    dimensions, because HHI's floor is `1/n` and every dimension this engine
    emits carries three to five segments. `atlas_enterprise` has 86% of
    revenue in one segment and `kestrel_retail` has a near-even four-way
    category split, and both were told the same thing at the same severity.

    Mutation: revert to gating on the raw HHI.
    """
    def dimensions_fired(sample: str):
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=profile)
        tables = dict(GENERATORS[spec.resolve_archetype()](profile).tables)
        return _concentration(tables)

    concentrated = dimensions_fired("atlas_enterprise")
    assert concentrated, "the sample whose whole story is concentration is silent"
    assert any(f.severity == "high" for f in concentrated)

    diversified = dimensions_fired("kestrel_retail")
    assert not diversified, (
        f"a near-even retail mix is still reported as concentrated: "
        f"{[f.statement for f in diversified]}")


def _segment_frame(shares: Dict[str, float]) -> pd.DataFrame:
    """A `segment_financials` slice with the shares stated exactly.

    The sample's own frame cannot express this: an even split of its five
    channels scores 5 x 0.2**2 = 0.20, which is *genuinely* above the 0.15 floor,
    so "spread it evenly and expect silence" tests the sample's segment count
    rather than the threshold.
    """
    return pd.DataFrame([
        {"month": pd.Period("2025-12", freq="M"), "dimension": "channel",
         "segment": name, "revenue": 1_000_000 * share, "share": share}
        for name, share in shares.items()])


def _hhi_finding(shares: Dict[str, float]):
    from kpi_maker.insight.detectors import _concentration
    found = _concentration({"segment_financials": _segment_frame(shares)})
    return found[0] if found else None


def test_an_even_split_is_never_reported_as_concentrated():
    """**The invariant the old thresholds could not satisfy, at every n.**

    This test replaces two that asserted the opposite. One claimed a
    perfectly even *four*-way split is "highly concentrated" (4 x 0.25**2 =
    0.25, exactly the old high bar) and the other could only demonstrate
    silence by using *eight* even segments — a count no archetype here emits.
    Both were arithmetically correct about the raw index and neither asked
    whether "an even split is highly concentrated" is a sentence anyone would
    defend. That is what a borrowed threshold does when nobody re-derives it
    for the data it lands on.

    Mutation: gate on the raw HHI, and every n from 2 to 6 goes red.
    """
    for n in range(2, 9):
        even = _hhi_finding({f"c{i}": 1 / n for i in range(n)})
        assert even is None, (
            f"{n} equal segments reported as concentrated: {even.statement}")


def test_the_plural_of_category_is_not_categorys():
    """The statement counts the segments, so it has to name them in the plural.

    Two of the seven dimensions this engine emits — `category` and
    `product_family` — do not take a bare "s", and "3 categorys" on a board
    pack is the kind of small wrongness that makes a reader distrust the
    arithmetic beside it. Asserted over the dimension names the generators
    actually produce, rather than over a wordlist that could drift from them.

    **This test exists because a mutation harness said it already did.**
    Deleting the irregular branch reported RED against a test that had never
    been written: pytest exits 5 for "no tests selected" and the harness read
    any non-zero exit as a failing test. A mutation checked against nothing
    certifies nothing — the same shape as 0.8's over-gentle double and 5.3d's
    `return "" or (...)`.

    Mutation: drop the `-y -> -ies` branch.
    """
    from kpi_maker.insight.detectors import _plural

    assert _plural("category") == "categories"
    assert _plural("product family") == "product families"
    # The regular ones must not be broken by the irregular rule.
    assert _plural("segment") == "segments"
    assert _plural("channel") == "channels"
    assert _plural("service line") == "service lines"
    # -ay/-ey/-oy/-uy keep the y.
    assert _plural("survey") == "surveys"

    # And every dimension the generators really emit pluralises to something
    # that is not the bare word plus "s" being wrong.
    for sample in ("kestrel_retail", "orbis_works", "lumen_exchange"):
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=profile)
        tables = dict(GENERATORS[spec.resolve_archetype()](profile).tables)
        seg = tables["segment_financials"]
        for dimension in seg["dimension"].unique():
            word = str(dimension).replace("_", " ")
            assert not _plural(word).endswith("ys"), word


def test_a_segment_name_reaches_prose_as_prose():
    """`segment_financials` carries `managed_service` and `product_family`.

    Printed raw, they read like a leaked database field beside the segments
    that happen to be single words — a consultancy's finding said "A shock to
    managed_service lands on the whole company" in the same sentence as a
    humanised dimension name.

    Both the concentration and the decomposition detectors had it, so it is
    fixed at the source. Same rule as 5.3c's `_base_layout`.

    **The constructed case is the load-bearing half, and the first version of
    this test did not have it.** Sweeping the real samples left the
    concentration mutation green: under `conftest`'s pinned
    `MASTERBI_HISTORY_END`, the only concentration finding that fires on a
    dimension with underscored segment names drops out, so every surviving
    one happens to name `enterprise` or `Enterprise`. The sweep was grading
    the samples' calendar rather than the rule — the same premise failure as
    5.3c's usable-segment guard and 5.2's hand-written sample list.

    Mutations: print `shares.index[0]` or `lead.segment` raw again; or make
    `_label` a no-op.
    """
    import re

    # Constructed, so it does not depend on which segments a sample happens
    # to have at the pinned month.
    named = _hhi_finding({"managed_service": 0.70, "advisory": 0.20,
                          "design": 0.10})
    assert named is not None
    assert "managed_service" not in named.statement, named.statement
    assert "managed service" in named.statement

    for sample in ("halberd_consulting", "orbis_works", "lumen_exchange",
                   "atlas_enterprise"):
        profile = load_profile(ROOT / "samples" / f"{sample}.json")
        spec = RunSpec(profile=profile)
        tables = dict(GENERATORS[spec.resolve_archetype()](profile).tables)
        results = compute(select(profile), tables, profile,
                          by=dimensions(tables))
        for finding in detect_all(results, tables, profile):
            for field_name in ("title", "statement", "recommendation"):
                text = getattr(finding, field_name) or ""
                # A KPI id may legitimately appear in prose; a *segment* name
                # may not, and the two are told apart by the underscore being
                # inside a word the sentence is treating as a noun phrase.
                offenders = [w for w in re.findall(r"[a-z]+_[a-z_]+", text)
                             if w not in {r.kpi.id for r in results}]
                assert not offenders, (
                    f"{sample} {finding.id} {field_name}: {offenders}")


def test_the_severity_bands_land_where_the_mix_is_actually_uneven():
    """A detector that fires one band too eagerly is worse than a silent one.

    The bands are stated on the *normalised* index, so they mean the same
    thing whether a company reports three segments or six — which is exactly
    what the raw index could not do.

    Mutation: swap `UNEVEN_HIGH` and `UNEVEN_MEDIUM`, or drop the
    normalisation so `n` decides the band.
    """
    from kpi_maker.insight.detectors import UNEVEN_HIGH, UNEVEN_MEDIUM

    assert 0 < UNEVEN_MEDIUM < UNEVEN_HIGH < 1

    # One segment owning most of it: the shape that actually worries a board.
    dominant = _hhi_finding({"enterprise": 0.7, "mid": 0.2, "smb": 0.1})
    assert dominant is not None and dominant.severity == "high"
    assert dominant.evidence["top_share"] == pytest.approx(0.7)
    assert "enterprise" in dominant.statement
    assert "highly uneven" in dominant.statement

    # Lopsided, but not a single point of failure. (H* = 0.168.)
    tilted = _hhi_finding({"a": 0.60, "b": 0.25, "c": 0.15})
    assert tilted is not None and tilted.severity == "medium"
    assert "moderately uneven" in tilted.statement

    # Mildly tilted is silent. (H* = 0.032 — and note the *raw* index for
    # this split is 0.355, which the old rule graded "highly concentrated".)
    mild = _hhi_finding({"a": 0.45, "b": 0.30, "c": 0.25})
    assert mild is None, mild.statement if mild else ""

    # **The same verdict at a different segment count**, which is the whole
    # point of normalising and the clearest statement of the bug. These two
    # splits are equally uneven -- H* = 0.168 and 0.167 -- but their raw
    # Herfindahl scores are 0.445 and 0.306, so the old rule called the first
    # "highly concentrated" and the second "moderately" for no reason except
    # that one has three segments and the other six.
    three = _hhi_finding({"a": 0.60, "b": 0.25, "c": 0.15})
    six = _hhi_finding({"a": 0.50, "b": 0.15, "c": 0.12,
                        "d": 0.10, "e": 0.08, "f": 0.05})
    assert three is not None and six is not None
    assert three.severity == six.severity == "medium", (
        three.severity, six.severity)
    assert three.evidence["hhi"] > six.evidence["hhi"], (
        "premise: the raw index really does disagree with the corrected one")


# --------------------------------------------------------------------------
# Archetype coverage
# --------------------------------------------------------------------------

def test_a_transactional_run_gets_more_than_three_detectors(run):
    """It got three of eight. The complaint the plan makes about this file."""
    archetype, profile, tables, results = run
    findings = detect_all(results, tables, profile)

    prefixes = {
        "breach_": "status_breaches", "bench_": "benchmark_gaps",
        "trend_": "trend_breaks", "segment_churn": "segment_outliers",
        "operating_leverage": "operating_leverage", "arr_bridge": "arr_bridge",
        "channel_cost": "channel_efficiency", "runway_": "runway",
        "decomp_": "driver_decomposition", "concentration_": "concentration",
    }
    fired = {name for f in findings for prefix, name in prefixes.items()
             if f.id.startswith(prefix)}
    # **`concentration` came out of this set, and the reason is the point.**
    # It used to be here because it fired for every archetype -- which it did
    # because it *could not not* fire: HHI's floor is 1/n and every dimension
    # here has three to five segments. Counting a detector that cannot say no
    # as coverage inflates the number it is supposed to measure, so the
    # retailer's honest count is 5 rather than the 6 recorded in 3.4b.
    #
    # Mutation: put "concentration" back in the required set, and the
    # ecommerce parametrisation goes red -- which is the old bug, asserted.
    assert len(fired) >= 5, f"{archetype} fired only {sorted(fired)}"
    assert {"driver_decomposition", "operating_leverage"} <= fired


def test_a_detector_that_cannot_apply_says_so_rather_than_failing(run):
    """"Needs the mrr_movements table" invites a retailer to go and find a file
    that does not exist for their business."""
    archetype, profile, tables, results = run
    detect_all(results, tables, profile)
    skipped = " ".join(detect_all.skipped)

    if archetype == "ecommerce":
        assert "subscription book" in skipped, skipped
        assert "needs the 'mrr_movements' table" not in skipped
    else:
        assert not detect_all.skipped, detect_all.skipped


def test_channel_efficiency_is_not_bound_to_the_saas_funnel():
    """It required `marketing.sqls` and raised on anything else — surfaced to
    the user as `needs the "Column(s) ['sqls'] do not exist" table`, which is
    not a sentence."""
    from kpi_maker.insight.detectors import _FUNNEL_COLUMNS, _channel_efficiency

    assert "leads" in _FUNNEL_COLUMNS, "only the subscription funnel is recognised"
    months = pd.period_range("2023-01", periods=24, freq="M")
    marketing = pd.DataFrame([
        {"month": m, "channel": c, "spend": 1000.0 * (3 if i > 17 and c == "paid" else 1),
         "leads": 100.0}
        for i, m in enumerate(months) for c in ("paid", "organic")])

    findings = _channel_efficiency({"marketing": marketing})
    assert findings, "a marketing table with `leads` produced nothing"
    assert "per lead" in findings[0].statement, findings[0].statement


def test_a_trend_break_statement_agrees_with_its_own_numbers(run):
    """It quoted the first value of the prior window against the last value of
    the recent one, so a decelerating series that was still rising read
    "reversed direction ... moving from 2,850,434 to 3,347,383" — a sentence
    contradicting itself, on the screen where ranking is the whole point."""
    _archetype, profile, tables, results = run
    by_id = {r.kpi.id: r for r in results if r.computed}
    findings = [f for f in detect_all(results, tables, profile)
                if f.id.startswith("trend_")]
    assert findings, "no trend break to check"

    for finding in findings:
        kpi = by_id[finding.id[len("trend_"):]].kpi
        # "... <verb> by X over the prior N months and <verb> by Y over the
        # last N ...". The second clause is the one the finding is about.
        _, _, tail = finding.statement.partition("months and ")
        recent_clause, _, _ = tail.partition("over the last")
        assert recent_clause, finding.statement

        wrong_way = "fell" if kpi.direction.value == "higher_is_better" else "rose"
        assert wrong_way in recent_clause, (
            f"{finding.id} claims a turn but its recent clause reads "
            f"'{recent_clause.strip()}'")
        assert finding.evidence["recent_slope"] != finding.evidence["prior_slope"]


def test_operating_leverage_is_not_a_subscription_question(run):
    """It read `by_id["arr_growth_yoy"]`, an id only the SaaS pack declares, so
    it fired for subscriptions and for nothing else. Every business either
    scales revenue faster than its people or it does not."""
    archetype, profile, tables, results = run
    findings = [f for f in detect_all(results, tables, profile)
                if f.id.startswith("operating_leverage")]
    assert findings, f"{archetype} got no operating-leverage finding"
    assert "arr_growth_yoy" not in {k for f in findings for k in f.kpi_ids} \
        or archetype == "saas"
    assert "Revenue grew" in findings[0].statement or \
           "revenue grew" in findings[0].statement


def test_operating_leverage_compares_a_year_against_a_year():
    """Trailing twelve months on both sides, so a December peak appears once on
    each and cancels itself. A six-month comparison here would have needed the
    seasonal adjustment that `_trend_breaks` uses."""
    from kpi_maker.insight.detectors import _operating_leverage

    months = pd.period_range(end=pd.Period("2026-02", freq="M"), periods=24,
                             freq="M")
    peak = {11: 3.0, 12: 4.0}
    financials = pd.DataFrame([
        {"month": m, "revenue": 100_000.0 * peak.get(m.month, 1.0)
         * (1.30 if i >= 12 else 1.0)}
        for i, m in enumerate(months)])
    headcount = pd.DataFrame([{"month": m, "fte": 50.0} for m in months])

    findings = _operating_leverage({}, {"monthly_financials": financials,
                                        "headcount": headcount})
    assert findings and findings[0].id == "operating_leverage_positive"
    # 30% revenue growth against flat headcount, and not a number distorted by
    # where in the year the window happens to end.
    assert findings[0].evidence["current"] == pytest.approx(0.30)
    assert findings[0].evidence["expected"] == pytest.approx(0.0)


def test_the_registry_and_the_name_list_agree():
    """A detector reachable by the spec but missing from `DETECTOR_NAMES` is
    invisible to the Studio's picker."""
    profile = load_profile(ARCHETYPES["saas"])
    tables = dict(GENERATORS["saas"](profile).tables)
    results = compute(select(profile), tables, profile)

    class _Spec:
        detectors = ["driver_decomposition", "concentration"]
        disabled: list = []
        params = None
        min_severity = None
        max_findings = None
        pinned: list = []

    findings = detect_all(results, tables, profile, spec=_Spec())
    assert findings, "selecting only the new detectors produced nothing"
    assert {"driver_decomposition", "concentration"} <= set(DETECTOR_NAMES)
