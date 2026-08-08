"""The five upload shapes we support properly.

Everyone's export is different, and the honest strategy is to support a narrow
set of shapes really well rather than half-working on everything
(ARCHITECTURE §7). Each shape declares the fact table it becomes and the fields
it needs, so the mapper has something concrete to score against and the quality
report can say exactly what is missing.

A field is `required` when the target fact table is useless without it.
Everything else narrows what can be computed rather than blocking the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Field:
    name: str                       # canonical name in the fact table
    semantic: str                   # what the profiler should have called it
    required: bool = False
    aliases: List[str] = field(default_factory=list)
    help: str = ""


@dataclass
class Shape:
    id: str
    label: str
    target_table: str
    description: str
    fields: List[Field]
    unlocks: str = ""               # what mapping this shape buys the user

    @property
    def required_fields(self) -> List[Field]:
        return [f for f in self.fields if f.required]


SHAPES: Dict[str, Shape] = {}


def _register(shape: Shape) -> Shape:
    SHAPES[shape.id] = shape
    return shape


_register(Shape(
    id="transaction_log",
    label="Transaction or invoice log",
    target_table="mrr_movements",
    description="One row per sale, invoice or subscription change.",
    unlocks="revenue, growth, movement analysis and the ARR bridge",
    fields=[
        Field("month", "date", True, ["date", "period", "invoice_date",
                                      "transaction_date", "closed_date", "created_at"],
              "When the transaction happened."),
        Field("customer_id", "identifier", True,
              ["customer", "cust_id", "cust", "account_id", "account",
               "client_id", "client", "company_id", "customer_number",
               "customer_ref", "acct_id"],
              "Who it was with."),
        Field("delta_mrr", "currency", True,
              ["amount", "value", "revenue", "mrr", "arr", "total", "net_amount",
               "line_total", "price"],
              "The value of the change."),
        Field("movement_type", "category", False,
              ["type", "movement", "event", "change_type", "reason"],
              "New, expansion, contraction, churn or reactivation."),
        Field("segment", "category", False,
              ["tier", "plan", "customer_segment", "size", "category"]),
    ],
))

_register(Shape(
    id="pnl_export",
    label="P&L / income statement export",
    target_table="monthly_financials",
    description="One row per month of revenue and cost lines.",
    unlocks="margin, EBITDA, burn, runway and every financial KPI",
    fields=[
        Field("month", "date", True, ["period", "date", "month_end", "fiscal_month"]),
        Field("revenue", "currency", True,
              ["total_revenue", "net_revenue", "sales", "turnover", "income"]),
        Field("cogs", "currency", False,
              ["cost_of_sales", "cost_of_goods_sold", "direct_costs", "cos"]),
        Field("sales_cost", "currency", False, ["sales", "sales_expense", "s_and_m"]),
        Field("marketing_cost", "currency", False, ["marketing", "marketing_expense"]),
        Field("rnd_cost", "currency", False,
              ["r_and_d", "research_and_development", "engineering", "product"]),
        Field("ga_cost", "currency", False,
              ["g_and_a", "general_and_admin", "admin", "overhead"]),
        Field("cash", "currency", False, ["cash_balance", "closing_cash", "bank"]),
    ],
))

_register(Shape(
    id="crm_deals",
    label="CRM deal / opportunity export",
    target_table="pipeline",
    description="One row per opportunity, won or lost.",
    unlocks="win rate, sales cycle, pipeline coverage and CAC",
    fields=[
        Field("month", "date", True,
              ["close_date", "closed_date", "created_date", "date"]),
        Field("opps_won", "measure", False, ["won", "is_won", "stage", "status"]),
        Field("avg_deal_size", "currency", False,
              ["amount", "deal_value", "value", "acv"]),
        Field("sales_cycle_days", "measure", False,
              ["cycle_days", "days_to_close", "age_days", "duration"]),
        Field("open_pipeline_value", "currency", False,
              ["pipeline", "open_value", "weighted_value"]),
    ],
))

_register(Shape(
    id="subscription_export",
    label="Subscription / billing export",
    target_table="customers",
    description="One row per customer or subscription.",
    unlocks="churn, retention, NRR, cohort analysis and concentration",
    fields=[
        Field("customer_id", "identifier", True,
              ["customer", "cust_id", "cust", "account_id", "subscription_id",
               "account", "sub_id"]),
        Field("acquired_month", "date", True,
              ["start_date", "created_at", "signup_date", "first_billed",
               "subscription_start", "cohort"]),
        Field("final_acv", "currency", True,
              ["acv", "arr", "mrr", "amount", "value", "contract_value", "plan_value"]),
        Field("churn_month", "date", False,
              ["end_date", "cancelled_at", "churn_date", "expiry", "terminated"]),
        Field("segment", "category", False, ["plan", "tier", "product", "package"]),
        Field("country", "category", False, ["region", "geo", "market", "location"]),
    ],
))

_register(Shape(
    id="headcount_roster",
    label="Headcount roster",
    target_table="headcount",
    description="One row per employee, or per month per function.",
    unlocks="revenue per FTE, headcount growth, attrition and operating leverage",
    fields=[
        Field("month", "date", True, ["period", "as_of", "date", "start_date"]),
        Field("function", "category", True,
              ["department", "team", "org", "cost_centre", "cost_center", "role"]),
        Field("fte", "measure", False, ["headcount", "employees", "count", "people"]),
        Field("cost", "currency", False,
              ["salary", "payroll", "total_cost", "compensation", "employee_cost"]),
    ],
))


def shape_catalog() -> List[Dict]:
    """The shapes as data, for the Studio's source picker."""
    return [
        {
            "id": s.id, "label": s.label, "target_table": s.target_table,
            "description": s.description, "unlocks": s.unlocks,
            "fields": [
                {"name": f.name, "semantic": f.semantic, "required": f.required,
                 "aliases": f.aliases, "help": f.help}
                for f in s.fields
            ],
        }
        for s in SHAPES.values()
    ]


def shape_for_table(table: str) -> Optional[Shape]:
    return next((s for s in SHAPES.values() if s.target_table == table), None)
