"""Design tokens for the chart layer.

Values are the validated reference palette. Both modes were run through
`validate_palette.js` at the exact slots used here:

    light categorical (#2a78d6,#eb6834,#1aa674) --pairs all -> ALL PASS
    dark  categorical (#3987e5,#d95926,#199e70) --pairs all -> ALL PASS

This block used to record a WARN — "aqua at 2.74:1 vs surface, relief rule
applies" — for a slot-3 green that has since been moved. It is the same
sentence twice: the note below on `series_3` explains the move, and leaving the
old measurement at the top of the file made the module disagree with itself.
Measured as shipped, against each mode's own surface:

    light  series_1 4.30   series_2 3.12   series_3 3.03
    dark   series_1 4.79   series_2 4.48   series_3 5.11

Every one clears the 3:1 graphical floor `design/palette.derive_tokens`
imposes on a user's brand colour — which was the whole point of moving it, and
is what `tests/design.py` now pins.
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
        # Darkened from #1baf7a, which sat at 2.74:1 against the surface —
        # below the 3:1 graphical floor `design/palette.derive_tokens` imposes
        # on a *user's* brand colour. That floor was only ever applied to slot
        # 1, so the shipped companions were never held to it: a user supplying
        # the old green would have had it moved with "too close to the page to
        # see as a line" while this one stayed. Produced by `ensure_readable`
        # itself, so the correction is the same one a user would get. 3.03:1,
        # same hue, and ΔE separation from the other two is unchanged.
        "series_3": "#1aa674",
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

# --------------------------------------------------------------------------
# Scenario notation (IBCS "Unify")
# --------------------------------------------------------------------------
#
# One visual language for actual / plan / prior, defined once and read by
# every chart builder, so a plan line cannot mean one thing on the trend
# exhibit and another on the bridge. Before 5.2 there was no notation at all —
# nothing drew a plan, because nothing produced one until 5.1 — and `dash`
# appeared ad hoc in three places for *reference* lines. Keeping those apart
# is the point: a reference line is chrome, a scenario is data.
#
# **Distinguished by weight and pattern, never by colour alone**, which is the
# same rule `STATUS_GLYPH` enforces for RAG and the reason it can survive a
# greyscale print and a colour-vision deficiency. Actual is the only solid,
# heaviest line: it is what happened, and it should read first.
#
# **Forecast is absent, and that is deliberate.** IBCS names four scenarios;
# nothing in this engine produces a forecast, so giving it a notation would be
# a visual vocabulary for something no chart can draw — the dead-spec-field
# pattern in a stylesheet. It goes in with its producer.
SCENARIO_NOTATION: Dict[str, Dict[str, object]] = {
    # Solid, full weight, full opacity.
    "actual": {"dash": "solid", "width": 2.4, "opacity": 1.0,
               "label": "Actual"},
    # Dashed and lighter: a commitment, not an outcome. IBCS draws plan as an
    # outline; on a line chart the equivalent is an unfilled dashed stroke.
    "plan": {"dash": "dash", "width": 1.8, "opacity": 0.95, "label": "Plan"},
    # Dotted and recessive. Prior year is context, and it should never compete
    # with the two lines the reader is being asked to compare.
    "prior": {"dash": "dot", "width": 1.4, "opacity": 0.75,
              "label": "Prior year"},
}

#: The token each scenario draws in. Plan and prior share the deemphasis role
#: rather than taking series slots of their own: `MAX_CATEGORICAL_SERIES` is 3
#: and a chart showing actual, plan and prior for *one* metric is showing one
#: subject three ways, not three subjects.
SCENARIO_TOKEN = {"actual": "series_1", "plan": "deemphasis",
                  "prior": "deemphasis"}

# Status is never colour-alone (palette.md: icon + label pairing).
STATUS_GLYPH = {"green": "●", "amber": "▲", "red": "■", "unscored": "◇", "unknown": "○"}
# "unscored" = we have the number but no threshold to judge it against.
# "unknown"  = we could not compute the number at all. Keep them distinct.
STATUS_LABEL = {"green": "On track", "amber": "Watch", "red": "Off track",
                "unscored": "No target", "unknown": "No data"}

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def token(name: str, mode: str = "light") -> str:
    return TOKENS[mode][name]
