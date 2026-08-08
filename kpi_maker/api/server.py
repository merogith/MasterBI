"""HTTP API + static host for the web UI.

A thin wrapper over `run_pipeline`. The engine knows nothing about HTTP, and
this module knows nothing about how KPIs are chosen — that separation is what
makes the UI swappable (ROADMAP M8).

Runs execute on a small thread pool and write into `runs/<run_id>/`. Each run
persists a `summary.json` so the results screen loads from one cheap read
rather than re-parsing artifacts on every poll.
"""
from __future__ import annotations

import json
import shutil
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..cli import load_profile, run_pipeline
from ..insight.detectors import DETECTOR_NAMES
from ..formula import FormulaError, describe_functions, evaluate, validate
from ..formula.evaluate import RowResolver, SeriesResolver
from ..kpi.schema import user_kpi
from ..kpi.selection import load_library, unknown_kpi_ids
from ..kpi.user_library import delete_user_kpi, save_user_kpi, user_kpi_ids
from ..ingest import detect_shape, profile_table, read_any, shape_catalog
from ..ingest.derive import derive_profile_fields
from ..ingest.quality import build_report
from ..prep import describe_ops
from ..prep.model import preview_column
from ..prep.recipe import preview_recipe
from ..pipeline.runner import plan_rerun
from ..profile.schema import CompanyProfile
from ..contract.schemas import FACT_SCHEMAS
from ..spec.schema import ALL_ARTIFACTS, RunSpec
from ..survey import as_json as survey_json
from ..survey import build_profile, surprise_profile

ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = ROOT / "samples"
RUNS_DIR = ROOT / "runs"
UI_DIR = ROOT / "ui"
UPLOADS_DIR = RUNS_DIR / "_uploads"

RUNS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# In-flight state. Completed runs are recovered from disk on restart, so this
# is a cache of live progress rather than the source of truth.
_STATE: Dict[str, Dict[str, Any]] = {}
_LOCK = Lock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")

ARTIFACT_LABELS = [
    ("dashboard.html", "Interactive dashboard", "dashboard",
     "Filterable, drill-down, light and dark"),
    ("report.pdf", "Executive report", "pdf", "15 pages, board-ready"),
    ("deck.pptx", "Board deck", "pptx", "One message per slide"),
    ("report.docx", "Editable report", "docx", "Word, with heading styles"),
    ("workbook.xlsx", "Data workbook", "xlsx", "Scorecard, tracker, definitions"),
    ("facts.csv", "KPI facts table", "csv", "Every computed metric"),
    ("findings.json", "Findings", "json", "Detector output"),
    ("profile.json", "Company profile", "json", "The run's input contract"),
    ("kpi_set.json", "KPI selection", "json", "What was chosen and why"),
]

