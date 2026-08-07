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
from ..profile.schema import CompanyProfile
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


def _execute(run_id: str, profile: CompanyProfile) -> None:
    run_dir = RUNS_DIR / run_id
    steps: List[str] = []

    def note(msg: str) -> None:
        steps.append(msg)
        _set(run_id, steps=list(steps))

    try:
        _set(run_id, status="running", steps=["Validating profile"])
        note("Selecting KPIs from the library")
        result = run_pipeline(profile, run_dir, quiet=True)
        note("Generating data and reconciling")
        note("Computing metrics and detecting findings")
        note("Rendering dashboard, report, deck and workbook")

        summary = _build_summary(run_id, run_dir, profile)
        summary["steps"] = steps
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
    _POOL.submit(_execute, run_id, profile)
    return {"run_id": run_id, "status": "queued", "company": profile.identity.name}


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
