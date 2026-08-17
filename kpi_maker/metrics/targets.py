"""What a KPI should be aiming at, from a rule a pack author can write.

`_resolve_target` matched a **fixed vocabulary of four exact strings**:

    max(benchmark_p50, current * 1.1)
    min(current * 0.85, 12)
    max(1.10, current + 0.05)
    min(current * 0.85, benchmark_p50)

Anything else — a fifth rule, one of these with different spacing, `current *
1.10` instead of `current * 1.1` — fell through to the benchmark median with
no error anywhere. Measured across the whole library today: **4 KPIs of 80
carry a `target_rule`, and all four are in the set**, so this is a trap rather
than a live bug. It becomes a live bug the moment anyone authors a fifth, and
Phase 4 plans ~600 record sheets across ~20 packs. A pack author would get a
plausible-looking target that silently ignored what they wrote, which is the
exact class of silence this project spends its time removing.

**The sandbox already exists**, so this adds no new evaluator: `formula/` parses
with `ast`, walks an explicit node whitelist and calls only registered
functions. The target rule is one more thing evaluated in it, with a small
named scope instead of a table's columns.

**The library's four rules are rewritten into the sandbox's own vocabulary**
(`MAX`, not `max`) rather than the sandbox growing lowercase aliases. One
formula language is the point; two spellings of the same function is how a
language ends up with three.

**Two failure modes, deliberately different.** A rule that does not parse, or
names something that does not exist, is an **authoring error** and raises where
the pack is loaded — a bad rule should never reach a run. A rule that is
perfectly good but cannot be evaluated *for this KPI on this run* — it wants a
benchmark and there is none published — is **data**, so it falls back and says
which fallback it took.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

from ..formula.errors import FormulaError
from ..formula.evaluate import Resolver, evaluate, validate
from ..formula.functions import ROW

#: Everything a target rule may name. Small on purpose: a target is a statement
#: about this metric against its own history and its cohort, and a rule that
#: could reach another KPI's series would make targets order-dependent.
TARGET_NAMES = ("current", "prior_year", "prior_month",
                "benchmark_p25", "benchmark_p50", "benchmark_p75",
                "alert_green", "alert_red")


class TargetResolver(Resolver):
    """The named scalars a target rule may use, and nothing else.

    `ROW` scope rather than `SERIES`: a target is one number, so the aggregate
    and time-shifting functions (`TTM`, `YOY`, `ROLLING`) have nothing to act
    on here and the scope check refuses them for free.
    """

    scope = ROW

    def __init__(self, values: Optional[Dict[str, Optional[float]]] = None) -> None:
        self.values = values or {}

    def resolve(self, name: str) -> Any:
        if name not in TARGET_NAMES:
            raise FormulaError(
                f"unknown name {name!r} in a target rule",
                hint=f"a target rule may use: {', '.join(TARGET_NAMES)}")
        value = self.values.get(name)
        if value is None:
            # Distinct from "unknown name": the rule is correct and this run
            # cannot supply the figure. `resolve_target` turns this into a
            # stated fallback rather than an error.
            raise FormulaError(f"{name} is not available for this KPI")
        return float(value)

    def known_names(self) -> List[str]:
        return list(TARGET_NAMES)


def validate_rule(rule: str) -> None:
    """Raise `FormulaError` unless this rule could ever be evaluated.

    Names are checked against `TARGET_NAMES` rather than against a run, so a
    pack fails at load — before any data exists — the way a formula that does
    not compile does.
    """
    dummy = TargetResolver(dict.fromkeys(TARGET_NAMES, 1.0))
    report = validate(rule, scope=ROW, resolver=dummy)
    if report["unknown"]:
        raise FormulaError(
            f"target rule names {', '.join(report['unknown'])}, which a target "
            f"rule cannot use",
            hint=f"available: {', '.join(TARGET_NAMES)}")

    # A condition is not a target, and it cannot be caught after the fact:
    # `sanitise` turns True into 1.0, so `current > benchmark_p50` arrives as a
    # perfectly ordinary number and would become a target of 1.0 in the metric's
    # own unit. Refused structurally instead — a comparison at the top level is
    # the mistake, while one inside `IF(condition, a, b)` is the point of it.
    body = ast.parse(rule.strip(), mode="eval").body
    if isinstance(body, (ast.Compare, ast.BoolOp)):
        raise FormulaError(
            "a target rule must produce a number, and this one is a condition",
            hint="use IF(condition, then, otherwise) to choose between two "
                 "targets")

    # Parsing is not enough either: `[current]` is a whitelisted node — the
    # sandbox allows list literals so `x in (a, b)` works — and would reach a
    # run and fall back silently, which is the failure this module exists to
    # end. One evaluation against dummy figures settles it at load.
    evaluate(rule, dummy)


def resolve_target(kpi, current: Optional[float],
                   prior_year: Optional[float] = None,
                   prior_month: Optional[float] = None) -> Optional[float]:
    """The target for one KPI, and the order the fallbacks are tried in."""
    # A target the user typed is not a suggestion to be improved on.
    if kpi.target_override is not None:
        return float(kpi.target_override)

    bench = kpi.benchmark
    bands = kpi.alert_bands
    p50 = bench.p50 if bench else None

    if kpi.target_rule:
        resolver = TargetResolver({
            "current": current,
            "prior_year": prior_year,
            "prior_month": prior_month,
            "benchmark_p25": bench.p25 if bench else None,
            "benchmark_p50": p50,
            "benchmark_p75": bench.p75 if bench else None,
            "alert_green": bands.green if bands else None,
            "alert_red": bands.red if bands else None,
        })
        try:
            value = evaluate(kpi.target_rule, resolver)
        except FormulaError:
            # The rule is well-formed — `validate_rule` said so when the pack
            # loaded — but this run cannot feed it. Fall through to the
            # benchmark, which is what a rule-less KPI gets anyway.
            value = None
        if value is not None:
            return float(value)

    if p50 is not None:
        return float(p50)
    if bands is not None:
        return float(bands.green)
    return None