app = FastAPI(title="KPI Dashboard Maker", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def allow_private_network(request, call_next):
    """Let the GitHub Pages build talk to this server.

    The published site is HTTPS on a public origin; this server is plain HTTP
    on a loopback one. Browsers permit that (loopback counts as a trustworthy
    origin, so it is not blocked as mixed content), but Chrome's Private
    Network Access rules additionally require a public page's preflight to be
    answered with this header before it may reach a local address.

    Loopback-only by nature: the server binds 127.0.0.1 unless told otherwise,
    so this widens what the *browser* permits, not what the network exposes.
    """
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network"):
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class RunRequest(BaseModel):
    mode: str                                  # sample | survey | surprise
    sample_id: Optional[str] = None
    answers: Optional[Dict[str, Any]] = None
    company_name: Optional[str] = None
    seed: Optional[int] = None
    # Adjustments applied on top of whatever the mode produces. This is what
    # lets a preset be launched already customised rather than only inspected
    # after the fact.
    spec: Optional[Dict[str, Any]] = None


class SpecPatch(BaseModel):
    """A partial RunSpec, deep-merged over the run's current one."""
    patch: Dict[str, Any]


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive merge, with lists replaced wholesale.

    Lists are ordered user intent — the exhibit order, the cleaning recipe, the
    excluded KPIs. Merging them element-wise would make "remove the third step"
    impossible to express.
    """
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_spec(run_id: str) -> RunSpec:
    path = RUNS_DIR / run_id / "spec.json"
    if not path.exists():
        raise HTTPException(404, f"Run {run_id!r} has no spec on disk")
    return RunSpec(**json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set(run_id: str, **fields) -> None:
    with _LOCK:
        _STATE.setdefault(run_id, {})
        _STATE[run_id].update(fields)


def _get(run_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        state = _STATE.get(run_id)
        return dict(state) if state else None


def _artifacts(run_dir: Path, run_id: str) -> List[Dict[str, Any]]:
    out = []
    for filename, label, kind, blurb in ARTIFACT_LABELS:
        path = run_dir / filename
        if not path.exists():
            continue
        out.append({
            "name": filename, "label": label, "kind": kind, "blurb": blurb,
            "size": path.stat().st_size,
            "url": f"/files/{run_id}/{filename}",
        })
    return out


def _build_summary(run_id: str, run_dir: Path, profile: CompanyProfile) -> Dict[str, Any]:
    """Everything the results screen needs, in one payload."""
    facts_path = run_dir / "facts.csv"
    findings_path = run_dir / "findings.json"
    kpi_set_path = run_dir / "kpi_set.json"

    kpis: List[Dict[str, Any]] = []
    if facts_path.exists():
        df = pd.read_csv(facts_path)
        kpis = json.loads(df.where(pd.notna(df), None).to_json(orient="records"))

    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else []
    kpi_set = json.loads(kpi_set_path.read_text(encoding="utf-8")) if kpi_set_path.exists() else {}

    north_id = kpi_set.get("north_star")
    north = next((k for k in kpis if k["kpi_id"] == north_id), None)

    period = ""
    fin_csv = run_dir / "data" / "monthly_financials.csv"
    if fin_csv.exists():
        fin = pd.read_csv(fin_csv)
        if not fin.empty:
            period = f"{fin['month'].iloc[0]} to {fin['month'].iloc[-1]}"

    tiles = [k for k in kpis if k.get("computed") and (k.get("tier") or 9) <= 1]

    by_sev: Dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    return {
        "run_id": run_id,
        "company": profile.identity.name,
        "currency": profile.identity.currency,
        "country": profile.identity.country,
        "business_model": profile.business_model.type.value,
        "customer_type": profile.business_model.customer_type.value,
        "stage": profile.size.stage.value,
        "objective": profile.intent.primary_objective.value,
        "audience": profile.intent.audience.value,
        "confidence": profile.confidence,
        "period": period,
        "north_star": north,
        "tiles": tiles,
        "kpis": kpis,
        "findings": findings,
        "severity_counts": by_sev,
        "rationale": kpi_set.get("rationale", {}),
        "dropped": kpi_set.get("dropped", {}),
        "warnings": [v for k, v in kpi_set.get("rationale", {}).items()
                     if k.startswith("_")],
        "artifacts": _artifacts(run_dir, run_id),
        "provenance": profile.provenance,
    }


def _execute(run_id: str, spec: RunSpec) -> None:
    run_dir = RUNS_DIR / run_id
    steps: List[str] = []

    def note(msg: str) -> None:
        steps.append(msg)
        _set(run_id, steps=list(steps))

    try:
        _set(run_id, status="running", steps=["Validating profile"])
        note("Selecting KPIs from the library")
        result = run_pipeline(spec.profile, run_dir, quiet=True, spec=spec)
        note("Generating data and reconciling")
        note("Computing metrics and detecting findings")
        note("Rendering dashboard, report, deck and workbook")

        summary = _build_summary(run_id, run_dir, spec.profile)
        summary["steps"] = steps
        summary["stages_ran"] = result.get("ran", [])
        summary["stages_reused"] = result.get("skipped", [])
        summary["seconds"] = result.get("seconds")
        (run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
        _set(run_id, status="done", finished_at=_now(), summary=summary)
    except Exception as exc:                             # noqa: BLE001
        _set(run_id, status="error", error=str(exc),
             traceback=traceback.format_exc(limit=6), finished_at=_now())


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/api/samples")
def list_samples() -> List[Dict[str, Any]]:
    gallery_path = SAMPLES_DIR / "gallery.json"
    if not gallery_path.exists():
        return []
    gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
    enriched = []
    for entry in gallery:
        path = SAMPLES_DIR / entry["file"]
        if not path.exists():
            continue
        try:
            profile = load_profile(path)
        except Exception:                                # noqa: BLE001
            continue
        enriched.append({
            **entry,
            "currency": profile.identity.currency,
            "country": profile.identity.country,
            "revenue": profile.financials.revenue,
            "headcount": profile.size.headcount_total,
            "stage": profile.size.stage.value,
            "customers": profile.market.customer_count,
            "objective": profile.intent.primary_objective.value,
        })
    return enriched


@app.get("/api/survey")
def get_survey() -> Dict[str, Any]:
    return survey_json()


@app.post("/api/runs")
def create_run(req: RunRequest) -> Dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]

    try:
        if req.mode == "sample":
            gallery = {e["id"]: e for e in json.loads(
                (SAMPLES_DIR / "gallery.json").read_text(encoding="utf-8"))}
            entry = gallery.get(req.sample_id or "")
            if entry is None:
                raise HTTPException(404, f"Unknown sample {req.sample_id!r}")
            profile = load_profile(SAMPLES_DIR / entry["file"])
        elif req.mode == "survey":
            profile = build_profile(req.answers or {},
                                    name=req.company_name or None,
                                    seed=req.seed)
        elif req.mode == "surprise":
            profile = surprise_profile(req.seed)
        else:
            raise HTTPException(400, f"Unknown mode {req.mode!r}")
        spec = RunSpec.for_profile(profile)
        if req.spec:
            # The caller's adjustments merge over the mode's defaults, so a
            # preset can be launched already customised.
            merged = _deep_merge(json.loads(spec.model_dump_json()), req.spec)
            spec = RunSpec(**merged)
    except HTTPException:
        raise
    except Exception as exc:                             # noqa: BLE001
        # Validation failures are the user's answers being inconsistent, not a
        # server fault — report them as such so the UI can show the message.
        raise HTTPException(422, str(exc))

    # `run_id` is the positional key of _set — passing it again as a field
    # collides with it.
    _set(run_id, status="queued", mode=req.mode,
         company=profile.identity.name, started_at=_now(), steps=[])
    _POOL.submit(_execute, run_id, spec)
    return {"run_id": run_id, "status": "queued", "company": profile.identity.name}


# --------------------------------------------------------------------------
# Spec: read, adjust, re-run
# --------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/spec")
def get_spec(run_id: str) -> Dict[str, Any]:
    return json.loads(_load_spec(run_id).model_dump_json())


@app.put("/api/runs/{run_id}/spec")
def put_spec(run_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Replace the spec and report what a re-run would rebuild."""
    try:
        validated = RunSpec(**spec)
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))

    # Catch a bad KPI id here rather than letting the re-run fail on it later:
    # the editor can point at the offending field only while the edit is still
    # in the user's hands.
    unknown = unknown_kpi_ids(validated.profile, validated.metrics)
    if unknown:
        raise HTTPException(
            422, f"Unknown KPI id(s) in the metrics spec: {', '.join(unknown)}")

    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(404, "Run not found")
    (run_dir / "spec.json").write_text(
        validated.model_dump_json(indent=2), encoding="utf-8")
    return plan_rerun(validated, run_dir)


