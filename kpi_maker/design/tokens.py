"""The CSS custom properties a palette implies — derived once, read by both.

7.4a gave every dual-role token an AA-legible `-text` twin, because a value
validated at the 3:1 graphical floor is right for a 2px chart line and wrong
for 13px chip text. It derived them in `tools/gen_tokens.py`, which writes
`web/src/tokens.css` **and nothing else** — so the fix reached the app and
stopped there. `render/dashboard.py` builds its own `:root` block from the same
`viz.theme.TOKENS` and went on painting status chips with graphical values.

Measured on a freshly rendered dashboard, the two modes disagree about which
roles fail, which is why neither alone is the answer:

    light   good 3.18/3.27   serious 2.50/2.57   warning 1.74/1.79
            series_1 4.19/4.30
    dark    critical 4.05/3.62

That is the artifact a board is emailed, and it is the one surface in this
product with no gate on it at all.

**The derivation has to run per render, not once at build time.** A user's
brand colour replaces `series_1` (`design/palette.derive_tokens`), and the
dashboard takes `tokens_light`/`tokens_dark` overrides for exactly that reason,
so a table of twins computed from the shipped palette would be wrong for every
branded run. Hence a function over a token dict rather than a constant — and
hence this module rather than a second copy in the renderer, which is the
"state the fact once and derive the rest" rule `tools/gen_tokens.py` was itself
written to apply.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..viz.theme import FONT_STACK
from .contrast import DUAL_ROLE, composite, parse_hex, text_variant

#: Dark needs a heavier wash for the same apparent weight — a 10% tint reads as
#: nothing against a near-black page. Strings rather than floats because the
#: exact spelling reaches `tokens.css`, which CI compares byte for byte.
SOFT_ALPHA = {"light": "0.10", "dark": "0.14"}


def soft(hex_colour: str, alpha: str) -> str:
    """A translucent wash of a colour, for hover and selection fills."""
    red, green, blue = parse_hex(hex_colour)
    return f"rgba({red}, {green}, {blue}, {alpha})"


def css_variables(tokens: Dict[str, str], mode: str) -> List[Tuple[str, str]]:
    """Every custom property one mode needs, as `(--name, value)` in order.

    The caller formats and indents; two callers with different indentation is
    not a reason for two derivations.

    Graphical values are emitted unchanged and the twins sit beside them. That
    separation is the whole point: forcing a chart line up to a text threshold
    would be this repo's recurring trap in reverse — a threshold borrowed for
    one population applied to another, which is how the HHI floor, the top-ten
    share and the cross-sector bands all went wrong in the other direction.
    """
    out: List[Tuple[str, str]] = [
        (f"--{name.replace('_', '-')}", value) for name, value in tokens.items()]

    # `--accent` is the first series slot: the app's links and focus rings are
    # the same blue as the first line on every chart, deliberately.
    out.append(("--accent", tokens["series_1"]))
    out.append(("--accent-soft", soft(tokens["series_1"], SOFT_ALPHA[mode])))

    # Four grounds, not two. The page and the surface are the obvious pair; the
    # accent wash is a background wherever a row is highlighted, and at 10%
    # over the page it composites to #e4ecf4 — dark enough that a colour
    # clearing 4.5:1 on the page came back at 4.03:1 on the north-star row.
    # Found by the axe gate after every other node on the screen was clean.
    alpha = float(SOFT_ALPHA[mode])
    grounds = (tokens["page"], tokens["surface"],
               composite(tokens["series_1"], tokens["page"], alpha),
               composite(tokens["series_1"], tokens["surface"], alpha))
    for role in DUAL_ROLE:
        name = "accent" if role == "series_1" else role
        out.append((f"--{name.replace('_', '-')}-text",
                    text_variant(tokens[role], *grounds)))

    out.append(("--font", FONT_STACK))
    return out


def css_block(tokens: Dict[str, str], mode: str, indent: str = "  ") -> str:
    """`css_variables` rendered as declarations, one per line."""
    return "\n".join(f"{indent}{name}: {value};"
                     for name, value in css_variables(tokens, mode))
