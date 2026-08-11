"""Deriving a token set from a brand colour, without losing the guarantee.

`viz/theme.py` ships two palettes that were validated all-pairs before release.
A brand colour dropped into slot 1 invalidates that validation, so this module
re-establishes it rather than assuming it: the two remaining categorical slots
are re-chosen against the brand, and the result is measured under normal,
deuteranopic and protanopic vision before it is returned.

Three properties are held on to deliberately:

**Three slots, always.** `MAX_CATEGORICAL_SERIES` is 3 because a fourth would
put yellow beside orange and fail the floor. A brand colour may change *which*
three, never *how many* — so when a slot has to move, it moves to another
colour, it is never dropped.

**The brand colour survives in charts.** It is only altered if it fails the 3:1
graphical floor against the surface, i.e. if the line would be near-invisible.
Slots 2 and 3 give way first; the brand is the last thing to move.

**Text is a separate question from fill.** The print renderers use the slot-1
colour as heading text, where the bar is 4.5:1 rather than 3:1. Rather than
force the chart colour up to a text bar it does not need, the derivation emits
a second token, `heading_accent`, which is the readable form of the same hue.
The default palette's own slot 1 sits at 4.30:1 against the page — below AA,
and shipped that way — so this is not a bar the defaults would clear either;
`heading_accent` therefore defaults to exactly today's value and only diverges
once a brand is actually supplied.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..viz.theme import MAX_CATEGORICAL_SERIES, TOKENS
from .contrast import (
    AA_TEXT,
    GRAPHICAL,
    MIN_DELTA_E,
    ColourError,
    delta_e,
    distinguishable,
    ensure_readable,
    parse_hex,
    ratio,
    simulate_cvd,
    to_hex,
)

SERIES_SLOTS: Tuple[str, ...] = tuple(
    f"series_{i}" for i in range(1, MAX_CATEGORICAL_SERIES + 1))

# Hue rotations tried when a default slot has to give way to the brand. Every
# 30 degrees is finer than the eye needs and coarse enough to stay cheap.
_ROTATIONS = tuple(range(30, 360, 30))

# A brand below this saturation is not used as a series colour at all. Two
# reasons, and the second is the real one: rotating the hue of a grey produces
# the same colour twelve times over, so no companion can be derived from it —
# and grey already *means* something in this palette. `deemphasis` is
# #c3c2b7 / #52514e, so a grey series would read as the faded one. Measured:
# every pure grey tested collides with slot 3 under deuteranopia (ΔE 9–23
# against a floor of 25), while saturation 0.15 upward passes everywhere.
_NEUTRAL_SATURATION = 0.12


@dataclass
class Adjustment:
    """One visible change, for the side-by-side swatch in the Studio."""
    token: str
    original: str
    applied: str
    reason: str
    detail: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"token": self.token, "original": self.original,
                "applied": self.applied, "reason": self.reason,
                "detail": self.detail}


@dataclass
class Palette:
    mode: str
    tokens: Dict[str, str]
    brand: Optional[str] = None
    adjustments: List[Adjustment] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    ok: bool = True
    min_delta_e: Optional[float] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "mode": self.mode, "brand": self.brand, "ok": self.ok,
            "tokens": dict(self.tokens),
            "series": [self.tokens[s] for s in SERIES_SLOTS],
            "adjustments": [a.as_dict() for a in self.adjustments],
            "notes": list(self.notes),
            "min_delta_e": (None if self.min_delta_e is None
                            else round(self.min_delta_e, 1)),
        }


# --------------------------------------------------------------------------

def _hls(colour: str) -> Tuple[float, float, float]:
    r, g, b = parse_hex(colour)
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def _from_hls(hue: float, lightness: float, saturation: float) -> str:
    return to_hex(tuple(
        c * 255 for c in colorsys.hls_to_rgb(hue % 1.0, lightness, saturation)))


def _worst_pair(colour: str, others: Sequence[str]) -> Tuple[float, str]:
    """Smallest perceptual gap to anything already chosen, and the vision that found it.

    Returning the vision matters for the explanation: "too close to your brand"
    is baffling when the two colours look obviously different to the reader
    being told. "Too close under deuteranopia" is a sentence they can act on.
    """
    worst, culprit = float("inf"), "normal"
    for other in others:
        for vision in ("normal", "deuteranopia", "protanopia"):
            a = colour if vision == "normal" else simulate_cvd(colour, vision)
            b = other if vision == "normal" else simulate_cvd(other, vision)
            distance = delta_e(a, b)
            if distance < worst:
                worst, culprit = distance, vision
    return worst, culprit


def _worst_distance(colour: str, others: Sequence[str]) -> float:
    return _worst_pair(colour, others)[0]


def _candidates(brand: str, default: str) -> List[str]:
    """Colours a displaced slot may move to, best-behaved first.

    The default is offered first so that a brand which happens not to clash
    keeps the shipped companion — the palette should change as little as the
    brand requires.
    """
    options = [default]
    brand_hue = _hls(brand)[0]
    # Match the displaced slot's own lightness and saturation so the newcomer
    # reads as part of the same palette rather than as a colour from elsewhere.
    _, lightness, saturation = _hls(default)
    for degrees in _ROTATIONS:
        options.append(_from_hls(brand_hue + degrees / 360.0, lightness, saturation))
    return options


def _choose_companions(brand: str, defaults: Sequence[str],
                       preferred: Optional[str] = None,
                       target: float = MIN_DELTA_E) -> Tuple[List[str], List[str]]:
    """Fill the remaining categorical slots against a fixed slot 1.

    Greedy max-min: each slot takes the candidate that is furthest from
    everything already chosen. Greedy rather than exhaustive because the
    candidate set is small, the result is deterministic, and a search that
    optimises the *sum* of distances will happily accept one bad pair to buy a
    great one elsewhere — which is exactly the failure this is preventing.
    """
    chosen: List[str] = [brand]
    notes: List[str] = []

    for index, default in enumerate(defaults):
        options = _candidates(brand, default)
        if index == 0 and preferred:
            options.insert(0, preferred)

        best, best_distance = None, -1.0
        for option in options:
            distance = _worst_distance(option, chosen)
            if distance >= target:
                best, best_distance = option, distance
                break                      # first acceptable, preserving order
            if distance > best_distance:
                best, best_distance = option, distance

        if best_distance < target:
            notes.append(
                f"slot {index + 2} could only reach ΔE {best_distance:.0f} "
                f"against the colours before it (floor {target:.0f}) — this "
                f"brand crowds the palette")
        chosen.append(best)

    return chosen[1:], notes


def _sequential(brand: str, mode: str) -> Dict[str, str]:
    """Rebuild the ordinal ramp in the brand hue, keeping the shipped lightness steps.

    The steps are what make the ramp readable in order; the hue is what makes
    it the user's. Keeping one and replacing the other is the whole trick.
    """
    base = TOKENS[mode]
    hue, _, saturation = _hls(brand)
    out = {}
    for i in range(1, 6):
        _, lightness, default_saturation = _hls(base[f"seq_{i}"])
        # Follow the default's saturation envelope, scaled towards the brand's
        # own — a pastel brand should not produce a vivid ramp.
        blended = default_saturation * (0.5 + 0.5 * min(saturation / 0.7, 1.0))
        out[f"seq_{i}"] = _from_hls(hue, lightness, blended)
    return out


# --------------------------------------------------------------------------

def derive_tokens(brand: Optional[str] = None, mode: str = "light",
                  accent: Optional[str] = None) -> Palette:
    """Token set for a brand colour, or the shipped palette unchanged if there is none.

    `brand` becomes slot 1, the sequential ramp's hue, the positive pole of the
    diverging scale and — in readable form — the heading accent. `accent`, when
    given, is *offered* for slot 2 and accepted only if it stays distinguishable.
    """
    if mode not in TOKENS:
        raise ColourError(f"unknown theme mode {mode!r}")

    tokens = dict(TOKENS[mode])
    if not brand:
        # The identity case has to be exact: a default DesignSpec must produce
        # byte-identical artifacts, so nothing is added or recomputed here.
        return Palette(mode=mode, tokens=tokens)

    brand = to_hex(parse_hex(brand))
    if accent:
        accent = to_hex(parse_hex(accent))

    adjustments: List[Adjustment] = []
    notes: List[str] = []
    surface, page = tokens["surface"], tokens["page"]
    readable = ensure_readable(brand, page, AA_TEXT)

    if _hls(brand)[2] < _NEUTRAL_SATURATION:
        # A neutral brand keeps the validated trio rather than displacing it.
        # It still reaches the deliverables — as heading colour, which is where
        # a grey or near-black brand mark usually wants to be anyway.
        tokens["heading_accent"] = readable["colour"]
        notes.append(
            f"{brand} is close to neutral, so the charts keep the validated "
            f"palette: grey already means 'de-emphasised' here, and a grey "
            f"series would be read as the faded one. Your colour is used for "
            f"headings and page furniture.")
        return Palette(mode=mode, tokens=tokens, brand=brand, notes=notes,
                       adjustments=adjustments, ok=True,
                       min_delta_e=_worst_distance(
                           tokens["series_3"], [tokens["series_1"], tokens["series_2"]]))

    # 1. The brand as a chart colour. Only moved if it would be near-invisible.
    chart = brand
    against_surface = ratio(brand, surface)
    if against_surface < GRAPHICAL:
        fixed = ensure_readable(brand, surface, GRAPHICAL)
        chart = fixed["colour"]
        adjustments.append(Adjustment(
            token="series_1", original=brand, applied=chart,
            reason="too close to the page to see as a line",
            detail=(f"{against_surface:.2f}:1 against the surface, below the "
                    f"{GRAPHICAL:.0f}:1 graphical floor — same hue, "
                    f"{ratio(chart, surface):.2f}:1 after")))

    # 2. The same colour as heading text, where the bar is higher.
    if readable["adjusted"]:
        adjustments.append(Adjustment(
            token="heading_accent", original=brand, applied=readable["colour"],
            reason="headings have to be readable, not just visible",
            detail=(f"{readable['original_ratio']}:1 against the page, below "
                    f"AA {AA_TEXT} — same hue, {readable['ratio']}:1 after")))

    # 3. The companions.
    defaults = [TOKENS[mode][s] for s in SERIES_SLOTS[1:]]
    companions, slot_notes = _choose_companions(chart, defaults, preferred=accent)
    notes.extend(slot_notes)

    for index, (slot, default, applied) in enumerate(
            zip(SERIES_SLOTS[1:], defaults, companions)):
        if applied.lower() == default.lower():
            continue
        # Measured against everything placed before this slot, which is what
        # the choice was made against — not just against the brand.
        earlier = [chart] + companions[:index]
        before, vision = _worst_pair(default, earlier)
        after = _worst_distance(applied, earlier)
        if accent and applied == accent:
            reason, detail = "your secondary brand colour", f"ΔE {after:.0f} clear"
        else:
            reason = "the shipped colour was too close to your brand to tell apart"
            detail = (f"ΔE {before:.0f} {'' if vision == 'normal' else 'under ' + vision + ' '}"
                      f"against the colours before it, below the {MIN_DELTA_E:.0f} "
                      f"floor — {after:.0f} after")
        adjustments.append(Adjustment(token=slot, original=default,
                                      applied=applied, reason=reason, detail=detail))

    if accent and accent not in companions:
        notes.append(
            f"your secondary colour {accent} was not used — it is too close to "
            f"{chart} to read as a separate series")

    tokens["series_1"] = chart
    for slot, colour in zip(SERIES_SLOTS[1:], companions):
        tokens[slot] = colour
    tokens["heading_accent"] = readable["colour"]
    tokens["diverge_pos"] = chart
    tokens.update(_sequential(chart, mode))

    series = [tokens[s] for s in SERIES_SLOTS]
    verdict = distinguishable(series)
    worst = min((_worst_distance(c, series[:i])
                 for i, c in enumerate(series) if i), default=None)

    return Palette(mode=mode, tokens=tokens, brand=brand,
                   adjustments=adjustments, notes=notes,
                   ok=bool(verdict["ok"]), min_delta_e=worst)


def heading_accent(tokens: Dict[str, str]) -> str:
    """The brand colour in text-safe form, or slot 1 when there is no brand.

    The shipped palette has no separate heading accent — slot 1 does that job,
    at 4.30:1 against the page. Falling back to it rather than inventing a
    readable variant is deliberate: raising the default to AA would change
    every unbranded PDF, which is a design decision, not a refactor.
    """
    return tokens.get("heading_accent") or tokens["series_1"]


def resolve_palette(design, mode: Optional[str] = None) -> Palette:
    """`DesignSpec` -> `Palette`, for the render stage.

    Takes the spec rather than the colour so that callers do not each have to
    remember that `auto` means light for print.
    """
    if mode is None:
        theme = getattr(getattr(design, "theme", None), "value", None) \
            or getattr(design, "theme", None) or "light"
        mode = "light" if theme == "auto" else str(theme)
    brand = getattr(design, "brand", None)
    return derive_tokens(getattr(brand, "primary", None), mode,
                         getattr(brand, "accent", None))