@app.patch("/api/runs/{run_id}/spec")
def patch_spec(run_id: str, body: SpecPatch) -> Dict[str, Any]:
    """Deep-merge a partial spec. The studio's per-field edits land here."""
    current = _load_spec(run_id)
    merged = _deep_merge(json.loads(current.model_dump_json()), body.patch)
    return put_spec(run_id, merged)


@app.get("/api/runs/{run_id}/plan")
def get_plan(run_id: str) -> Dict[str, Any]:
    """What a re-run would rebuild, and roughly how long it would take.

    The studio calls this on every edit so the action bar can say "3 stages,
    ~4s" before the user commits to waiting.
    """
    return plan_rerun(_load_spec(run_id), RUNS_DIR / run_id)


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str) -> Dict[str, Any]:
    """Re-run the stages the current spec has invalidated."""
    spec = _load_spec(run_id)
    report = plan_rerun(spec, RUNS_DIR / run_id)
    _set(run_id, status="queued", mode="rerun",
         company=spec.profile.identity.name, started_at=_now(), steps=[])
    _POOL.submit(_execute, run_id, spec)
    return {"run_id": run_id, "status": "queued", **report}


# --------------------------------------------------------------------------
# Catalog: the KPI library, and the formula editor's support endpoints
# --------------------------------------------------------------------------

