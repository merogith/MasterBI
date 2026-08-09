"""XLSX workbook and CSV bundle.

The workbook is a deliverable in its own right, not a data dump. It carries:

  Summary      — scorecard with RAG conditional formatting
  Tracker      — actual vs target vs prior year per month, with LIVE formulas
                 so the client can extend it next month without us
  Definitions  — the KPI record sheets (formula, owner, source, pitfalls)
  Assumptions  — provenance and the benchmark caveat
  <fact tables>— one tab per raw table, pivot-ready
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pandas as pd

from ..fmt import is_missing
from ..insight.detectors import Finding
from ..kpi.schema import KPISet
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile

NUMBER_FORMATS = {
    "currency": '#,##0',
    "pct": '0.0%',
    "months": '#,##0.0" mo"',
    "days": '#,##0" d"',
    "hours": '#,##0.0" h"',
    "count": '#,##0',
    "ratio": '#,##0.00',
    "score": '#,##0',
}


def flatten_periods(df: pd.DataFrame) -> pd.DataFrame:
    """Render Period columns as text WITHOUT turning missing values into "NaT".

    `astype(str)` on a period column stringifies nulls, so an unchurned
    customer's empty churn_month becomes the literal text "NaT" — which then
    reads as a populated string in every downstream tool. Nulls must stay null.
    """
    export = df.copy()
    for col in export.columns:
        if isinstance(export[col].dtype, pd.PeriodDtype):
            missing = export[col].isna()
            export[col] = export[col].astype(str)
            export.loc[missing, col] = None
    return export


def write_csv_bundle(tables: Dict[str, pd.DataFrame], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in tables.items():
        path = out_dir / f"{name}.csv"
        flatten_periods(df).to_csv(path, index=False, encoding="utf-8-sig")
        written.append(path)
    return written


def render_workbook(path: Path, profile: CompanyProfile, kpi_set: KPISet,
                    results: List[MetricResult], findings: List[Finding],
                    tables: Dict[str, pd.DataFrame], checks: List[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    computed = [r for r in results if r.computed]

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        book = writer.book
        fmt = {
            "title": book.add_format({"bold": True, "font_size": 15, "font_color": "#0b0b0b"}),
            "sub": book.add_format({"font_color": "#52514e", "text_wrap": True, "valign": "top"}),
            "header": book.add_format({
                "bold": True, "bg_color": "#f0efec", "border": 1,
                "border_color": "#e1e0d9", "font_color": "#0b0b0b", "text_wrap": True,
                "valign": "vcenter",
            }),
            "group": book.add_format({"bold": True, "bg_color": "#f9f9f7"}),
            "text": book.add_format({"text_wrap": True, "valign": "top"}),
            "green": book.add_format({"bg_color": "#e6f4e6", "font_color": "#0b5f0b"}),
            "amber": book.add_format({"bg_color": "#fdf1dd", "font_color": "#8a5a10"}),
            "red": book.add_format({"bg_color": "#fae3e3", "font_color": "#8f1f1f"}),
        }
        unit_fmt = {u: book.add_format({"num_format": f}) for u, f in NUMBER_FORMATS.items()}
        status_fmt = {"green": fmt["green"], "amber": fmt["amber"], "red": fmt["red"]}

        # ---------------- Summary ----------------
        ws = book.add_worksheet("Summary")
        writer.sheets["Summary"] = ws
        ws.set_column("A:A", 34)
        ws.set_column("B:F", 16)
        ws.set_column("G:G", 22)
        ws.write(0, 0, f"{profile.identity.name} — KPI scorecard", fmt["title"])
        ws.write(1, 0, (
            f"{profile.business_model.type.value.upper()} · {profile.identity.country} · "
            f"objective: {profile.intent.primary_objective.value} · "
            f"audience: {profile.intent.audience.value} · "
            f"profile confidence {profile.confidence:.0%}"
        ), fmt["sub"])

        headers = ["KPI", "Current", "12mo ago", "Target", "Cohort median", "Status", "Owner"]
        row = 3
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt["header"])
        row += 1

        for r in sorted(computed, key=lambda x: (x.kpi.perspective.value, int(x.kpi.tier))):
            k = r.kpi
            nf = unit_fmt.get(k.unit, unit_fmt["ratio"])
            ws.write(row, 0, k.name)
            ws.write_number(row, 1, r.current, nf) if not is_missing(r.current) else ws.write(row, 1, "—")
            ws.write_number(row, 2, r.prior_year, nf) if not is_missing(r.prior_year) else ws.write(row, 2, "—")
            ws.write_number(row, 3, r.target, nf) if not is_missing(r.target) else ws.write(row, 3, "—")
            bench = k.benchmark.p50 if k.benchmark else None
            ws.write_number(row, 4, bench, nf) if bench is not None else ws.write(row, 4, "—")
            ws.write(row, 5, r.status.title(), status_fmt.get(r.status))
            ws.write(row, 6, k.owner_role)
            row += 1
        ws.freeze_panes(4, 1)
        ws.autofilter(3, 0, row - 1, len(headers) - 1)

        # ---------------- Tracker (live formulas) ----------------
        ws = book.add_worksheet("Tracker")
        writer.sheets["Tracker"] = ws
        ws.set_column("A:A", 30)
        ws.write(0, 0, "Monthly performance tracker", fmt["title"])
        ws.write(1, 0, (
            "Actual by month with target and variance as LIVE formulas — extend the "
            "columns next month and the variance follows. Edit targets in the Target column."
        ), fmt["sub"])

        series_kpis = [r for r in computed if r.series is not None][:20]
        if series_kpis:
            months = list(series_kpis[0].series.index)
            hdr_row = 3
            ws.write(hdr_row, 0, "KPI", fmt["header"])
            ws.write(hdr_row, 1, "Target", fmt["header"])
            for j, m in enumerate(months):
                ws.write(hdr_row, 2 + j, str(m), fmt["header"])
            ws.write(hdr_row, 2 + len(months), "Latest vs target", fmt["header"])

            for i, r in enumerate(series_kpis):
                rr = hdr_row + 1 + i
                nf = unit_fmt.get(r.kpi.unit, unit_fmt["ratio"])
                ws.write(rr, 0, r.kpi.name)
                if not is_missing(r.target):
                    ws.write_number(rr, 1, r.target, nf)
                for j, m in enumerate(months):
                    v = r.series.get(m)
                    if not is_missing(v):
                        ws.write_number(rr, 2 + j, float(v), nf)
                last_col = _col_letter(2 + len(months) - 1)
                # Live formula: variance recomputes if the user edits a cell.
                ws.write_formula(
                    rr, 2 + len(months),
                    f"=IF(OR(B{rr + 1}=\"\",{last_col}{rr + 1}=\"\"),\"\","
                    f"{last_col}{rr + 1}-B{rr + 1})",
                    nf,
                )
            ws.set_column(2, 2 + len(months), 12)
            ws.freeze_panes(hdr_row + 1, 2)

        # ---------------- Definitions ----------------
        defs = pd.DataFrame([{
            "KPI": r.kpi.name,
            "ID": r.kpi.id,
            "Perspective": r.kpi.perspective.value,
            "Tier": int(r.kpi.tier),
            "Leading/Lagging": r.kpi.timing.value,
            "Formula": r.kpi.formula,
            "Unit": r.kpi.unit,
            "Frequency": r.kpi.frequency,
            "Owner": r.kpi.owner_role,
            "Origin": "user-defined" if r.kpi.origin.value == "user" else "library",
            "Basis": r.basis,
            "Source systems": ", ".join(r.kpi.source_systems),
            "Benchmark source": r.kpi.benchmark.source if r.kpi.benchmark else "",
            "Interpretation": r.kpi.interpretation or "",
            "Known pitfalls": r.kpi.pitfalls or "",
            "Why selected": kpi_set.rationale.get(r.kpi.id, ""),
        } for r in computed])
        defs.to_excel(writer, sheet_name="Definitions", index=False, startrow=1)
        ws = writer.sheets["Definitions"]
        ws.write(0, 0, "KPI record sheets", fmt["title"])
        for c, name in enumerate(defs.columns):
            ws.write(1, c, name, fmt["header"])
        ws.set_column("A:A", 28)
        ws.set_column("F:F", 44)
        ws.set_column("L:N", 52, fmt["text"])
        ws.freeze_panes(2, 1)

        # ---------------- Assumptions ----------------
        ws = book.add_worksheet("Assumptions")
        writer.sheets["Assumptions"] = ws
        ws.set_column("A:A", 40)
        ws.set_column("B:B", 70)
        ws.write(0, 0, "Assumptions, provenance and caveats", fmt["title"])
        r0 = 2
        ws.write(r0, 0, "Field", fmt["header"])
        ws.write(r0, 1, "How we know it", fmt["header"])
        r0 += 1
        for field, source in sorted(profile.provenance.items()):
            ws.write(r0, 0, field)
            ws.write(r0, 1, source)
            r0 += 1
        r0 += 1
        ws.write(r0, 0, "Benchmark caveat", fmt["group"])
        ws.write(r0, 1, (
            "Peer figures are illustrative placeholders assembled from public "
            "commentary, NOT a licensed benchmark dataset. Suitable for calibrating "
            "a sample; not for external reporting."
        ), fmt["text"])
        r0 += 2
        ws.write(r0, 0, "Reconciliation checks", fmt["group"])
        ws.write(r0, 1, f"{len(checks)} assertions passed before rendering", fmt["text"])
        r0 += 1
        for c in checks:
            ws.write(r0, 1, c)
            r0 += 1

        # ---------------- Findings ----------------
        if findings:
            fdf = pd.DataFrame([{
                "Severity": f.severity, "Finding": f.title, "Detail": f.statement,
                "Recommendation": f.recommendation or "", "Impact": f.impact or "",
                "Effort": f.effort or "", "Related KPIs": ", ".join(f.kpi_ids),
            } for f in findings])
            fdf.to_excel(writer, sheet_name="Findings", index=False, startrow=1)
            ws = writer.sheets["Findings"]
            ws.write(0, 0, "Detected findings", fmt["title"])
            for c, name in enumerate(fdf.columns):
                ws.write(1, c, name, fmt["header"])
            ws.set_column("A:B", 26)
            ws.set_column("C:D", 62, fmt["text"])
            ws.freeze_panes(2, 0)

        # ---------------- Raw fact tables ----------------
        for name, df in tables.items():
            export = flatten_periods(df)
            sheet = f"data_{name}"[:31]
            export.to_excel(writer, sheet_name=sheet, index=False)
            sh = writer.sheets[sheet]
            for c, colname in enumerate(export.columns):
                sh.write(0, c, colname, fmt["header"])
            sh.freeze_panes(1, 0)

    return path


def _col_letter(idx: int) -> str:
    """0-based column index -> Excel letter."""
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
