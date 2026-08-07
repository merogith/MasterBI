"""Design tokens for the chart layer.

Values are the validated reference palette. Both modes were run through
`validate_palette.js` at the exact slots used here:

    light categorical (#2a78d6,#eb6834,#1baf7a) --pairs all -> ALL PASS
        WARN: aqua at 2.74:1 vs surface -> relief rule applies, so every chart
        using slot 3 ships direct labels, and the dashboard always includes the
        scorecard table view.
    dark  categorical (#3987e5,#d95926,#199e70) --pairs all -> ALL PASS
    sequential blue ordinal ramp (5 steps, light)              -> ALL PASS

Categorical use is capped at THREE slots. That is the documented all-pairs-safe
limit for this palette; a fourth would put yellow beside orange and fail the
normal-vision floor. More than three categories folds into "Other" or facets.
"""
from __future__ import annotations

from typing import Dict

MAX_CATEGORICAL_SERIES = 3

TOKENS: Dict[str, Dict[str, str]] = {
    "light": {
        "surface": "#fcfcfb",
        "page": "#f9f9f7",
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "border": "rgba(11,11,11,0.10)",
        "series_1": "#2a78d6",
        "series_2": "#eb6834",
        "series_3": "#1baf7a",
        "deemphasis": "#c3c2b7",
        "good": "#0ca30c",
        "warning": "#fab219",
        "serious": "#ec835a",
        "critical": "#d03b3b",
        "delta_up": "#006300",
        # Sequential blue, ordinal-safe range (light end clears 2:1).
        "seq_1": "#86b6ef",
        "seq_2": "#5598e7",
        "seq_3": "#2a78d6",
        "seq_4": "#1c5cab",
        "seq_5": "#104281",
        "diverge_pos": "#2a78d6",
        "diverge_neg": "#e34948",
        "diverge_mid": "#f0efec",
    },
    "dark": {
        "surface": "#1a1a19",
        "page": "#0d0d0d",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "border": "rgba(255,255,255,0.10)",
        "series_1": "#3987e5",
        "series_2": "#d95926",
        "series_3": "#199e70",
        "deemphasis": "#52514e",
        "good": "#0ca30c",
        "warning": "#fab219",
        "serious": "#ec835a",
        "critical": "#d03b3b",
        "delta_up": "#0ca30c",
        "seq_1": "#184f95",
        "seq_2": "#1c5cab",
        "seq_3": "#2a78d6",
        "seq_4": "#5598e7",
        "seq_5": "#9ec5f4",
        "diverge_pos": "#3987e5",
        "diverge_neg": "#e66767",
        "diverge_mid": "#383835",
    },
}

STATUS_TOKEN = {
    "green": "good",
    "amber": "warning",
    "red": "critical",
    "unscored": "muted",
    "unknown": "muted",
}

# Status is never colour-alone (palette.md: icon + label pairing).
STATUS_GLYPH = {"green": "●", "amber": "▲", "red": "■", "unscored": "◇", "unknown": "○"}
# "unscored" = we have the number but no threshold to judge it against.
# "unknown"  = we could not compute the number at all. Keep them distinct.
STATUS_LABEL = {"green": "On track", "amber": "Watch", "red": "Off track",
                "unscored": "No target", "unknown": "No data"}

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def token(name: str, mode: str = "light") -> str:
    return TOKENS[mode][name]