class UserKpiRequest(BaseModel):
    """The four fields that decide what the user sees, plus optional extras."""
    name: str
    expression: str
    unit: str
    direction: str
    id: Optional[str] = None
    perspective: Optional[str] = None
    timing: Optional[str] = None
    tier: Optional[int] = None
    owner_role: Optional[str] = None
    interpretation: Optional[str] = None


class FormulaRequest(BaseModel):
    expression: str
    scope: str = "series"           # series | row
    run_id: Optional[str] = None    # required for preview; optional for validate
    table: Optional[str] = None     # row scope only


def _kpi_payload(kpi) -> Dict[str, Any]:
    return {
        "id": kpi.id,
        "name": kpi.name,
        "perspective": kpi.perspective.value,
        "tier": int(kpi.tier),
        "timing": kpi.timing.value,
        "direction": kpi.direction.value,
        "unit": kpi.unit,
        "owner_role": kpi.owner_role,
        "formula": kpi.formula,
        "compute": kpi.compute.model_dump(mode="json"),
        "origin": kpi.origin.value,
        "core": kpi.core,
        "benchmark_p50": kpi.benchmark.p50 if kpi.benchmark else None,
        "interpretation": kpi.interpretation,
        "pitfalls": kpi.pitfalls,
        "applies_when": kpi.applies_when,
    }


@app.get("/api/catalog/kpis")
def list_kpis(pack: Optional[str] = None) -> Dict[str, Any]:
    """Every KPI available, library and user alike."""
    kpis = load_library([pack] if pack else None)
    return {
        "kpis": [_kpi_payload(k) for k in sorted(kpis, key=lambda k: (int(k.tier), k.id))],
        "user_ids": user_kpi_ids(),
    }


@app.post("/api/catalog/kpis")
def save_kpi(req: UserKpiRequest) -> Dict[str, Any]:
    """Create or replace a stored user KPI. The formula is validated first."""
    try:
        validate(req.expression, scope="series")
    except FormulaError as exc:
        raise HTTPException(422, str(exc))

    extras = {k: v for k, v in (
        ("perspective", req.perspective), ("timing", req.timing),
        ("tier", req.tier), ("owner_role", req.owner_role),
        ("interpretation", req.interpretation),
    ) if v is not None}

    try:
        kpi = user_kpi(req.name, req.expression, req.unit, req.direction,
                       kpi_id=req.id, **extras)
        save_user_kpi(kpi)
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))
    return _kpi_payload(kpi)


