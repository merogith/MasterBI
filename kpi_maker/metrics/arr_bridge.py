"""Financial snapshot boundaries and movement components for the ARR bridge."""
from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class ARRBridge:
    opening: float
    closing: float
    movements: Dict[str, float]

    @property
    def net_movement(self) -> float:
        return sum(self.movements.values())

    @property
    def residual(self) -> float:
        explained_closing = self.opening + self.net_movement
        if isclose(self.closing, explained_closing, rel_tol=0, abs_tol=0.01):
            return 0.0
        return self.closing - explained_closing


def trailing_arr_bridge(tables: Dict[str, pd.DataFrame]) -> Optional[ARRBridge]:
    fin = tables.get("monthly_financials")
    mov = tables.get("mrr_movements")
    if fin is None or mov is None or not {"month", "arr"} <= set(fin):
        return None

    # ARR is a stock reported in monthly_financials. A trimmed movement log
    # can explain changes without containing the ARR already on the books.
    months = pd.PeriodIndex(fin["month"], freq="M")
    snapshots = pd.Series(fin["arr"].to_numpy(), index=months).dropna().sort_index()
    if snapshots.empty or not snapshots.index.is_unique:
        return None
    end = snapshots.index[-1]
    start = end - 12
    if start not in snapshots.index:
        return None
    opening, closing = float(snapshots.loc[start]), float(snapshots.loc[end])
    if not (isfinite(opening) and isfinite(closing)):
        return None

    movement_months = pd.PeriodIndex(mov["month"], freq="M")
    recent = mov.loc[(movement_months > start) & (movement_months <= end)]
    totals = recent.groupby("movement_type")["delta_mrr"].sum() * 12
    return ARRBridge(opening, closing, {kind: float(value) for kind, value in totals.items()})
