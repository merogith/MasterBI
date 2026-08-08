"""Tests for the formula language.

A formula engine is a language, and languages are where sandboxes leak and edge
cases hide. This is the most adversarial suite in the repo on purpose.

Five groups:

  1. **Sandbox** — every escape route Python offers must be refused. These are
     not hypothetical: `().__class__.__bases__[0].__subclasses__()` walks from a
     literal to every loaded class, so `Subscript` and unguarded `Attribute` are
     the whole ballgame.
  2. **Grain** — the fact tables come in three shapes and only one is already a
     series. A monthly column resolves; a month-by-dimension column must be
     aggregated; an entity table cannot produce a monthly series at all.
  3. **Scope** — time functions belong to KPIs, not to calculated columns, and
     saying so at validation beats producing nonsense at evaluation.
  4. **Equivalence** — six builtins re-expressed as formulas must match their
     hand-written implementations. This is the strongest evidence the engine is
     real rather than merely plausible.
  5. **Contract** — validate and evaluate must agree. A validator that refuses
     working input is worse than no validator.

    python -m tests.formula
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile
from kpi_maker.datagen.saas import generate
from kpi_maker.formula import FormulaError, evaluate, references, validate
from kpi_maker.formula.evaluate import RowResolver, SeriesResolver
from kpi_maker.kpi.schema import user_kpi
from kpi_maker.kpi.selection import select
from kpi_maker.metrics.engine import compute
from kpi_maker.spec.schema import MetricsSpec

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "northwind_saas.json"

_failures: List[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not ok:
        _failures.append(f"{name}: {detail}")
        print(f"  FAIL   {name}" + (f"  — {detail}" if detail else ""))


def must_raise(name: str, expression: str, resolver, contains: str = "") -> None:
    """The expression must be refused. Silent success is the failure mode."""
    try:
        evaluate(expression, resolver)
    except FormulaError as exc:
        check(name, contains.lower() in str(exc).lower() if contains else True,
              f"raised, but message lacked {contains!r}: {exc}")
        return
    except Exception as exc:                                # noqa: BLE001
        # A non-FormulaError still means it did not execute, but the user would
        # see a stack trace instead of an explanation.
        check(name, False, f"raised {type(exc).__name__} instead of FormulaError: {exc}")
        return
    check(name, False, "was ACCEPTED — the sandbox let this through")


# --------------------------------------------------------------------------

def test_whitelist_directly() -> None:
    """The whitelist must reject these at PARSE time, before any evaluation.

    Written because a mutation test caught the suite passing for the wrong
    reason: with `Subscript` added to the allow-list and the dunder guard
    disabled, every "escape refused" case below still passed, because the
    evaluator's fallthrough and `dotted_name` happened to catch them second.
    That is defence in depth working, but it meant a hole in the actual
    security boundary was invisible to the tests. These assert the boundary
    itself, so it cannot quietly stop being load-bearing.
    """
    print("\nwhitelist — refused at parse time, not merely at evaluation")
    from kpi_maker.formula.sandbox import ARITHMETIC_NODES, parse

    for name, expression in [
        ("subscript", "arr[0]"),
        ("slice", "arr[0:2]"),
        ("lambda", "(lambda: 1)()"),
        ("list comprehension", "[x for x in arr]"),
        ("generator", "(x for x in arr)"),
        ("dict literal", "{'a': 1}"),
        ("set literal", "{1, 2}"),
        ("f-string", "f'{arr}'"),
        ("walrus", "(x := 1)"),
        ("starred", "MAX(*arr)"),
        ("dunder name", "__builtins__"),
        ("dunder attribute", "().__class__"),
        ("nested dunder", "IF(1, 1, x.__class__)"),
    ]:
        try:
            parse(expression, allowed=ARITHMETIC_NODES)
            check(f"parse refuses {name}", False, "PARSED — the whitelist has a hole")
        except FormulaError:
            check(f"parse refuses {name}", True)

    # And the things that must still parse, so the whitelist is not just "no".
    for name, expression in [
        ("arithmetic", "a + b * 2 - 1"),
        ("call", "SUM(t.c)"),
        ("keyword filter", "SUM(t.c, k='v')"),
        ("comparison", "a > b"),
        ("ternary", "1 if a > b else 2"),
        ("dotted name", "monthly_financials.revenue"),
    ]:
        try:
            parse(expression, allowed=ARITHMETIC_NODES)
            check(f"parse allows {name}", True)
        except FormulaError as exc:
            check(f"parse allows {name}", False, str(exc))


def test_sandbox(series_resolver) -> None:
    print("\nsandbox — every escape must be refused")
    escapes = [
        ("dunder attribute", "().__class__"),
        ("subclass walk", "().__class__.__bases__"),
        ("import", "__import__('os')"),
        ("builtins", "__builtins__"),
        ("globals", "globals()"),
        ("eval", "eval('1')"),
        ("exec", "exec('x=1')"),
        ("open a file", "open('/etc/passwd')"),
        ("getattr", "getattr(arr, 'values')"),
        ("subscript", "arr[0]"),
        ("slice", "arr[0:2]"),
        ("lambda", "(lambda: 1)()"),
        ("comprehension", "[x for x in arr]"),
        ("generator", "(x for x in arr)"),
        ("dict literal", "{'a': 1}"),
        ("set literal", "{1, 2}"),
        ("f-string", "f'{arr}'"),
        ("walrus", "(x := 1)"),
        ("attribute call", "arr.sum()"),
        ("chained attribute call", "monthly_financials.revenue.sum()"),
        ("unknown function", "DESTROY(arr)"),
        ("method on a string", "'abc'.upper()"),
    ]
    for name, expression in escapes:
        must_raise(name, expression, series_resolver)

    # A dangerous node inside a branch that would not be taken must still be
    # refused: the tree is checked whole, before anything is evaluated.
    must_raise("dangerous node in a dead branch",
               "IF(1 > 0, 1, ().__class__)", series_resolver)

    # Resource limits.
    must_raise("huge exponent", "2 ** 999999", series_resolver, contains="exponent")
    must_raise("deeply nested", "(" * 250 + "1" + ")" * 250, series_resolver)
    must_raise("very long expression", " + ".join(["1"] * 500), series_resolver,
               contains="complex")


def test_grain(tables, series_resolver) -> None:
    print("\ngrain — three table shapes, three behaviours")

    monthly = evaluate("monthly_financials.revenue", series_resolver)
    check("monthly column resolves directly",
          isinstance(monthly, pd.Series) and len(monthly) == 36, f"got {type(monthly)}")

    must_raise("month x dimension column needs an aggregate", "marketing.spend",
               series_resolver, contains="several rows per month")
    must_raise("entity table is not a monthly series", "customers.initial_acv",
               series_resolver, contains="no month column")
    must_raise("entity table cannot be aggregated by month",
               "SUM(customers.initial_acv)", series_resolver, contains="no month column")

    agg = evaluate("SUM(marketing.spend)", series_resolver)
    expected = tables["marketing"].groupby("month")["spend"].sum()
    check("aggregate groups by month",
          np.allclose(agg.dropna().values, expected.values), "values differ")

    filtered = evaluate("SUM(mrr_movements.delta_mrr, movement_type='churn')",
                        series_resolver)
    mov = tables["mrr_movements"]
    expected = (mov[mov.movement_type == "churn"].groupby("month")["delta_mrr"].sum()
                .reindex(series_resolver.index, fill_value=0.0))
    check("keyword filter matches a manual groupby",
          np.allclose(filtered.values, expected.values), "values differ")

    both_syntaxes = evaluate("SUM(mrr_movements.delta_mrr, movement_type == 'churn')",
                             series_resolver)
    check("'=' and '==' filters agree",
          np.allclose(filtered.values, both_syntaxes.values))

    must_raise("unknown table", "SUM(nope.thing)", series_resolver, contains="unknown table")
    must_raise("unknown column", "monthly_financials.nope", series_resolver,
               contains="no column")


def test_scope(tables, series_resolver) -> None:
    print("\nscope — time functions belong to KPIs, not to rows")
    row = RowResolver(tables["customers"], "customers")

    for fname, expression in [
        ("TTM", "TTM(initial_acv)"), ("YOY", "YOY(initial_acv)"),
        ("SHIFT", "SHIFT(initial_acv, 1)"), ("ROLLING", "ROLLING(initial_acv, 3)"),
        ("CUMSUM", "CUMSUM(initial_acv)"), ("SUM aggregate", "SUM(customers.initial_acv)"),
    ]:
        must_raise(f"{fname} refused in row scope", expression, row)
        # ...and refused at VALIDATION, before any data is touched.
        try:
            validate(expression, scope="row")
            check(f"{fname} refused by validate too", False, "validate accepted it")
        except FormulaError:
            check(f"{fname} refused by validate too", True)

    delta = evaluate("final_acv - initial_acv", row)
    check("row arithmetic works", len(delta) == len(tables["customers"]))

    conditional = evaluate("IF(final_acv > initial_acv, 1, 0)", row)
    manual = (tables["customers"].final_acv > tables["customers"].initial_acv).astype(float)
    check("row IF matches a manual comparison",
          np.allclose(conditional.values, manual.values))

    # Maths is legal in both scopes.
    for expression in ["ABS(initial_acv)", "CLIP(initial_acv, 0, 1000)",
                       "SAFE_DIV(final_acv, initial_acv)", "ROUND(initial_acv, 2)"]:
        try:
            evaluate(expression, row)
            check(f"shared function in row scope: {expression.split('(')[0]}", True)
        except FormulaError as exc:
            check(f"shared function in row scope: {expression.split('(')[0]}", False, str(exc))


def test_equivalence(profile, tables) -> None:
    print("\nequivalence — builtins re-expressed as formulas")
    cases = [
        ("rule_of_40", "YOY(monthly_financials.arr) + SAFE_DIV("
                       "TTM(monthly_financials.ebitda), TTM(monthly_financials.revenue))"),
        ("arpa", "SAFE_DIV(monthly_financials.mrr, customer_count)"),
        ("sm_pct_revenue", "SAFE_DIV(TTM(monthly_financials.sales_cost) + "
                           "TTM(monthly_financials.marketing_cost), "
                           "TTM(monthly_financials.revenue))"),
        ("quick_ratio",
         "SAFE_DIV(TTM(SUM(mrr_movements.delta_mrr, movement_type='new')) + "
         "TTM(SUM(mrr_movements.delta_mrr, movement_type='expansion')), "
         "ABS(TTM(SUM(mrr_movements.delta_mrr, movement_type='churn'))) + "
         "ABS(TTM(SUM(mrr_movements.delta_mrr, movement_type='contraction'))))"),
        ("net_new_arr", "SUM(mrr_movements.delta_mrr) * 12"),
        ("rnd_pct_revenue", "SAFE_DIV(monthly_financials.rnd_cost, "
                            "monthly_financials.revenue)"),
    ]
    customs = [user_kpi(f"chk {bid}", expr, "ratio", "higher_is_better",
                        kpi_id=f"chk_{bid}").model_dump(mode="json")
               for bid, expr in cases]
    kpi_set = select(profile, overrides=MetricsSpec(
        custom=customs, pinned=[bid for bid, _ in cases]))
    results = {r.kpi.id: r for r in compute(kpi_set, tables, profile)}

    for bid, _ in cases:
        builtin, formula = results.get(bid), results.get(f"chk_{bid}")
        if not (builtin and formula and builtin.computed and formula.computed):
            check(f"{bid} equivalence", False,
                  f"not both computed ({builtin and builtin.reason}"
                  f" / {formula and formula.reason})")
            continue
        a, b = builtin.series.dropna(), formula.series.dropna()
        common = a.index.intersection(b.index)
        check(f"{bid} equivalence",
              len(common) > 0 and np.allclose(a[common].values, b[common].values,
                                              rtol=1e-9, atol=1e-9),
              f"{builtin.current} vs {formula.current} over {len(common)} months")


def test_references_and_cycles(profile, tables) -> None:
    print("\nreferences and cycles")
    check("references finds KPI ids and columns",
          references("SAFE_DIV(arr, customer_count) + SUM(marketing.spend)")
          == ["arr", "customer_count", "marketing.spend"],
          str(references("SAFE_DIV(arr, customer_count) + SUM(marketing.spend)")))
    check("filter columns are not references",
          references("SUM(mrr_movements.delta_mrr, movement_type='churn')")
          == ["mrr_movements.delta_mrr"],
          str(references("SUM(mrr_movements.delta_mrr, movement_type='churn')")))
    check("function names are not references",
          "SUM" not in references("SUM(marketing.spend)"))

    def reason_for(kpi_id, customs):
        kpi_set = select(profile, overrides=MetricsSpec(custom=customs))
        return {r.kpi.id: r for r in compute(kpi_set, tables, profile)}[kpi_id].reason

    pair = [user_kpi("A", "b_m + 1", "ratio", "higher_is_better", kpi_id="a_m"),
            user_kpi("B", "a_m * 2", "ratio", "higher_is_better", kpi_id="b_m")]
    reason = reason_for("a_m", [k.model_dump(mode="json") for k in pair])
    check("mutual cycle detected and named", "circular" in reason and "a_m" in reason, reason)

    selfref = [user_kpi("S", "s_m + 1", "ratio", "higher_is_better", kpi_id="s_m")]
    reason = reason_for("s_m", [k.model_dump(mode="json") for k in selfref])
    check("self-reference detected", "circular" in reason, reason)

    # A formula may depend on a KPI the selection engine did not choose.
    dep = [user_kpi("Ent share", "SAFE_DIV(enterprise_customers, customer_count)",
                    "pct", "higher_is_better", kpi_id="ent_share")]
    kpi_set = select(profile, overrides=MetricsSpec(
        custom=[k.model_dump(mode="json") for k in dep], excluded=["enterprise_customers"]))
    result = {r.kpi.id: r for r in compute(kpi_set, tables, profile)}["ent_share"]
    check("formula can use an unselected KPI", result.computed,
          result.reason)
    check("that dependency stays off the scorecard",
          not any(k.id == "enterprise_customers" for k in kpi_set.kpis))


def test_validate_agrees_with_evaluate(series_resolver) -> None:
    print("\ncontract — validate and evaluate must agree")
    expressions = [
        "SUM(marketing.spend)",
        "SAFE_DIV(SUM(marketing.spend), SUM(marketing.leads))",
        "SUM(mrr_movements.delta_mrr, movement_type='churn')",
        "SUM(customers.initial_acv)",
        "marketing.spend",
        "monthly_financials.revenue",
        "monthly_financials.nope",
        "SUM(nope.thing)",
        "TTM(monthly_financials.revenue)",
    ]
    for expression in expressions:
        try:
            report = validate(expression, scope="series", resolver=series_resolver)
            validates = report["ok"]
        except FormulaError:
            validates = False
        try:
            evaluate(expression, series_resolver)
            evaluates = True
        except FormulaError:
            evaluates = False
        check(f"agree: {expression[:46]}", validates == evaluates,
              f"validate={validates} evaluate={evaluates}")


def test_numeric_edges(series_resolver) -> None:
    print("\nnumeric edges")
    zero = evaluate("SAFE_DIV(monthly_financials.revenue, 0)", series_resolver)
    check("SAFE_DIV by zero yields NaN, not inf", bool(zero.isna().all()))

    raw = evaluate("monthly_financials.revenue / 0", series_resolver)
    check("plain division by zero is sanitised to NaN", bool(raw.isna().all()))

    default = evaluate("SAFE_DIV(monthly_financials.revenue, 0, -1)", series_resolver)
    check("SAFE_DIV default is honoured", bool((default == -1).all()))

    constant = evaluate("42", series_resolver)
    check("a constant becomes a full series",
          isinstance(constant, pd.Series) and len(constant) == 36
          and bool((constant == 42).all()))

    comparison = evaluate("monthly_financials.revenue > 0", series_resolver)
    check("a boolean series becomes numeric",
          comparison.dtype == float and bool((comparison == 1.0).all()))

    must_raise("a bare string is not a number", "'hello'", series_resolver)
    must_raise("empty formula", "", series_resolver)
    must_raise("whitespace only", "   ", series_resolver)


# --------------------------------------------------------------------------

def run() -> int:
    profile = load_profile(SAMPLE)
    data = generate(profile)
    tables = data.tables
    series_resolver = SeriesResolver(
        tables, pd.Index(tables["monthly_financials"]["month"]), profile=profile)

    suites: List[Callable] = [
        lambda: test_whitelist_directly(),
        lambda: test_sandbox(series_resolver),
        lambda: test_grain(tables, series_resolver),
        lambda: test_scope(tables, series_resolver),
        lambda: test_equivalence(profile, tables),
        lambda: test_references_and_cycles(profile, tables),
        lambda: test_validate_agrees_with_evaluate(series_resolver),
        lambda: test_numeric_edges(series_resolver),
    ]
    for suite in suites:
        try:
            suite()
        except Exception as exc:                            # noqa: BLE001
            _failures.append(f"crash: {type(exc).__name__}: {exc}")
            print(f"  CRASH  {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=4)

    print(f"\n{'=' * 70}")
    print(f"checks: {_checks}  failures: {len(_failures)}")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(run())
