"""Apply an ordered cleaning recipe, recording what each step did.

The runner owns the things every op would otherwise repeat: resolving which
table a step targets, copying before mutating, catching an op's failure and
attributing it to the right step, and writing the lineage entry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .lineage import Lineage
from .ops import OPS, OpError


def apply_recipe(tables: Dict[str, pd.DataFrame], recipe,
                 ctx=None) -> Tuple[Dict[str, pd.DataFrame], Lineage]:
    """Run every enabled step in order. Returns (tables, lineage).

    Steps apply in sequence, so a later step sees the output of an earlier one
    — which is what makes "parse the dates, then roll up to months" express the
    obvious thing.
    """
    out = dict(tables)
    lineage = Lineage()

    for position, step in enumerate(recipe.active, start=1):
        spec = OPS.get(step.op)
        if spec is None:
            raise OpError(
                f"step {position}: unknown operation {step.op!r} — "
                f"available: {', '.join(sorted(OPS))}")

        targets = _targets(out, step, position)
        params = spec.parse(step.params)

        for name in targets:
            before = out[name]
            try:
                after = spec.apply(before, params)
                sentence = spec.describe(before, after, params)
            except OpError:
                raise
            except Exception as exc:                        # noqa: BLE001
                raise OpError(
                    f"step {position} ({step.op} on {name}): {exc}") from exc

            if not isinstance(after, pd.DataFrame):
                raise OpError(f"step {position}: {step.op} did not return a table")

            out[name] = after
            lineage.record(op=step.op, table=name, before=before, after=after,
                           sentence=f"{name}: {sentence}",
                           params=dict(step.params), note=step.note)

    return out, lineage


def _targets(tables: Dict[str, pd.DataFrame], step, position: int) -> List[str]:
    """Which tables a step applies to.

    `table: null` means every table, which is only sensible for ops that are
    naturally table-agnostic — trimming whitespace, for instance. An op that
    names a specific column and is pointed at every table would fail on the
    first one that lacks it, so a missing column is tolerated in that case and
    fatal when the table was named explicitly.
    """
    if step.table:
        if step.table not in tables:
            raise OpError(
                f"step {position}: no table {step.table!r} — "
                f"available: {', '.join(sorted(tables))}")
        return [step.table]
    return sorted(tables)


def preview_recipe(tables: Dict[str, pd.DataFrame], recipe,
                   table: Optional[str] = None,
                   rows: int = 10) -> Dict[str, Any]:
    """Apply a recipe and report what changed, without keeping the result.

    Runs on the whole frame rather than a sample: row counts are the main thing
    a user checks here, and a head() would report a duplicate count that is
    simply wrong.
    """
    after, lineage = apply_recipe(tables, recipe)
    focus = table or next(iter(sorted(after)), None)

    before_frame = tables.get(focus)
    after_frame = after.get(focus)

    return {
        "table": focus,
        "steps": lineage.as_list(),
        "summary": lineage.summary(),
        "rows_before": int(len(before_frame)) if before_frame is not None else 0,
        "rows_after": int(len(after_frame)) if after_frame is not None else 0,
        "columns_before": list(map(str, before_frame.columns)) if before_frame is not None else [],
        "columns_after": list(map(str, after_frame.columns)) if after_frame is not None else [],
        "preview": _records(after_frame, rows),
    }


def _records(frame: Optional[pd.DataFrame], rows: int) -> List[Dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    head = frame.head(rows)
    return [
        {str(c): (None if pd.isna(v) else
                  (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                   else str(v)))
         for c, v in row.items()}
        for _, row in head.iterrows()
    ]
