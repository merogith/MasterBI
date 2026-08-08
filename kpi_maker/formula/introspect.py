"""Which names does a formula use?

Needed in three places: cycle detection before evaluation, dependency ordering
in the metrics engine, and showing a user what their formula depends on while
they type it.
"""
from __future__ import annotations

import ast
from typing import List

from .sandbox import ARITHMETIC_NODES, dotted_name, parse


def references(expression: str) -> List[str]:
    """Every name the formula reads, excluding function names.

    A function call's target is a Name node too, so it has to be excluded
    explicitly — otherwise `SUM(x)` would report a dependency on a KPI called
    "SUM" and the dependency graph would be full of phantoms.
    """
    tree = parse(expression, allowed=ARITHMETIC_NODES)

    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    # Inside an aggregate the second argument is a filter over the table's own
    # columns, not a reference to anything the outer scope can resolve. Those
    # subtrees are skipped wholesale.
    filter_nodes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        from .functions import FUNCTIONS
        fn = FUNCTIONS.get(node.func.id) if isinstance(node.func, ast.Name) else None
        if fn is None or not fn.takes_column_ref:
            continue
        if len(node.args) == 2:
            filter_nodes.update(id(n) for n in ast.walk(node.args[1]))
        for keyword in node.keywords:
            filter_nodes.update(id(n) for n in ast.walk(keyword.value))

    names: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        if id(node) in filter_nodes:
            continue
        # Only take the top of an attribute chain: walking `a.b.c` yields the
        # Attribute for `a.b` as well, and reporting both is noise.
        if isinstance(node, ast.Name) and node.id in called:
            continue
        try:
            name = dotted_name(node)
        except Exception:                                   # noqa: BLE001
            continue
        names.append(name)

    # Drop names that are a prefix of a longer one from the same chain.
    out = []
    for name in names:
        if any(other != name and other.startswith(name + ".") for other in names):
            continue
        if name not in out:
            out.append(name)
    return sorted(out)


def kpi_references(expression: str) -> List[str]:
    """Just the bare names — the ones that could be another KPI's id."""
    return [n for n in references(expression) if "." not in n]


def aggregate_columns(expression: str) -> List[str]:
    """`table.column` references that are the argument of an aggregate.

    These follow different rules from an ordinary reference: `marketing.spend`
    is not a valid value on its own (several rows per month) but is perfectly
    valid as `SUM(marketing.spend)`. A validator that checked both the same way
    would reject formulas that evaluate correctly — which is the worst direction
    for a validator to be wrong in.
    """
    from .functions import FUNCTIONS

    tree = parse(expression, allowed=ARITHMETIC_NODES)
    out: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        fn = FUNCTIONS.get(node.func.id)
        if fn is None or not fn.takes_column_ref or not node.args:
            continue
        try:
            name = dotted_name(node.args[0])
        except Exception:                                   # noqa: BLE001
            continue
        if name not in out:
            out.append(name)
    return out
