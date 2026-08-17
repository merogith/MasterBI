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

from ..datagen import GENERATORS
from ..datagen import available as _archetypes
from ..design.logo import load_logo
from ..design.palette import derive_tokens
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

# `GENERATORS` is re-exported here under the name the CLI and the tests already
# look for. Importing `..datagen` is what registers every shipped archetype, so
# a new sector is a module with `@generator("…")` on it and nothing in the
# pipeline changes.


# --------------------------------------------------------------------------
# Compute spine
# --------------------------------------------------------------------------

@stage("resolve", reads=("profile",), label="Validating profile")
def _resolve(ctx) -> Any:
    return ctx.spec.profile


@stage("source", needs=("resolve",), reads=("profile", "source"),
       label="Generating data and reconciling")
def _source(ctx) -> Any:
    if ctx.spec.source.kind.value == "upload":
        return _source_from_uploads(ctx)

    archetype = ctx.spec.resolve_archetype()
    generator = GENERATORS.get(archetype)
    if generator is None:
        raise ValueError(
            f"No data generator for business model {archetype!r}. "
            f"Available: {', '.join(_archetypes())}."
        )
    # A sector simulated by a neighbouring archetype produces figures that
    # reconcile but are not a model of its economics. That belongs in the same
    # place as every other caveat the run carries, not in a log line nobody
    # reads.
    note = ctx.spec.archetype_note()
    if note is not None:
        ctx.gate_warnings.append(note)
        ctx.say(f"  WARNING   {note}")
    profile = ctx.get("resolve")
    # Seed and history live on the profile, so a spec override is applied by
    # handing the generator an adjusted copy rather than by threading two more
    # parameters through every archetype. The rest of the knobs are about how
    # the simulation behaves, not about who the company is, so they travel as
    # `GeneratorParams`.
    seed = ctx.spec.resolve_seed()
    months = ctx.spec.resolve_history_months()
    if seed != profile.seed or months != profile.history_months:
        profile = profile.model_copy(update={"seed": seed, "history_months": months})
    return generator(profile, ctx.spec.source.generator)


def _source_from_uploads(ctx) -> Any:
    """Read the user's files instead of generating a company."""
    from pathlib import Path

    from ..ingest.pipeline import build_from_uploads

    uploads = ctx.spec.source.uploads
    if not uploads:
        raise ValueError(
            "source.kind is 'upload' but no files are listed in source.uploads")

    base = Path(ctx.uploads_dir)
    paths = [Path(u) if Path(u).is_absolute() else base / u for u in uploads]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise ValueError(f"uploaded file(s) not found: {', '.join(missing)}")

    data, origins, plans = build_from_uploads(paths, ctx.get("resolve"), ctx.spec)
    # The origin map has to reach the metrics stage, which is where `basis` is
    # decided. It is not a stage output because it describes the data rather
    # than being one.
    ctx.origins = origins
    # The plans are *not* set on the context — they travel on `data`, which is
    # this stage's cached output, so a warm re-run that reuses `source` and
    # rebuilds `model` still has them. See `GeneratedData.upload_plans`.
    for plan in plans:
        ctx.say(f"  Source    {plan.filename} -> {plan.table} ({plan.note})")
    ctx.say(f"  Source    {len(data.tables)} table(s) from {len(paths)} upload(s)")
    return data


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


@stage("model", needs=("clean", "source"), reads=("model",),
       label="Building the fact-table model")
