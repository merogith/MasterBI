"""Is this pack a scorecard's worth of candidates?

`validate.py` asks whether one sheet is coherent. This asks whether a pack, or
the group of packs a profile loads together, can actually furnish a balanced
scorecard — which is a different question with different answers, and the
reason the plan's proposed thresholds had to be re-scoped before they were
worth enforcing. See `authoring/__init__.py` for the three scoping decisions
and what measuring the shipped packs said about each.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from ..kpi.schema import KPI, Perspective, Tier, Timing
from ..kpi.selection import TIER_CAPS, load_library
from .validate import (
    LEVELS,
    Finding,
    aggregates_a_time_series,
    compiles,
    validate_sheet,
)

LIBRARY = Path(__file__).resolve().parents[1] / "kpi" / "library"

#: A pack has to offer selection a choice. Two is the same floor
#: `kpi/selection.py` applies to the finished scorecard — below it, a
#: perspective is represented by whatever happens to exist rather than by
#: something chosen.
MIN_CANDIDATES_PER_PERSPECTIVE = 2

#: Redundancy: two sheets in one perspective whose names collapse to the same
#: words are usually one metric authored twice under different ids. A real
#: correlation check needs data and belongs in `tests/stress.py`; this catches
#: the copy-paste case, which is the one that happens while authoring.
_STOPWORDS = {"rate", "per", "of", "the", "and", "total", "average", "avg",
              "monthly", "yoy", "growth", "ratio", "pct", "percent"}


@dataclass
class Report:
    group: str
    packs: List[str]
    kpis: int = 0
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        counts = {level: sum(1 for f in self.findings if f.level == level)
                  for level in LEVELS}
        return (f"{self.group}: {self.kpis} KPIs, "
                + ", ".join(f"{counts[level]} {level}" for level in LEVELS))


def _words(name: str) -> Set[str]:
    return {w for w in name.lower().replace("(", " ").replace(")", " ").split()
            if w not in _STOPWORDS}


def lint_group(packs: Sequence[str], *, group: Optional[str] = None,
               kpis: Optional[List[KPI]] = None) -> Report:
    """Lint the packs a profile loads together.

    Together, because a supplementary pack points its `driver_parent`s into the
    pack it supplements — `saas_standard` has nine into `saas`. Linted per
    file those read as dangling; linted per group, as zero. The group is what a
    run actually sees, and `load_groups` takes it from `resolve_packs` rather
    than assuming.
    """
    packs = list(packs)
    report = Report(group=group or "+".join(packs), packs=packs)
    kpis = load_library(packs, include_user=False) if kpis is None else kpis
    report.kpis = len(kpis)
    if not kpis:
        report.findings.append(
            Finding("error", "empty", "the group defines no KPIs",
                    pack=report.group))
        return report

    ids = {k.id for k in kpis}
    from ..survey import build_profile

    profile = build_profile({})

    # A duplicate id inside a load group is not a style problem: `_load_packs`
    # refuses it, and one that slipped through would have the later sheet
    # shadow the earlier one silently.
    seen: Dict[str, str] = {}
    for kpi in kpis:
        if kpi.id in seen:
            report.findings.append(Finding(
                "error", "duplicate-id",
                f"{kpi.id!r} is defined twice in this group", pack=report.group,
                kpi_id=kpi.id))
        seen[kpi.id] = kpi.id

    for kpi in kpis:
        report.findings += validate_sheet(kpi, pack=report.group, known_ids=ids,
                                          profile=profile)
        why = compiles(kpi, ids)
        if why:
            report.findings.append(Finding(
                "error", "formula", why, pack=report.group, kpi_id=kpi.id))
        grain = aggregates_a_time_series(kpi)
        if grain:
            report.findings.append(Finding(
                "error", "entity-grain", grain, pack=report.group,
                kpi_id=kpi.id))

    # --- Balanced Scorecard coverage --------------------------------------
    # A minimum only. The maximum is a property of the *selected* scorecard and
    # `kpi/selection.py` already owns it; applied to a pack it fails three of
    # the four shipped ones, because a pack is a menu.
    for perspective in Perspective:
        count = sum(1 for k in kpis if k.perspective == perspective)
        if count < MIN_CANDIDATES_PER_PERSPECTIVE:
            report.findings.append(Finding(
                "error", "coverage",
                f"only {count} candidate(s) for the {perspective.value} "
                f"perspective; selection needs at least "
                f"{MIN_CANDIDATES_PER_PERSPECTIVE} to have a choice",
                pack=report.group))

    # --- Leading share ----------------------------------------------------
    # Reported, not gated, and the difference is a lesson this rule taught on
    # its first use. Gating the *pack* at 30% is a proxy, and a bad one: the
    # e-commerce pack cleared it at 30.4% while the scorecard a retailer
    # actually got was still 26% leading, because selection caps tier 1 at six
    # and ten of that pack's twelve tier-1 sheets are lagging. A pack-level
    # threshold measures the menu; the user eats the meal.
    leading = sum(1 for k in kpis if k.timing == Timing.leading)
    share = leading / len(kpis)
    report.findings.append(Finding(
        "info", "leading-share",
        f"{share:.0%} of the pack is leading ({leading} of {len(kpis)})",
        pack=report.group))

    # --- Core seeds against the exec cap ----------------------------------
    # `core: true` seeds a sheet **past** the tier caps, so the exec tier holds
    # however many cores the group happens to contain rather than the six it
    # advertises, and every non-core tier-1 sheet becomes unreachable the
    # moment the cores reach that number.
    #
    # Invisible in a diff and invisible at run time: the drop is reported as
    # "tier 1 already at cap", which reads like a scoring outcome and is not
    # one — the loop never ran a round. Measured in 4.3b, when `general` (five
    # tier-1 cores, authored as the whole library) first met a supplementary
    # pack: realisation scored highest of every tier-1 candidate on the
    # consultancy sample and was dropped, and take rate went the same way on
    # the platform. So it is reported at the *group*, which is the only place
    # the arithmetic exists — neither file is wrong on its own.
    cap = TIER_CAPS.get(Tier.exec_l1, 6)
    cores = [k for k in kpis if k.core and k.tier == Tier.exec_l1]
    if len(cores) >= cap:
        blocked = sorted(k.id for k in kpis
                         if k.tier == Tier.exec_l1 and not k.core)
        report.findings.append(Finding(
            "warn", "core-cap",
            f"{len(cores)} tier-1 sheets are `core` against an exec cap of "
            f"{cap}, so the greedy loop has no slot left to fill and "
            f"{len(blocked)} tier-1 sheet(s) can never be selected for this "
            f"group however they score"
            + (f": {', '.join(blocked)}" if blocked else ""),
            pack=report.group))

    # --- Redundancy -------------------------------------------------------
    for perspective in Perspective:
        within = [k for k in kpis if k.perspective == perspective]
        for i, left in enumerate(within):
            for right in within[i + 1:]:
                shared = _words(left.name) & _words(right.name)
                if shared and shared == _words(left.name) == _words(right.name):
                    report.findings.append(Finding(
                        "warn", "redundant",
                        f"{left.id!r} and {right.id!r} reduce to the same name "
                        f"in the {perspective.value} perspective",
                        pack=report.group))

    report.findings += _selected_scorecard_findings(report)
    return report


def _selected_scorecard_findings(report: Report) -> List[Finding]:
    """Would a real run on this pack carry a quality warning?

    The gate, and it needs no threshold of its own: `kpi/selection.py` already
    decides when a scorecard is too lagging or too thin, and says so in
    `rationale` as `_leading_warning` and `_coverage_warning`. Those warnings
    reach the user and there is nothing the user can do about either — both are
    properties of the library. So an author should not be able to ship a pack
    that produces one.

    Checked by selecting for a real profile in a sector that resolves to this
    group, which is the only way to see the caps and the scoring. `kestrel
    _retail` carried "only 22% of the selected KPIs are leading" on every run
    while every pack-level check passed.
    """
    from ..kpi.selection import select
    from ..profile import sectors as sector_map
    from ..survey import build_profile

    sector = _sector_for(report.packs)
    if sector is None:
        return []
    try:
        profile = build_profile({"business_model": sector})
        kpi_set = select(profile)
    except Exception as exc:                              # noqa: BLE001
        return [Finding("error", "unselectable",
                        f"selecting for {sector!r} raised "
                        f"{type(exc).__name__}: {exc}", pack=report.group)]

    return warnings_a_user_cannot_act_on(
        kpi_set, group=report.group, sector=sector,
        approximate=not sector_map.resolve_packs(sector).exact)


def warnings_a_user_cannot_act_on(kpi_set, *, group: str, sector: str,
                                  approximate: bool = False) -> List[Finding]:
    """The quality warnings `select()` raises that are the library's fault.

    Split out as a pure function over a `KPISet` so it can be tested against a
    lagging-heavy scorecard without one having to exist in the shipped library
    — which it no longer does, and which is exactly how the first version of
    this rule ended up with no test that failed when it was deleted.
    """
    out: List[Finding] = []
    for key in ("_leading_warning", "_coverage_warning"):
        message = kpi_set.rationale.get(key)
        if message:
            out.append(Finding(
                "error", key.strip("_").replace("_", "-"),
                f"a run for {sector!r} would carry this warning, and the user "
                f"cannot act on it — {message}", pack=group))
    if approximate:
        # A sector on the fallback pack is *expected* to be thinner; saying so
        # is 0.1's whole design. Do not fail an approximation for approximating.
        # `coverage-warning`, not `coverage`: the rule name is derived from
        # the rationale key. Written as the shorter string this filter matched
        # nothing and an approximated sector was failed for approximating,
        # which is the one thing 0.1 exists to make acceptable.
        out = [f for f in out if f.rule != "coverage-warning"]
    return out


def _sector_for(packs: Sequence[str]) -> Optional[str]:
    """A sector whose own packs are exactly this group, or None."""
    from ..profile import sectors, taxonomy

    for sector in taxonomy.load().sectors:
        if sorted(sectors.resolve_packs(sector.id).value) == sorted(packs):
            return sector.id
    return None


def load_groups() -> Dict[str, List[str]]:
    """Every pack combination a real profile loads, from the taxonomy.

    Derived rather than listed: a sector gaining its own pack in 4.3 starts
    being linted as the group it will actually run as, with nothing here to
    remember to edit.
    """
    from ..profile import sectors, taxonomy

    # Exactly what `resolve_packs` returns, and nothing added. The first
    # version of this appended `general` to every group on the assumption that
    # it is always loaded beside a sector pack. It is not: a SaaS run loads
    # `saas` alone and a retailer loads `general` alone. Linting a group that
    # never occurs is worse than not linting — it would have passed a
    # cross-pack `driver_parent` that breaks in every real run.
    groups: Dict[str, List[str]] = {}
    for sector in taxonomy.load().sectors:
        packs = list(sectors.resolve_packs(sector.id).value)
        groups.setdefault("+".join(sorted(packs)), packs)
    return groups


def lint_all() -> List[Report]:
    """Every load group, plus any pack no group happens to reach.

    The orphan pass is the interesting half. A pack no sector resolves to is a
    pack that ships to nobody — it is not linted, not selected, and not read,
    and it rots quietly. Reported as an error rather than linted in isolation,
    because "which group should this have been in?" is a question only an
    author can answer, and guessing one produces a page of dangling parents
    that are not really dangling.
    """
    from ..kpi.selection import pack_files

    reports = [lint_group(packs, group=name)
               for name, packs in sorted(load_groups().items())]

    # Which *files* those groups load, asked of the loader rather than guessed.
    # Guessing it — comparing file stems to pack names — reported
    # `saas_standard` as reachable by nobody when `saas` loads it through the
    # `{pack}_*.yaml` convention. A linter with a false positive is worse than
    # no linter: it teaches the author to ignore it.
    covered = {path.name for report in reports
               for path in pack_files(report.packs)}

    orphans = [p.stem for p in sorted(LIBRARY.glob("*.yaml"))
               if p.name not in covered]
    if orphans:
        unreachable = Report(group="(library)", packs=orphans)
        for pack in orphans:
            unreachable.kpis += len(load_library([pack], include_user=False))
            unreachable.findings.append(Finding(
                "error", "unreachable",
                f"{pack!r} is not resolved by any sector in the taxonomy, so "
                f"nothing loads it and nobody sees its record sheets — wire it "
                f"to a sector or delete it", pack="(library)"))
        reports.append(unreachable)
    return reports
