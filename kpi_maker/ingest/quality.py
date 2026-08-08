"""What the user sees before anything renders.

The honest answer to a partial upload is a narrower dashboard, not a fuller one
padded with invention. But "narrower" reads as "broken" unless the user is told
why — so this report says what mapped, what did not, and specifically how many
KPIs each missing table would unlock if they supplied it.

That last part is the difference between "your dashboard has 6 KPIs" and "your
dashboard has 6 KPIs; a headcount roster would add 4 more".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ..contract.schemas import FACT_SCHEMAS, REQUIRED_TABLES
from .shapes import SHAPES, shape_for_table


@dataclass
class QualityReport:
    tables_present: List[str] = field(default_factory=list)
    tables_missing: List[Dict[str, Any]] = field(default_factory=list)
    tables_modelled: List[str] = field(default_factory=list)
    mapped_fields: List[Dict[str, Any]] = field(default_factory=list)
    unmapped_columns: List[str] = field(default_factory=list)
    schema_problems: List[str] = field(default_factory=list)
    gate_warnings: List[str] = field(default_factory=list)
    blocking: List[str] = field(default_factory=list)
    kpis_available: int = 0
    kpis_blocked: int = 0

    @property
    def can_run(self) -> bool:
        return not self.blocking

    def as_dict(self) -> Dict[str, Any]:
        return {
            "can_run": self.can_run,
            "tables_present": self.tables_present,
            "tables_missing": self.tables_missing,
            "tables_modelled": self.tables_modelled,
            "mapped_fields": self.mapped_fields,
            "unmapped_columns": self.unmapped_columns,
            "schema_problems": self.schema_problems,
            "gate_warnings": self.gate_warnings,
            "blocking": self.blocking,
            "kpis_available": self.kpis_available,
            "kpis_blocked": self.kpis_blocked,
        }


def build_report(tables: Dict[str, pd.DataFrame], profile,
                 origins: Optional[Dict[str, str]] = None,
                 proposals: Optional[List] = None) -> QualityReport:
    report = QualityReport()
    origins = origins or {}

    present = [name for name, frame in tables.items()
               if frame is not None and not frame.empty]
    report.tables_present = sorted(present)
    report.tables_modelled = sorted(n for n in present
                                    if origins.get(n) == "modelled")

    for name in REQUIRED_TABLES:
        if name not in present:
            report.blocking.append(
                f"{name} is required and was not mapped — without it there is no "
                f"monthly spine to compute anything against")

    report.tables_missing = [
        _missing_entry(name, profile) for name in sorted(FACT_SCHEMAS)
        if name not in present
    ]

    for proposal in proposals or []:
        for match in proposal.matches:
            if match.column:
                report.mapped_fields.append({
                    "table": proposal.target_table, **match.as_dict()})
        report.unmapped_columns.extend(proposal.unmapped_columns)
    report.unmapped_columns = sorted(set(report.unmapped_columns))

    from ..contract.schemas import validate_schemas
    _, problems = validate_schemas(tables)
    report.schema_problems = problems

    report.kpis_available, report.kpis_blocked = _kpi_counts(present, profile)
    return report


def _missing_entry(table: str, profile) -> Dict[str, Any]:
    shape = shape_for_table(table)
    unlocks, blocked = _kpis_needing(table, profile)
    return {
        "table": table,
        "unlocks_kpis": blocked,
        "unlocks": shape.unlocks if shape else "",
        "supply_by": shape.label if shape else "",
        "shape_id": shape.id if shape else None,
        "example_kpis": unlocks[:4],
    }


# Which fact table each metric needs. Derived by running the metrics engine
# against a table-by-table probe would be exact but expensive; this map is the
# same information stated once, and the count is only ever advisory.
TABLE_KPIS = {
    "monthly_financials": ["arr", "arr_growth_yoy", "gross_margin_pct",
                           "ebitda_margin", "rule_of_40", "cash_runway_months",
                           "burn_multiple", "billings", "fcf_margin",
                           "rnd_pct_revenue", "sm_pct_revenue", "crpo_growth"],
    "mrr_movements": ["net_new_arr", "nrr", "grr", "logo_churn_rate",
                      "expansion_share", "quick_ratio", "customer_count",
                      "arpa", "revenue_concentration_top10"],
    "customers": ["revenue_concentration_top10", "enterprise_customers"],
    "pipeline": ["win_rate", "pipeline_coverage", "sales_cycle_days",
                 "blended_cac", "pipeline_created"],
    "marketing": ["lead_to_mql_rate", "mql_to_sql_rate"],
    "headcount": ["arr_per_fte", "headcount_growth", "employee_attrition"],
    "product_usage": ["activation_rate", "engagement_ratio", "time_to_value_days"],
    "sales_capacity": ["quota_attainment", "arr_per_rep", "rep_ramp_months"],
}


def _kpis_needing(table: str, profile) -> tuple:
    """(kpi ids, how many of them this profile would actually have selected)."""
    ids = TABLE_KPIS.get(table, [])
    try:
        from ..kpi.selection import select
        selected = {k.id for k in select(profile).kpis}
    except Exception:                                       # noqa: BLE001
        return ids, len(ids)
    relevant = [i for i in ids if i in selected]
    return relevant or ids, len(relevant)


def _kpi_counts(present: List[str], profile) -> tuple:
    try:
        from ..kpi.selection import select
        selected = {k.id for k in select(profile).kpis}
    except Exception:                                       # noqa: BLE001
        return 0, 0

    reachable = set()
    for table in present:
        reachable.update(TABLE_KPIS.get(table, []))

    available = len(selected & reachable)
    return available, len(selected) - available
