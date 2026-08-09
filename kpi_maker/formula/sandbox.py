"""The AST whitelist. The single security boundary for user expressions.

Both expression languages in the codebase parse with `ast` and walk an explicit
allow-list of node types — never `eval()`, never `exec()`. This module owns that
list so there is exactly one of it:

  * `kpi/expr.py` — booleans over a CompanyProfile (`applies_when`)
  * `formula/evaluate.py` — arithmetic over pandas Series

Two whitelists would drift, and the one that drifted would be the one nobody
was looking at. A node type is dangerous or it is not; that judgement belongs in
one place.

**What is excluded, and why it matters.** Python's object graph is fully
reachable from any single attribute access: `().__class__.__bases__[0]
.__subclasses__()` walks from an empty tuple to every loaded class, including
subprocess handles. So `ast.Subscript` and unrestricted `ast.Attribute` are not
merely unnecessary here — they are the entire sandbox escape. Attribute access
is permitted syntactically because both languages need dotted names
(`business_model.type`, `monthly_financials.revenue`), but each resolver turns a
dotted chain into a *lookup in a dict it controls* rather than a `getattr` walk,
and rejects any name it does not recognise.
"""
from __future__ import annotations

import ast
from typing import Iterable, Tuple

from .errors import FormulaError

# Shared by both languages: literals, names, dotted chains, boolean and
# comparison logic. Deliberately no Subscript, no Call, no comprehension, no
# lambda, no walrus, no f-string, no starred argument.
BASE_NODES: Tuple[type, ...] = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name,
    ast.Attribute, ast.Constant, ast.Tuple, ast.List,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Load,
)

# Arithmetic, and calls restricted to the function registry. `ast.Call` is safe
# only because the evaluator resolves `node.func` against the registry by name
# and refuses anything else — including a call whose target is an attribute or
# the result of another expression.
ARITHMETIC_NODES: Tuple[type, ...] = BASE_NODES + (
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.Call, ast.keyword, ast.IfExp,
)

# Node types worth naming explicitly when refused. A bare "disallowed syntax:
# Subscript" leaves the user guessing; these say what to do instead.
_EXPLANATIONS = {
    "Subscript": "indexing with [] is not available; use a function such as "
                 "SHIFT() or a filter argument instead",
    "Lambda": "inline functions are not available",
    "ListComp": "comprehensions are not available; use an aggregate function",
    "SetComp": "comprehensions are not available; use an aggregate function",
    "DictComp": "comprehensions are not available; use an aggregate function",
    "GeneratorExp": "generators are not available; use an aggregate function",
    "JoinedStr": "f-strings are not available",
    "NamedExpr": "assignment inside an expression is not available",
    "Starred": "argument unpacking is not available",
    "Await": "await is not available",
    "Dict": "dictionary literals are not available",
    "Set": "set literals are not available",
    "Slice": "slicing is not available",
}


def parse(expression: str, *, allowed: Iterable[type]) -> ast.Expression:
    """Parse and structurally validate, or raise FormulaError.

    Checks the whole tree up front rather than as the evaluator walks it, so a
    dangerous node buried in a branch that would not have been taken is still
    refused. `IF(1 > 0, 1, ().__class__)` must fail, not short-circuit past the
    problem.
    """
    text = (expression or "").strip()
    if not text:
        raise FormulaError("the formula is empty")

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(
            f"cannot parse the formula: {exc.msg}",
            offset=(exc.offset - 1) if exc.offset else None,
        ) from exc

    allowed_set = tuple(allowed)
    for node in ast.walk(tree):
        if isinstance(node, allowed_set):
            continue
        name = type(node).__name__
        raise FormulaError(
            f"{name} is not allowed in a formula",
            offset=getattr(node, "col_offset", None),
            hint=_EXPLANATIONS.get(name),
        )

    _reject_dunder(tree)
    return tree


def _reject_dunder(tree: ast.AST) -> None:
    """Belt and braces over the resolvers' own name checks.

    Every resolver already rejects unknown names, so a dunder would fail there
    anyway. Refusing it here as well means a future resolver written with a
    permissive fallback cannot quietly reopen the object graph.
    """
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        if name and name.startswith("__"):
            raise FormulaError(
                f"{name!r} is not a valid name in a formula",
                offset=getattr(node, "col_offset", None),
            )


def dotted_name(node: ast.AST) -> str:
    """Flatten an attribute chain to 'a.b.c'. Raises on anything else."""
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        raise FormulaError(
            "a name must be a plain identifier or a dotted path",
            offset=getattr(node, "col_offset", None),
        )
    parts.append(current.id)
    return ".".join(reversed(parts))
