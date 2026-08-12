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
import os
import shutil
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..cli import load_profile, run_pipeline
from ..contract.schemas import FACT_SCHEMAS
from ..design.contrast import AA_TEXT, GRAPHICAL, MIN_DELTA_E, ColourError, ratio
from ..design.logo import LogoError, load_logo
from ..design.palette import derive_tokens
from ..formula import FormulaError, describe_functions, evaluate, validate
from ..formula.evaluate import RowResolver, SeriesResolver
from ..ingest import detect_shape, profile_table, read_any, shape_catalog
from ..ingest.derive import derive_profile_fields
from ..ingest.quality import build_report
from ..insight.detectors import DETECTOR_NAMES
from ..kpi.schema import user_kpi
from ..kpi.selection import load_library, unknown_kpi_ids
from ..kpi.user_library import delete_user_kpi, save_user_kpi, user_kpi_ids
from ..pipeline.runner import RunCancelled, plan_rerun
from ..prep import describe_ops
from ..prep.model import preview_column
from ..prep.recipe import preview_recipe
from ..profile.schema import CompanyProfile
from ..render.sections import REGISTRY as SECTION_REGISTRY
from ..render.sections import default_order
from ..spec.schema import ALL_ARTIFACTS, PATCHABLE_SECTIONS, RunSpec
from ..store import COLUMNS as STORE_COLUMNS
from ..store import store as _open_store
from ..survey import as_json as survey_json
from ..survey import build_profile, surprise_profile
from ..viz.charts import default_exhibits

ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = ROOT / "samples"
# Overridable by environment so a test can point the *real* server at a
# throwaway tree — the browser smoke test drives the same process a user would,
# and must not write into the developer's own history. The desktop build needs
# the same hook for a different reason: runs belong in a user data directory,
# not beside a read-only executable.
RUNS_DIR = Path(os.environ.get("MASTERBI_RUNS_DIR") or ROOT / "runs")
UI_DIR = ROOT / "ui"
# The rewritten front end (1.1b): Vite build output, inside the package because
# that is what ships — no Node at runtime, and 1.3's one-file executable bundles
# it. This is what a user gets, when it has been built.
#
# `ui/` is still here, and not as a fallback anyone should rely on: the GitHub
# Pages demo is produced from it by `tools/build_pages.py`, which patches its
# `index.html` and serves `app.js` behind `static_shim.js`. Deleting it would
# take the hosted demo with it, so it goes when 1.2 builds the Pages bundle
# from this same source. `MASTERBI_UI=legacy` selects it meanwhile, and it is
# also what serves a checkout where the bundle was never built.
UI_DIST_DIR = Path(__file__).resolve().parents[1] / "ui_dist"
SERVE_LEGACY_UI = os.environ.get("MASTERBI_UI") == "legacy"
UPLOADS_DIR = RUNS_DIR / "_uploads"

RUNS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# In-flight state: live progress, the current summary, tracebacks. The durable
# facts about a run — its mode, timings and outcome — are written through to the
# store, so this is a cache in front of it rather than the source of truth.
_STATE: Dict[str, Dict[str, Any]] = {}
# One cancel event per in-flight run, added by `_execute` before the pipeline
# starts and removed when it stops however it stops. Absent means "nothing to
# cancel" rather than "cancel failed", which is why the endpoint treats a
# missing id as a no-op on an already-finished run.
_CANCEL: Dict[str, Event] = {}
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


def _store():
    """The run index for the *current* `RUNS_DIR`.

    Resolved per call, never captured at import: `tools/build_pages.py` rebinds
    `RUNS_DIR` to the site tree and the tests rebind it to a tmp directory. A
    module-level handle would put a `runs.db` in whichever of those ran first.
    """
    return _open_store(RUNS_DIR)


def _set(run_id: str, **fields) -> None:
    with _LOCK:
        _STATE.setdefault(run_id, {})
        _STATE[run_id].update(fields)
    # Write through whatever the index has a column for. Progress events carry
    # none — they set `progress` alone — so the several-times-a-second stage
    # reports never reach SQLite, and the four lifecycle transitions all do.
    durable = {k: v for k, v in fields.items() if k in STORE_COLUMNS}
    if durable:
        _store().upsert(run_id, **durable)


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