@app.delete("/api/catalog/kpis/{kpi_id}")
def remove_kpi(kpi_id: str) -> Dict[str, Any]:
    try:
        removed = delete_user_kpi(kpi_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not removed:
        raise HTTPException(404, f"No user KPI {kpi_id!r}")
    return {"deleted": kpi_id}


@app.get("/api/catalog/functions")
def list_functions() -> Dict[str, Any]:
    return {"functions": describe_functions()}


def _series_resolver(run_id: Optional[str], profile=None):
    """A resolver bound to a run's real tables, when there is a run."""
    if run_id is None:
        return None
    tables = _load_run_tables(run_id)
    if not tables:
        return None
    fin = tables.get("monthly_financials")
    index = pd.Index(fin["month"]) if fin is not None else None
    known = {k.id for k in load_library(None)}
    # Validation only needs to know whether a name IS a KPI, not what it
    # evaluates to — computing every referenced metric to answer a keystroke
    # would make the editor unusable.
    return SeriesResolver(tables, index, profile=profile,
                          kpi_lookup=lambda n: _KNOWN if n in known else None)


_KNOWN = pd.Series(dtype="float64")   # sentinel: "this name resolves"


def _load_run_tables(run_id: str) -> Dict[str, pd.DataFrame]:
    data_dir = RUNS_DIR / run_id / "data"
    if not data_dir.exists():
        return {}
    return {p.stem: pd.read_csv(p) for p in sorted(data_dir.glob("*.csv"))}


@app.post("/api/formula/validate")
def validate_formula(req: FormulaRequest) -> Dict[str, Any]:
    """Parse and check without evaluating.

    Structure, function names, arity and scope are always checked. Names are
    only checked when a run is supplied, because the editor validates while the
    user types and there may be no data to resolve against yet.
    """
    resolver = None
    if req.scope == "row":
        if not (req.run_id and req.table):
            resolver = None
        else:
            frame = _load_run_tables(req.run_id).get(req.table)
            resolver = RowResolver(frame, req.table) if frame is not None else None
    else:
        spec = None
        try:
            spec = _load_spec(req.run_id) if req.run_id else None
        except HTTPException:
            spec = None
        resolver = _series_resolver(req.run_id, spec.profile if spec else None)

    try:
        return {"ok": True, **validate(req.expression, scope=req.scope, resolver=resolver)}
    except FormulaError as exc:
        return {"ok": False, "error": exc.as_dict()}


@app.post("/api/formula/preview")
def preview_formula(req: FormulaRequest) -> Dict[str, Any]:
    """Evaluate against a run's real data and return something to look at."""
    if not req.run_id:
        raise HTTPException(400, "preview needs a run_id")
    tables = _load_run_tables(req.run_id)
    if not tables:
        raise HTTPException(404, "That run has no data on disk")

    try:
        spec = _load_spec(req.run_id)
        if req.scope == "row":
            frame = tables.get(req.table or "")
            if frame is None:
                raise HTTPException(404, f"No table {req.table!r} in this run")
            return {"scope": "row",
                    **preview_column(frame, req.table or "", req.expression)}

        # Series scope: evaluate for real, which means computing any KPI the
        # formula references.
        from ..datagen.saas import GeneratedData
        from ..metrics.engine import MetricContext, _Evaluator
        from ..kpi.selection import load_all_known

        ctx = MetricContext(profile=spec.profile, tables=tables)
        evaluator = _Evaluator(ctx, {k.id: k for k in load_all_known()})
        resolver = SeriesResolver(tables, ctx.fin.index, profile=spec.profile,
                                  kpi_lookup=evaluator._lookup)
        series = evaluate(req.expression, resolver)
        tail = series.dropna().tail(12)
        return {
            "scope": "series",
            "points": [{"month": str(m), "value": None if pd.isna(v) else float(v)}
                       for m, v in tail.items()],
            "current": float(tail.iloc[-1]) if len(tail) else None,
            "non_null": int(series.notna().sum()),
            "total": int(len(series)),
        }
    except FormulaError as exc:
        raise HTTPException(422, str(exc))
    except HTTPException:
        raise
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, f"could not evaluate: {exc}")


class RecipeRequest(BaseModel):
    """A recipe to try, plus the table to show the result on."""
    steps: List[Dict[str, Any]] = []
    table: Optional[str] = None


@app.get("/api/ops")
def list_ops() -> Dict[str, Any]:
    """The cleaning op registry, served from the engine so the UI cannot drift."""
    return {"ops": describe_ops(), "shapes": shape_catalog()}


