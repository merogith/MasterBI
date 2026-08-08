"""The cleaning operations.

Each op is registered with a pydantic params model, an `apply`, and a
`describe` that writes the sentence the lineage log and the op card both show.
Ops are pure `DataFrame -> DataFrame`; the recipe runner owns ordering, copying
and recording, so an op never has to think about any of that.

Two rules every op here follows:

  * **Never silently widen the damage.** An op that cannot do what it was asked
    raises `OpError` with the reason. Half-applying a transformation is worse
    than refusing it, because the user believes it worked.
  * **Say what happened in a sentence.** "Removed 12 duplicate rows on
    customer_id" is what ends up in the methodology appendix. A user defending
    these numbers to their board needs prose, not a parameter dump.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


class OpError(ValueError):
    """An operation that cannot be applied as specified."""


@dataclass
class Op:
    name: str
    params_model: Type[BaseModel]
    apply: Callable[[pd.DataFrame, BaseModel], pd.DataFrame]
    describe: Callable[[pd.DataFrame, pd.DataFrame, BaseModel], str]
    label: str
    help: str

    def parse(self, params: Dict[str, Any]) -> BaseModel:
        try:
            return self.params_model(**(params or {}))
        except Exception as exc:                            # noqa: BLE001
            raise OpError(f"{self.name}: invalid parameters — {exc}") from exc


OPS: Dict[str, Op] = {}


def op(name: str, label: str, help: str, params_model: Type[BaseModel]):
    def wrap(fn):
        # `fn` returns (frame, sentence) so the description can use facts only
        # the operation knows — how many rows it matched, which columns it hit.
        def apply(frame, params):
            return fn(frame, params)[0]

        def describe(before, after, params):
            return fn(before, params)[1]

        OPS[name] = Op(name=name, params_model=params_model, apply=apply,
                       describe=describe, label=label, help=help)
        return fn
    return wrap


def _require_columns(frame: pd.DataFrame, columns: List[str], op_name: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise OpError(
            f"{op_name}: no column named {', '.join(repr(m) for m in missing)} — "
            f"available: {', '.join(map(str, frame.columns))}")


def _plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def _map_text(series: pd.Series, fn: Callable[[pd.Series], pd.Series]) -> pd.Series:
    """Apply a string transform to the present values only.

    `series.astype(str)` turns every missing value into the literal text "nan",
    "None" or "<NA>" depending on how it was stored. Those then survive as real
    categories — a blank segment becomes a segment *named* "nan", which reaches
    the charts and the scorecard as though someone sold to it. It also makes
    `trim` non-idempotent, because the marker it produced on the first pass gets
    stringified differently on the second.

    So: transform where present, leave missing missing.
    """
    present = series.notna()
    out = series.copy().astype(object)
    if present.any():
        out.loc[present] = fn(series[present].astype(str))
    out.loc[~present] = None
    return out


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

class ColumnsParams(BaseModel):
    columns: List[str] = Field(default_factory=list)


@op("select_columns", "Keep only these columns",
    "Drop every column except the ones listed.", ColumnsParams)
def _select_columns(frame, p):
    _require_columns(frame, p.columns, "select_columns")
    out = frame[p.columns].copy()
    dropped = len(frame.columns) - len(p.columns)
    return out, f"Kept {_plural(len(p.columns), 'column')}, dropping {dropped}."


class DropColumnsParams(BaseModel):
    columns: List[str] = Field(default_factory=list)


@op("drop_columns", "Remove columns", "Drop the listed columns.", DropColumnsParams)
def _drop_columns(frame, p):
    present = [c for c in p.columns if c in frame.columns]
    out = frame.drop(columns=present).copy()
    return out, f"Removed {_plural(len(present), 'column')}: {', '.join(present)}."


class RenameParams(BaseModel):
    mapping: Dict[str, str] = Field(default_factory=dict)


@op("rename", "Rename columns", "Rename columns to the canonical names.", RenameParams)
def _rename(frame, p):
    _require_columns(frame, list(p.mapping), "rename")
    out = frame.rename(columns=p.mapping).copy()
    pairs = ", ".join(f"{a} to {b}" for a, b in p.mapping.items())
    return out, f"Renamed {_plural(len(p.mapping), 'column')}: {pairs}."


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------

class CastParams(BaseModel):
    column: str
    to: str = Field(pattern="^(number|integer|text|boolean)$")
    # European exports write 1.234,56 where Anglo ones write 1,234.56. Reading
    # the first as Anglo yields 1.23456 — a plausible number that is wrong by
    # three orders of magnitude, so the convention has to be explicit.
    decimal: str = Field(default="anglo", pattern="^(anglo|european)$")


@op("cast", "Change a column's type",
    "Convert a column to a number, integer, text or boolean. "
    "Values that cannot convert become missing rather than failing the run.",
    CastParams)
def _cast(frame, p):
    _require_columns(frame, [p.column], "cast")
    out = frame.copy()
    before_missing = int(out[p.column].isna().sum())
    if p.to == "number":
        out[p.column] = _to_number(out[p.column], p.decimal)
    elif p.to == "integer":
        out[p.column] = _to_number(out[p.column], p.decimal).round().astype("Int64")
    elif p.to == "boolean":
        out[p.column] = out[p.column].map(
            lambda v: _as_bool(v)).astype("boolean")
    else:
        out[p.column] = out[p.column].astype(str)
    new_missing = int(out[p.column].isna().sum()) - before_missing
    tail = (f", leaving {_plural(new_missing, 'value')} unconvertible and blank"
            if new_missing > 0 else "")
    style = " (European decimals)" if p.decimal == "european" else ""
    return out, f"Converted {p.column} to {p.to}{style}{tail}."


def _to_number(series: pd.Series, convention: str = "anglo") -> pd.Series:
    """Parse money-ish text: currency symbols, thousands separators, (500) = -500."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    text = (series.astype(str).str.strip()
            .str.replace(r"[$\u20ac\u00a3\u20ba\u00a5%\s]", "", regex=True)
            .str.replace(r"^\((.*)\)$", r"-\1", regex=True))
    if convention == "european":
        text = text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        text = text.str.replace(",", "", regex=False)
    return pd.to_numeric(text, errors="coerce")


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def _as_bool(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return pd.NA


class ParseDatesParams(BaseModel):
    column: str
    to_month: bool = False        # collapse to a YYYY-MM period


@op("parse_dates", "Parse a date column",
    "Read a column as dates, optionally collapsing to the month the pipeline "
    "reports on. Unparseable values become missing.", ParseDatesParams)
def _parse_dates(frame, p):
    _require_columns(frame, [p.column], "parse_dates")
    out = frame.copy()
    parsed = pd.to_datetime(out[p.column], errors="coerce", format="mixed")
    failed = int(parsed.isna().sum() - out[p.column].isna().sum())
    out[p.column] = parsed.dt.to_period("M") if p.to_month else parsed
    grain = " and collapsed it to months" if p.to_month else ""
    tail = f", with {_plural(failed, 'value')} unreadable as a date" if failed > 0 else ""
    return out, f"Parsed {p.column} as a date{grain}{tail}."


class TrimParams(BaseModel):
    columns: List[str] = Field(default_factory=list)   # empty = every text column


@op("trim", "Trim whitespace",
    "Strip leading and trailing spaces from text columns.", TrimParams)
def _trim(frame, p):
    out = frame.copy()
    targets = p.columns or [c for c in out.columns if out[c].dtype == object]
    for column in targets:
        if column in out.columns and out[column].dtype == object:
            out[column] = _map_text(out[column], lambda s: s.str.strip())
            # An emptied cell is missing, not an empty category.
            out[column] = out[column].replace({"": None})
    return out, f"Trimmed whitespace from {_plural(len(targets), 'text column')}."


class NormalizeCaseParams(BaseModel):
    columns: List[str] = Field(default_factory=list)
    to: str = Field(default="lower", pattern="^(lower|upper|title)$")


@op("normalize_case", "Normalise text case",
    "Make text in a column consistently lower, upper or title case — so "
    "'SMB', 'smb' and 'Smb' stop counting as three segments.", NormalizeCaseParams)
def _normalize_case(frame, p):
    out = frame.copy()
    targets = [c for c in (p.columns or out.columns) if c in out.columns
               and out[c].dtype == object]
    for column in targets:
        out[column] = _map_text(out[column], lambda s: getattr(s.str, p.to)())
    return out, f"Set {_plural(len(targets), 'column')} to {p.to} case."


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

class DropDuplicatesParams(BaseModel):
    subset: List[str] = Field(default_factory=list)    # empty = whole row
    keep: str = Field(default="first", pattern="^(first|last)$")


@op("drop_duplicates", "Remove duplicate rows",
    "Drop repeated rows, optionally judging duplication on specific columns.",
    DropDuplicatesParams)
def _drop_duplicates(frame, p):
    subset = p.subset or None
    if subset:
        _require_columns(frame, subset, "drop_duplicates")
    out = frame.drop_duplicates(subset=subset, keep=p.keep).copy()
    removed = len(frame) - len(out)
    on = f" on {', '.join(subset)}" if subset else ""
    return out, f"Removed {_plural(removed, 'duplicate row')}{on}."


class FillMissingParams(BaseModel):
    column: str
    strategy: str = Field(default="zero",
                          pattern="^(zero|mean|median|forward|backward|value)$")
    value: Optional[float] = None


@op("fill_missing", "Fill missing values",
    "Replace blanks with zero, the column's mean or median, the previous or "
    "next value, or a number you choose.", FillMissingParams)
def _fill_missing(frame, p):
    _require_columns(frame, [p.column], "fill_missing")
    out = frame.copy()
    blanks = int(out[p.column].isna().sum())
    series = out[p.column]

    if p.strategy == "zero":
        filled, how = series.fillna(0), "zero"
    elif p.strategy == "mean":
        filled, how = series.fillna(series.mean()), f"the mean ({series.mean():,.2f})"
    elif p.strategy == "median":
        filled, how = series.fillna(series.median()), f"the median ({series.median():,.2f})"
    elif p.strategy == "forward":
        filled, how = series.ffill(), "the previous value"
    elif p.strategy == "backward":
        filled, how = series.bfill(), "the next value"
    else:
        if p.value is None:
            raise OpError("fill_missing: strategy 'value' needs a value")
        filled, how = series.fillna(p.value), f"{p.value:,.2f}"

    out[p.column] = filled
    return out, f"Filled {_plural(blanks, 'missing value')} in {p.column} with {how}."


class FilterParams(BaseModel):
    expression: str            # row-scope formula, evaluated by the P1 engine
    keep: bool = True          # False = drop matching rows


@op("filter", "Keep or drop rows by condition",
    "Write a condition such as `revenue > 0` or `segment = 'SMB'`. Uses the "
    "same formula language as calculated columns.", FilterParams)
def _filter(frame, p):
    from ..formula import FormulaError, evaluate
    from ..formula.evaluate import RowResolver
    try:
        mask = evaluate(p.expression, RowResolver(frame, ""))
    except FormulaError as exc:
        raise OpError(f"filter: {exc}") from exc
    if not isinstance(mask, pd.Series):
        raise OpError("filter: the expression must be a condition over the rows")

    keep_mask = mask.fillna(0).astype(bool)
    if not p.keep:
        keep_mask = ~keep_mask
    out = frame[keep_mask].copy()
    verb = "Kept" if p.keep else "Dropped"
    affected = len(frame) - len(out) if p.keep else len(frame) - len(out)
    return out, (f"{verb} rows where {p.expression} — "
                 f"{_plural(len(out), 'row')} of {len(frame):,} remain "
                 f"({affected:,} removed).")


class ClipOutliersParams(BaseModel):
    column: str
    method: str = Field(default="iqr", pattern="^(iqr|zscore|percentile)$")
    factor: float = 1.5        # IQR multiplier, z threshold, or tail percentile
    action: str = Field(default="clip", pattern="^(clip|drop|null)$")


@op("clip_outliers", "Handle outliers",
    "Find outliers by inter-quartile range, z-score or percentile, then cap "
    "them at the boundary, blank them, or drop the rows.", ClipOutliersParams)
def _clip_outliers(frame, p):
    _require_columns(frame, [p.column], "clip_outliers")
    out = frame.copy()
    series = pd.to_numeric(out[p.column], errors="coerce")

    if p.method == "iqr":
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        span = q3 - q1
        low, high = q1 - p.factor * span, q3 + p.factor * span
        how = f"outside {p.factor}x the inter-quartile range"
    elif p.method == "zscore":
        mean, std = series.mean(), series.std()
        low, high = mean - p.factor * std, mean + p.factor * std
        how = f"more than {p.factor} standard deviations from the mean"
    else:
        tail = min(max(p.factor, 0.0), 50.0) / 100.0
        low, high = series.quantile(tail), series.quantile(1 - tail)
        how = f"in the top or bottom {tail:.1%}"

    outliers = ((series < low) | (series > high)) & series.notna()
    count = int(outliers.sum())

    if p.action == "clip":
        out[p.column] = series.clip(low, high)
        did = f"capped at [{low:,.2f}, {high:,.2f}]"
    elif p.action == "null":
        out.loc[outliers, p.column] = np.nan
        did = "blanked"
    else:
        out = out[~outliers].copy()
        did = "removed with their rows"

    return out, (f"Found {_plural(count, 'outlier')} in {p.column} {how}; {did}.")


class DropRowsParams(BaseModel):
    where_missing: List[str] = Field(default_factory=list)


@op("drop_incomplete_rows", "Drop rows missing key values",
    "Remove rows where any of the listed columns is blank.", DropRowsParams)
def _drop_incomplete(frame, p):
    columns = [c for c in p.where_missing if c in frame.columns]
    if not columns:
        raise OpError("drop_incomplete_rows: name at least one column that exists")
    out = frame.dropna(subset=columns).copy()
    removed = len(frame) - len(out)
    return out, (f"Removed {_plural(removed, 'row')} missing a value in "
                 f"{', '.join(columns)}.")


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------

class MapValuesParams(BaseModel):
    column: str
    mapping: Dict[str, str] = Field(default_factory=dict)
    default: Optional[str] = None


@op("map_values", "Recode values",
    "Replace values in a column — 'Enterprise'/'ENT'/'Ent' all become one "
    "segment name.", MapValuesParams)
def _map_values(frame, p):
    _require_columns(frame, [p.column], "map_values")
    out = frame.copy()
    original = out[p.column]
    present = original.notna()
    as_text = original.where(~present, original.astype(str))
    mapped = as_text.map(lambda v: p.mapping.get(v, ...) if pd.notna(v) else None)
    # `...` marks "no mapping matched" so a default of None stays distinguishable
    # from a value the mapping deliberately blanked.
    fallback = p.default if p.default is not None else None
    out[p.column] = [
        (fallback if fallback is not None else orig) if m is ... else m
        for m, orig in zip(mapped, as_text)
    ]
    changed = int(sum(
        1 for a, b in zip(as_text, out[p.column])
        if not (pd.isna(a) and pd.isna(b)) and a != b))
    return out, (f"Recoded {_plural(changed, 'value')} in {p.column} across "
                 f"{len(p.mapping)} mapping(s).")


class SignParams(BaseModel):
    column: str
    by_column: str
    negative_when: List[str] = Field(default_factory=list)


@op("apply_sign", "Apply a sign convention",
    "Make values negative where another column says they are a reduction. Most "
    "exports store amounts as positive magnitudes and leave the sign implied by "
    "a type column — which reads as a contradiction to anything that checks.",
    SignParams)
def _apply_sign(frame, p):
    _require_columns(frame, [p.column, p.by_column], "apply_sign")
    out = frame.copy()
    kinds = {str(k).strip().lower() for k in p.negative_when}
    mask = out[p.by_column].astype(str).str.strip().str.lower().isin(kinds)
    values = pd.to_numeric(out[p.column], errors="coerce")
    # Absolute value first: re-running must not flip already-negative rows back.
    out[p.column] = values.abs().where(~mask, -values.abs())
    flipped = int((mask & (values > 0)).sum())
    return out, (f"Made {_plural(flipped, 'value')} in {p.column} negative where "
                 f"{p.by_column} is one of {', '.join(sorted(kinds))}.")


class ScaleParams(BaseModel):
    column: str
    factor: float
    reason: str = ""


@op("unit_scale", "Rescale a column",
    "Multiply a column by a constant — thousands to units, cents to currency.",
    ScaleParams)
def _unit_scale(frame, p):
    _require_columns(frame, [p.column], "unit_scale")
    out = frame.copy()
    out[p.column] = pd.to_numeric(out[p.column], errors="coerce") * p.factor
    because = f" ({p.reason})" if p.reason else ""
    return out, f"Multiplied {p.column} by {p.factor:,g}{because}."


class CurrencyParams(BaseModel):
    columns: List[str] = Field(default_factory=list)
    rate: float
    from_currency: str = ""
    to_currency: str = ""


@op("currency_convert", "Convert currency",
    "Apply a single exchange rate to money columns. One rate for the whole "
    "period — a time-varying rate is a different problem and this does not "
    "pretend to solve it.", CurrencyParams)
def _currency_convert(frame, p):
    _require_columns(frame, p.columns, "currency_convert")
    out = frame.copy()
    for column in p.columns:
        out[column] = pd.to_numeric(out[column], errors="coerce") * p.rate
    pair = (f" from {p.from_currency} to {p.to_currency}"
            if p.from_currency and p.to_currency else "")
    return out, (f"Converted {_plural(len(p.columns), 'column')}{pair} "
                 f"at a rate of {p.rate:,g}.")


class DeriveColumnParams(BaseModel):
    name: str
    expression: str


@op("derive_column", "Add a calculated column",
    "Add a column from a formula over the other columns, using the same "
    "language as KPI formulas.", DeriveColumnParams)
def _derive_column(frame, p):
    from ..formula import FormulaError, evaluate
    from ..formula.evaluate import RowResolver
    if p.name in frame.columns:
        raise OpError(
            f"derive_column: {p.name!r} already exists — pick another name "
            f"rather than silently replacing source data")
    try:
        values = evaluate(p.expression, RowResolver(frame, ""))
    except FormulaError as exc:
        raise OpError(f"derive_column: {exc}") from exc
    out = frame.copy()
    out[p.name] = values
    return out, f"Added {p.name} = {p.expression}."


# --------------------------------------------------------------------------
# Reshape
# --------------------------------------------------------------------------

class ResampleParams(BaseModel):
    date_column: str = "month"
    how: str = Field(default="sum", pattern="^(sum|mean|last|first)$")
    group_by: List[str] = Field(default_factory=list)


@op("resample_monthly", "Roll up to months",
    "Collapse daily or weekly rows into the monthly grain the pipeline "
    "reports on.", ResampleParams)
def _resample_monthly(frame, p):
    _require_columns(frame, [p.date_column], "resample_monthly")
    out = frame.copy()
    months = pd.to_datetime(out[p.date_column], errors="coerce", format="mixed")
    if months.isna().all():
        raise OpError(f"resample_monthly: {p.date_column} does not read as dates")
    out[p.date_column] = months.dt.to_period("M")

    keys = [p.date_column] + [c for c in p.group_by if c in out.columns]
    numeric = [c for c in out.columns
               if c not in keys and pd.api.types.is_numeric_dtype(out[c])]
    grouped = getattr(out.groupby(keys, dropna=False)[numeric], p.how)().reset_index()
    return grouped, (f"Rolled {len(frame):,} rows up to {len(grouped):,} monthly "
                     f"rows, taking the {p.how} of {_plural(len(numeric), 'measure')}.")


class UnpivotParams(BaseModel):
    id_columns: List[str] = Field(default_factory=list)
    value_columns: List[str] = Field(default_factory=list)
    name_to: str = "variable"
    value_to: str = "value"


@op("unpivot", "Unpivot wide columns to rows",
    "Turn month-per-column layouts — the shape most finance exports arrive in "
    "— into one row per month.", UnpivotParams)
def _unpivot(frame, p):
    _require_columns(frame, p.id_columns + p.value_columns, "unpivot")
    value_columns = p.value_columns or [c for c in frame.columns
                                        if c not in p.id_columns]
    out = frame.melt(id_vars=p.id_columns, value_vars=value_columns,
                     var_name=p.name_to, value_name=p.value_to)
    return out, (f"Unpivoted {_plural(len(value_columns), 'column')} into "
                 f"{len(out):,} rows keyed by {p.name_to}.")


def describe_ops() -> List[Dict[str, Any]]:
    """The registry as data, for the Studio's op picker."""
    return [
        {
            "name": o.name,
            "label": o.label,
            "help": o.help,
            "params": o.params_model.model_json_schema().get("properties", {}),
            "required": o.params_model.model_json_schema().get("required", []),
        }
        for o in sorted(OPS.values(), key=lambda o: o.name)
    ]
