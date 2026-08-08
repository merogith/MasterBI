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
from typing import Dict, List

import pandas as pd

from .identities import CHECKS, Tier, growth_note


class ReconciliationError(AssertionError):
    """Raised when data fails an identity it must satisfy to be rendered."""


@dataclass
class GateResult:
    checks: List[str] = field(default_factory=list)      # human-readable, for the appendix
    warnings: List[str] = field(default_factory=list)    # Tier 2 misses on uploaded data
    skipped: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return True      # a returned GateResult means nothing fatal fired


def run_gate(tables: Dict[str, pd.DataFrame], profile,
             source: str = "synthetic") -> GateResult:
    """Check every identity. Raise on the ones this source may not violate."""
    fatal_tiers = {Tier.structural, Tier.calibration} if source == "synthetic" \
        else {Tier.structural}

    result = GateResult()
    failures: List[str] = []

    for spec in CHECKS:
        outcome = spec.run(tables, profile)
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

    if failures:
        raise ReconciliationError(
            f"{len(failures)} identity check(s) failed:\n  - "
            + "\n  - ".join(failures)
        )

    note = growth_note(tables, profile)
    if note:
        result.checks.append(note)
    return result
