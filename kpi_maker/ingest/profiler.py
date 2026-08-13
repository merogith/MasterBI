"""What is actually in this file, and what is wrong with it.

Moved out of `api/server.py`, where it was business logic living in a route,
and extended to do the two things the route never did: guess what a column
*means* rather than only what type it holds, and turn each problem it finds
into a cleaning op the user can accept with one click.

The suggestions are offered, never applied. A user needs to see their data
before it changes, and the lineage log has to record a decision rather than
an assumption.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

# Semantic types. Wider than a dtype: `customer_id` and `revenue` are both
# int64, and only one of them should ever be summed.
SEMANTIC = ("date", "currency", "identifier", "category", "measure", "text", "boolean")

_CURRENCY_HINT = re.compile(r"(amount|revenue|cost|price|value|spend|mrr|arr|acv|"
                            r"fee|salary|budget|total|paid|invoice|billing)", re.I)
_ID_HINT = re.compile(r"(^id$|_id$|number|code|reference|sku|account)", re.I)
_DATE_HINT = re.compile(r"(date|month|day|period|created|closed|start|end|when)", re.I)
_CURRENCY_CHARS = re.compile(r"^\s*[-(]?\s*[$€£₺¥]\s*[\d.,\s]+\)?\s*$")

# Above this share of distinct values, a text column is free text rather than
# a category worth grouping by.
CATEGORY_MAX_DISTINCT_RATIO = 0.5
CATEGORY_MAX_DISTINCT = 200


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    semantic: str
    non_null: int
    null_pct: float
    unique: int
    samples: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    suggestions: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "dtype": self.dtype, "semantic": self.semantic,
            "non_null": self.non_null, "null_pct": round(self.null_pct, 4),
            "unique": self.unique, "samples": self.samples, "stats": self.stats,
            "problems": self.problems, "suggestions": self.suggestions,
        }


@dataclass
class TableProfile:
    rows: int
    columns: List[ColumnProfile]
    duplicate_rows: int = 0
    candidate_keys: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    suggestions: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "columns": [c.as_dict() for c in self.columns],
            "duplicate_rows": self.duplicate_rows,
            "candidate_keys": self.candidate_keys,
            "problems": self.problems,
            "suggestions": self.suggestions + [
                s for c in self.columns for s in c.suggestions],
        }


def profile_table(frame: pd.DataFrame, table: Optional[str] = None) -> TableProfile:
    columns = [_profile_column(frame[c], str(c)) for c in frame.columns]

    duplicates = int(frame.duplicated().sum())
    problems: List[str] = []
    suggestions: List[Dict[str, Any]] = []

    if duplicates:
        problems.append(f"{duplicates:,} exactly duplicated row(s)")
        suggestions.append(_suggest(
            "drop_duplicates", table, {},
            f"Remove {duplicates:,} exactly duplicated row(s)"))

    keys = [c.name for c in columns
            if c.unique == len(frame) and len(frame) > 0 and c.null_pct == 0
            and c.semantic in ("identifier", "text", "category")]

    return TableProfile(rows=int(len(frame)), columns=columns,
                        duplicate_rows=duplicates, candidate_keys=keys,
                        problems=problems, suggestions=suggestions)


def _profile_column(series: pd.Series, name: str) -> ColumnProfile:
    non_null = series.dropna()
    semantic = _semantic_type(series, name)

    profile = ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        semantic=semantic,
        non_null=int(non_null.size),
        null_pct=float(series.isna().mean()) if len(series) else 0.0,
        unique=int(non_null.nunique()) if non_null.size else 0,
        samples=[str(v) for v in non_null.head(3)],
    )

    _add_stats(profile, non_null, semantic)
    _add_problems(profile, series, non_null, semantic, name)
    return profile


def normalise_name(name: str) -> str:
    """`Cust ID` -> `cust_id`, so the hint patterns can match real headers.

    Matching against the raw name meant `_id$` never fired on "Cust ID" —
    spaces and capitals defeated every pattern, which is to say the hints only
    worked on names that were already tidy.
    """
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def numeric_convention(sample: pd.Series):
    """(rate, convention) for text that might be numbers.

    European exports write 1.234,56 where Anglo ones write 1,234.56. Reading
    the first as Anglo gives 1.23456 — a plausible-looking number that is
    wrong by three orders of magnitude, which is far worse than failing.
    """
    stripped = (sample.astype(str).str.strip()
                .str.replace(r"[$€£₺¥%\s]", "", regex=True)
                .str.replace(r"^\((.*)\)$", r"-\1", regex=True))   # (500) = -500
    if stripped.empty:
        return 0.0, "anglo"

    anglo = pd.to_numeric(stripped.str.replace(",", "", regex=False), errors="coerce")
    euro = pd.to_numeric(
        stripped.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce")
    anglo_rate, euro_rate = float(anglo.notna().mean()), float(euro.notna().mean())

    # Parse rate alone cannot decide this, and trusting it is dangerous.
    # "850,00" under the Anglo rule strips the comma to 85000 — it parses
    # perfectly and is a hundred times too large. Both conventions "succeed",
    # so the decimal marker has to be identified structurally instead.
    #
    # The tell is the LAST separator: a comma followed by exactly two digits at
    # the end of the value is a decimal comma, and a dot in the same position is
    # a decimal point. Grouping separators never appear there.
    euro_marks = stripped.str.contains(r",\d{2}$", regex=True, na=False).sum()
    anglo_marks = stripped.str.contains(r"\.\d{1,2}$", regex=True, na=False).sum()
    # A dot with three trailing digits is thousands, not a fraction: 1.234.
    thousand_dots = stripped.str.contains(r"\.\d{3}(?:\D|$)", regex=True, na=False).sum()

    if euro_marks > anglo_marks or (thousand_dots and euro_marks):
        return max(euro_rate, anglo_rate), "european"
    if anglo_marks > euro_marks:
        return anglo_rate, "anglo"
    if euro_rate > anglo_rate:
        return euro_rate, "european"
    return anglo_rate, "anglo"


def _semantic_type(series: pd.Series, name: str) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "text"

    key = normalise_name(name)

    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series) or isinstance(
            series.dtype, pd.PeriodDtype):
        return "date"

    sample = non_null.head(200).astype(str)

    # Name hints beat value shape for dates: a column called `month` holding
    # "2024-01" is a date, but so is one holding "202401", which no parser
    # recognises without being told what it is.
    if _DATE_HINT.search(key) and _parse_rate(sample) > 0.7:
        return "date"
    if not pd.api.types.is_numeric_dtype(series) and _parse_rate(sample) > 0.85:
        return "date"

    if sample.str.match(_CURRENCY_CHARS).mean() > 0.5:
        return "currency"

    if pd.api.types.is_numeric_dtype(series):
        # An integer column of mostly-distinct values under an id-shaped name
        # is a key, not a measure. Summing it is always wrong.
        if _ID_HINT.search(key) and non_null.nunique() > len(non_null) * 0.9:
            return "identifier"
        if _CURRENCY_HINT.search(key):
            return "currency"
        return "measure"

    # Numbers stored as text — the most common upload defect there is. Judging
    # only already-numeric columns meant every one of them fell through to
    # "text" and never got a conversion suggested, which is precisely the case
    # the profiler exists to catch.
    rate, _convention = numeric_convention(sample)
    if rate > 0.8:
        return "currency" if _CURRENCY_HINT.search(key) else "measure"

    if _ID_HINT.search(key):
        return "identifier"

    distinct_ratio = non_null.nunique() / max(len(non_null), 1)
    if (distinct_ratio <= CATEGORY_MAX_DISTINCT_RATIO
            and non_null.nunique() <= CATEGORY_MAX_DISTINCT):
        return "category"
    return "text"


def _parse_rate(sample: pd.Series) -> float:
    try:
        return float(pd.to_datetime(sample, errors="coerce",
                                    format="mixed").notna().mean())
    except Exception:                                       # noqa: BLE001
        return 0.0


def _add_stats(profile: ColumnProfile, non_null: pd.Series, semantic: str) -> None:
    if non_null.empty:
        return
    if semantic in ("measure", "currency"):
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if not numeric.empty:
            profile.stats = {
                "min": float(numeric.min()), "max": float(numeric.max()),
                "mean": float(numeric.mean()), "median": float(numeric.median()),
            }
    elif semantic == "category":
        counts = non_null.astype(str).value_counts().head(6)
        profile.stats = {"top_values": {str(k): int(v) for k, v in counts.items()}}
    elif semantic == "date":
        parsed = pd.to_datetime(non_null.astype(str), errors="coerce", format="mixed")
        if parsed.notna().any():
            profile.stats = {"earliest": str(parsed.min().date()),
                             "latest": str(parsed.max().date())}


def _add_problems(profile: ColumnProfile, series: pd.Series, non_null: pd.Series,
                  semantic: str, name: str) -> None:
    if profile.null_pct > 0.4:
        profile.problems.append(f"{profile.null_pct:.0%} of values are missing")
        if semantic in ("measure", "currency"):
            profile.suggestions.append(_suggest(
                "fill_missing", None, {"column": name, "strategy": "zero"},
                f"Fill the {profile.null_pct:.0%} missing values in {name} with zero"))
    elif 0 < profile.null_pct <= 0.4 and semantic in ("measure", "currency"):
        profile.problems.append(f"{profile.null_pct:.1%} of values are missing")

    if non_null.empty:
        profile.problems.append("the column is entirely empty")
        return

    # Numbers stored as text: the single most common upload defect, and it
    # silently makes every metric over the column impossible.
    if semantic in ("measure", "currency") and not pd.api.types.is_numeric_dtype(series):
        rate, convention = numeric_convention(non_null.head(200).astype(str))
        style = " (European decimal format)" if convention == "european" else ""
        profile.problems.append(f"numeric values are stored as text{style}")
        profile.suggestions.append(_suggest(
            "cast", None, {"column": name, "to": "number", "decimal": convention},
            f"Convert {name} to a number{style}"))

    if semantic == "date" and not pd.api.types.is_datetime64_any_dtype(series) \
            and not isinstance(series.dtype, pd.PeriodDtype):
        rate = _parse_rate(non_null.head(200).astype(str))
        unreadable = 1.0 - rate
        if unreadable > 0.001:
            profile.problems.append(f"{unreadable:.0%} of values do not read as dates")
        profile.suggestions.append(_suggest(
            "parse_dates", None, {"column": name, "to_month": True},
            f"Parse {name} as dates and collapse to months"))

    if series.dtype == object:
        text = non_null.astype(str)
        if (text != text.str.strip()).any():
            profile.problems.append("values have leading or trailing spaces")
            profile.suggestions.append(_suggest(
                "trim", None, {"columns": [name]},
                f"Trim whitespace from {name}"))
        if semantic == "category":
            folded = text.str.strip().str.lower().nunique()
            if folded < text.nunique():
                profile.problems.append(
                    f"{text.nunique() - folded} value(s) differ only by case or spacing")
                profile.suggestions.append(_suggest(
                    "normalize_case", None, {"columns": [name], "to": "lower"},
                    f"Normalise the case of {name} so variants stop counting separately"))

    if semantic in ("measure", "currency"):
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if len(numeric) >= 8:
            q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
            span = q3 - q1
            if span > 0:
                outliers = int(((numeric < q1 - 3 * span) | (numeric > q3 + 3 * span)).sum())
                if outliers:
                    profile.problems.append(
                        f"{outliers} value(s) far outside the normal range "
                        f"(max {numeric.max():,.0f} vs median {numeric.median():,.0f})")
                    profile.suggestions.append(_suggest(
                        "clip_outliers", None,
                        {"column": name, "method": "iqr", "factor": 3.0, "action": "clip"},
                        f"Cap {outliers} extreme value(s) in {name}"))
        if (numeric < 0).any() and _CURRENCY_HINT.search(name):
            profile.problems.append(f"{int((numeric < 0).sum())} negative value(s)")


def _suggest(op: str, table: Optional[str], params: Dict[str, Any],
             reason: str) -> Dict[str, Any]:
    return {"op": op, "table": table, "params": params, "reason": reason}
