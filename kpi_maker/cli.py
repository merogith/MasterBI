"""Command line entry point.

    python -m kpi_maker run --profile samples/northwind_saas.json --out ./out

The CLI is a thin wrapper over `run_pipeline`. Any UI — Streamlit now, Next.js
later — calls the same function and gets the same artifacts. Keeping the engine
free of UI imports is what makes that swap cheap.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .datagen.saas import generate
from .insight.detectors import detect_all
from .kpi.selection import select
from .metrics.engine import compute, facts_table
from .profile.schema import CompanyProfile
from .render.dashboard import render_dashboard
from .render.deck import render_deck
from .render.doc import render_doc
from .render.report import render_report
from .render.workbook import render_workbook, write_csv_bundle
from .viz.charts import build_all
from .viz.export import render_all as render_chart_images

GENERATORS = {"saas": generate}


class PipelineResult(dict):
    """Artifact paths plus the in-memory objects, for programmatic callers."""


def run_pipeline(profile: CompanyProfile, out_dir: Path,
                 quiet: bool = False) -> PipelineResult:
    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    out_dir.mkdir(parents=True, exist_ok=True)
    model = profile.business_model.type.value

    # 1. KPI selection ------------------------------------------------------
    kpi_set = select(profile)
    say(f"  KPIs      {len(kpi_set.kpis)} selected, {len(kpi_set.dropped)} excluded "
        f"(north star: {kpi_set.north_star}, {kpi_set.leading_share:.0%} leading)")
    for key in ("_coverage_warning", "_leading_warning"):
        if key in kpi_set.rationale:
            say(f"  WARNING   {kpi_set.rationale[key]}")

    # 2. Data ---------------------------------------------------------------
    generator = GENERATORS.get(model)
    if generator is None:
        raise SystemExit(
            f"No data generator for business model {model!r}. "
            f"Available: {', '.join(sorted(GENERATORS))}."
        )
    data = generator(profile)
    rows = sum(len(t) for t in data.tables.values())
    say(f"  Data      {len(data.tables)} fact tables, {rows:,} rows, "
        f"{len(data.checks)} reconciliation checks passed")

    # 3. Metrics ------------------------------------------------------------
    results = compute(kpi_set, data.tables, profile)
    ok = [r for r in results if r.computed]
    say(f"  Metrics   {len(ok)}/{len(results)} computed")
    for r in results:
        if not r.computed:
            say(f"            - {r.kpi.id}: {r.reason}")

    # 4. Insights -----------------------------------------------------------
    findings = detect_all(results, data.tables, profile)
    by_sev: Dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    say(f"  Findings  {len(findings)} "
        f"({', '.join(f'{v} {k}' for k, v in sorted(by_sev.items()))})")

    # 5. Artifacts ----------------------------------------------------------
    anomaly_notes = [a.description for a in data.anomalies]

    fin = data.tables.get("monthly_financials")
    period = (f"{fin['month'].iloc[0]} to {fin['month'].iloc[-1]}"
              if fin is not None and not fin.empty else "")

    dashboard_path = out_dir / "dashboard.html"
    dashboard_path.write_text(
        render_dashboard(profile, kpi_set, results, findings,
                         data.tables, data.checks, anomaly_notes),
        encoding="utf-8",
    )

    workbook_path = out_dir / "workbook.xlsx"
    render_workbook(workbook_path, profile, kpi_set, results, findings,
                    data.tables, data.checks)

    # Static chart images, shared by all three print deliverables. Built once
    # from the same ChartSpec objects the dashboard uses.
    print_specs = build_all(results, data.tables, mode="light",
                            currency=profile.identity.currency)
    images = render_chart_images(print_specs, out_dir / "charts")
    say(f"  Charts    {len(images)}/{len(print_specs)} exported to PNG")

    report_path = out_dir / "report.pdf"
    render_report(report_path, profile, kpi_set, results, findings, images,
                  print_specs, data.checks, anomaly_notes, period)

    deck_path = out_dir / "deck.pptx"
    render_deck(deck_path, profile, kpi_set, results, findings, images,
                print_specs, period)

    doc_path = out_dir / "report.docx"
    render_doc(doc_path, profile, kpi_set, results, findings, images,
               print_specs, data.checks, anomaly_notes, period)

    csv_paths = write_csv_bundle(data.tables, out_dir / "data")

    facts = facts_table(results)
    facts_path = out_dir / "facts.csv"
    facts.to_csv(facts_path, index=False, encoding="utf-8-sig")

    (out_dir / "profile.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "kpi_set.json").write_text(
        kpi_set.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "findings.json").write_text(
        json.dumps([asdict(f) for f in findings], indent=2, default=str),
        encoding="utf-8",
    )

    say(f"  Artifacts dashboard.html · report.pdf · deck.pptx · report.docx · "
        f"workbook.xlsx · facts.csv · {len(csv_paths)} CSVs · "
        f"profile.json · kpi_set.json · findings.json")

    return PipelineResult(
        out_dir=out_dir, dashboard=dashboard_path, workbook=workbook_path,
        report=report_path, deck=deck_path, doc=doc_path, images=images,
        facts=facts_path, csvs=csv_paths, profile=profile, kpi_set=kpi_set,
        results=results, findings=findings, tables=data.tables, checks=data.checks,
    )


def load_profile(path: Path) -> CompanyProfile:
    if not path.exists():
        raise SystemExit(f"Profile not found: {path}")
    return CompanyProfile(**json.loads(path.read_text(encoding="utf-8")))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kpi-maker",
        description="Profile-driven KPI selection, dashboards and reports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the full pipeline for a profile")
    run.add_argument("--profile", type=Path, required=True, help="path to a profile JSON")
    run.add_argument("--out", type=Path, default=Path("./out"), help="output directory")
    run.add_argument("--seed", type=int, help="override the profile's random seed")
    run.add_argument("--quiet", action="store_true")

    validate = sub.add_parser("validate", help="validate a profile and the KPI library")
    validate.add_argument("--profile", type=Path, required=True)

    serve = sub.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.add_argument("--open", action="store_true", dest="open_browser",
                       help="open the UI in a browser once the server is up")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        url = f"http://{args.host}:{args.port}"
        print(f"KPI Dashboard Maker  ->  {url}")

        if args.open_browser:
            # uvicorn.run blocks, so the opener waits on the port from a side
            # thread rather than a fixed sleep — on a cold start the import of
            # pandas and plotly can outlast any delay short enough to feel
            # responsive.
            import socket
            import threading
            import webbrowser

            def open_when_ready() -> None:
                for _ in range(120):                     # ~60s ceiling
                    with socket.socket() as probe:
                        probe.settimeout(0.4)
                        if probe.connect_ex((args.host, args.port)) == 0:
                            webbrowser.open(url)
                            return
                    time.sleep(0.5)

            threading.Thread(target=open_when_ready, daemon=True).start()

        uvicorn.run("kpi_maker.api.server:app", host=args.host, port=args.port,
                    reload=args.reload, log_level="warning")
        return 0

    if args.command == "validate":
        profile = load_profile(args.profile)
        kpi_set = select(profile)
        print(f"Profile OK: {profile.identity.name}")
        print(f"  confidence     {profile.confidence:.0%}")
        print(f"  KPIs selected  {len(kpi_set.kpis)} (north star: {kpi_set.north_star})")
        print(f"  leading share  {kpi_set.leading_share:.0%}")
        for kid, reason in sorted(kpi_set.dropped.items()):
            print(f"  excluded       {kid}: {reason}")
        return 0

    profile = load_profile(args.profile)
    if args.seed is not None:
        profile.seed = args.seed
    print(f"Running pipeline for {profile.identity.name} ({profile.business_model.type.value})")
    result = run_pipeline(profile, args.out, quiet=args.quiet)
    print(f"\nDone. Open {result['dashboard']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
