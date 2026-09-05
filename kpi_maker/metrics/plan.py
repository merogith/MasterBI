"""Resolve a KPI's plan line for this run.

`targets.py` answers "what should this number be?" with one figure. This
answers "what did the business say it would be, month by month?", which is a
different question and the one IBCS notation is built on — actual against
plan, with the variance as the message rather than the level.

Two sources, and the distinction between them is carried all the way to the
reader on `MetricResult.plan_basis`:

* **stated** — figures the user supplied in `spec.plan.values`. A real plan.
* **derived** — built here from the target the pack already resolves, and only
  when `spec.plan.derive_from_target` is switched on. Not a plan; a path to a
  target, labelled as such.

The engine never invents the first kind. A KPI with no stated plan and no
derivation has **no plan line at all** — not a zero, not the target repeated
twelve times. Variance against a fabricated budget is worse than no variance,
because it looks like performance against a commitment nobody made.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

#: Kept as strings on the spec so a saved run round-trips through JSON; parsed
#: against the metric series' own period index here, in one place.
_MONTH_FORMAT = "%Y-%m"


def stated_plan(values: dict, index: pd.Index) -> Optional[pd.Series]:
    """The user's own figures, aligned to this metric's months.

    Months the user did not state are left as NaN rather than filled. A budget
    set for the second half of a year is a real thing, and interpolating the
    first half would put numbers on the page that nobody approved.
    """
    if not values:
        return None
    parsed = {}
    for month, value in values.items():
        try:
            period = pd.Period(str(month), freq="M")
        except Exception:                                 # noqa: BLE001
            # A malformed month is the user's typo, not a reason to lose the
            # rest of the plan. It is reported by the spec's own validation;
            # here it is simply not a month.
            continue
        parsed[period] = float(value)
    if not parsed:
        return None
    series = pd.Series(parsed, dtype=float).reindex(index)
    return None if series.dropna().empty else series


def derived_plan(actual: pd.Series, target: Optional[float]) -> Optional[pd.Series]:
    """A path to the target, shaped like last year rather than like a ruler.

    **Why not a straight line.** A seasonal business budgeted flat posts a
    variance every month that is purely the calendar — the exact artefact 3.4b
    spent an item removing from the detectors, and it would be worse here
    because a plan variance reads as a management failure rather than as a
    detector's noise. Scaling the prior year's own twelve months keeps the
    shape the business actually has and moves only the level.

    Refuses rather than approximating when it cannot see a full prior year, on
    the same rule 3.4b's seasonal index uses: with fewer than twelve months
    there is no shape to borrow, and the fallback would be the straight line
    this function exists to avoid.

    **One consequence, stated so nobody reads more into it than is there.** The
    target is the level for the final month, so at the final month the derived
    plan *is* the target and `vs_plan` equals `vs_target` exactly — measured,
    not argued. The new information a derived plan carries is the **path**: the
    eleven months before it, which is what a plan line on a chart and a
    month-by-month variance need and what a single target scalar cannot give.
    A stated plan has no such relationship; its variance is its own.
    """
    if target is None:
        return None
    clean = actual.dropna()
    if len(clean) < 12:
        return None

    # The plan covers the months the actual covers, and each one is built from
    # the same month a year earlier. Writing it the other way round — taking
    # the last twelve months and pushing them forward — produces a plan for
    # twelve months that do not exist yet and an empty line for every month
    # that does. Measured when this was first written: 0 of 25 KPIs got a plan.
    latest = clean.index[-1]
    base = latest - 12
    if base not in clean.index:
        return None

    # The target is a level for the *final* month, not an annual total, so the
    # scale is set by that month rather than by the sum. Anything else would
    # land the plan somewhere the target never named.
    anchor = float(clean.loc[base])
    if anchor == 0 or not pd.notna(anchor):
        return None
    scale = float(target) / anchor

    plan = pd.Series(index=actual.index, dtype=float)
    for period in actual.index:
        prior = period - 12
        if prior in clean.index:
            plan.loc[period] = float(clean.loc[prior]) * scale
    return None if plan.dropna().empty else plan


def resolve(kpi_id: str, actual: pd.Series, target: Optional[float],
            spec) -> tuple:
    """(plan series, basis) for one KPI. `(None, "")` when there is no plan.

    `spec` is a `PlanSpec`, or None for a run with no plan block at all —
    which is every run that existed before 5.1, and must stay identical.
    """
    if spec is None:
        return None, ""

    plan = stated_plan(spec.values.get(kpi_id) or {}, actual.index)
    if plan is not None:
        return plan, "stated"

    if spec.derive_from_target:
        plan = derived_plan(actual, target)
        if plan is not None:
            return plan, "derived"

    return None, ""
