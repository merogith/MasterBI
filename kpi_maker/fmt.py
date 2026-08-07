"""Shared value formatting. One implementation so a number reads identically
in the dashboard, the workbook and the report."""
from __future__ import annotations

from typing import Optional

import numpy as np

CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "TRY": "₺", "JPY": "¥"}


def is_missing(value: Optional[float]) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))


def fmt_value(value: Optional[float], unit: str, currency: str = "USD",
              compact: bool = True) -> str:
    if is_missing(value):
        return "—"
    value = float(value)
    if unit == "pct":
        return f"{value:.1%}"
    if unit == "currency":
        sym = CURRENCY_SYMBOL.get(currency, "")
        if compact:
            for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if abs(value) >= threshold:
                    return f"{sym}{value / threshold:,.1f}{suffix}"
        return f"{sym}{value:,.0f}"
    if unit in ("months", "days", "hours"):
        short = {"months": "mo", "days": "d", "hours": "h"}[unit]
        return f"{value:,.1f} {short}"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "score":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def fmt_delta(value: Optional[float], unit: str, currency: str = "USD") -> str:
    if is_missing(value):
        return "—"
    sign = "+" if value >= 0 else "−"
    return f"{sign}{fmt_value(abs(value), unit, currency)}"
