"""Evaluate a parsed formula against a scope's bindings.

The walk is small; the interesting part is name resolution, which differs per
scope and is the only place a name becomes a value. Neither resolver ever calls
`getattr` on user input — each looks the name up in a mapping it built itself
and refuses anything it does not recognise.
"""
from __future__ import annotations

import ast
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .errors import FormulaError
from .functions import FUNCTIONS, ROW, SERIES, Function, sanitise, scope_error
from .sandbox import ARITHMETIC_NODES, dotted_name, parse

Scope = str        # "series" | "row"

# A formula is a one-line expression over at most a few thousand rows. Anything
# that manages to be slow is a mistake or an attack, and either way the studio
# must not hang on it.
MAX_NODES = 400


class Resolver:
    """Binds names to values for one scope. Subclasses own the lookup."""

    scope: Scope = SERIES

    def resolve(self, name: str) -> Any:
        raise NotImplementedError

    def known_names(self) -> List[str]:
        return []

    def column_frame(self, reference: str) -> Tuple[pd.DataFrame, Optional[str]]:
        """Resolve `table.column` to (frame, column) for an aggregate."""
        raise FormulaError(
            f"aggregate functions are not available in a {self.scope} formula")

    @property
    def index(self):
        return None


# --------------------------------------------------------------------------

class SeriesResolver(Resolver):
    """KPI ids, monthly table columns and profile scalars -> monthly Series.

    Resolution order is deliberate: KPI ids first, because composing existing
    metrics is the main thing users will do, and a KPI id is the most specific
    kind of name. A table column needs a dot, so the two cannot collide.
    """

    scope = SERIES

    def __init__(self, tables: Dict[str, pd.DataFrame], month_index,
                 kpi_lookup: Optional[Callable[[str], Optional[pd.Series]]] = None,
                 profile=None) -> None:
        self.tables = tables
        self.month_index = month_index
        self.kpi_lookup = kpi_lookup
        self.profile = profile

    @property
    def index(self):
        return self.month_index

    def resolve(self, name: str) -> Any:
        if "." not in name:
            if self.kpi_lookup is not None:
                series = self.kpi_lookup(name)
                if series is not None:
                    return series
            raise FormulaError(
                f"unknown name {name!r}",
                hint="expected a KPI id, or table.column for a fact table",
            )

        head, _, tail = name.partition(".")

        if head == "profile":
            return self._profile_value(tail, name)

        frame = self.tables.get(head)
        if frame is None:
            raise FormulaError(
                f"unknown table {head!r} in {name!r}",
                hint=f"available: {', '.join(sorted(self.tables))}",
            )
        if tail not in frame.columns:
            raise FormulaError(
                f"{head!r} has no column {tail!r}",
                hint=f"columns: {', '.join(map(str, frame.columns))}",
            )
        return self._as_monthly(frame, head, tail, name)

    def _as_monthly(self, frame: pd.DataFrame, table: str, column: str,
                    name: str) -> pd.Series:
        """A monthly-grain column resolves directly; anything else must aggregate.

        The fact tables come in three grains and only one of them is a series
        already. Silently summing a month x dimension table would hide the
        choice of aggregation, and silently broadcasting an entity table's
        column would invent 36 copies of a number that has no month.
        """
        if "month" not in frame.columns:
            raise FormulaError(
                f"{table!r} has no month column, so {name!r} is not a monthly series",
                hint=f"{table} is one row per entity; use it in a calculated "
                     f"column instead of a KPI formula",
            )
        if frame["month"].duplicated().any():
            raise FormulaError(
                f"{table!r} has several rows per month, so {name!r} is ambiguous",
                hint=f"wrap it in an aggregate, e.g. SUM({name})",
            )
        return (frame.set_index("month")[column]
                .reindex(self.month_index).astype("float64"))

    def _profile_value(self, path: str, name: str) -> float:
        obj: Any = self.profile
        for part in path.split("."):
            if obj is None:
                break
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        if obj is None:
            raise FormulaError(f"unknown profile field {name!r}")
        if isinstance(obj, bool) or not isinstance(obj, (int, float)):
            raise FormulaError(
                f"{name!r} is not a number",
                hint="only numeric profile fields can be used in a formula",
            )
        return float(obj)

    def column_frame(self, reference: str) -> Tuple[pd.DataFrame, Optional[str]]:
        head, _, tail = reference.partition(".")
        frame = self.tables.get(head)
        if frame is None:
            raise FormulaError(
                f"unknown table {head!r}",
                hint=f"available: {', '.join(sorted(self.tables))}",
            )
        if "month" not in frame.columns:
            raise FormulaError(
                f"{head!r} has no month column, so it cannot be aggregated by month",
                hint=f"{head} is one row per entity, not a time series",
            )
        if tail and tail not in frame.columns:
            raise FormulaError(f"{head!r} has no column {tail!r}")
        return frame, (tail or None)

    def known_names(self) -> List[str]:
        names = [f"{t}.{c}" for t, f in sorted(self.tables.items())
                 for c in f.columns if c != "month"]
        return sorted(names)


