"""Typed analysis plans and deterministic computations; no generated code."""

from __future__ import annotations

import math
import re
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(StrictModel):
    column: str
    operator: Literal["equals", "not_equals", "contains", "gte", "lte"] = "equals"
    value: str = Field(max_length=300)


class Measure(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    column: str = ""
    aggregation: Literal[
        "count", "sum", "mean", "median", "min", "max", "distinct", "ratio"
    ] = "count"
    denominator: str = ""


class Analysis(StrictModel):
    title: str = Field(default="My data story", min_length=1, max_length=100)
    context: str = Field(default="", max_length=2000)
    table: str
    dimension: str = ""
    date_grain: Literal["original", "day", "month", "year"] = "original"
    measures: list[Measure] = Field(
        default_factory=lambda: [Measure(name="Records")], min_length=1, max_length=6
    )
    chart: Literal["bar", "line", "table"] = "bar"
    color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    trim_text: bool = False
    drop_duplicates: bool = False
    sections: list[Literal["summary", "chart", "quality", "data"]] = Field(
        default_factory=lambda: ["summary", "chart", "quality", "data"], min_length=1
    )


def number(value):
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def numeric(series):
    return pd.to_numeric(series, errors="coerce").replace(
        [float("inf"), -float("inf")], float("nan")
    )


def profile(frame):
    result = []
    for col in frame.columns:
        s = frame[col].replace("", pd.NA)
        nonnull = s.dropna()
        nums = numeric(nonnull)
        identifier = bool(re.search(r"(^id$|_id$|^id_|code$|postcode|zip)", col, re.I))
        is_number = len(nonnull) > 0 and nums.notna().all() and not identifier
        is_date = (
            len(nonnull) > 0
            and nonnull.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}").all()
        )
        result.append(
            {
                "name": col,
                "type": "number" if is_number else "date" if is_date else "text",
                "missing": int(s.isna().sum()),
                "distinct": int(s.nunique()),
                "numeric_values": int(nums.notna().sum()),
            }
        )
    return result


def suggest(table, frame, title):
    columns = profile(frame)
    dates = [c["name"] for c in columns if c["type"] == "date"]
    dimensions = [
        c["name"] for c in columns if c["type"] == "text" and 1 < c["distinct"] <= 30
    ]
    # Start with counts. Choosing sum/mean requires the meaning of a measure.
    return Analysis(
        title=title,
        table=table,
        dimension=(dates or dimensions or [""])[0],
        date_grain="month" if dates else "original",
    )


def validate(spec, tables):
    if spec.table not in tables:
        raise ValueError("Choose an available table.")
    frame = tables[spec.table]
    cols = set(frame.columns)
    if spec.dimension and spec.dimension not in cols:
        raise ValueError(f"Unknown grouping column: {spec.dimension}")
    names = [m.name for m in spec.measures]
    if len(set(names)) != len(names):
        raise ValueError("Give each metric a unique name.")
    for m in spec.measures:
        if m.aggregation != "count" and m.column not in cols:
            raise ValueError(f"Choose a column for {m.name}.")
        if m.aggregation == "ratio" and m.denominator not in cols:
            raise ValueError(f"Choose a denominator for {m.name}.")
    for f in spec.filters:
        if f.column not in cols:
            raise ValueError(f"Unknown filter column: {f.column}")
        if f.operator in ("gte", "lte") and number(f.value) is None:
            raise ValueError("Numeric filters need a finite number.")
    return spec


