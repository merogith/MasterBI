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

from ..contract.schemas import REQUIRED_TABLES, schemas_for
from .shapes import shape_for_table
from .table_kpis import TABLE_KPIS


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

    from ..profile.sectors import resolve_archetype
    archetype = resolve_archetype(profile.business_model.type.value).value

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

    # The archetype's own tables, not the union. Listing what is missing is
    # advice, and advice has to be for this business: a retailer asked to go and
    # find `mrr_movements` and `sales_capacity` has been handed a SaaS to-do
    # list, and nothing on it will ever apply to them.
    report.tables_missing = [
        _missing_entry(name, profile) for name in sorted(schemas_for(archetype))
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

    # `validate_schemas` has taken an archetype since it was written — its own
    # docstring says "a retailer held to the union would be asked for `mrr`" —
    # and this, the one caller that judges a user's own upload, never passed it.
    # So a retailer was told their data was missing `final_acv`.
    _, problems = validate_schemas(tables, archetype=archetype)
    report.schema_problems = problems

    report.kpis_available, report.kpis_blocked = _kpi_counts(
        [entry["table"] for entry in report.tables_missing], profile)
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


# `TABLE_KPIS` — which KPIs each fact table unlocks — is **generated**, by
# `tools/gen_table_kpis.py`, and imported above.
#
# It was a hand-written dict of SaaS ids, which failed twice over. Visibly: the
# intersection with any other pack's scorecard was empty, so a retailer
# uploading a P&L was told `monthly_financials` would unlock *nothing* — the
# exact discouragement this report exists to remove. Invisibly: it restated a
# fact the metrics engine already owns, so it was wrong the moment anyone added
# a KPI, and it was — `orders`, `traffic`, `inventory` and `buyers` were not
# keys at all. The generator derives it by taking each table away and seeing
# which KPIs stop computing, so the map cannot disagree with the engine without
# CI saying so.


def _kpis_needing(table: str, profile) -> tuple:
    """(kpi ids, how many of them this profile would actually have selected).

    A count of `None` means "no opinion", not "none". The two used to be the
    same answer: when the map knew nothing about a profile's pack the
    intersection came out empty and the report said supplying the table would
    unlock zero KPIs — which reads as "don't bother" and was simply wrong.
    Silence is the honest answer to a question we cannot answer.
    """
    ids = TABLE_KPIS.get(table, [])
    try:
        from ..kpi.selection import select
        selected = {k.id for k in select(profile).kpis}
    except Exception:                                       # noqa: BLE001
        return ids, None
    relevant = [i for i in ids if i in selected]
    if not relevant:
        return ids, None
    return relevant, len(relevant)


def _kpi_counts(missing: List[str], profile) -> tuple:
    """(available, blocked) for this profile's scorecard.

    Counted from what is **missing**, not from what is present, and the
    difference is not a refactor. A KPI needing both the P&L and the orders file
    appears under both, so counting the union of present tables called it
    available on the strength of the P&L alone — while `tables_missing` on the
    same screen said supplying orders would unlock it. The report contradicted
    itself: four missing tables, thirteen KPIs named against them, and "0
    blocked" printed underneath.

    One missing dependency blocks a KPI. That is what the map means, and it is
    what the user sees when the tile is absent from the dashboard.
    """
    try:
        from ..kpi.selection import select
        selected = {k.id for k in select(profile).kpis}
    except Exception:                                       # noqa: BLE001
        return 0, 0

    blocked = set()
    for table in missing:
        blocked.update(TABLE_KPIS.get(table, []))

    blocked &= selected
    return len(selected) - len(blocked), len(blocked)