def _model(ctx) -> Dict[str, pd.DataFrame]:
    tables = ctx.get("clean")
    spec = ctx.spec.model

    # What shape detection proposed for each upload, with anything the user
    # wrote explicitly layered over it per field. Without this the detected
    # mapping would have nowhere to go: `source` decides which table a file
    # becomes, but renaming its columns has to wait until after `clean`, which
    # works on the names the user recognises.
    #
    # `source` is in `needs` for this alone — the tables come through `clean` —
    # because reading a stage the graph does not say you need is what `ctx.get`
    # raises on, and rightly.
    from ..ingest.pipeline import detected_mapping
    mapping = detected_mapping(getattr(ctx.get("source"), "upload_plans", None) or [],
                               spec.mapping)

    if mapping:
        # Mapping runs AFTER cleaning on purpose: the user cleans using their
        # own column names, which are the ones the profiler reported problems
        # against, and only then are those columns renamed to canonical ones.
        from ..ingest.pipeline import apply_mapping
        tables = apply_mapping(tables, mapping)

    if spec.calculated_columns:
        from ..prep.model import apply_model
        tables = apply_model(tables, spec, ctx)

    if ctx.spec.source.kind.value == "upload":
        # An export carries the lines an accountant types; the contract wants
        # the ones an accountant derives. Both are the same numbers, and the
        # gate would reject any other value for the derived ones — so computing
        # them here is completing the user's data, not modelling it. Done after
        # the user's own calculated columns so an explicit definition wins.
        from ..ingest.pipeline import derive_pl_columns
        fin = tables.get("monthly_financials")
        if fin is not None and not fin.empty:
            tables = dict(tables)
            tables["monthly_financials"], added = derive_pl_columns(fin)
            if added:
                ctx.say(f"  Model     derived {', '.join(added)} from the P&L lines "
                        f"supplied")

        # Uploaded data meets the contract here, once it is canonical. Tier 1
        # still raises; Tier 2 becomes warnings the appendix reports.
        from ..ingest.pipeline import gate_uploaded
        uploaded_warnings = gate_uploaded(tables, ctx.get("resolve"))
        ctx.gate_warnings.extend(uploaded_warnings)
        for warning in uploaded_warnings:
            ctx.say(f"  WARNING   {warning}")

    return tables


@stage("select", needs=("resolve",), reads=("profile", "metrics"),
       label="Selecting KPIs from the library")
def _select(ctx) -> Any:
    return select(ctx.get("resolve"), overrides=ctx.spec.metrics)


@stage("metrics", needs=("select", "model", "source"),
       reads=("profile", "metrics", "source"),
       label="Computing metrics")
def _metrics(ctx) -> List[Any]:
    # Every cut the data offers, not one chosen here. The dimensions come from
    # `segment_financials`, which the generator emits for whatever the archetype
    # can actually be sliced by — customer segment for a subscription business,
    # channel *and* category for a transactional one. Nothing in the spec picks
    # a dimension because nothing needs to: the cost is about 250ms per cut
    # against a 2.3s pipeline, and a decomposition that can only see one
    # dimension cannot rank it against another.
    #
    # An upload with no `segment_financials` yields no dimensions and this is
    # exactly the blended computation it was before.
    from ..metrics.engine import dimensions
    tables = ctx.get("model")
    return compute(ctx.get("select"), tables, ctx.get("resolve"),
                   origins=getattr(ctx, "origins", None),
                   by=dimensions(tables))


@stage("analyse", needs=("metrics",), reads=("profile", "analysis"),
       label="Detecting findings")
def _analyse(ctx) -> List[Any]:
    return detect_all(ctx.get("metrics"), ctx.get("model"), ctx.get("resolve"),
                      spec=ctx.spec.analysis, locale=ctx.spec.resolve_locale(),
                      # A KPI the user pinned is one they have said they care
                      # about, and nothing else in the product knows that.
                      watched=set(ctx.spec.metrics.pinned or ()))


@stage("narrate", needs=("analyse", "select", "metrics"),
       reads=("profile", "ai"), label="Writing the narrative")
