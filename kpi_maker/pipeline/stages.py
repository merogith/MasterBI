"""The stages themselves.

Each one is a small function over a `RunContext`, doing exactly what
`cli.run_pipeline` used to do inline. Behaviour is deliberately unchanged in
this pass — the value here is that the steps are now addressable, so a caller
can re-run from any point and skip anything it did not ask for.

`clean` and `model` are pass-throughs today. They exist because the fact-table
contract is where real uploaded data has to land (ARCHITECTURE §3), and having
the seam already in the graph means P2 adds an implementation rather than
re-plumbing the pipeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

import pandas as pd

from ..datagen.saas import generate as generate_subscription
from ..insight.detectors import detect_all
from ..kpi.selection import select
from ..metrics.engine import compute, facts_table
from ..render.dashboard import render_dashboard
from ..render.deck import render_deck
from ..render.doc import render_doc
from ..render.report import render_report
from ..render.workbook import render_workbook, write_csv_bundle
from ..viz.charts import build_all
from ..viz.export import render_all as render_chart_images
from .graph import stage

# Generator archetypes. A registry rather than the old inline dict so P4 adds a
# sector by registering here instead of editing the pipeline.
GENERATORS = {"saas": generate_subscription}


# --------------------------------------------------------------------------
# Compute spine
# --------------------------------------------------------------------------

@stage("resolve", reads=("profile",), label="Validating profile")
def _resolve(ctx) -> Any:
    return ctx.spec.profile


@stage("source", needs=("resolve",), reads=("profile", "source"),
       label="Generating data and reconciling")
def _source(ctx) -> Any:
    archetype = ctx.spec.resolve_archetype()
    generator = GENERATORS.get(archetype)
    if generator is None:
        raise ValueError(
            f"No data generator for business model {archetype!r}. "
            f"Available: {', '.join(sorted(GENERATORS))}."
        )
    profile = ctx.get("resolve")
    # The generator reads seed and history from the profile, so a spec override
    # is applied by handing it an adjusted copy rather than by threading two
    # more parameters through every archetype.
    seed = ctx.spec.resolve_seed()
    months = ctx.spec.resolve_history_months()
    if seed != profile.seed or months != profile.history_months:
        profile = profile.model_copy(update={"seed": seed, "history_months": months})
    return generator(profile)


@stage("clean", needs=("source",), reads=("cleaning",),
       label="Cleaning and transforming")
def _clean(ctx) -> Dict[str, pd.DataFrame]:
    tables = dict(ctx.get("source").tables)
    if not ctx.spec.cleaning.active:
        return tables
    from ..prep.recipe import apply_recipe
    cleaned, lineage = apply_recipe(tables, ctx.spec.cleaning, ctx)
    # Stash the log on the context so `render` can put it in the methodology
    # appendix — a transformation nobody can see is a transformation nobody can
    # defend.
    ctx.lineage = lineage
    ctx.say(f"  Cleaned   {lineage.summary()}")
    return cleaned


@stage("model", needs=("clean",), reads=("model",),
       label="Building the fact-table model")
def _model(ctx) -> Dict[str, pd.DataFrame]:
    tables = ctx.get("clean")
    spec = ctx.spec.model
    if spec.mapping:
        raise NotImplementedError(
            "Column mapping lands with ingestion in P2. Clear `model.mapping` "
            "to run."
        )
    if not spec.calculated_columns:
        return tables
    from ..prep.model import apply_model
    return apply_model(tables, spec, ctx)


@stage("select", needs=("resolve",), reads=("profile", "metrics"),
       label="Selecting KPIs from the library")
def _select(ctx) -> Any:
    return select(ctx.get("resolve"), overrides=ctx.spec.metrics)


@stage("metrics", needs=("select", "model", "source"),
       reads=("profile", "metrics", "source"),
       label="Computing metrics")
def _metrics(ctx) -> List[Any]:
    return compute(ctx.get("select"), ctx.get("model"), ctx.get("resolve"),
                   origins=getattr(ctx, "origins", None))


@stage("analyse", needs=("metrics",), reads=("profile", "analysis"),
       label="Detecting findings")
def _analyse(ctx) -> List[Any]:
    return detect_all(ctx.get("metrics"), ctx.get("model"), ctx.get("resolve"),
                      spec=ctx.spec.analysis)


@stage("visualise", needs=("metrics",), reads=("profile", "design"),
       label="Building chart specs")
def _visualise(ctx) -> Dict[str, List[Any]]:
    """Both themes, built once.

    The dashboard needs light and dark; the print deliverables need light. This
    used to run three times per run because each renderer built its own.
    """
    results, tables = ctx.get("metrics"), ctx.get("model")
    currency = ctx.spec.resolve_currency()
    return {
        "light": build_all(results, tables, mode="light", currency=currency),
        "dark": build_all(results, tables, mode="dark", currency=currency),
    }


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------

@stage("charts_png", needs=("visualise",), reads=("design",),
       artifact="charts_png", label="Exporting chart images")
def _charts_png(ctx) -> Dict[str, bytes]:
    return render_chart_images(ctx.get("visualise")["light"], ctx.out_dir / "charts")


@stage("dashboard", needs=("visualise", "analyse", "select", "model", "source"),
       reads=("profile", "design"),
       artifact="dashboard", label="Rendering the dashboard")
def _dashboard(ctx):
    specs = ctx.get("visualise")
    path = ctx.out_dir / "dashboard.html"
    path.write_text(
        render_dashboard(
            ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
            ctx.get("analyse"), ctx.get("model"), ctx.get("source").checks,
            [a.description for a in ctx.get("source").anomalies],
            specs_light=specs["light"], specs_dark=specs["dark"],
        ),
        encoding="utf-8",
    )
    return path


@stage("workbook", needs=("analyse", "select", "model", "source"),
       reads=("profile", "design"),
       artifact="workbook", label="Writing the workbook")
def _workbook(ctx):
    path = ctx.out_dir / "workbook.xlsx"
    render_workbook(path, ctx.get("resolve"), ctx.get("select"),
                    ctx.get("metrics"), ctx.get("analyse"), ctx.get("model"),
                    ctx.get("source").checks)
    return path


@stage("report_pdf", needs=("charts_png", "analyse", "select", "visualise", "source"),
       reads=("profile", "design"),
       artifact="report_pdf", label="Rendering the PDF report")
def _report_pdf(ctx):
    path = ctx.out_dir / "report.pdf"
    render_report(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
                  ctx.get("analyse"), ctx.get("charts_png"),
                  ctx.get("visualise")["light"], ctx.get("source").checks,
                  [a.description for a in ctx.get("source").anomalies], ctx.period)
    return path


@stage("deck_pptx", needs=("charts_png", "analyse", "select", "visualise"),
       reads=("profile", "design"),
       artifact="deck_pptx", label="Building the deck")
def _deck_pptx(ctx):
    path = ctx.out_dir / "deck.pptx"
    render_deck(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
                ctx.get("analyse"), ctx.get("charts_png"),
                ctx.get("visualise")["light"], ctx.period)
    return path


@stage("doc_docx", needs=("charts_png", "analyse", "select", "visualise", "source"),
       reads=("profile", "design"),
       artifact="doc_docx", label="Writing the editable report")
def _doc_docx(ctx):
    path = ctx.out_dir / "report.docx"
    render_doc(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
               ctx.get("analyse"), ctx.get("charts_png"),
               ctx.get("visualise")["light"], ctx.get("source").checks,
               [a.description for a in ctx.get("source").anomalies], ctx.period)
    return path


@stage("csv_bundle", needs=("model",), reads=(), artifact="csv_bundle",
       label="Writing the CSV bundle")
def _csv_bundle(ctx) -> List[Any]:
    return write_csv_bundle(ctx.get("model"), ctx.out_dir / "data")


@stage("facts_csv", needs=("metrics",), reads=(), artifact="facts_csv",
       label="Writing the facts table")
def _facts_csv(ctx):
    path = ctx.out_dir / "facts.csv"
    facts_table(ctx.get("metrics")).to_csv(path, index=False, encoding="utf-8-sig")
    return path


@stage("json_dumps", needs=("select", "analyse", "resolve"), reads=("profile",),
       artifact="json_dumps", label="Writing the reproducibility inputs")
def _json_dumps(ctx) -> List[Any]:
    out = ctx.out_dir
    (out / "profile.json").write_text(
        ctx.get("resolve").model_dump_json(indent=2), encoding="utf-8")
    (out / "kpi_set.json").write_text(
        ctx.get("select").model_dump_json(indent=2), encoding="utf-8")
    (out / "findings.json").write_text(
        json.dumps([asdict(f) for f in ctx.get("analyse")], indent=2, default=str),
        encoding="utf-8")
    return [out / "profile.json", out / "kpi_set.json", out / "findings.json"]
