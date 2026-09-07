"""WCAG 2.x contrast, and how to fix a colour that fails it.

The chart palette in `viz/theme.py` is not a set of colours somebody liked. Its
docstring records that both modes were run through an all-pairs contrast check
before shipping. A brand colour dropped into that palette can break the
guarantee, so it has to be measured rather than trusted.

Two thresholds matter here, and both are about a colour against the *surface*
behind it:

  * **4.5:1** — WCAG AA for text (§1.4.3), the bar for anything a reader has to
    read rather than merely notice.
  * **3:1** — the non-text / graphical-object bar (§1.4.11), the bar for a line
    or a bar segment, which only has to be visible.

Whether two *series* can be told apart from each other is a different question
and contrast is the wrong instrument for it — see the second half of this
module, which measures perceptual distance instead.
"""
from __future__ import annotations

import colorsys
import re
from typing import Dict, Optional, Tuple

AA_TEXT = 4.5
AA_LARGE = 3.0
GRAPHICAL = 3.0

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ColourError(ValueError):
    """A colour that cannot be parsed."""


def parse_hex(colour: str) -> Tuple[int, int, int]:
    """'#2a78d6' or '2a78d6' or '#28d' -> (r, g, b)."""
    match = _HEX.match(str(colour).strip())
    if not match:
        raise ColourError(
            f"{colour!r} is not a hex colour — use #rrggbb or #rgb")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb)


def _channel_luminance(value: int) -> float:
    """sRGB -> linear, per WCAG's relative-luminance definition."""
    fraction = value / 255.0
    return (fraction / 12.92 if fraction <= 0.04045
            else ((fraction + 0.055) / 1.055) ** 2.4)


def relative_luminance(colour: str) -> float:
    r, g, b = parse_hex(colour)
    return (0.2126 * _channel_luminance(r)
            + 0.7152 * _channel_luminance(g)
            + 0.0722 * _channel_luminance(b))


def ratio(foreground: str, background: str) -> float:
    """Contrast ratio, 1.0 to 21.0. Order does not matter."""
    a, b = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def passes(foreground: str, background: str, target: float = AA_TEXT) -> bool:
    return ratio(foreground, background) >= target


# --------------------------------------------------------------------------

def ensure_readable(colour: str, surface: str, target: float = AA_TEXT,
                    steps: int = 100) -> Dict[str, object]:
    """Nudge a colour until it clears `target` against `surface`, keeping its hue.

    Lightness only: hue and saturation are what make a brand colour recognisable
    as *theirs*, and shifting either to win a contrast argument produces a
    colour the user does not accept. Darkening or lightening the same hue is a
    change they can see is still their colour.

    Direction is chosen by the surface: on a light surface a colour has to get
    darker to gain contrast, on a dark one lighter. Trying both and picking the
    nearest would sometimes flip a brand from navy to pale blue, which is a
    bigger change than the one it avoids.
    """
    original_ratio = ratio(colour, surface)
    if original_ratio >= target:
        return {
            "colour": _normalise(colour), "original": _normalise(colour),
            "adjusted": False, "ratio": round(original_ratio, 2),
            "original_ratio": round(original_ratio, 2), "target": target,
            "note": "",
        }

    r, g, b = parse_hex(colour)
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    darken = relative_luminance(surface) > 0.5

    best: Optional[str] = None
    for step in range(1, steps + 1):
        moved = lightness * (1 - step / steps) if darken else \
            lightness + (1 - lightness) * (step / steps)
        candidate = to_hex(tuple(
            c * 255 for c in colorsys.hls_to_rgb(hue, moved, saturation)))
        if ratio(candidate, surface) >= target:
            best = candidate
            break

    if best is None:
        # Only reachable when even pure black or white cannot clear the target
        # against this surface — a mid-grey surface, in practice.
        best = "#000000" if darken else "#ffffff"

    return {
        "colour": best, "original": _normalise(colour), "adjusted": True,
        "ratio": round(ratio(best, surface), 2),
        "original_ratio": round(original_ratio, 2), "target": target,
        "note": ("Darkened" if darken else "Lightened")
                + f" to reach {target}:1 against the page — same hue, "
                  f"{original_ratio:.2f}:1 was not readable.",
    }


# --------------------------------------------------------------------------
# Categorical separation — a different question from contrast
# --------------------------------------------------------------------------
#
# WCAG contrast is the wrong tool for asking whether two series are
# distinguishable from EACH OTHER. Blue and orange have nearly the same
# luminance — 1.38:1 by contrast ratio — and are instantly separable. Judging
# categorical slots by contrast ratio rejects the palette this project already
# validated, so the measure has to be perceptual difference instead.
#
# The threshold below is calibrated against real values rather than picked:
# the shipped trio scores 87–114 in every pair, while two shades of the same
# blue score 2–16. 25 sits clearly between them.
MIN_DELTA_E = 25.0

