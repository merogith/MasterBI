"""Run the identity checks, with strictness that depends on where the data came from.

    synthetic -> both tiers fatal. Hitting the profile is the generator's job.
    upload    -> Tier 1 fatal, Tier 2 reported. The data is the truth and the
                 profile is the guess, so a mismatch is a finding.

`ReconciliationError` lives here now rather than in the generator, because the
rule it enforces — nothing renders on data that fails reconciliation — was never
really about synthetic data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .identities import CHECKS, Tier, growth_note


class ReconciliationError(AssertionError):
    """Raised when data fails an identity it must satisfy to be rendered."""


class UncheckedArchetype(ReconciliationError):
    """A generated archetype that no structural identity was written for."""


def _guard_against_vacuity(archetype: Optional[str], source: str,
                           coverage: int, failures: List[str]) -> None:
    """Refuse to pass a generated archetype nothing actually checked.

    Every check skips when the table it needs is absent, which is right for a
    partial upload and dangerous for a new sector: an archetype that emits no
    `mrr_movements` skips all fifteen subscription identities and the gate
    reports a clean pass over an empty set. "Nothing renders on data that fails
    reconciliation" would then be satisfied vacuously, which is worse than not
    having the rule — the report claims to be reconciled and is not.

    Universal P&L identities deliberately do not count. They hold for any
    business, so letting them satisfy the guard would wave a sector through on
    arithmetic that says nothing about what it sells — exactly the vacuous pass
    this exists to stop.

    Uploads are exempt. Partial coverage is the normal case there: the user
    brought a transaction log, not a complete data warehouse, and P2's whole
    design is to report what could not be checked rather than refuse to render.
    """
    if source != "synthetic" or archetype is None or coverage or failures:
        return
    raise UncheckedArchetype(
        f"no structural identity ran for archetype {archetype!r}, so the "
        f"reconciliation gate checked nothing specific to it. Generated data "
        f"must be verifiable: add checks with archetypes=({archetype!r},) in "
        f"contract/identities.py before shipping this generator."
    )


@dataclass
class GateResult:
    checks: List[str] = field(default_factory=list)      # human-readable, for the appendix
    warnings: List[str] = field(default_factory=list)    # Tier 2 misses on uploaded data
    skipped: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return True      # a returned GateResult means nothing fatal fired


def run_gate(tables: Dict[str, pd.DataFrame], profile,
             source: str = "synthetic",
             archetype: Optional[str] = None) -> GateResult:
    """Check every identity. Raise on the ones this source may not violate."""
    fatal_tiers = {Tier.structural, Tier.calibration} if source == "synthetic" \
        else {Tier.structural}

    result = GateResult()
    failures: List[str] = []
    # Structural checks written *for this archetype* that actually executed.
    # Counted for the vacuity guard below; see its comment for why universal
    # checks are deliberately not counted.
    sector_coverage = 0

    for spec in CHECKS:
        if not spec.applies_to(archetype):
            continue
        outcome = spec.run(tables, profile)
        if (not outcome.skipped and spec.tier is Tier.structural
                and spec.archetypes is not None):
            sector_coverage += 1
        if outcome.skipped:
            result.skipped.append(f"{spec.name}: skipped ({outcome.detail})")
            continue
        if outcome.passed:
            result.checks.append(f"{spec.name}: pass")
            continue

        message = f"{spec.name} FAILED. {outcome.detail}".strip()
        if spec.tier in fatal_tiers:
            failures.append(message)
        else:
            # Tier 2 on an upload: the data disagrees with the profile. Say so
            # loudly in the appendix, but let the run produce artifacts — the
            # numbers are the user's own.
            result.warnings.append(
                f"{spec.name}: the data does not match the profile. {outcome.detail}")
            result.checks.append(f"{spec.name}: reported (see warnings)")

    _guard_against_vacuity(archetype, source, sector_coverage, failures)

    if failures:
        raise ReconciliationError(
            f"{len(failures)} identity check(s) failed:\n  - "
            + "\n  - ".join(failures)
        )

    note = growth_note(tables, profile)
    if note:
        result.checks.append(note)
    return result
