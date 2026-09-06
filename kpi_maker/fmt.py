"""Shared value formatting. One implementation so a number reads identically
in the dashboard, the workbook and the report.

`locale` decides the separators only. That is a deliberately small definition:
a German reader needs `1.234,50` rather than `1,234.50` or the number is
briefly unreadable, and getting that wrong in a board pack is the kind of
mistake that costs more trust than it should. Translating the *words* is a
different and much larger job — every section title, every finding sentence,
every KPI name — and pretending `locale` did that would be worse than not
offering it.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "TRY": "₺", "JPY": "¥"}

# Separator conventions, keyed by the name `DesignSpec.locale` carries. Two
# families cover every country the profile schema offers today; a third
# (space-grouped, as France and much of Scandinavia write it) is here because
# it is one line and the alternative is a French report reading as wrong.
SEPARATORS = {
    "en": {"group": ",", "decimal": "."},
    "eu": {"group": ".", "decimal": ","},
    "fr": {"group": " ", "decimal": ","},   # narrow no-break space
}

# Which family a locale tag belongs to. Matched on the language subtag, so
# "de-AT" and "de" both land on "eu".
_LANGUAGE_FAMILY = {
    "en": "en", "de": "eu", "tr": "eu", "nl": "eu", "it": "eu", "es": "eu",
    "pt": "eu", "da": "eu", "fi": "eu", "fr": "fr", "sv": "fr", "nb": "fr",
}

# Countries whose default reading is not English, for profiles that set a
# country but never a language. Deliberately not a full list: an unknown
# country falls back to "en", which is the safe default rather than a guess.
_COUNTRY_FAMILY = {
    "DE": "eu", "TR": "eu", "NL": "eu", "IT": "eu", "ES": "eu", "PT": "eu",
    "AT": "eu", "BE": "eu", "DK": "eu", "FI": "eu",
    "FR": "fr", "SE": "fr", "NO": "fr",
}

DEFAULT_LOCALE = "en"


def family_for(locale: Optional[str]) -> str:
    """The separator family a locale tag belongs to. Unknown tags read as en."""
    if not locale:
        return DEFAULT_LOCALE
    tag = str(locale).replace("_", "-").lower()
    if tag in SEPARATORS:
        return tag
    return _LANGUAGE_FAMILY.get(tag.split("-")[0], DEFAULT_LOCALE)


def family_for_country(country: Optional[str]) -> Optional[str]:
    """The separator family implied by a country code, or None if unknown."""
    return _COUNTRY_FAMILY.get(str(country or "").upper()) or None


def _localise(text: str, locale: Optional[str]) -> str:
    """Re-punctuate an anglo-formatted number for `locale`.

    Formatting anglo-first and swapping afterwards keeps one set of format
    strings. Swapping through a placeholder matters: replacing "," with "."
    and then "." with "," in sequence turns 1,234.50 into 1.234.50, and the
    bug only shows on numbers that have both separators.
    """
    sep = SEPARATORS[family_for(locale)]
    if sep["group"] == "," and sep["decimal"] == ".":
        return text
    return (text.replace(",", "\x00")
                .replace(".", sep["decimal"])
                .replace("\x00", sep["group"]))


def is_missing(value: Optional[float]) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))


def fmt_value(value: Optional[float], unit: str, currency: str = "USD",
              compact: bool = True, locale: Optional[str] = None) -> str:
    if is_missing(value):
        return "—"
    value = float(value)
    if unit == "pct":
        return _localise(f"{value:.1%}", locale)
    if unit == "currency":
        sym = CURRENCY_SYMBOL.get(currency, "")
        if compact:
            for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if abs(value) >= threshold:
                    return sym + _localise(f"{value / threshold:,.1f}", locale) + suffix
        return sym + _localise(f"{value:,.0f}", locale)
    if unit in ("months", "days", "hours"):
        short = {"months": "mo", "days": "d", "hours": "h"}[unit]
        return _localise(f"{value:,.1f}", locale) + f" {short}"
    if unit in ("count", "score"):
        return _localise(f"{value:,.0f}", locale)
    return _localise(f"{value:,.2f}", locale)


def fmt_percent(value: Optional[float], decimals: int = 1,
                locale: Optional[str] = None) -> str:
    """A percentage at a chosen precision, localised.

    `fmt_value(..., "pct")` always prints one decimal. Prose sometimes wants a
    whole number — "41% of gross new ARR is lost" reads better than "40.7%" in
    a headline — and before this existed those call sites used a raw
    `f"{x:.0%}"`, which is anglo-only. That produced sentences carrying a
    localised currency and an unlocalised percentage side by side.
    """
    if is_missing(value):
        return "—"
    return _localise(f"{float(value):.{decimals}%}", locale)


def fmt_delta(value: Optional[float], unit: str, currency: str = "USD",
              locale: Optional[str] = None) -> str:
    if is_missing(value):
        return "—"
    sign = "+" if value >= 0 else "−"
    return f"{sign}{fmt_value(abs(value), unit, currency, locale=locale)}"


def fmt_move(current: Optional[float], prior: Optional[float], unit: str,
             locale: Optional[str] = None) -> Optional[str]:
    """How far a metric moved against a prior reading, unsigned.

    **A percentage metric moves in points, not per cent**, and conflating the
    two is how "gross margin rose 8%" comes to mean two different things on
    one page. The rule lived inline in `render/dashboard._stat_tile` and was
    about to be spelled a third way by 5.3g's driver drill-down, so it is here
    now and `web/src/lib/format.ts` mirrors it under a drift test — the same
    arrangement as `STATUS_LABEL` and the design tokens.

    Unsigned on purpose: the direction is an arrow, and whether the move is
    *good* is `KPI.improves_with`, which is the one place that decides.
    """
    if is_missing(current) or is_missing(prior) or not prior:
        return None
    change = float(current) - float(prior)
    if unit == "pct":
        # **Times one hundred, and that factor is a bug this function was
        # written to inherit.** `pct` values are stored as fractions, so the
        # inline version in `_stat_tile` formatted 0.079 as "0.1 pts" — every
        # percentage metric's year-on-year move on every tile, understated a
        # hundredfold since the tiles were written. It read as a plausible
        # "barely moved": one real dashboard showed gross margin going 28.5%
        # to 32.9% as "0.0 pts", and EBITDA 5.4% to 10.8% as "0.1 pts".
        #
        # Invisible until the rule was pulled out of the renderer and run
        # against inputs whose answer was known. Extracting shared code is
        # not usually a way to find defects; here it was the only thing that
        # asked what the function returns.
        return _localise(f"{abs(change) * 100:.1f} pts", locale)
    return fmt_percent(abs(change) / abs(float(prior)), 0, locale)