def compute(spec, tables):
    validate(spec, tables)
    raw = tables[spec.table]
    frame = raw.copy()
    if spec.trim_text:
        frame = frame.apply(
            lambda s: s.map(lambda v: v.strip() if isinstance(v, str) else v)
        )
    frame = frame.replace("", pd.NA)
    duplicate_count = int(frame.duplicated().sum())
    if spec.drop_duplicates:
        frame = frame.drop_duplicates()
    for f in spec.filters:
        s = frame[f.column]
        if f.operator == "equals":
            mask = s.astype("string").eq(f.value)
        elif f.operator == "not_equals":
            mask = s.astype("string").ne(f.value)
        elif f.operator == "contains":
            mask = s.astype("string").str.contains(f.value, regex=False)
        elif f.operator == "gte":
            mask = numeric(s) >= float(f.value)
        else:
            mask = numeric(s) <= float(f.value)
        frame = frame[mask.fillna(False)]
    warnings = []
    if duplicate_count:
        warnings.append(
            f"{duplicate_count:,} duplicate rows {'removed' if spec.drop_duplicates else 'retained; review before removing'}."
        )
    missing = int(frame.isna().sum().sum())
    if missing:
        warnings.append(
            f"{missing:,} missing cells. Numeric aggregations exclude missing and nonnumeric values; they are never filled with zero."
        )
    for m in spec.measures:
        if m.aggregation not in ("count", "distinct"):
            for col in {m.column, m.denominator} - {""}:
                excluded = int((frame[col].notna() & numeric(frame[col]).isna()).sum())
                if excluded:
                    warnings.append(
                        f"{col}: {excluded:,} nonnumeric or infinite values excluded from {m.name}."
                    )

    def aggregate(group, m):
        if m.aggregation == "count":
            return len(group)
        if m.aggregation == "distinct":
            return int(group[m.column].nunique())
        s = numeric(group[m.column])
        if m.aggregation == "ratio":
            # Paired complete observations prevent incompatible populations.
            denominator = numeric(group[m.denominator])
            valid = s.notna() & denominator.notna()
            den = denominator[valid].sum(min_count=1)
            return (
                number(s[valid].sum(min_count=1) / den)
                if pd.notna(den) and den != 0
                else None
            )
        if m.aggregation == "sum":
            return number(s.sum(min_count=1))
        return number(getattr(s, m.aggregation)()) if s.notna().any() else None

    totals = [
        {
            "name": m.name,
            "value": aggregate(frame, m),
            "definition": "Row count"
            if m.aggregation == "count"
            else f"sum({m.column}) / sum({m.denominator}); paired nonmissing rows"
            if m.aggregation == "ratio"
            else f"{m.aggregation}({m.column}); nonmissing values",
            "observations": len(frame)
            if m.aggregation == "count"
            else int(frame[m.column].notna().sum()),
        }
        for m in spec.measures
    ]
    if spec.dimension:
        group_values = frame[spec.dimension].astype("string").fillna("(Missing)")
        if spec.date_grain != "original":
            dates = pd.to_datetime(frame[spec.dimension], errors="coerce", utc=True)
            fmt = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[spec.date_grain]
            group_values = dates.dt.strftime(fmt).fillna("(Missing / invalid date)")
            if dates.isna().any():
                warnings.append(
                    f"{int(dates.isna().sum())} missing or invalid dates are shown in a separate group."
                )
        counts = frame.groupby(group_values, sort=True, dropna=False).size()
        grouped = pd.DataFrame({"records": counts})
        # Aggregate column-wise: a high-cardinality upload must not instantiate
        # one DataFrame and run Python once for every unique identifier.
        for i, m in enumerate(spec.measures):
            if m.aggregation == "count":
                values = counts
            elif m.aggregation == "distinct":
                values = frame[m.column].groupby(group_values).nunique()
            else:
                numbers = numeric(frame[m.column])
                if m.aggregation == "ratio":
                    denominator = numeric(frame[m.denominator])
                    valid = numbers.notna() & denominator.notna()
                    num = numbers.where(valid).groupby(group_values).sum(min_count=1)
                    den = (
                        denominator.where(valid).groupby(group_values).sum(min_count=1)
                    )
                    values = num / den.replace(0, float("nan"))
                elif m.aggregation == "sum":
                    values = numbers.groupby(group_values).sum(min_count=1)
                else:
                    values = getattr(numbers.groupby(group_values), m.aggregation)()
            grouped[f"metric_{i}"] = values
        group_count = len(grouped)
        if spec.chart != "line":
            grouped = grouped.sort_values(
                "metric_0", ascending=False, na_position="last", kind="stable"
            )
        rows = [
            {
                "label": str(key),
                "records": int(row["records"]),
                "values": [
                    number(row[f"metric_{i}"]) for i in range(len(spec.measures))
                ],
            }
            for key, row in grouped.head(20).iterrows()
        ]
    else:
        rows = [
            {
                "label": "All records",
                "values": [m["value"] for m in totals],
                "records": len(frame),
            }
        ]
        group_count = 1
    if group_count > 20:
        warnings.append(
            f"Showing 20 of {group_count:,} groups. Headline metrics use all filtered records."
        )
    summary = [f"{len(frame):,} of {len(raw):,} source records included."]
    summary += [
        f"{t['name']}: {t['value']:,.4g}. Definition: {t['definition']}."
        if t["value"] is not None
        else f"{t['name']}: unavailable for this selection."
        for t in totals
    ]
    if spec.dimension and group_count > 1:
        valid_groups = grouped["metric_0"].dropna()
        if len(valid_groups) > 1:
            highest, lowest = valid_groups.idxmax(), valid_groups.idxmin()
            if valid_groups[highest] != valid_groups[lowest]:
                summary.append(
                    f"Observed {spec.measures[0].name} is highest for {highest} "
                    f"({valid_groups[highest]:,.4g}; {int(grouped.loc[highest, 'records']):,} records) "
                    f"and lowest for {lowest} ({valid_groups[lowest]:,.4g}; "
                    f"{int(grouped.loc[lowest, 'records']):,} records). This is a descriptive comparison."
                )
    return {
        "rows": rows,
        "totals": totals,
        "source_rows": len(raw),
        "included_rows": len(frame),
        "groups": group_count,
        "warnings": list(dict.fromkeys(warnings)),
        "summary": summary,
        "columns": profile(raw),
        "preview": frame.head(8).fillna("").astype(str).to_dict("records"),
    }