def _submit(run_id: str, spec: RunSpec) -> None:
    """Queue a run, with its cancel event registered *before* it is queued.

    The pool has two workers, so a third run waits. Creating the event inside
    `_execute` would leave that waiting run uncancellable — the user would
    press Cancel, get told there was nothing to cancel, and then watch it start.
    """
    # A new attempt has no outcome yet. Without this, re-running a cancelled run
    # leaves it labelled with the stage it stopped at long after it finished —
    # a row reading "done" beside "cancelled at charts_png". Half a stale fact
    # is worse than none, and the same applies to a previous run's error.
    _set(run_id, error=None, cancelled_stage=None, finished_at=None,
         traceback=None)

    cancel = Event()
    with _LOCK:
        _CANCEL[run_id] = cancel
    _POOL.submit(_execute, run_id, spec, cancel)


def _execute(run_id: str, spec: RunSpec, cancel: Event) -> None:
    run_dir = RUNS_DIR / run_id
    # Every stage the run has reported on, newest state per stage, in the order
    # the engine reached them. A dict rather than a list because a stage
    # reports twice — running, then done or reused — and the second report
    # replaces the first rather than appending to it.
    stages: Dict[str, Dict[str, Any]] = {}

    def progress(event: Dict[str, Any]) -> None:
        stages[event["stage"]] = event
        _set(run_id, progress={
            "current": event,
            "stages": list(stages.values()),
            "done": sum(1 for e in stages.values() if e["state"] != "running"),
            "total": event["total"],
            "eta_seconds": event["eta_seconds"],
            "elapsed": event["elapsed"],
        })

    try:
        _set(run_id, status="running", progress=None)
        result = run_pipeline(spec.profile, run_dir, quiet=True, spec=spec,
                              on_progress=progress, cancel=cancel)

        summary = _build_summary(run_id, run_dir, spec.profile)
        summary["stages_ran"] = result.get("ran", [])
        summary["stages_reused"] = result.get("skipped", [])
        summary["seconds"] = result.get("seconds")
        (run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
        _set(run_id, status="done", finished_at=_now(), summary=summary,
             seconds=summary["seconds"], stages_ran=summary["stages_ran"],
             stages_reused=summary["stages_reused"])
    except RunCancelled as stop:
        # Still no summary.json: that file is what makes a run look *finished*,
        # to the results screen and to the reconcile pass alike. A cancelled run
        # is not a short run. It is the index, not the artifact directory, that
        # remembers it — which is why cancelling one no longer erases it.
        _set(run_id, status="cancelled", cancelled_stage=stop.stage,
             error=str(stop), finished_at=_now())
    except Exception as exc:                             # noqa: BLE001
        _set(run_id, status="error", error=str(exc),
             traceback=traceback.format_exc(limit=6), finished_at=_now())
    finally:
        with _LOCK:
            # Only retire our own event. A re-run of the same id queued while
            # this one was finishing has already registered its own, and
            # popping that would make the new run uncancellable.
            if _CANCEL.get(run_id) is cancel:
                del _CANCEL[run_id]


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
         company=profile.identity.name, started_at=_now(), progress=None)
    _store().add_version(run_id, json.loads(spec.model_dump_json()),
                         author="user", message=f"created from {req.mode}")
    _submit(run_id, spec)
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


@app.get("/api/runs/{run_id}/spec/versions")
def list_spec_versions(run_id: str) -> List[Dict[str, Any]]:
    """Every spec this run has actually built from, oldest first.

    Metadata only. The specs themselves are large and the caller usually wants
    the list — one version is `?seq=` below.
    """
    return _store().versions(run_id)


@app.get("/api/runs/{run_id}/spec/versions/{seq}")
def get_spec_version(run_id: str, seq: int) -> Dict[str, Any]:
    version = _store().version(run_id, seq)
    if version is None:
        raise HTTPException(404, f"No version {seq} for run {run_id}")
    return version


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
         company=spec.profile.identity.name, started_at=_now(), progress=None)
    # The re-run is about to overwrite the artifacts the previous spec produced,
    # and `spec.json` was overwritten by whatever edit prompted it. Without this
    # row nothing can say what the outgoing dashboard was built from. A re-run
    # with nothing dirty rebuilds nothing, so it replaces nothing and is not a
    # version — otherwise leaning on the button fills the history with rows that
    # each produced the same artifacts as the one before.
    if report["dirty"]:
        _store().add_version(
            run_id, json.loads(spec.model_dump_json()), author="user",
            message=f"re-run, rebuilding {len(report['dirty'])} stages")
    _submit(run_id, spec)
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
        from ..kpi.selection import load_all_known
        from ..metrics.engine import MetricContext, _Evaluator

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

    This is the only upload route. It returns everything the Source panel and
    the "Bring your data" screen need to show the user what they have and what
    it could become — and every suggestion is an offer, not a change.
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
        # The key this file occupies once adopted as a source. Returned rather
        # than left for the UI to derive: `load_uploads` decides it, and two
        # implementations of that rule would drift.
        "table_key": stored.stem,
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
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))

    # A step may target a table this run does not have yet — the usual case
    # right after adopting an upload, where the recipe names the new file's
    # columns but the run still holds the previous source's data. That is a
    # sequencing state, not a bad recipe, so say so instead of failing.
    pending = sorted({s.table for s in recipe.active
                      if s.table and s.table not in tables})
    if pending:
        return {
            "table": body.table, "steps": [], "preview": [],
            "pending_tables": pending,
            "summary": (f"Applies to {', '.join(pending)}, which arrives when you "
                        f"re-run with the new source."),
        }

    try:
        return {**preview_recipe(tables, recipe, table=body.table),
                "pending_tables": []}
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))