def _narrate(ctx) -> Dict[str, Any]:
    """AI prose per section — the only stage that is not deterministic.

    Two things about where it sits in the graph.

    It **reads `ai` and `profile`, not `design`.** Narration is keyed on what
    the numbers say, not on how the report is laid out, so changing a brand
    colour or reordering sections reuses the cached prose instead of buying it
    again. Section selection is applied downstream, where the renderers already
    drop content for sections they are not drawing.

    And it **returns an empty result rather than being pruned** when AI is off.
    A stage that vanishes from the graph would change every downstream cache
    key and make "the default run is byte-identical" a claim about the cache
    rather than about the output. Two empty containers cost nothing and keep
    the graph the same shape either way.

    The notes ride along with the prose because they are cached together: on a
    re-run that reuses this stage, "narrative was dropped for the risks
    section" has to survive, or the appendix would quietly stop explaining a
    gap that is still there.
    """
    empty: Dict[str, Any] = {"sections": {}, "notes": []}
    ai = ctx.spec.ai
    wanted = ai.resolve_narrate_sections() if ai.enabled else []
    if not wanted:
        return empty

    # Imported here, not at module scope. The AI package is the only part of
    # the pipeline with an optional third-party dependency behind it, and a run
    # that never enables it should never touch the import.
    from ..ai.meter import Meter
    from ..ai.narrator import narrate
    from ..render.sections import SectionContext
    from ..render.sections import build as build_sections

    section_ctx = SectionContext(
        profile=ctx.get("resolve"), kpi_set=ctx.get("select"),
        results=ctx.get("metrics"), findings=ctx.get("analyse"),
        checks=ctx.get("source").checks if ctx.spec.source.kind.value != "upload"
        else [],
        period=ctx.period, origins=ctx.origins or {})
    # Built here only for the briefs the narrator is shown. Cheap — every
    # section function is selection and sorting over lists already in memory —
    # and it is what lets the prompt say "this section already contains these
    # five bullets, frame them" instead of asking for prose in the dark.
    contents = build_sections(section_ctx, wanted)

    meter = Meter()
    try:
        narrative, meter = narrate(
            ctx.get("resolve"), ctx.get("metrics"), ctx.get("analyse"),
            contents, ctx.period, spec=ai, meter=meter,
            locale=ctx.spec.resolve_locale())
    except Exception as exc:                     # noqa: BLE001
        # The pipeline must ALWAYS produce a deliverable (ARCHITECTURE §5).
        # An unexpected failure in the one non-deterministic stage is exactly
        # the case that rule was written for, so it degrades to no prose and
        # says why rather than taking the report down with it.
        narrative = {}
        meter.note(f"Narration failed and was skipped: {exc}")

    meter.write(ctx.out_dir)
    for note in meter.notes:
        ctx.say(f"  WARNING   {note}")
    if narrative:
        ctx.say(f"  Narrated  {len(narrative)} section(s), "
                f"{meter.spent:,} tokens, ~${meter.cost():.2f}")
    return {"sections": narrative, "notes": list(meter.notes),
            "usage": meter.summary()}


def _logo(ctx):
    """The brand logo, loaded once per run.

    Memoised alongside the palette because four render stages want it and the
    file should be validated once, not four times with four chances to differ.
    """
    cached = getattr(ctx, "_logo_cache", "unset")
    if cached == "unset":
        cached = load_logo(ctx.spec.design.brand.logo_path, ctx.uploads_dir)
        object.__setattr__(ctx, "_logo_cache", cached)
    return cached


def _caveats(ctx) -> List[str]:
    """Everything this run had to approximate, for the appendix.

    Two sources, deliberately merged here rather than at either one: the source
    stage records a sector simulated by a neighbouring archetype and a Tier 2
    identity miss on uploaded data, while the selection engine records a
    scorecard built on the cross-sector pack. A reader needs both or neither,
    and every print renderer needs the same list.
    """
    notes = list(ctx.gate_warnings)
    try:
        rationale = ctx.get("select").rationale
    except KeyError:
        return notes
    notes.extend(rationale[k] for k in sorted(rationale)
                 if k.endswith("_warning") and rationale[k] not in notes)
    return notes


def _narration(ctx) -> Dict[str, Any]:
    """The narrate stage's output, or nothing if it was pruned.

    A caller can ask for `--only dashboard`, and `required_stages` walks back
    from artifacts, so `narrate` is present whenever a renderer needs it. This
    guard covers the direct-call path used by the tests and by
    `tools/build_pages.py`, where a renderer may be exercised on its own.
    """
    try:
        return ctx.get("narrate") or {}
    except KeyError:
        return {}


def _palettes(ctx) -> Dict[str, Dict[str, str]]:
    """The derived token set for each mode, memoised on the context.

    Derivation is cheap but not free — a brand colour runs a greedy search over
    candidate companions under three vision models — and the dashboard stage
    alone would otherwise do it twice.
    """
    cached = getattr(ctx, "_palette_cache", None)
    if cached is None:
        brand = ctx.spec.design.brand
        cached = {mode: derive_tokens(brand.primary, mode, brand.accent).tokens
                  for mode in ("light", "dark")}
        object.__setattr__(ctx, "_palette_cache", cached)
    return cached


@stage("visualise", needs=("metrics",), reads=("profile", "design"),
       label="Building chart specs")
