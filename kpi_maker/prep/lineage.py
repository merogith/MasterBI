"""What was done to the data, and what it cost.

Every op records rows in and out, cells changed, and a sentence a non-technical
reader can follow. That log renders into the report's methodology appendix,
which is what makes a cleaned dataset defensible rather than merely tidy — the
same reason `CompanyProfile._provenance` exists for the profile.

The rule this enforces: no transformation is invisible. If a step removed 340
rows, the appendix says so, and says which step.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class LineageEntry:
    step: int
    op: str
    table: str
    sentence: str                      # for the appendix and the op card
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    cells_changed: int = 0
    params: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def rows_removed(self) -> int:
        return max(self.rows_before - self.rows_after, 0)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["rows_removed"] = self.rows_removed
        return out


class Lineage:
    """The ordered record of a cleaning run."""

    def __init__(self) -> None:
        self.entries: List[LineageEntry] = []

    def record(self, op: str, table: str, before: pd.DataFrame,
               after: pd.DataFrame, sentence: str,
               params: Optional[Dict[str, Any]] = None,
               note: str = "") -> LineageEntry:
        entry = LineageEntry(
            step=len(self.entries) + 1,
            op=op, table=table, sentence=sentence,
            rows_before=len(before), rows_after=len(after),
            columns_before=len(before.columns), columns_after=len(after.columns),
            cells_changed=count_changed_cells(before, after),
            params=dict(params or {}), note=note,
        )
        self.entries.append(entry)
        return entry

    def as_list(self) -> List[Dict[str, Any]]:
        return [e.as_dict() for e in self.entries]

    def summary(self) -> str:
        """One line for the run log."""
        if not self.entries:
            return "no cleaning applied"
        removed = sum(e.rows_removed for e in self.entries)
        changed = sum(e.cells_changed for e in self.entries)
        return (f"{len(self.entries)} step(s), {removed:,} row(s) removed, "
                f"{changed:,} cell(s) changed")

    def appendix_lines(self) -> List[str]:
        """The methodology paragraph, one sentence per step."""
        return [f"{e.step}. {e.sentence}" for e in self.entries]


def count_changed_cells(before: pd.DataFrame, after: pd.DataFrame) -> int:
    """How many values actually moved.

    Only compares the rows and columns present in both, because a step that
    dropped rows already reports that as `rows_removed` — counting the removed
    cells again would make a filter look like a rewrite.
    """
    shared_cols = [c for c in before.columns if c in after.columns]
    if not shared_cols:
        return 0
    shared_idx = before.index.intersection(after.index)
    if len(shared_idx) == 0:
        return 0

    left = before.loc[shared_idx, shared_cols]
    right = after.loc[shared_idx, shared_cols]
    try:
        # `ne` treats NaN != NaN, which would count every missing value as a
        # change on any op that touched the frame at all.
        differs = left.ne(right) & ~(left.isna() & right.isna())
        return int(differs.to_numpy().sum())
    except Exception:                                       # noqa: BLE001
        # Mismatched dtypes after a cast make an element-wise compare
        # impossible; the row/column counts still tell the story.
        return 0