@app.get("/api/runs/{run_id}/lineage")
def get_lineage(run_id: str) -> Dict[str, Any]:
    """What was done to this run's data. Reads the recipe from the stored spec."""
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
    origins = dict.fromkeys(spec.source.fill_gaps, "modelled")
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
        "sections": [{"id": sid, "title": SECTION_REGISTRY[sid].title}
                     for sid in default_order()],
        "exhibits": default_exhibits(),
        "widths": ["half", "full"],
    }


# --------------------------------------------------------------------------
# The AI layer. Every route here is inert unless the user has turned it on.
# --------------------------------------------------------------------------

class ChangeRequest(BaseModel):
    """One accepted hunk from the diff review."""
    path: str
    value: Any = None


class ApplyRequest(BaseModel):
    changes: List[ChangeRequest]


@app.get("/api/ai/status")
def ai_status() -> Dict[str, Any]:
    """Whether a model could run, and what it would cost per million tokens.

    Answered without constructing a client, so the studio can ask on every page
    load. When the answer is no it carries the sentence that says what to do
    about it rather than a bare false.
    """
    from ..ai.client import availability
    from ..ai.meter import PRICES
    from ..spec.schema import NARRATABLE_SECTIONS

    state = availability()
    return {**state, "prices": {m: {"input": i, "output": o}
                                for m, (i, o) in PRICES.items()},
            "default_model": "claude-opus-5",
            "narratable_sections": list(NARRATABLE_SECTIONS)}


@app.post("/api/ai/estimate/{run_id}")
def ai_estimate(run_id: str) -> Dict[str, Any]:
    """Price the two requests before either is sent.

    ROADMAP M7 asks for metering "surfaced to the user before they commit", and
    a receipt after the fact is not that. This builds the exact prompts the
    narrator and the planner would send and counts them, so the number in the
    studio is the number that will be spent rather than a guess at it.
    """
    from ..ai.client import AIUnavailable, build_client
    from ..ai.meter import estimate
    from ..ai.narrator import build_request as narrator_request
    from ..ai.planner import build_request as planner_request
    from ..ai.planner import catalog

    spec = _load_spec(run_id)
    # Built first, deliberately. Assembling the prompts means re-running the
    # compute spine, and doing that only to discover there is no key would
    # spend a second of the user's time to tell them something the status
    # endpoint already knew.
    try:
        client = build_client(spec.ai.model)
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc))

    try:
        payload = _run_inputs(run_id, spec)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))

    requests = {
        "plan": planner_request(spec, catalog(spec.profile)),
        "narrate": narrator_request(
            spec.profile, payload["results"], payload["findings"],
            payload["contents"], payload["period"],
            max_paragraphs=spec.ai.max_paragraphs),
    }
    try:
        return estimate(client, requests, spec.ai.model)
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/ai/plan/{run_id}")
def ai_plan(run_id: str) -> Dict[str, Any]:
    """Ask for a RunSpec patch. Nothing is applied.

    The response carries rejected changes alongside accepted ones so the studio
    can show what the planner wanted and why it was refused — a suggestion that
    silently disappears is indistinguishable from one that was never made.
    """
    from ..ai.client import AIUnavailable
    from ..ai.planner import propose

    spec = _load_spec(run_id)
    try:
        return propose(spec).as_dict()
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/ai/apply/{run_id}")
def ai_apply(run_id: str, body: ApplyRequest) -> Dict[str, Any]:
    """Write the hunks the user accepted, and report what a re-run would rebuild.

    Applied here rather than by the studio assembling a nested merge patch: the
    planner speaks in dotted paths, and turning those into nested dictionaries
    in JavaScript would put the one step that must not go wrong in the layer
    with no schema. `apply_changes` validates, so an accepted set that does not
    compose is a 422 and not a broken spec on disk.
    """
    from ..ai.planner import Change, apply_changes

    current = _load_spec(run_id)
    changes = [Change(path=c.path, value=c.value) for c in body.changes]
    illegal = [c.path for c in changes
               if c.path.split(".")[0] not in PATCHABLE_SECTIONS]
    if illegal:
        raise HTTPException(422, f"not patchable: {', '.join(illegal)}")
    try:
        merged = apply_changes(current, changes)
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(422, str(exc))
    payload = json.loads(merged.model_dump_json())
    result = put_spec(run_id, payload)
    # Recorded here rather than in `put_spec`, which the studio also calls on
    # every debounced keystroke. What makes this one worth keeping is that the
    # author was the planner: the paths are the only record of what the model
    # changed once `spec.json` has been overwritten.
    _store().add_version(run_id, payload, author="planner",
                         message=", ".join(c.path for c in changes))
    return result


