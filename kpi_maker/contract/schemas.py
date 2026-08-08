"""The canonical fact tables, declared once.

Everything downstream assumes these columns exist with these types. Until now
that assumption lived implicitly in whichever metric happened to read a column,
so a mis-shaped table surfaced as a KeyError four stages later with no
indication of what the data should have looked like.

Declared with pandera rather than by hand because the failure report is the
product here: a user who uploaded the wrong file needs to know which column is
missing and which values are out of range, not that something raised.

`strict=False` throughout — extra columns are fine. Calculated columns add
their own, and an upload that carries more than we need is not an error.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd
import pandera.pandas as pa

def _parses_as_month(series: pd.Series) -> bool:
    """Accept anything that reads as a month, whatever it is stored as."""
    if isinstance(series.dtype, pd.PeriodDtype):
        return True
    try:
        return pd.PeriodIndex(series.astype(str), freq="M") is not None
    except (ValueError, TypeError):
        return False


# No dtype assertion on `month`, deliberately. In memory it is `period[M]`;
# after a CSV round-trip — which is exactly what the API and the static build
# do — it is a string. Both are the same month, so the contract checks that it
# *reads* as one rather than picking a storage format and rejecting the other.
MONTH = pa.Column(
    nullable=False,
    checks=pa.Check(_parses_as_month, element_wise=False,
                    error="not readable as a YYYY-MM month"),
    description="period, as YYYY-MM",
)

# Same reasoning: an id may be an integer here and a string in someone's export.
# What matters is that it is present and identifies a row.
IDENTIFIER = pa.Column(nullable=False)


def _money(nullable: bool = False, **checks) -> pa.Column:
    # coerce=True so an int64 column of whole currency units validates as the
    # float the metrics engine will treat it as.
    return pa.Column(float, nullable=nullable, coerce=True,
                     checks=list(checks.values()) or None)


NON_NEGATIVE = pa.Check.ge(0)
A_FRACTION = pa.Check.in_range(-1.0, 1.0)

FACT_SCHEMAS: Dict[str, pa.DataFrameSchema] = {

    "monthly_financials": pa.DataFrameSchema(
        {
            "month": MONTH,
            "mrr": _money(ge=NON_NEGATIVE),
            "arr": _money(ge=NON_NEGATIVE),
            "revenue": _money(ge=NON_NEGATIVE),
            "cogs": _money(),
            "gross_profit": _money(),
            "gross_margin_pct": pa.Column(float, coerce=True, checks=A_FRACTION),
            "sales_cost": _money(),
            "marketing_cost": _money(),
            "rnd_cost": _money(),
            "ga_cost": _money(),
            "total_opex": _money(),
            "ebitda": _money(),
            "cash": _money(),
            "net_burn": _money(),
        },
        strict=False, unique=["month"], name="monthly_financials",
        description="One row per month. The spine every metric reindexes onto.",
    ),

    "customers": pa.DataFrameSchema(
        {
            "customer_id": IDENTIFIER,
            "segment": pa.Column(nullable=False),
            "acquired_month": MONTH,
            "initial_acv": _money(ge=NON_NEGATIVE),
            "final_acv": _money(ge=NON_NEGATIVE),
            "is_active": pa.Column(bool, coerce=True),
        },
        strict=False, unique=["customer_id"], name="customers",
        description="One row per customer. No month column — entity grain.",
    ),

    "mrr_movements": pa.DataFrameSchema(
        {
            "month": MONTH,
            "customer_id": IDENTIFIER,
            "movement_type": pa.Column(
                checks=pa.Check.isin(
                    ["new", "expansion", "contraction", "churn", "reactivation"])),
            "delta_mrr": pa.Column(float, coerce=True),
        },
        strict=False, name="mrr_movements",
        description="One row per customer per month per movement type.",
    ),

    "headcount": pa.DataFrameSchema(
        {
            "month": MONTH,
            "function": pa.Column(nullable=False),
            "fte": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "cost": _money(),
        },
        strict=False, name="headcount",
        description="Month x function.",
    ),

    "marketing": pa.DataFrameSchema(
        {
            "month": MONTH,
            "channel": pa.Column(nullable=False),
            "spend": _money(),
            "leads": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, name="marketing",
        description="Month x channel.",
    ),

    "pipeline": pa.DataFrameSchema(
        {
            "month": MONTH,
            "opps_won": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "win_rate": pa.Column(float, coerce=True,
                                  checks=pa.Check.in_range(0.0, 1.0)),
            "sales_cycle_days": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, unique=["month"], name="pipeline",
    ),

    "product_usage": pa.DataFrameSchema(
        {
            "month": MONTH,
            "mau": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "dau": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "new_accounts": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "activated_accounts": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, unique=["month"], name="product_usage",
    ),

    "sales_capacity": pa.DataFrameSchema(
        {
            "month": MONTH,
            "reps_total": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "reps_ramping": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "reps_productive": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, unique=["month"], name="sales_capacity",
    ),
}

# Which tables a run genuinely cannot proceed without. Everything else narrows
# the scorecard rather than stopping it — the selection engine already drops a
# KPI whose data is missing, with a recorded reason.
REQUIRED_TABLES = ("monthly_financials",)


def _describe(table: str, exc) -> List[str]:
    """Turn pandera's failure_cases frame into sentences a user can act on.

    Two cases need special handling or they read as nonsense. A missing column
    reports the *schema* name in the `column` field and the missing column in
    `failure_case`, so the naive format names the wrong thing. And a
    series-wise check has no offending value, so it reports `failure_case:
    False`, which tells the reader nothing.
    """
    out: List[str] = []
    # pandera emits one row per failing VALUE, so a bad column yields thousands.
    # Report the first few per (column, check) and then say how many more.
    from collections import Counter
    shown: Counter = Counter()
    suppressed: Counter = Counter()

    for _, row in exc.failure_cases.iterrows():
        check = str(row.get("check") or "")
        column = row.get("column") or "(frame)"
        value = row.get("failure_case")
        key = (column, check)

        if shown[key] >= 3:
            suppressed[key] += 1
            continue
        shown[key] += 1

        if check == "column_in_dataframe":
            message = f"{table}: required column {value!r} is missing"
        elif isinstance(value, bool) or value is None or pd.isna(value):
            # A series-wise check has no single offending value.
            message = f"{table}.{column}: {check}"
        else:
            message = f"{table}.{column}: {check} — failing value {value!r}"

        if message not in out:
            out.append(message)

    for (column, check), extra in suppressed.items():
        out.append(f"{table}.{column}: {extra} further value(s) fail {check}")
    return out


def validate_schemas(tables: Dict[str, pd.DataFrame],
                     strict: bool = False) -> Tuple[List[str], List[str]]:
    """Check every known table against its schema.

    Returns (passed, problems). Unknown tables are ignored — a user may bring
    data we have no opinion about, and having an opinion is the schema's job,
    not a precondition for the table existing.
    """
    passed: List[str] = []
    problems: List[str] = []

    for name in REQUIRED_TABLES:
        if name not in tables or tables[name] is None or tables[name].empty:
            problems.append(f"{name}: required table is missing or empty")

    for name, frame in tables.items():
        schema = FACT_SCHEMAS.get(name)
        if schema is None or frame is None or frame.empty:
            continue
        try:
            # lazy=True collects every failure instead of stopping at the first,
            # so the user fixes their file once rather than in a loop.
            schema.validate(frame, lazy=True)
            passed.append(f"{name}: schema ok")
        except pa.errors.SchemaErrors as exc:
            problems.extend(_describe(name, exc))
        except Exception as exc:                            # noqa: BLE001
            problems.append(f"{name}: could not validate — {exc}")

    if strict and problems:
        from .gate import ReconciliationError
        raise ReconciliationError(
            f"{len(problems)} schema problem(s):\n  - " + "\n  - ".join(problems))
    return passed, problems