_D65 = (0.95047, 1.0, 1.08883)


def _to_lab(colour: str) -> Tuple[float, float, float]:
    r, g, b = (_channel_luminance(c) for c in parse_hex(colour))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / _D65[0]
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / _D65[1]
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / _D65[2]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(first: str, second: str) -> float:
    """CIE76 perceptual distance. Bigger is more distinguishable."""
    a, b = _to_lab(first), _to_lab(second)
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# Viénot, Brettel & Mollon (1999) dichromat simulation, applied in linear RGB.
# Deuteranopia and protanopia together cover the large majority of colour
# vision deficiency; tritanopia is rare enough that a palette passing these two
# is not usually the thing that fails someone.
_CVD = {
    "deuteranopia": ((0.625, 0.375, 0.0), (0.70, 0.30, 0.0), (0.0, 0.30, 0.70)),
    "protanopia": ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)),
}


def simulate_cvd(colour: str, kind: str) -> str:
    matrix = _CVD[kind]
    linear = [_channel_luminance(c) for c in parse_hex(colour)]
    out = []
    for row in matrix:
        value = sum(m * c for m, c in zip(row, linear))
        value = max(0.0, min(1.0, value))
        # Back to sRGB.
        srgb = (12.92 * value if value <= 0.0031308
                else 1.055 * value ** (1 / 2.4) - 0.055)
        out.append(srgb * 255)
    return to_hex(tuple(out))


def distinguishable(colours, target: float = MIN_DELTA_E) -> Dict[str, object]:
    """Are these categorical colours separable — in normal and colour-deficient vision?

    Checked under simulated deuteranopia and protanopia as well as directly,
    because a red/green pair that looks obvious to most readers vanishes for
    roughly one man in twelve. That is the property that makes the shipped
    blue/orange/green trio a considered choice rather than a nice-looking one.
    """
    failures = []
    for i, first in enumerate(colours):
        for second in colours[i + 1:]:
            for vision in ("normal",) + tuple(_CVD):
                a = first if vision == "normal" else simulate_cvd(first, vision)
                b = second if vision == "normal" else simulate_cvd(second, vision)
                distance = delta_e(a, b)
                if distance < target:
                    failures.append({
                        "pair": [_normalise(first), _normalise(second)],
                        "vision": vision,
                        "delta_e": round(distance, 1),
                    })
    return {"ok": not failures, "failures": failures, "target": target}


#: Palette roles that carry *text* somewhere as well as being drawn as a mark.
#: A 2px severity rule and a 10.5px severity chip are the same colour doing two
#: jobs with two different contrast requirements, and only the first was ever
#: checked.
DUAL_ROLE = ("series_1", "good", "warning", "critical", "serious")


def composite(colour: str, over: str, alpha: float) -> str:
    """A translucent wash flattened against what is behind it.

    A wash is a background like any other once it is painted, and text drawn
    on it has to clear the bar against *that*, not against the page it happens
    to sit on. `--accent-soft` at 10% over the page composites to `#e4ecf4`,
    which is darker than the page by enough to fail a colour that clears it —
    which is exactly how the north-star row's link came back at 4.03:1 after
    everything on the page around it had been fixed.
    """
    fg, bg = parse_hex(colour), parse_hex(over)
    return to_hex(tuple(  # type: ignore[arg-type]
        round(alpha * fg[i] + (1 - alpha) * bg[i]) for i in range(3)))


def text_variant(colour: str, *backgrounds: str) -> str:
    """The AA-legible version of `colour` against the worst of `backgrounds`.

    **The rule this applies already existed and was only ever pointed at one
    colour.** `derive_tokens` runs `ensure_readable(brand, page, AA_TEXT)` on
    the hex a *user* supplies and on nothing else, so the fifteen the product
    ships were never checked as text — `muted` does not appear in that module
    at all. Measured with axe against a live server, that left 183 failing
    nodes across six screens, 125 on the results screen alone.

    Worst-of rather than one named background because a token is used on both
    the page and a card, and a value that clears 4.5:1 on one and not the other
    is a defect that only shows on some screens.

    Graphical values are deliberately *not* run through this. A chart line
    needs 3:1 under WCAG 1.4.11, and forcing it to a text threshold would be
    this repo's recurring trap in reverse — a threshold borrowed for one
    population applied to another, which is how the HHI floor, the top-ten
    share and the cross-sector bands all went wrong.
    """
    worst = min(backgrounds, key=lambda bg: ratio(colour, bg))
    fixed = ensure_readable(colour, worst, AA_TEXT)
    return str(fixed["colour"])


def _normalise(colour: str) -> str:
    return to_hex(parse_hex(colour))
