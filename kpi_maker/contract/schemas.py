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

from typing import Dict, List, Optional, Tuple

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

# --------------------------------------------------------------------------
# Universal — every archetype emits these, whatever it sells
# --------------------------------------------------------------------------

# A P&L is a P&L. Nothing here is about subscriptions: a retailer, an agency
# and a SaaS vendor all have revenue, cost of goods and operating lines. The
# subscription-only columns live in the extension below, so an e-commerce
# generator does not have to emit `mrr` as a column of NaN to satisfy a
# validator that had no business asking for it.
_PL_COLUMNS = {
    "month": MONTH,
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
}

_SUBSCRIPTION_PL_COLUMNS = {
    "mrr": _money(ge=NON_NEGATIVE),
    "arr": _money(ge=NON_NEGATIVE),
}


def _financials(columns, note: str) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        dict(columns), strict=False, unique=["month"], name="monthly_financials",
        description=f"One row per month. The spine every metric reindexes onto. {note}",
    )


UNIVERSAL_SCHEMAS: Dict[str, pa.DataFrameSchema] = {

    "monthly_financials": _financials(_PL_COLUMNS, "Universal P&L columns."),

    # Company revenue split across whichever dimensions this archetype can be
    # sliced by. Long — `dimension` names the cut — because a subscription
    # business slices by customer segment and a transactional one by channel
    # *and* category; a wide table would need a different shape per archetype
    # and everything downstream would have to learn which.
    "segment_financials": pa.DataFrameSchema(
        {
            "month": MONTH,
            "dimension": pa.Column(nullable=False),
            "segment": pa.Column(nullable=False),
            "revenue": _money(),
            "share": pa.Column(float, coerce=True, checks=A_FRACTION),
        },
        strict=False, name="segment_financials",
        description=("Month x dimension x segment. Shares sum to 1.0 within a "
                     "month and dimension, so segment revenue sums to the "
                     "company's."),
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
}


# --------------------------------------------------------------------------
# Subscription
# --------------------------------------------------------------------------

SUBSCRIPTION_SCHEMAS: Dict[str, pa.DataFrameSchema] = {

    "monthly_financials": _financials(
        {**_PL_COLUMNS, **_SUBSCRIPTION_PL_COLUMNS},
        "Plus the recurring-revenue columns."),

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


# --------------------------------------------------------------------------
# E-commerce
# --------------------------------------------------------------------------

ECOMMERCE_SCHEMAS: Dict[str, pa.DataFrameSchema] = {

    "orders": pa.DataFrameSchema(
        {
            "month": MONTH,
            "channel": pa.Column(nullable=False),
            "category": pa.Column(nullable=False),
            "orders": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "units": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "gross_revenue": _money(ge=NON_NEGATIVE),
            "discounts": _money(ge=NON_NEGATIVE),
            "returns": _money(ge=NON_NEGATIVE),
        },
        strict=False, name="orders",
        description="Month x channel x category. Everything else derives from this.",
    ),

    "traffic": pa.DataFrameSchema(
        {
            "month": MONTH,
            "channel": pa.Column(nullable=False),
            "sessions": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "add_to_carts": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "checkouts": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "orders": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, name="traffic",
        description="Month x channel. The funnel, derived backwards from orders.",
    ),

    "inventory": pa.DataFrameSchema(
        {
            "month": MONTH,
            "category": pa.Column(nullable=False),
            "units_sold": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "units_on_hand": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "stockout_days": pa.Column(float, coerce=True,
                                       checks=pa.Check.in_range(0.0, 31.0)),
        },
        strict=False, name="inventory",
        description="Month x category.",
    ),

    "buyers": pa.DataFrameSchema(
        {
            "month": MONTH,
            "new_buyers": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "repeat_buyers": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "active_buyers": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
        },
        strict=False, unique=["month"], name="buyers",
        description=("Month. How many people actually bought, split by whether "
                     "they had bought before — retail's analogue of the "
                     "movements table."),
    ),

    # Same table name as subscription's, deliberately different columns. A
    # buyer has orders and a last order date; a subscriber has an ACV and a
    # churn month. Sharing the name keeps "customers" meaning the customer
    # table everywhere, and the per-archetype lookup is what stops one
    # definition being applied to the other.
    "customers": pa.DataFrameSchema(
        {
            "customer_id": IDENTIFIER,
            "segment": pa.Column(nullable=False),
            "acquired_month": MONTH,
            "last_order_month": MONTH,
            "orders": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "revenue": _money(),
            "is_active": pa.Column(bool, coerce=True),
        },
        strict=False, unique=["customer_id"], name="customers",
        description="One row per buyer. No month column — entity grain.",
    ),
}


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------
#
# A firm that sells its people's time against engagements: agencies,
# consultancies, engineering practices. What it has that neither of the other
# two archetypes does is a *stock of sold work* — the backlog — and a capacity
# constraint measured in hours rather than in units or seats. Both are in the
# tables, because a services firm with no backlog table cannot be asked the one
# question its board asks first.

PROJECT_SCHEMAS: Dict[str, pa.DataFrameSchema] = {

    "projects": pa.DataFrameSchema(
        {
            "project_id": IDENTIFIER,
            "customer_id": IDENTIFIER,
            "service_line": pa.Column(nullable=False),
            "segment": pa.Column(nullable=False),
            "won_month": MONTH,
            "start_month": MONTH,
            "end_month": MONTH,
            "contract_value": _money(ge=NON_NEGATIVE),
            "budget_hours": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "actual_hours": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "recognised_revenue": _money(ge=NON_NEGATIVE),
            "is_active": pa.Column(bool, coerce=True),
        },
        strict=False, unique=["project_id"], name="projects",
        description=("One row per engagement. No month column — entity grain. "
                     "`budget_hours` against `actual_hours` is the overrun, "
                     "which is what realisation measures the cost of."),
    ),

    # Month x service line x role, which is the grain at which a services firm
    # actually manages itself: a partner-heavy month on a fixed fee is a margin
    # problem invisible at company level.
    "timesheets": pa.DataFrameSchema(
        {
            "month": MONTH,
            "service_line": pa.Column(nullable=False),
            "role": pa.Column(nullable=False),
            "billable_hours": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "available_hours": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "standard_rate": _money(ge=NON_NEGATIVE),
            # Recognised fee over hours at the standard rate. Below 1.0 on an
            # overrunning fixed fee, above it when a job comes in under budget,
            # so it is bounded generously rather than at one.
            "realisation": pa.Column(float, coerce=True,
                                     checks=pa.Check.in_range(0.0, 3.0)),
            "fee_revenue": _money(),
        },
        strict=False, name="timesheets",
        description=("Month x service line x role. `billable_hours / "
                     "available_hours` is utilisation; `fee_revenue` is what "
                     "those hours actually earned."),
    ),

    "backlog": pa.DataFrameSchema(
        {
            "month": MONTH,
            "opening_backlog": _money(ge=NON_NEGATIVE),
            "bookings": _money(ge=NON_NEGATIVE),
            "revenue_recognised": _money(ge=NON_NEGATIVE),
            "closing_backlog": _money(ge=NON_NEGATIVE),
        },
        strict=False, unique=["month"], name="backlog",
        description=("Month. Sold work not yet delivered, rolled forward. "
                     "`bookings / revenue_recognised` is book-to-bill."),
    ),

    # Same name, third shape — see the note on the e-commerce one. A client of
    # a services firm has engagements and a last active month where a
    # subscriber has an ACV and a buyer has orders.
    "customers": pa.DataFrameSchema(
        {
            "customer_id": IDENTIFIER,
            "segment": pa.Column(nullable=False),
            "acquired_month": MONTH,
            "last_project_month": MONTH,
            "projects": pa.Column(float, coerce=True, checks=NON_NEGATIVE),
            "revenue": _money(),
            "is_active": pa.Column(bool, coerce=True),
        },
        strict=False, unique=["customer_id"], name="customers",
        description="One row per client. No month column — entity grain.",
    ),
}


# --------------------------------------------------------------------------
# The per-archetype lookup
# --------------------------------------------------------------------------

SCHEMAS_BY_ARCHETYPE: Dict[str, Dict[str, pa.DataFrameSchema]] = {
    "saas": {**UNIVERSAL_SCHEMAS, **SUBSCRIPTION_SCHEMAS},
    "ecommerce": {**UNIVERSAL_SCHEMAS, **ECOMMERCE_SCHEMAS},
    "project": {**UNIVERSAL_SCHEMAS, **PROJECT_SCHEMAS},
}

# The best-effort set for callers with no archetype to hand. Ingestion is the
# case: a user uploads a file before anyone has decided what kind of business
# it describes, and the quality report still has to say what is wrong with it.
#
# It cannot be a true union, because two archetypes disagree about `customers`
# — a buyer has orders and a last order date where a subscriber has an ACV and
# a churn month. A dict cannot hold both, and guessing would mean telling a
# retailer their customer file is missing `final_acv`. So the unclassified path
# keeps the subscription reading, which is what ingestion was built against,
# and anything that knows its archetype uses `schemas_for`.
FACT_SCHEMAS: Dict[str, pa.DataFrameSchema] = {
    **UNIVERSAL_SCHEMAS, **SUBSCRIPTION_SCHEMAS,
}


def schemas_for(archetype: Optional[str] = None) -> Dict[str, pa.DataFrameSchema]:
    """The schema set an archetype is held to, or the union when unknown."""
    if archetype is None:
        return FACT_SCHEMAS
    return SCHEMAS_BY_ARCHETYPE.get(archetype, FACT_SCHEMAS)


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
                     strict: bool = False,
                     archetype: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Check every known table against its schema.

    Returns (passed, problems). Unknown tables are ignored — a user may bring
    data we have no opinion about, and having an opinion is the schema's job,
    not a precondition for the table existing.

    `archetype` narrows the schema set. Without it the union is used, which is
    right for an upload nobody has classified yet but wrong for a generator: a
    retailer held to the union would be asked for `mrr`.
    """
    known = schemas_for(archetype)
    passed: List[str] = []
    problems: List[str] = []

    for name in REQUIRED_TABLES:
        if name not in tables or tables[name] is None or tables[name].empty:
            problems.append(f"{name}: required table is missing or empty")

    for name, frame in tables.items():
        schema = known.get(name)
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