@app.get("/api/ai/usage/{run_id}")
def ai_usage(run_id: str) -> Dict[str, Any]:
    """What this run actually spent, or an empty report if it spent nothing."""
    path = RUNS_DIR / run_id / "ai.json"
    if not path.exists():
        return {"calls": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
                "notes": [], "detail": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _run_inputs(run_id: str, spec: RunSpec) -> Dict[str, Any]:
    """Recompute the compute spine for a finished run, without rendering.

    The estimate needs the same results, findings and section briefs the
    narrator would see, and the stage cache is process-local — a server
    restarted since the run has nothing to reuse. Running the compute half
    costs ~10% of a full run (measured in P0) and is the honest way to price
    the request that will actually be sent.
    """
    from ..pipeline.runner import execute
    from ..render.sections import SectionContext
    from ..render.sections import build as build_sections

    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise FileNotFoundError("Run not found")
    # `json_dumps` is what pulls `analyse` into the walk. Asking for
    # `facts_csv` alone stops at `metrics` — the graph pruning correctly, and
    # leaving the findings this needs unbuilt.
    result = execute(spec, run_dir, artifacts=["facts_csv", "json_dumps"],
                     uploads_dir=UPLOADS_DIR)
    values = result.values
    ctx = SectionContext(
        profile=values["resolve"], kpi_set=values["select"],
        results=values["metrics"], findings=values["analyse"],
        period="")
    wanted = spec.ai.resolve_narrate_sections()
    return {"results": values["metrics"], "findings": values["analyse"],
            "contents": build_sections(ctx, wanted), "period": ""}


class BrandRequest(BaseModel):
    primary: Optional[str] = None
    accent: Optional[str] = None
    logo_path: Optional[str] = None


@app.post("/api/design/preview")
def preview_design(req: BrandRequest) -> Dict[str, Any]:
    """What a brand colour will actually do, before a run is spent on it.

    Returns both palettes with every adjustment the derivation made, so the
    Studio can show the original and the applied swatch side by side. A colour
    that gets moved should be visibly moved — a silent correction is how a user
    ends up believing their brand is on the page when it is not.
    """
    try:
        palettes = {mode: derive_tokens(req.primary, mode, req.accent)
                    for mode in ("light", "dark")}
    except ColourError as exc:
        raise HTTPException(400, str(exc))

    logo: Dict[str, Any] = {"path": req.logo_path, "ok": req.logo_path is None}
    if req.logo_path:
        try:
            loaded = load_logo(req.logo_path, UPLOADS_DIR)
            logo.update(ok=True, data_uri=loaded.data_uri(), mime=loaded.mime)
        except LogoError as exc:
            logo.update(ok=False, error=str(exc))

    out = {mode: p.as_dict() for mode, p in palettes.items()}
    for mode, p in palettes.items():
        surface, page = p.tokens["surface"], p.tokens["page"]
        out[mode]["against_surface"] = round(ratio(p.tokens["series_1"], surface), 2)
        out[mode]["heading_ratio"] = round(
            ratio(p.tokens.get("heading_accent", p.tokens["series_1"]), page), 2)
    return {"palettes": out, "logo": logo,
            "thresholds": {"text": AA_TEXT, "graphical": GRAPHICAL,
                           "separation": MIN_DELTA_E}}


@app.get("/api/runs")
def list_runs() -> List[Dict[str, Any]]:
    """Every run this installation knows about, newest first.

    The index is the source, not a glob of `summary.json`: that scan could only
    see runs that finished, so it reported every recovered run as mode
    "restored" with no start time, and dropped cancelled and failed runs
    entirely along with the work 0.6 kept on disk for them.
    """
    index = _store()
    # Picks up directories this server did not create — a CLI run, or a
    # `runs.db` that was deleted while the artifacts stayed.
    index.reconcile(RUNS_DIR)
    with _LOCK:
        live = {k: dict(v) for k, v in _STATE.items()}

    out: List[Dict[str, Any]] = []
    for row in index.list():
        run_id = row["run_id"]
        state = live.pop(run_id, None)
        if state is not None:
            # Only ahead of the index inside a single write, but prefer it so a
            # run cannot read as queued while its first stage is running.
            row = {**row, **{k: v for k, v in state.items()
                             if k in STORE_COLUMNS}}
        elif not (RUNS_DIR / run_id).exists():
            # The artifacts were deleted from underneath the index. Say so; a
            # run that quietly vanishes from history is the bug being fixed.
            row["status"] = "missing"
        out.append(_run_row(run_id, row))

    # A run that reached `_STATE` but not yet the index cannot normally exist,
    # since `_set` writes through before returning. Carried anyway so a future
    # write-through failure loses durability, not the run.
    for run_id, state in live.items():
        out.append(_run_row(run_id, state))

    return sorted(out, key=lambda r: r.get("started_at") or "", reverse=True)


def _run_row(run_id: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """The history drawer's shape. `cancelled_stage` is what a resume starts from."""
    return {
        "run_id": run_id,
        "status": source.get("status"),
        "company": source.get("company"),
        "mode": source.get("mode"),
        "started_at": source.get("started_at"),
        "finished_at": source.get("finished_at"),
        "cancelled_stage": source.get("cancelled_stage"),
        # A re-run reads `spec.json`, so a run that failed before writing one
        # cannot be resumed. The server knows; offering the button anyway and
        # letting it 404 would be a working-looking control that does nothing.
        "resumable": (RUNS_DIR / run_id / "spec.json").exists(),
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    state = _get(run_id)
    if state is not None:
        return state

    summary_path = RUNS_DIR / run_id / "summary.json"
    if summary_path.exists():
        return {"run_id": run_id, "status": "done",
                "summary": json.loads(summary_path.read_text(encoding="utf-8"))}
    # No summary means the run did not finish — cancelled, or failed. Before the
    # index that was indistinguishable from never having existed, so the answer
    # was a 404 for a run whose stages were sitting on disk.
    row = _store().get(run_id)
    if row is not None:
        return row
    raise HTTPException(404, "Run not found")


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> Dict[str, Any]:
    """Ask a running pipeline to stop at its next stage boundary.

    Fire and forget: the caller does not wait, because the engine finishes the
    stage it is inside first and that can take a couple of seconds. The reply
    says whether anything was actually signalled, so a Cancel on a run that has
    already finished reads as `"idle"` rather than pretending to have stopped it.
    """
    with _LOCK:
        event = _CANCEL.get(run_id)
    if event is None:
        return {"status": "idle", "run_id": run_id}
    event.set()
    return {"status": "cancelling", "run_id": run_id}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> Dict[str, str]:
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    # Before the row, so a crash between the two leaves a row pointing at no
    # directory — which lists as "missing" — rather than a directory nothing
    # indexes, which the next reconcile would silently resurrect.
    _store().delete(run_id)
    with _LOCK:
        _STATE.pop(run_id, None)
        # Deleting a run mid-flight should stop it too, and must clear the
        # event either way: a re-run under the same id would otherwise inherit
        # a set flag and cancel itself before its first stage.
        stale = _CANCEL.pop(run_id, None)
    if stale is not None:
        stale.set()
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


if not SERVE_LEGACY_UI and UI_DIST_DIR.exists():
    # Registered last, so it cannot shadow `/api/*` or `/files/*` — FastAPI
    # matches routes in declaration order.
    @app.get("/{path:path}")
    def serve_app(path: str):
        """Serve the bundle, and the shell for anything that is not a file.

        Client-side routing means `/samples` and `/runs/abc` are real URLs the
        user can reload or link to, but they are not files on disk. A static
        mount answers 404 for them, which is what makes hand-rolled SPA routing
        appear to work until the first refresh.
        """
        root = UI_DIST_DIR.resolve()
        target = (root / path).resolve()
        if path and target.is_file() and str(target).startswith(str(root)):
            return FileResponse(target)
        return FileResponse(root / "index.html")

elif UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
else:
    @app.get("/")
    def missing_ui() -> JSONResponse:
        return JSONResponse({"error": f"UI directory not found at {UI_DIR}"}, 500)
