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
        [entry["table"] for entry in report.tables_missing], profile,
        tables, origins)
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


def _kpi_counts(missing: List[str], profile,
                tables: Optional[Dict[str, pd.DataFrame]] = None,
                origins: Optional[Dict[str, str]] = None) -> tuple:
    """(available, blocked) for this profile's scorecard.

    **Computed over the supplied tables, not predicted from the table map**, and
    that is the whole point of this function. The screen above it says "this is
    what the dashboard will contain", which is a statement about the run that is
    about to happen — so the only defensible way to produce it is to ask the
    engine that will produce that run.

    Measured on a P&L export mapped to `monthly_financials`: the map said 6 KPIs
    available and 19 blocked, and the run computed 2. The four it overstated
    were `arr`, `arr_growth_yoy`, `billings` and `crpo_growth`, and they share a
    cause. `TABLE_KPIS` is table-granular and the engine is column-granular: all
    four read `arr`, `mrr`, `rpo` or `crpo` — columns of `monthly_financials`
    that the generator emits and a P&L export does not carry. The table was
    present, so nothing marked them blocked; the columns were absent, so the run
    reported "insufficient data" on four rows the user had been promised. A
    number that is an upper bound must not be printed as a fact, and this is the
    same "second copy of a fact the engine already owns" that
    `tools/gen_table_kpis.py` exists to stop, one level up: the map answers the
    *predictive* question correctly ("what would the headcount roster get me?"),
    and this is not that question.

    It costs one `compute` over data that is already in memory — 0.23s on a
    36-month upload — on a request the user is already waiting on. That is what
    makes reporting affordable where predicting was necessary in
    `_kpis_needing`, which asks about tables nobody has supplied and so has
    nothing to compute over.

    The map is still the fallback, because a report that answers is better than
    one that raises: `compute` sees a user's own file, and a number that is
    slightly optimistic beats a screen that will not render. The fallback keeps
    the rule it was given and the reason is still good — it counts from what is
    **missing**, not from what is present, because a KPI needing both the P&L
    and the orders file appears under both, and counting the union of present
    tables called it available on the strength of the P&L alone while
    `tables_missing` on the same screen said supplying orders would unlock it.
    Four missing tables, thirteen KPIs named against them, and "0 blocked"
    printed underneath. One missing dependency blocks a KPI; that rule was
    right, and the defect above is that a *table* is the wrong unit for it.
    """
    try:
        from ..kpi.selection import select
        kpi_set = select(profile)
        selected = {k.id for k in kpi_set.kpis}
    except Exception:                                       # noqa: BLE001
        return 0, 0

    if tables:
        try:
            from ..metrics.engine import compute
            results = compute(kpi_set, tables, profile, origins=origins or {})
            available = sum(1 for r in results if r.computed)
            return available, len(selected) - available
        except Exception:                                   # noqa: BLE001
            pass

    blocked = set()
    for table in missing:
        blocked.update(TABLE_KPIS.get(table, []))

    blocked &= selected
    return len(selected) - len(blocked), len(blocked)