class RowResolver(Resolver):
    """Bare column names within one table -> that table's columns."""

    scope = ROW

    def __init__(self, frame: pd.DataFrame, table_name: str = "") -> None:
        self.frame = frame
        self.table_name = table_name

    @property
    def index(self):
        return self.frame.index

    def resolve(self, name: str) -> Any:
        # Accept both `revenue` and `monthly_financials.revenue` so a user who
        # learned the KPI syntax is not caught out by the shorter form here.
        column = name.split(".")[-1] if name.startswith(f"{self.table_name}.") else name
        if column in self.frame.columns:
            return self.frame[column]
        raise FormulaError(
            f"unknown column {name!r}",
            hint=f"columns in {self.table_name}: "
                 f"{', '.join(map(str, self.frame.columns))}",
        )

    def known_names(self) -> List[str]:
        return sorted(map(str, self.frame.columns))


# --------------------------------------------------------------------------

def _evaluate_node(node: ast.AST, resolver: Resolver) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body, resolver)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)) or node.value is None:
            return node.value
        if isinstance(node.value, str):
            return node.value
        raise FormulaError(f"unsupported literal {node.value!r}")

    if isinstance(node, (ast.Name, ast.Attribute)):
        return resolver.resolve(dotted_name(node))

    if isinstance(node, ast.Call):
        return _evaluate_call(node, resolver)

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand, resolver)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return ~operand if isinstance(operand, pd.Series) else (not operand)

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, resolver)
        right = _evaluate_node(node.right, resolver)
        return sanitise(_binop(node.op, left, right))

    if isinstance(node, ast.BoolOp):
        values = [_evaluate_node(v, resolver) for v in node.values]
        if any(isinstance(v, pd.Series) for v in values):
            # `and`/`or` cannot short-circuit element-wise, so combine as masks.
            result = values[0]
            for other in values[1:]:
                result = (result & other) if isinstance(node.op, ast.And) else (result | other)
            return result
        return all(values) if isinstance(node.op, ast.And) else any(values)

    if isinstance(node, ast.Compare):
        return _compare(node, resolver)

    if isinstance(node, ast.IfExp):
        return FUNCTIONS["IF"].fn(
            _evaluate_node(node.test, resolver),
            _evaluate_node(node.body, resolver),
            _evaluate_node(node.orelse, resolver),
        )

    raise FormulaError(f"cannot evaluate {type(node).__name__}")


def _binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    if isinstance(op, ast.Pow):
        # An unbounded exponent turns a one-line formula into a memory bomb.
        if isinstance(right, (int, float)) and abs(right) > 32:
            raise FormulaError("exponent is too large", hint="use a power of 32 or less")
        return left ** right
    raise FormulaError(f"unsupported operator {type(op).__name__}")


def _compare(node: ast.Compare, resolver: Resolver) -> Any:
    left = _evaluate_node(node.left, resolver)
    result = None
    for op, comparator in zip(node.ops, node.comparators):
        right = _evaluate_node(comparator, resolver)
        current = _compare_one(op, left, right)
        result = current if result is None else (result & current)
        left = right
    return result


