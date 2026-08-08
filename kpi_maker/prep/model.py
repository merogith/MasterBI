"""Calculated columns — the row-level half of the formula language.

A KPI is a monthly series; a calculated column is a new field on a fact table,
evaluated one row at a time. Same grammar, different scope: `final_acv -
initial_acv` on `customers` is a column, `SAFE_DIV(arr, customer_count)` is a
KPI, and the engine refuses time functions in the first because a row has no
time axis.

Columns are applied in the `model` stage, in the order the user wrote them, so
a later column may build on an earlier one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..formula import FormulaError, evaluate
from ..formula.evaluate import RowResolver


class ModelError(ValueError):
    """A calculated column that cannot be built."""


def apply_model(tables: Dict[str, pd.DataFrame], spec, ctx=None) -> Dict[str, pd.DataFrame]:
    """Add every calculated column to its table.

    Copy-on-write per touched table: the source stage's frames are cached and
    handed to whatever runs next, so mutating them in place would leak a
    calculated column into a re-run that no longer defines it.
    """
    if not spec.calculated_columns:
        return tables

    out = dict(tables)
    for column in spec.calculated_columns:
        frame = out.get(column.table)
        if frame is None:
            raise ModelError(
                f"calculated column {column.name!r} targets unknown table "
                f"{column.table!r} — available: {', '.join(sorted(out))}"
            )
        if column.name in frame.columns:
            raise ModelError(
                f"{column.table} already has a column named {column.name!r} — "
                f"pick another name rather than silently replacing source data"
            )

        try:
            values = evaluate(column.expression, RowResolver(frame, column.table))
        except FormulaError as exc:
            raise ModelError(
                f"calculated column {column.table}.{column.name}: {exc}") from exc

        frame = frame.copy()
        frame[column.name] = values
        out[column.table] = frame

    return out


def preview_column(frame: pd.DataFrame, table: str, expression: str,
                   rows: int = 8) -> Dict[str, Any]:
    """Evaluate against a sample so the editor can show the result as it is typed.

    Runs on the head of the frame rather than the whole thing: the point is to
    show the user what their expression does, and 8 rows does that for the cost
    of none of the wait.
    """
    sample = frame.head(rows)
    values = evaluate(expression, RowResolver(sample, table))
    if not isinstance(values, pd.Series):
        values = pd.Series(values, index=sample.index)

    referenced = _referenced_columns(expression, sample)
    preview = sample[referenced].copy() if referenced else pd.DataFrame(index=sample.index)
    preview["= result"] = values.values

    return {
        "columns": list(map(str, preview.columns)),
        "rows": [
            [None if pd.isna(v) else (float(v) if isinstance(v, (int, float)) else str(v))
             for v in row]
            for row in preview.itertuples(index=False)
        ],
        "non_null": int(values.notna().sum()),
        "total": int(len(values)),
    }


def _referenced_columns(expression: str, frame: pd.DataFrame) -> List[str]:
    from ..formula.introspect import references
    try:
        names = references(expression)
    except FormulaError:
        return []
    return [n.split(".")[-1] for n in names if n.split(".")[-1] in frame.columns]
