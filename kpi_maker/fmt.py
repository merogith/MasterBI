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
