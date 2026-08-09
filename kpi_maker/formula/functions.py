"""The function registry — the whole vocabulary of the language.

Each entry declares the scopes it is legal in, so `TTM` used in a calculated
column is refused at validation with an explanation rather than producing a
column of nonsense. Adding a function here is the only way to add one: the
evaluator resolves calls by name against this dict and refuses everything else.

Naming follows spreadsheet convention (upper case, `IF` not `where`) because the
audience is finance and ops, not Python programmers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .errors import FormulaError

SERIES = "series"
ROW = "row"
BOTH = (SERIES, ROW)


@dataclass
class Function:
    name: str
    fn: Callable
    min_args: int
    max_args: Optional[int]        # None = variadic
    scopes: Tuple[str, ...]
    signature: str
    help: str
    # Aggregates take a `table.column` reference rather than an evaluated value,
    # because they need the raw frame to group by month. The evaluator checks
    # this flag before evaluating the argument.
    takes_column_ref: bool = False

    def check_arity(self, count: int) -> None:
        if count < self.min_args or (self.max_args is not None and count > self.max_args):
            expected = (f"{self.min_args}" if self.max_args == self.min_args
                        else f"{self.min_args}-{self.max_args}" if self.max_args
                        else f"{self.min_args} or more")
            raise FormulaError(
                f"{self.name} takes {expected} argument(s), got {count}",
                hint=self.signature,
            )


FUNCTIONS: Dict[str, Function] = {}


def register(name: str, min_args: int, max_args: Optional[int], scopes,
             signature: str, help: str, takes_column_ref: bool = False):
    def wrap(fn: Callable) -> Callable:
        FUNCTIONS[name] = Function(
            name=name, fn=fn, min_args=min_args, max_args=max_args,
            scopes=tuple(scopes), signature=signature, help=help,
            takes_column_ref=takes_column_ref,
        )
        return fn
    return wrap


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def sanitise(value: Any) -> Any:
    """Infinities become NaN.

    Division by a near-zero denominator is the single most common way a formula
    produces a number that is technically correct and completely useless — the
    same reason `metrics/engine.py` calls `.replace([inf, -inf], nan)` after
    every division. Doing it centrally means a user's formula gets the same
    treatment as a hand-written metric.
    """
    if isinstance(value, pd.Series):
        return value.replace([np.inf, -np.inf], np.nan)
    if isinstance(value, (int, float, np.floating)) and not np.isfinite(value):
        return np.nan
    return value


def _as_series(value: Any, like: Optional[pd.Series]) -> pd.Series:
    if isinstance(value, pd.Series):
        return value
    if like is None:
        raise FormulaError("expected a series here, got a single number")
    return pd.Series(value, index=like.index, dtype="float64")


def _first_series(*values) -> Optional[pd.Series]:
    for v in values:
        if isinstance(v, pd.Series):
            return v
    return None


# --------------------------------------------------------------------------
# Maths — legal everywhere
# --------------------------------------------------------------------------

@register("ABS", 1, 1, BOTH, "ABS(x)", "Absolute value.")
def _abs(x):
    return x.abs() if isinstance(x, pd.Series) else abs(x)


@register("ROUND", 1, 2, BOTH, "ROUND(x, digits=0)", "Round to a number of digits.")
def _round(x, digits=0):
    digits = int(digits)
    return x.round(digits) if isinstance(x, pd.Series) else round(x, digits)


@register("MIN", 1, None, BOTH, "MIN(a, b, ...)", "Smallest of its arguments, element-wise.")
def _min(*args):
    return _reduce_elementwise(args, np.minimum)


@register("MAX", 1, None, BOTH, "MAX(a, b, ...)", "Largest of its arguments, element-wise.")
def _max(*args):
    return _reduce_elementwise(args, np.maximum)


def _reduce_elementwise(args, op):
    if len(args) == 1:
        return args[0]
    result = args[0]
    for other in args[1:]:
        if isinstance(result, pd.Series) or isinstance(other, pd.Series):
            base = _first_series(result, other)
            result = op(_as_series(result, base), _as_series(other, base))
        else:
            result = op(result, other)
    return result


@register("CLIP", 3, 3, BOTH, "CLIP(x, low, high)",
          "Constrain x to the range [low, high].")
def _clip(x, low, high):
    if isinstance(x, pd.Series):
        return x.clip(lower=low, upper=high)
    return max(low, min(high, x))


@register("SAFE_DIV", 2, 3, BOTH, "SAFE_DIV(numerator, denominator, default=NaN)",
          "Divide, yielding the default where the denominator is zero or missing. "
          "Prefer this over `/` whenever the denominator can be zero.")
def _safe_div(num, den, default=np.nan):
    if isinstance(den, pd.Series):
        den = den.replace(0, np.nan)
    elif den == 0:
        den = np.nan
    result = sanitise(num / den)
    if isinstance(result, pd.Series):
        return result.fillna(default) if default is not np.nan else result
    return default if (result is np.nan or pd.isna(result)) else result


@register("IF", 3, 3, BOTH, "IF(condition, then, otherwise)",
          "Element-wise choice between two values.")
def _if(condition, then, otherwise):
    if isinstance(condition, pd.Series):
        base = _first_series(then, otherwise, condition)
        return pd.Series(
            np.where(condition.fillna(False).astype(bool),
                     _as_series(then, base), _as_series(otherwise, base)),
            index=condition.index,
        )
    return then if condition else otherwise


@register("COALESCE", 1, None, BOTH, "COALESCE(a, b, ...)",
          "The first non-missing value, element-wise.")
def _coalesce(*args):
    result = args[0]
    for other in args[1:]:
        if isinstance(result, pd.Series):
            result = result.fillna(other if not isinstance(other, pd.Series) else other)
        elif result is None or (isinstance(result, float) and np.isnan(result)):
            result = other
    return result


# --------------------------------------------------------------------------
# Time — series scope only. There is no time axis on a single row.
# --------------------------------------------------------------------------

_NO_TIME_AXIS = ("a calculated column is evaluated one row at a time and has no "
                 "time axis; use this in a KPI formula instead")


@register("SHIFT", 1, 2, (SERIES,), "SHIFT(x, months=1)",
          "The value from N months earlier.")
def _shift(x, months=1):
    return _require_series(x, "SHIFT").shift(int(months))


@register("ROLLING", 2, 3, (SERIES,), "ROLLING(x, months, how='mean')",
          "Rolling window over the last N months. how = mean | sum | min | max.")
def _rolling(x, months, how="mean"):
    window = _require_series(x, "ROLLING").rolling(int(months))
    how = str(how).lower()
    if how not in ("mean", "sum", "min", "max"):
        raise FormulaError(f"ROLLING: unknown aggregation {how!r}",
                           hint="use mean, sum, min or max")
    return getattr(window, how)()


@register("TTM", 1, 1, (SERIES,), "TTM(x)",
          "Trailing twelve months — the sum of the last 12 values. The right "
          "smoothing for anything lumpy, such as billings or burn.")
def _ttm(x):
    return _require_series(x, "TTM").rolling(12).sum()


@register("YOY", 1, 1, (SERIES,), "YOY(x)",
          "Year-on-year change as a fraction: x / x 12 months ago - 1.")
def _yoy(x):
    s = _require_series(x, "YOY")
    return sanitise(s / s.shift(12) - 1)


@register("MOM", 1, 1, (SERIES,), "MOM(x)", "Month-on-month change as a fraction.")
def _mom(x):
    s = _require_series(x, "MOM")
    return sanitise(s / s.shift(1) - 1)


@register("DELTA", 1, 2, (SERIES,), "DELTA(x, months=1)",
          "Absolute change over N months, in x's own unit.")
def _delta(x, months=1):
    s = _require_series(x, "DELTA")
    return s - s.shift(int(months))


@register("CUMSUM", 1, 1, (SERIES,), "CUMSUM(x)", "Running total from the first month.")
def _cumsum(x):
    return _require_series(x, "CUMSUM").cumsum()


def _require_series(value, fname: str) -> pd.Series:
    if not isinstance(value, pd.Series):
        raise FormulaError(f"{fname} needs a series, got a single number",
                           hint="reference a KPI or a monthly column")
    return value


# --------------------------------------------------------------------------
# Aggregates — series scope. Collapse a month x dimension table to monthly.
# --------------------------------------------------------------------------

_AGG_HELP = ("Aggregate a column to a monthly series. The optional filter picks "
             "rows first, e.g. SUM(mrr_movements.delta_mrr, movement_type = 'churn').")

for _name, _how in (("SUM", "sum"), ("AVG", "mean"), ("COUNT", "size"),
                    ("MIN_OF", "min"), ("MAX_OF", "max"),
                    ("COUNT_DISTINCT", "nunique")):
    def _make(how=_how, name=_name):
        def _agg(frame: pd.DataFrame, column: Optional[str], index) -> pd.Series:
            if how == "size":
                grouped = frame.groupby("month").size()
            else:
                grouped = getattr(frame.groupby("month")[column], how)()
            return grouped.reindex(index, fill_value=0.0 if how in ("sum", "size") else np.nan)
        register(
            name, 1, 2, (SERIES,),
            f"{name}(table.column [, filter])", _AGG_HELP, takes_column_ref=True,
        )(_agg)
    _make()


# --------------------------------------------------------------------------

def describe_functions() -> List[Dict[str, Any]]:
    """The registry as data, for autocomplete and the help panel."""
    return [
        {
            "name": f.name,
            "signature": f.signature,
            "help": f.help,
            "scopes": list(f.scopes),
        }
        for f in sorted(FUNCTIONS.values(), key=lambda f: f.name)
    ]


def scope_error(fn: Function, scope: str) -> FormulaError:
    return FormulaError(
        f"{fn.name} cannot be used in a {scope} formula",
        hint=_NO_TIME_AXIS if scope == ROW else
        f"{fn.name} is only available in a {'/'.join(fn.scopes)} formula",
    )
