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
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional

from .pipeline.runner import execute, plan_rerun
from .profile.schema import CompanyProfile
from .spec.schema import RunSpec


class PipelineResult(dict):
    """Artifact paths plus the in-memory objects, for programmatic callers."""


def run_pipeline(profile: CompanyProfile, out_dir: Path,
                 quiet: bool = False, spec: Optional[RunSpec] = None,
                 on_progress: Optional[Callable[[Dict], None]] = None,
                 cancel: Optional[Event] = None) -> PipelineResult:
    """Run the pipeline for a profile.

    Signature preserved from before the stage graph existed: the API server and
    the static site builder both call this, and neither should have to know how
    the work is scheduled. Pass a `spec` to adjust any stage; omit it and the
    defaults reproduce the original behaviour exactly.

    `on_progress` and `cancel` are forwarded to the runner unchanged. They live
    here rather than only on `execute` because the server calls this function,
    not that one, and a progress bar that the server cannot reach is the bug
    this pair exists to fix.
    """
    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    if spec is None:
        spec = RunSpec.for_profile(profile)

    result = execute(spec, out_dir, say=say, on_progress=on_progress,
                     cancel=cancel)
    values = result.values

    kpi_set = values.get("select")
    if kpi_set is not None:
        say(f"  KPIs      {len(kpi_set.kpis)} selected, {len(kpi_set.dropped)} excluded "
            f"(north star: {kpi_set.north_star}, {kpi_set.leading_share:.0%} leading)")
        # Any `_*_warning` key, not a fixed list of two: a new warning added to
        # the selection engine should reach the console without also having to
        # be added here, which is how `_sector_warning` would have been missed.
        for key in sorted(k for k in kpi_set.rationale if k.endswith("_warning")):
            say(f"  WARNING   {kpi_set.rationale[key]}")

    data = values.get("source")
    tables = values.get("model", {})
    if data is not None:
        rows = sum(len(t) for t in tables.values())
        say(f"  Data      {len(tables)} fact tables, {rows:,} rows, "
            f"{len(data.checks)} reconciliation checks passed")

    results = values.get("metrics", [])
    if results:
        ok = [r for r in results if r.computed]
        say(f"  Metrics   {len(ok)}/{len(results)} computed")
        for r in results:
            if not r.computed:
                say(f"            - {r.kpi.id}: {r.reason}")

    findings = values.get("analyse", [])
    by_sev: Dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    say(f"  Findings  {len(findings)} "
        f"({', '.join(f'{v} {k}' for k, v in sorted(by_sev.items()))})")

    images = values.get("charts_png", {})
    if images:
        say(f"  Charts    {len(images)}/{len(values['visualise']['light'])} "
            f"exported to PNG")

    csv_paths = values.get("csv_bundle", [])
    say(f"  Artifacts {' · '.join(sorted(a for a in spec.outputs.resolved()))}")
    if result.skipped:
        say(f"  Reused    {len(result.skipped)} unchanged stage(s): "
            f"{', '.join(result.skipped)}")

    return PipelineResult(
        out_dir=out_dir,
        dashboard=out_dir / "dashboard.html", workbook=out_dir / "workbook.xlsx",
        report=out_dir / "report.pdf", deck=out_dir / "deck.pptx",
        doc=out_dir / "report.docx", facts=out_dir / "facts.csv",
        images=images, csvs=csv_paths,
        profile=values.get("resolve", profile), kpi_set=kpi_set,
        results=results, findings=findings, tables=tables,
        checks=data.checks if data is not None else [],
        spec=spec, ran=result.ran, skipped=result.skipped,
        seconds=result.seconds, warnings=result.warnings,
    )


def load_profile(path: Path) -> CompanyProfile:
    if not path.exists():
        raise SystemExit(f"Profile not found: {path}")
    return CompanyProfile(**json.loads(path.read_text(encoding="utf-8")))


def load_spec(path: Path) -> RunSpec:
    """Read a RunSpec, accepting a bare profile as shorthand.

    A profile JSON is a valid thing to point `--spec` at: it means "this
    company, every other decision left at its default". Requiring the user to
    wrap it in `{"profile": {...}}` would make the common case the awkward one.
    """
    if not path.exists():
        raise SystemExit(f"Spec not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "profile" not in raw:
        return RunSpec.for_profile(CompanyProfile(**raw))
    return RunSpec(**raw)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kpi-maker",
        description="Profile-driven KPI selection, dashboards and reports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the full pipeline for a profile")
    run.add_argument("--profile", type=Path, help="path to a profile JSON")
    run.add_argument("--spec", type=Path,
                     help="path to a RunSpec JSON (or a bare profile)")
    run.add_argument("--out", type=Path, default=Path("./out"), help="output directory")
    run.add_argument("--seed", type=int, help="override the profile's random seed")
    run.add_argument("--only", help="comma-separated artifacts to build, e.g. "
                                    "dashboard,facts_csv. Default: all of them")
    run.add_argument("--quiet", action="store_true")

    replan = sub.add_parser(
        "plan", help="show what a re-run would rebuild, without running it")
    replan.add_argument("--spec", type=Path, required=True)
    replan.add_argument("--out", type=Path, default=Path("./out"))

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

    if args.command == "plan":
        spec = load_spec(args.spec)
        report = plan_rerun(spec, args.out)
        print(f"Artifacts requested : {', '.join(report['artifacts'])}")
        print(f"Rebuild             : {', '.join(report['dirty']) or 'nothing'}")
        print(f"Reuse               : {', '.join(report['reused']) or 'nothing'}")
        print(f"Estimated           : ~{report['estimated_seconds']}s")
        return 0

    if args.command == "validate":
        from .kpi.selection import select
        profile = load_profile(args.profile)
        kpi_set = select(profile)
        print(f"Profile OK: {profile.identity.name}")
        print(f"  confidence     {profile.confidence:.0%}")
        print(f"  KPIs selected  {len(kpi_set.kpis)} (north star: {kpi_set.north_star})")
        print(f"  leading share  {kpi_set.leading_share:.0%}")
        for kid, reason in sorted(kpi_set.dropped.items()):
            print(f"  excluded       {kid}: {reason}")
        return 0

    if not args.profile and not args.spec:
        raise SystemExit("run needs --profile or --spec")

    spec = load_spec(args.spec) if args.spec else RunSpec.for_profile(
        load_profile(args.profile))
    if args.seed is not None:
        spec.source.generator.seed = args.seed
    if args.only:
        spec.outputs.artifacts = [a.strip() for a in args.only.split(",") if a.strip()]

    profile = spec.profile
    print(f"Running pipeline for {profile.identity.name} "
          f"({profile.business_model.type.value})")
    result = run_pipeline(profile, args.out, quiet=args.quiet, spec=spec)
    print(f"\nDone in {result['seconds']}s. Open {result['dashboard']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
