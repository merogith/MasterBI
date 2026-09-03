"""KPI selection engine.

Turns a CompanyProfile into a KPISet by applying, in order:
  1. applicability  — `applies_when` evaluated against the profile
  2. feasibility    — can we actually compute it from available data?
  3. scoring        — intent alignment, perspective gap, timing, audience
  4. coverage       — Balanced Scorecard min/max per perspective
  5. tier caps      — 1 north star, 4-6 L1, 12-20 L2, adjusted for KPI literacy

Every drop is recorded with a reason. "Why isn't X on my dashboard?" must
always have an answer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from ..profile import sectors
from ..profile.schema import Audience, CompanyProfile, KpiExperience
from .expr import evaluate
from .schema import KPI, AlertBands, KPISet, Perspective, Tier, Timing

LIBRARY_DIR = Path(__file__).parent / "library"

# Scoring weights. Intent dominates deliberately: the same company with a
# growth objective and a cash objective should get visibly different dashboards.
W_INTENT_PRIMARY = 5.0
W_INTENT_SECONDARY = 2.0
W_PERSPECTIVE_GAP = 3.0
W_LEADING = 1.5
W_AUDIENCE = 2.0
W_TIER = 2.0
W_REDUNDANCY = -4.0

# Balanced Scorecard coverage constraints (Kaplan & Norton).
MIN_PER_PERSPECTIVE = 2
MAX_PER_PERSPECTIVE = 7

# Target leading/lagging mix. Not a hard constraint — the library may simply
# not contain enough leading metrics for a given model — but a miss is reported.
MIN_LEADING_SHARE = 0.30

# Tier caps. Lowered when the audience has little KPI experience — a dashboard
# nobody can read has zero value regardless of how correct it is.
TIER_CAPS = {Tier.exec_l1: 6, Tier.functional_l2: 14, Tier.operational_l3: 12}
TIER_CAPS_LOW_LITERACY = {Tier.exec_l1: 4, Tier.functional_l2: 8, Tier.operational_l3: 4}

# Audiences care about different perspectives.
AUDIENCE_WEIGHTS: Dict[Audience, Dict[Perspective, float]] = {
    Audience.board: {Perspective.financial: 1.0, Perspective.customer: 0.6,
                     Perspective.process: 0.2, Perspective.learning: 0.3},
    Audience.investor: {Perspective.financial: 1.0, Perspective.customer: 0.8,
                        Perspective.process: 0.2, Perspective.learning: 0.2},
    Audience.bank: {Perspective.financial: 1.0, Perspective.customer: 0.4,
                    Perspective.process: 0.1, Perspective.learning: 0.0},
    Audience.exec: {Perspective.financial: 0.8, Perspective.customer: 0.8,
                    Perspective.process: 0.6, Perspective.learning: 0.5},
    Audience.department_head: {Perspective.financial: 0.4, Perspective.customer: 0.6,
                              Perspective.process: 1.0, Perspective.learning: 0.6},
    Audience.operational_team: {Perspective.financial: 0.2, Perspective.customer: 0.5,
                                Perspective.process: 1.0, Perspective.learning: 0.4},
}

# Pairs measuring substantially the same thing. Keeping both wastes a slot and
# makes the dashboard look padded.
REDUNDANT_PAIRS = [
    ("cac_payback_months", "ltv_cac_ratio"),
    ("logo_churn_rate", "grr"),
    ("blended_cac", "magic_number"),
    ("lead_to_mql_rate", "mql_to_sql_rate"),
]

# Only ids that a shipped pack actually defines belong here. `gmv`, `oee` and
# `utilization_rate` used to be listed for marketplace, manufacturing and
# services; none of the three exists, so all three silently fell through to the
# tier-0 fallback below and the map read as coverage the library did not have.
# Re-add each one with its pack in ROADMAP M2 / Phase 4.
NORTH_STAR_BY_MODEL = {
    "saas": "arr",
    "ecommerce": "contribution_margin",
}


def pack_files(packs: Optional[List[str]] = None) -> List[Path]:
    """Which files a pack name actually loads.

    A pack may span several files (`saas.yaml`, `saas_standard.yaml`, ...) so a
    sector library can grow without any one file becoming unreviewable. That
    convention is easy to miss from outside: `authoring/lint.py` first reported
    `saas_standard` as reachable by no sector, because it compared file stems
    against pack *names* and reimplemented the glob without the `_*` half. One
    function, so the linter and the loader cannot disagree about what a pack is.
    """
    if not packs:
        return sorted(LIBRARY_DIR.glob("*.yaml"))
    files: List[Path] = []
    for pack in packs:
        matches = sorted(LIBRARY_DIR.glob(f"{pack}.yaml")) + \
                  sorted(LIBRARY_DIR.glob(f"{pack}_*.yaml"))
        if not matches:
            raise FileNotFoundError(f"KPI pack not found: {pack}")
        files.extend(matches)
    return files


def _load_packs(packs: Optional[List[str]] = None) -> List[KPI]:
    """Load and validate KPI record sheets from the shipped library."""
    files = pack_files(packs)

    # A duplicate id WITHIN one sector's packs is an authoring bug and still
    # raises. Across sectors it is correct and expected: a retailer's gross
    # margin and a software vendor's are the same definition with different
    # benchmarks — p50 0.42 against 0.75 — and forcing one record on both would
    # tell a healthy retailer it is failing. So the guard applies to a
    # requested pack group; the load-everything view takes the first and
    # records the rest.
    scoped = bool(packs)

    kpis: List[KPI] = []
    seen: Dict[str, str] = {}
    for path in files:
        if not path.exists():
            raise FileNotFoundError(f"KPI pack not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for entry in raw:
            kpi = KPI(**entry)   # pydantic validates the record sheet is complete
            if kpi.id in seen:
                if scoped:
                    raise ValueError(
                        f"duplicate KPI id {kpi.id!r} in {path.name} — already "
                        f"defined in {seen[kpi.id]}. Two files in the same pack "
                        f"may not define the same KPI.")
                continue
            seen[kpi.id] = path.name
            kpis.append(kpi)
    return kpis


def load_library(packs: Optional[List[str]] = None,
                 include_user: bool = True) -> List[KPI]:
    """The shipped packs, with stored user KPIs layered on top.

    A user KPI sharing an id with a library one **replaces** it. "Your CAC
    payback is wrong, here is how we calculate it" is a real and reasonable
    thing to want, and it is the whole point of letting people define metrics.
    """
    return _load_with_overrides(packs, include_user)[0]


def _load_with_overrides(packs: Optional[List[str]],
                         include_user: bool = True) -> Tuple[List[KPI], Dict[str, str]]:
    """(kpis, notes) where notes explains each user replacement or load failure."""
    kpis = _load_packs(packs)
    if not include_user:
        return kpis, {}

    from .user_library import load_user_kpis
    user_kpis, broken = load_user_kpis()

    notes: Dict[str, str] = {
        kpi_id: f"user KPI could not be loaded: {reason}"
        for kpi_id, reason in broken.items()
    }

    by_id = {k.id: i for i, k in enumerate(kpis)}
    for kpi in user_kpis:
        if kpi.id in by_id:
            kpis[by_id[kpi.id]] = kpi
            notes[kpi.id] = "user definition replaces the library record sheet"
        else:
            by_id[kpi.id] = len(kpis)
            kpis.append(kpi)
    return kpis, notes


def load_all_known() -> List[KPI]:
    """Every KPI that exists anywhere — every pack, plus user definitions.

    The resolution universe for formula references. A formula may reference a
    KPI the selection engine did not pick, and that should compute rather than
    fail; see `metrics/engine.py:_Evaluator`.
    """
    return load_library(None, include_user=True)


def _packs_for(profile: CompanyProfile) -> List[str]:
    """The packs this company's sector resolves to.

    Not `[business_model.type]`: eight of the ten declared sectors have no pack
    of their own, and naming them here is what used to raise
    `FileNotFoundError: KPI pack not found: manufacturing`. `sectors` falls back
    to the cross-sector `general` pack and reports that it did.
    """
    return list(sectors.resolve_packs(profile.business_model.type.value).value)


def candidates_for(profile: CompanyProfile, overrides=None) -> List[KPI]:
    """Every KPI this company's packs define, before any selection happens.

    The catalog, as against `select()`'s result. The studio needs it to offer
    a pin/exclude list and the planner needs it to know which ids exist; both
    were reaching for `load_library(_packs_for(...))` and would have drifted
    the first time pack resolution changed.
    """
    packs = (list(overrides.packs) if overrides is not None and overrides.packs
             else _packs_for(profile))
    try:
        library = load_library(packs)
    except FileNotFoundError:
        return []
    return sorted(library, key=lambda k: (int(k.tier), k.id))


def unknown_kpi_ids(profile: CompanyProfile, overrides) -> List[str]:
    """KPI ids referenced by a metrics spec that no loaded pack defines.

    Exposed so an editor can reject a bad id at the moment it is typed. Finding
    out at run time instead means the studio accepts the edit, the re-run fails
    somewhere else, and the user has to guess which field was at fault.
    """
    if overrides is None:
        return []
    packs = list(overrides.packs) if overrides.packs else _packs_for(profile)
    try:
        known = {k.id for k in load_library(packs)}
        known |= {e.get("id", "") for e in (overrides.custom or [])}
    except FileNotFoundError:
        return []
    referenced = set(overrides.pinned) | set(overrides.excluded) | set(overrides.overrides)
    return sorted(referenced - known)


def _apply_overrides(library: List[KPI], overrides) -> List[KPI]:
    """Layer the user's per-KPI edits over the library record sheets.

    Applied before scoring, not after, because `tier` feeds the selection score
    and the tier caps. Promoting a KPI to L1 has to actually change what gets
    picked, or the control is decorative.
    """
    if overrides is None or not overrides.overrides:
        return library

    out: List[KPI] = []
    for kpi in library:
        edit = overrides.overrides.get(kpi.id)
        if edit is None:
            out.append(kpi)
            continue

        changes: Dict[str, object] = {}
        if edit.target is not None:
            changes["target_override"] = edit.target
        if edit.tier is not None:
            changes["tier"] = Tier(edit.tier)
        if edit.alert_green is not None or edit.alert_red is not None:
            base = kpi.alert_bands
            green = edit.alert_green if edit.alert_green is not None else (
                base.green if base else None)
            red = edit.alert_red if edit.alert_red is not None else (
                base.red if base else None)
            if green is None or red is None:
                raise ValueError(
                    f"{kpi.id}: setting one alert band requires the other — "
                    f"this KPI has no library bands to fall back on"
                )
            # AlertBands validates green/red against `direction`, so an
            # inconsistent pair fails here with the KPI id attached rather than
            # producing a scorecard that rates everything green.
            changes["alert_bands"] = AlertBands(green=green, red=red)

        out.append(kpi.model_copy(update=changes) if changes else kpi)
    return out


def _score(kpi: KPI, profile: CompanyProfile, perspective_counts: Dict[Perspective, int]) -> float:
    score = 0.0

    primary = profile.intent.primary_objective.value
    if primary in kpi.serves_objectives:
        score += W_INTENT_PRIMARY
    for secondary in profile.intent.secondary:
        if secondary.value in kpi.serves_objectives:
            score += W_INTENT_SECONDARY

    # Under-filled perspectives get a boost — this is what enforces balance
    # without hard-coding a quota per perspective up front.
    if perspective_counts.get(kpi.perspective, 0) < MIN_PER_PERSPECTIVE:
        score += W_PERSPECTIVE_GAP

    if kpi.timing == Timing.leading:
        score += W_LEADING

    audience_weights = AUDIENCE_WEIGHTS.get(profile.intent.audience, {})
    score += W_AUDIENCE * audience_weights.get(kpi.perspective, 0.5)

    # Lower tier number = more senior = more important.
    score += W_TIER * (3 - int(kpi.tier)) / 3.0

    if kpi.benchmark is not None:
        score += 0.5   # a KPI we can benchmark is more actionable

    return score


def _feasible(kpi: KPI, profile: CompanyProfile) -> Tuple[bool, str]:
    missing = set(profile.data_availability.missing)
    needed = set(kpi.requires_data)
    blocked = needed & missing
    if blocked:
        return False, f"requires unavailable data: {', '.join(sorted(blocked))}"

    # A record sheet can describe a metric this build cannot compute: `nps` and
    # `support_first_response_hours` are declared `kind: builtin` and no
    # `@metric` is registered for either. `requires_data` did not catch them,
    # because it tests what the profile *claims* to have — Atlas lists
    # `support_desk` in `has`, so the KPI passed the gate, was selected, and
    # then reported "no implementation registered for this KPI id" on the
    # scorecard.
    #
    # Whether an implementation exists is a fact about the library, not about
    # the company, so it belongs here: the KPI drops with a reason the user can
    # read in the "considered but not selected" list, rather than appearing as
    # a broken row. Imported lazily — `metrics.engine` reaches back into this
    # module for the formula resolution universe.
    if not kpi.is_formula:
        from ..metrics.engine import _REGISTRY

        if kpi.compute_ref not in _REGISTRY:
            return False, ("no implementation in this build — the record sheet "
                           "defines it but nothing computes it yet")
    return True, ""


def select(profile: CompanyProfile, extra_packs: Optional[List[str]] = None,
           overrides=None) -> KPISet:
    """Choose the scorecard.

    `overrides` is a `spec.MetricsSpec` (or None for the library's own answer).
    The user can force a KPI in, force one out, and edit its tier, target and
    alert bands — but the coverage and leading-indicator warnings still fire, so
    overriding the algorithm tells you what it cost rather than hiding it.
    """
    packs = (list(overrides.packs) if overrides is not None and overrides.packs
             else _packs_for(profile))
    packs = packs + list(extra_packs or [])
    library, load_notes = _load_with_overrides(packs)

    # Re-band for this company before anything reads a benchmark. Scoring gives
    # a benchmarked KPI half a point, `metrics/targets.py` resolves against the
    # quartiles, and `KPI.status` scores a `target_band` metric on the
    # inter-quartile range — so a band resolved after selection would be a band
    # the choice of scorecard never saw. See `profile/benchmarks.py` for what
    # each number is allowed to claim.
    from ..profile import benchmarks

    library = benchmarks.resolve(library, profile)

    pinned = set(overrides.pinned) if overrides is not None else set()
    excluded = set(overrides.excluded) if overrides is not None else set()

    # Per-run custom KPIs. Unlike a stored user KPI — which is a reusable pack
    # entry and competes for a slot like anything else — one added to THIS run
    # is an explicit "put this on my scorecard", so it is pinned by default.
    custom_notes: Dict[str, str] = {}
    if overrides is not None and overrides.custom:
        by_id = {k.id: i for i, k in enumerate(library)}
        for entry in overrides.custom:
            try:
                kpi = KPI(**entry)
            except Exception as exc:                     # noqa: BLE001
                raise ValueError(
                    f"custom KPI {entry.get('id') or entry.get('name') or '?'!r} "
                    f"is not valid: {exc}"
                ) from exc
            if kpi.id in by_id:
                library[by_id[kpi.id]] = kpi
                custom_notes[kpi.id] = "custom KPI defined for this run"
            else:
                by_id[kpi.id] = len(library)
                library.append(kpi)
                custom_notes[kpi.id] = "custom KPI defined for this run"
            pinned.add(kpi.id)

    library = _apply_overrides(library, overrides)
    known = {k.id for k in library}
    for unknown in sorted((pinned | excluded) - known):
        # Silently ignoring a typo'd id would leave the user staring at a
        # scorecard that ignored their instruction with no explanation.
        raise ValueError(
            f"unknown KPI id {unknown!r} in the metrics spec — "
            f"not in packs {', '.join(packs)}"
        )

    dropped: Dict[str, str] = {}
    rationale: Dict[str, str] = {}

    # Surface where a definition came from. A user replacing a library metric
    # must be visible in the output, or two runs of "the same" KPI silently
    # disagree and nobody can tell why.
    for kpi_id, note in {**load_notes, **custom_notes}.items():
        rationale[f"_origin:{kpi_id}"] = note

    # A scorecard built on the cross-sector pack because this sector has none of
    # its own must say so wherever the rationale is read — the appendix, the
    # studio, the dropped-KPI disclosure. Silently substituting content is the
    # one thing worse than the crash this replaced.
    pack_resolution = sectors.resolve_packs(profile.business_model.type.value)
    if not pack_resolution.exact and (overrides is None or not overrides.packs):
        rationale["_sector_warning"] = pack_resolution.note

    # --- Layers 1 & 2: applicability and feasibility -----------------------
    candidates: List[KPI] = []
    for kpi in library:
        if kpi.id in excluded:
            dropped[kpi.id] = "excluded by user"
            continue
        # A pinned KPI skips the applicability and feasibility gates: the user
        # asserting they track something outranks our inference that they
        # cannot. It still has to compute, and reports honestly if it does not.
        if kpi.id in pinned:
            candidates.append(kpi)
            continue
        if kpi.applies_when and not evaluate(kpi.applies_when, profile):
            dropped[kpi.id] = f"not applicable: {kpi.applies_when}"
            continue
        ok, reason = _feasible(kpi, profile)
        if not ok:
            dropped[kpi.id] = reason
            continue
        candidates.append(kpi)

    # --- North star --------------------------------------------------------
    north_star_id = NORTH_STAR_BY_MODEL.get(profile.business_model.type.value)
    north_star = next((k for k in candidates if k.id == north_star_id), None)
    if north_star is None:
        north_star = next(
            (k for k in candidates if k.tier == Tier.north_star),
            min(candidates, key=lambda k: int(k.tier)) if candidates else None,
        )
    if north_star is None:
        raise ValueError(
            f"no applicable KPIs for business model "
            f"{profile.business_model.type.value!r} — is the pack missing?"
        )

    selected: List[KPI] = [north_star]
    rationale[north_star.id] = "North Star for this business model"
    perspective_counts: Dict[Perspective, int] = {north_star.perspective: 1}
    tier_counts: Dict[Tier, int] = {}

    # --- Core KPIs: seeded, never competed for ----------------------------
    # Without this, a scoring tie-break could drop the growth rate from a
    # margin-focused scorecard or retention from a cash-focused one. Both
    # would read as negligent to anyone senior looking at the output.
    # A user-pinned KPI is seeded the same way, for the same reason: it must not
    # lose a scoring tie-break to a metric the user did not ask for.
    for kpi in candidates:
        if kpi.id == north_star.id or not (kpi.core or kpi.id in pinned):
            continue
        selected.append(kpi)
        perspective_counts[kpi.perspective] = perspective_counts.get(kpi.perspective, 0) + 1
        tier_counts[kpi.tier] = tier_counts.get(kpi.tier, 0) + 1
        rationale[kpi.id] = (
            "Pinned by the user" if kpi.id in pinned else
            "Core metric — included on every scorecard for this business model "
            "regardless of objective"
        )

    caps = (
        TIER_CAPS_LOW_LITERACY
        if profile.org_culture.kpi_experience == KpiExperience.low
        else TIER_CAPS
    )

    # --- Layers 3-5: greedy scored selection under coverage constraints ----
    # Re-scoring each round is what lets the perspective-gap bonus actually
    # steer the result; a single sort would not balance.
    seeded = {k.id for k in selected}
    pool = [k for k in candidates if k.id not in seeded]
    while pool:
        scored = []
        for kpi in pool:
            base = _score(kpi, profile, perspective_counts)
            selected_ids = {k.id for k in selected}
            for a, b in REDUNDANT_PAIRS:
                if (kpi.id == a and b in selected_ids) or (kpi.id == b and a in selected_ids):
                    base += W_REDUNDANCY
            scored.append((base, kpi))
        scored.sort(key=lambda t: (-t[0], t[1].id))

        best_score, best = scored[0]
        pool = [k for k in pool if k.id != best.id]

        if perspective_counts.get(best.perspective, 0) >= MAX_PER_PERSPECTIVE:
            dropped[best.id] = (
                f"{best.perspective.value} perspective already at cap "
                f"({MAX_PER_PERSPECTIVE})"
            )
            continue
        if tier_counts.get(best.tier, 0) >= caps.get(best.tier, 99):
            dropped[best.id] = f"tier {int(best.tier)} already at cap ({caps.get(best.tier)})"
            continue

        selected.append(best)
        perspective_counts[best.perspective] = perspective_counts.get(best.perspective, 0) + 1
        tier_counts[best.tier] = tier_counts.get(best.tier, 0) + 1
        rationale[best.id] = _explain(best, profile, best_score)

    # --- Coverage assertion -----------------------------------------------
    # Under-covering a perspective is a silent quality failure, so surface it
    # rather than shipping a lopsided scorecard.
    thin = [
        p.value for p in Perspective
        if perspective_counts.get(p, 0) < MIN_PER_PERSPECTIVE
        and any(k.perspective == p for k in candidates)
    ]
    if thin:
        rationale["_coverage_warning"] = (
            f"Perspectives below the {MIN_PER_PERSPECTIVE}-KPI minimum: {', '.join(thin)}. "
            "The library pack may need extending for this profile."
        )

    # A scorecard of only lagging indicators is a post-mortem, not a tracker.
    # If the library cannot reach the target mix, say so rather than quietly
    # shipping a backward-looking dashboard.
    leading = sum(1 for k in selected if k.timing == Timing.leading)
    share = leading / len(selected) if selected else 0.0
    if share < MIN_LEADING_SHARE:
        rationale["_leading_warning"] = (
            f"Only {share:.0%} of the selected KPIs are leading indicators "
            f"(target {MIN_LEADING_SHARE:.0%}). The available library pack is "
            f"lagging-heavy; adding leading metrics (pipeline, funnel, usage, "
            f"engagement, capacity) would improve early-warning value."
        )

    return KPISet(
        north_star=north_star.id,
        kpis=selected,
        rationale=rationale,
        dropped=dropped,
    )


def _explain(kpi: KPI, profile: CompanyProfile, score: float) -> str:
    reasons = []
    if profile.intent.primary_objective.value in kpi.serves_objectives:
        reasons.append(f"directly serves the '{profile.intent.primary_objective.value}' objective")
    if kpi.timing == Timing.leading:
        reasons.append("leading indicator")
    if kpi.benchmark:
        reasons.append("externally benchmarkable")
    reasons.append(f"{kpi.perspective.value} perspective coverage")
    return f"{'; '.join(reasons)} (score {score:.1f})"