def _compare_one(op: ast.cmpop, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    raise FormulaError(f"unsupported comparison {type(op).__name__}")


def _evaluate_call(node: ast.Call, resolver: Resolver) -> Any:
    if not isinstance(node.func, ast.Name):
        raise FormulaError(
            "only the built-in functions can be called",
            offset=getattr(node, "col_offset", None),
        )
    name = node.func.id
    fn = FUNCTIONS.get(name)
    if fn is None:
        raise FormulaError(
            f"unknown function {name!r}",
            offset=getattr(node, "col_offset", None),
            hint=_suggest(name),
        )
    # Aggregates accept `column='value'` as an equality filter. In a spreadsheet
    # `=` means comparison, but Python's parser reads it inside a call as a
    # keyword argument — so rather than forcing users to write `==`, the
    # keyword form is given the meaning they already expect.
    if node.keywords and not fn.takes_column_ref:
        raise FormulaError(f"{name}: keyword arguments are not supported",
                           hint=fn.signature)
    if resolver.scope not in fn.scopes:
        raise scope_error(fn, resolver.scope)

    fn.check_arity(len(node.args))

    if fn.takes_column_ref:
        return _evaluate_aggregate(node, fn, resolver)

    args = [_evaluate_node(arg, resolver) for arg in node.args]
    return sanitise(fn.fn(*args))


def _evaluate_aggregate(node: ast.Call, fn: Function, resolver: Resolver) -> Any:
    """`SUM(table.column [, filter])` — group by month, optionally filtered.

    The column argument is NOT evaluated first: resolving `marketing.spend` as a
    value would fail (it is not monthly), and the aggregate is precisely the
    thing that makes it monthly. So the reference is read syntactically.
    """
    reference = dotted_name(node.args[0])
    frame, column = resolver.column_frame(reference)
    if column is None and fn.name != "COUNT":
        raise FormulaError(f"{fn.name} needs a column, e.g. {fn.name}({reference}.amount)")

    table_name = reference.split(".")[0]
    inner = RowResolver(frame, table_name)
    mask: Optional[pd.Series] = None

    # Positional filter: an arbitrary condition, e.g. `delta_mrr > 0`.
    if len(node.args) == 2:
        mask = _evaluate_node(node.args[1], inner)
        if not isinstance(mask, pd.Series):
            raise FormulaError(f"{fn.name}: the filter must be a condition over the table",
                               hint="e.g. delta_mrr > 0")

    # Keyword filters: `movement_type='churn'`, ANDed together.
    for keyword in node.keywords:
        if keyword.arg is None:
            raise FormulaError(f"{fn.name}: argument unpacking is not supported")
        column_values = inner.resolve(keyword.arg)
        equals = column_values == _evaluate_node(keyword.value, inner)
        mask = equals if mask is None else (mask & equals)

    if mask is not None:
        frame = frame[mask.fillna(False).astype(bool)]

    return sanitise(fn.fn(frame, column, resolver.index))


def _suggest(name: str) -> Optional[str]:
    import difflib
    close = difflib.get_close_matches(name.upper(), list(FUNCTIONS), n=1, cutoff=0.6)
    return f"did you mean {close[0]}?" if close else None


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def _parse_checked(expression: str) -> ast.Expression:
    tree = parse(expression, allowed=ARITHMETIC_NODES)
    count = sum(1 for _ in ast.walk(tree))
    if count > MAX_NODES:
        raise FormulaError(
            f"the formula is too complex ({count} terms, limit {MAX_NODES})",
            hint="split it into two KPIs and reference one from the other",
        )
    return tree


def validate(expression: str, scope: Scope = SERIES,
             resolver: Optional[Resolver] = None) -> Dict[str, Any]:
    """Parse and check without evaluating.

    Structure, function names, arity and scope are checked always. Names are
    checked only when a resolver is supplied, because the editor validates a
    formula before there is a run to resolve names against.
    """
    tree = _parse_checked(expression)
    used_functions: List[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            raise FormulaError("only the built-in functions can be called")
        fn = FUNCTIONS.get(node.func.id)
        if fn is None:
            raise FormulaError(f"unknown function {node.func.id!r}",
                               hint=_suggest(node.func.id))
        if scope not in fn.scopes:
            raise scope_error(fn, scope)
        fn.check_arity(len(node.args))
        used_functions.append(fn.name)

    from .introspect import references
    names = references(expression)

    unknown: List[str] = []
    if resolver is not None:
        for name in names:
            try:
                resolver.resolve(name)
            except FormulaError:
                unknown.append(name)

    return {
        "ok": not unknown,
        "references": names,
        "functions": sorted(set(used_functions)),
        "unknown": unknown,
    }


def evaluate(expression: str, resolver: Resolver) -> Any:
    """Evaluate a formula. Raises FormulaError on anything the user got wrong."""
    tree = _parse_checked(expression)
    result = _evaluate_node(tree, resolver)

    if isinstance(result, pd.Series):
        if result.dtype == bool:
            result = result.astype("float64")
        return sanitise(result).astype("float64")

    if isinstance(result, (bool, np.bool_)):
        result = float(result)
    if isinstance(result, (int, float, np.integer, np.floating)):
        # A formula that collapses to one number is still a valid KPI — it just
        # has the same value every month.
        if resolver.index is None:
            return float(result)
        return pd.Series(float(result), index=resolver.index, dtype="float64")

    raise FormulaError(
        f"the formula produced a {type(result).__name__}, not a number",
        hint="a formula must end in a number or a series",
    )