@app.post("/api/ingest/profile")
async def ingest_profile(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Read, profile and shape-match an upload. Nothing is applied.

    Replaces the old /api/upload, which profiled columns inside the route and
    stopped there. This returns everything the Source panel needs to show the
    user what they have and what it could become — and every suggestion is an
    offer, not a change.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    stored.write_bytes(await file.read())

    try:
        result = read_any(stored)
    except Exception as exc:                             # noqa: BLE001
        stored.unlink(missing_ok=True)
        raise HTTPException(422, str(exc))

    profile = profile_table(result.frame)
    proposals = detect_shape(result.frame, profile)

    return {
        "filename": file.filename,
        "stored_as": stored.name,
        "read": result.as_dict(),
        "profile": profile.as_dict(),
        "shapes": [p.as_dict() for p in proposals[:3]],
        "preview": json.loads(
            result.frame.head(10).where(pd.notna(result.frame.head(10)), None)
            .to_json(orient="records")),
    }


@app.post("/api/runs/{run_id}/clean/preview")
def clean_preview(run_id: str, body: RecipeRequest) -> Dict[str, Any]:
    """Apply a recipe to this run's tables and report what it did.

    Nothing is persisted: the point is to let the user see the diff before
    committing the recipe to the spec.
    """
    from ..spec.schema import CleaningRecipe, CleaningStep
    tables = _load_run_tables(run_id)
    if not tables:
        raise HTTPException(404, "That run has no data on disk")
    try:
        recipe = CleaningRecipe(steps=[CleaningStep(**s) for s in body.steps])
        return preview_recipe(tables, recipe, table=body.table)
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))


@app.get("/api/runs/{run_id}/lineage")
def get_lineage(run_id: str) -> Dict[str, Any]:
    """What was done to this run's data. Reads the recipe from the stored spec."""
    from ..spec.schema import CleaningRecipe
    spec = _load_spec(run_id)
    tables = _load_run_tables(run_id)
    if not spec.cleaning.active:
        return {"steps": [], "summary": "no cleaning applied"}
    if not tables:
        raise HTTPException(404, "That run has no data on disk")
    try:
        report = preview_recipe(tables, spec.cleaning)
        return {"steps": report["steps"], "summary": report["summary"]}
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))


@app.get("/api/runs/{run_id}/quality")
def get_quality(run_id: str) -> Dict[str, Any]:
    """What is present, what is missing, and what each gap would unlock.

    Shown before a run so a narrow dashboard reads as a consequence of the
    data supplied rather than as a broken product.
    """
    spec = _load_spec(run_id)
    tables = _load_run_tables(run_id)
    origins = {t: "modelled" for t in spec.source.fill_gaps}
    return build_report(tables, spec.profile, origins=origins).as_dict()


@app.post("/api/ingest/derive")
def ingest_derive(body: Dict[str, Any]) -> Dict[str, Any]:
    """What the uploaded data can tell us about the company.

    So the shortened survey asks only for what no file contains — sector,
    objective, audience — instead of for numbers the file already holds.
    """
    run_id = body.get("run_id")
    if not run_id:
        raise HTTPException(400, "derive needs a run_id")
    tables = _load_run_tables(run_id)
    if not tables:
        raise HTTPException(404, "That run has no data on disk")
    return derive_profile_fields(tables, body.get("filename", "upload")).as_dict()


@app.get("/api/catalog/options")
def catalog_options() -> Dict[str, Any]:
    """What the studio is allowed to offer. Served from the engine's own
    registries so the UI cannot drift from what the pipeline supports."""
    return {
        "artifacts": ALL_ARTIFACTS,
        "detectors": DETECTOR_NAMES,
        "themes": ["light", "dark", "auto"],
        "ops": describe_ops(),
        "shapes": shape_catalog(),
        "fact_tables": sorted(FACT_SCHEMAS),
    }


@app.get("/api/runs")
def list_runs() -> List[Dict[str, Any]]:
    out = []
    with _LOCK:
        live = {k: dict(v) for k, v in _STATE.items()}
    for run_id, state in live.items():
        out.append({
            "run_id": run_id, "status": state.get("status"),
            "company": state.get("company"), "mode": state.get("mode"),
            "started_at": state.get("started_at"),
        })
    # Recover completed runs from earlier server sessions.
    for path in sorted(RUNS_DIR.glob("*/summary.json")):
        run_id = path.parent.name
        if run_id in live:
            continue
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            continue
        out.append({"run_id": run_id, "status": "done",
                    "company": summary.get("company"), "mode": "restored",
                    "started_at": None})
    return sorted(out, key=lambda r: r.get("started_at") or "", reverse=True)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    state = _get(run_id)
    if state is None:
        summary_path = RUNS_DIR / run_id / "summary.json"
        if summary_path.exists():
            return {"run_id": run_id, "status": "done",
                    "summary": json.loads(summary_path.read_text(encoding="utf-8"))}
        raise HTTPException(404, "Run not found")
    return state


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> Dict[str, str]:
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    with _LOCK:
        _STATE.pop(run_id, None)
    return {"status": "deleted"}