def _visualise(ctx) -> Dict[str, List[Any]]:
    """Both themes, built once.

    The dashboard needs light and dark; the print deliverables need light. This
    used to run three times per run because each renderer built its own.
    """
    results, tables = ctx.get("metrics"), ctx.get("model")
    currency = ctx.spec.resolve_currency()
    palette, design = _palettes(ctx), ctx.spec.design
    locale = ctx.spec.resolve_locale()
    return {
        "light": build_all(results, tables, mode="light", currency=currency,
                           tokens=palette["light"], exhibits=design.exhibits,
                           widths=design.exhibit_widths, locale=locale),
        "dark": build_all(results, tables, mode="dark", currency=currency,
                          tokens=palette["dark"], exhibits=design.exhibits,
                          widths=design.exhibit_widths, locale=locale),
    }


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------

@stage("charts_png", needs=("visualise",), reads=("design",),
       artifact="charts_png", label="Exporting chart images")
def _charts_png(ctx) -> Dict[str, bytes]:
    return render_chart_images(ctx.get("visualise")["light"], ctx.out_dir / "charts",
                               tokens=_palettes(ctx)["light"])


@stage("dashboard", needs=("visualise", "analyse", "select", "model", "source",
                           "narrate"),
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
            tokens_light=_palettes(ctx)["light"],
            tokens_dark=_palettes(ctx)["dark"], logo=_logo(ctx),
            narrative=_narration(ctx).get("sections"),
            locale=ctx.spec.resolve_locale(),
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


@stage("report_pdf", needs=("charts_png", "analyse", "select", "visualise",
                            "source", "narrate"),
       reads=("profile", "design"),
       artifact="report_pdf", label="Rendering the PDF report")
def _report_pdf(ctx):
    path = ctx.out_dir / "report.pdf"
    render_report(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
                  ctx.get("analyse"), ctx.get("charts_png"),
                  ctx.get("visualise")["light"], ctx.get("source").checks,
                  [a.description for a in ctx.get("source").anomalies], ctx.period,
                  tokens=_palettes(ctx)["light"],
                  section_order=ctx.spec.design.sections, logo=_logo(ctx),
                  origins=ctx.origins,
                  narrative=_narration(ctx).get("sections"),
                  ai_notes=_narration(ctx).get("notes"),
                  caveats=_caveats(ctx),
                  page_size=ctx.spec.resolve_page_size(),
                  font_stack=ctx.spec.resolve_font_stack(),
                  footer_text=ctx.spec.resolve_footer_text(),
                  locale=ctx.spec.resolve_locale())
    return path


@stage("deck_pptx", needs=("charts_png", "analyse", "select", "visualise",
                           "narrate"),
       reads=("profile", "design"),
       artifact="deck_pptx", label="Building the deck")
def _deck_pptx(ctx):
    path = ctx.out_dir / "deck.pptx"
    render_deck(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
                ctx.get("analyse"), ctx.get("charts_png"),
                ctx.get("visualise")["light"], ctx.period,
                tokens=_palettes(ctx)["light"],
                section_order=ctx.spec.design.sections, logo=_logo(ctx),
                narrative=_narration(ctx).get("sections"),
                caveats=_caveats(ctx),
                font_stack=ctx.spec.resolve_font_stack(),
                footer_text=ctx.spec.resolve_footer_text(),
                locale=ctx.spec.resolve_locale())
    return path


@stage("doc_docx", needs=("charts_png", "analyse", "select", "visualise",
                          "source", "narrate"),
       reads=("profile", "design"),
       artifact="doc_docx", label="Writing the editable report")
def _doc_docx(ctx):
    path = ctx.out_dir / "report.docx"
    render_doc(path, ctx.get("resolve"), ctx.get("select"), ctx.get("metrics"),
               ctx.get("analyse"), ctx.get("charts_png"),
               ctx.get("visualise")["light"], ctx.get("source").checks,
               [a.description for a in ctx.get("source").anomalies], ctx.period,
               tokens=_palettes(ctx)["light"],
               section_order=ctx.spec.design.sections, logo=_logo(ctx),
               origins=ctx.origins,
               narrative=_narration(ctx).get("sections"),
               ai_notes=_narration(ctx).get("notes"),
               caveats=_caveats(ctx),
               font_stack=ctx.spec.resolve_font_stack(),
               footer_text=ctx.spec.resolve_footer_text(),
               locale=ctx.spec.resolve_locale())
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