@app.get("/api/runs/{run_id}/table/{table}")
def get_table(run_id: str, table: str, limit: int = 200) -> Dict[str, Any]:
    """Preview a fact table for the Data tab."""
    path = RUNS_DIR / run_id / "data" / f"{table}.csv"
    if not path.exists():
        raise HTTPException(404, f"No table {table!r} in this run")
    df = pd.read_csv(path)
    total = len(df)
    head = df.head(limit)
    return {
        "table": table, "rows": total, "columns": list(df.columns),
        "preview": json.loads(head.where(pd.notna(head), None).to_json(orient="records")),
        "truncated": total > limit,
    }


@app.get("/api/runs/{run_id}/tables")
def list_tables(run_id: str) -> List[Dict[str, Any]]:
    data_dir = RUNS_DIR / run_id / "data"
    if not data_dir.exists():
        return []
    out = []
    for path in sorted(data_dir.glob("*.csv")):
        try:
            rows = sum(1 for _ in path.open("r", encoding="utf-8-sig")) - 1
        except Exception:                                # noqa: BLE001
            rows = 0
        out.append({"name": path.stem, "rows": max(rows, 0),
                    "url": f"/files/{run_id}/data/{path.name}"})
    return out


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Profile an uploaded spreadsheet.

    Deterministic column profiling only — this is the honest half of Mode 3
    (ROADMAP M6). The mapping and narrative agents are not wired up, so the UI
    presents this as an inspection step rather than pretending to be an AI.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls", ".tsv"):
        raise HTTPException(400, f"Unsupported file type {suffix!r}. "
                                 f"Use CSV, TSV or Excel.")
    target = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    target.write_bytes(await file.read())

    try:
        if suffix in (".csv", ".tsv"):
            df = pd.read_csv(target, sep="\t" if suffix == ".tsv" else ",")
        else:
            df = pd.read_excel(target)
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, f"Could not parse the file: {exc}")

    columns = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        inferred = "unknown"
        if pd.api.types.is_numeric_dtype(series):
            inferred = "number"
        elif pd.api.types.is_datetime64_any_dtype(series):
            inferred = "date"
        elif non_null.size:
            parsed = pd.to_datetime(non_null.head(50), errors="coerce", format="mixed")
            inferred = "date" if parsed.notna().mean() > 0.8 else "text"
        columns.append({
            "name": str(col),
            "inferred_type": inferred,
            "non_null": int(non_null.size),
            "null_pct": round(float(series.isna().mean()), 4),
            "unique": int(non_null.nunique()) if non_null.size else 0,
            "sample": [None if pd.isna(v) else str(v) for v in non_null.head(3)],
        })

    return {
        "filename": file.filename,
        "rows": int(len(df)),
        "columns": columns,
        "stored_as": target.name,
        "note": (
            "Column profiling is deterministic. Automatic mapping to the KPI "
            "data model and the AI narrative layer are not connected in this "
            "build — see ROADMAP M6 and M7."
        ),
    }


@app.get("/files/{run_id}/{path:path}")
def serve_file(run_id: str, path: str):
    run_dir = (RUNS_DIR / run_id).resolve()
    target = (run_dir / path).resolve()
    # Path traversal guard: the resolved target must stay inside the run dir.
    if not str(target).startswith(str(run_dir)) or not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "runs": len(_STATE), "ui": UI_DIR.exists()}


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
else:
    @app.get("/")
    def missing_ui() -> JSONResponse:
        return JSONResponse({"error": f"UI directory not found at {UI_DIR}"}, 500)
