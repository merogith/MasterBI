"""Executes the stage graph.

The runner is deliberately small: work out what needs doing, do it in
dependency order, record the hashes so the next call can work it out again.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..spec.schema import RunSpec
from . import stages as _stages       # noqa: F401  (registers every stage)
from .cache import STORE, read_hashes, stage_hash, write_hashes
from .graph import STAGES, required_stages

# Rough per-stage cost, in seconds, for a mid-size company. Only used to tell
# the user what a re-run will cost before they commit to it; measured on the
# reference profile rather than guessed.
COST_HINT = {
    "resolve": 0.01, "source": 0.50, "clean": 0.02, "model": 0.02,
    "select": 0.07, "metrics": 0.10, "analyse": 0.03, "visualise": 0.38,
    "charts_png": 2.06, "dashboard": 0.30, "workbook": 0.35,
    "report_pdf": 0.99, "deck_pptx": 0.15, "doc_docx": 0.36,
    "csv_bundle": 0.03, "facts_csv": 0.02, "json_dumps": 0.02,
}


@dataclass
class RunContext:
    spec: RunSpec
    out_dir: Path
    values: Dict[str, Any] = field(default_factory=dict)
    say: Callable[[str], None] = lambda msg: None
    # Set by the `clean` stage; read by the renderers for the methodology
    # appendix. Not a stage output because it describes how a stage ran rather
    # than being an input to anything downstream.
    lineage: Any = None
    # Set by the source stage for uploads: {table: measured|modelled}. Read by
    # `metrics` to decide each result's basis.
    origins: Any = None
    # Tier 2 identity misses on uploaded data — reported, never fatal.
    gate_warnings: Any = None
    # Where `source.uploads` names are resolved from. Uploads are shared
    # across runs — the same file can drive several — so they live beside the
    # run directories rather than inside one. Defaulting here rather than in
    # the stage keeps one answer to "where is that file?"; two produced a run
    # that stored uploads in one place and looked for them in another.
    uploads_dir: Any = None

    def __post_init__(self) -> None:
        if self.uploads_dir is None:
            self.uploads_dir = Path(self.out_dir).parent / "_uploads"

    def get(self, stage_name: str) -> Any:
        if stage_name not in self.values:
            raise KeyError(
                f"stage {stage_name!r} was read before it ran — its consumer is "
                f"missing it from `needs`"
            )
        return self.values[stage_name]

    @property
    def period(self) -> str:
        """Reporting window, as the print deliverables title it."""
        tables = self.values.get("model") or {}
        fin = tables.get("monthly_financials")
        if fin is None or fin.empty:
            return ""
        return f"{fin['month'].iloc[0]} to {fin['month'].iloc[-1]}"


@dataclass
class StagePlan:
    name: str
    digest: str
    dirty: bool
    reason: str


def _plan(spec: RunSpec, out_dir: Path,
          artifacts: Optional[Sequence[str]] = None) -> List[StagePlan]:
    """Hash every stage in dependency order and mark what changed."""
    wanted = list(artifacts) if artifacts is not None else spec.outputs.resolved()
    order = required_stages(wanted)
    previous = read_hashes(out_dir)

    digests: Dict[str, str] = {}
    plan: List[StagePlan] = []
    for name in order:
        st = STAGES[name]
        reads = {section: spec.section(section) for section in st.reads}
        digest = stage_hash(name, reads, [digests[d] for d in st.needs])
        digests[name] = digest

        if previous.get(name) != digest:
            # Distinguish "you changed something" from "this never ran", because
            # the first is the user's edit and the second is just a cold start.
            reason = "changed" if name in previous else "not run before"
            plan.append(StagePlan(name, digest, True, reason))
        else:
            plan.append(StagePlan(name, digest, False, "unchanged"))
    return plan


def plan_rerun(spec: RunSpec, out_dir: Path,
               artifacts: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """What a re-run would do, without doing it.

    This is what lets the UI say "Re-run — 3 stages affected, ~4s" instead of
    making the user click and hope.
    """
    plan = _plan(spec, out_dir, artifacts)
    dirty = [p.name for p in plan if p.dirty]
    reused = [p.name for p in plan if not p.dirty]
    return {
        "dirty": dirty,
        "reused": reused,
        "reasons": {p.name: p.reason for p in plan},
        "estimated_seconds": round(sum(COST_HINT.get(n, 0.1) for n in dirty), 1),
        "artifacts": list(artifacts) if artifacts is not None
        else spec.outputs.resolved(),
    }


@dataclass
class RunResult:
    values: Dict[str, Any]
    ran: List[str]
    skipped: List[str]
    timings: Dict[str, float]
    out_dir: Path

    @property
    def seconds(self) -> float:
        return round(sum(self.timings.values()), 3)


def execute(spec: RunSpec, out_dir: Path, *,
            artifacts: Optional[Sequence[str]] = None,
            say: Optional[Callable[[str], None]] = None,
            uploads_dir: Optional[Path] = None,
            force: bool = False) -> RunResult:
    """Run every stage that needs running, in dependency order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    speak = say or (lambda msg: None)
    ctx = RunContext(spec=spec, out_dir=out_dir, say=speak,
                     uploads_dir=uploads_dir)

    plan = _plan(spec, out_dir, artifacts)
    hashes = read_hashes(out_dir)
    ran: List[str] = []
    skipped: List[str] = []
    timings: Dict[str, float] = {}

    for item in plan:
        st = STAGES[item.name]
        cached = None if force else STORE.get(item.name, item.digest)

        # A clean stage still has to produce its value if it is not in the
        # store, because something downstream may be about to read it. Skipping
        # on "unchanged" alone would hand the next stage a hole.
        if cached is not None and not item.dirty:
            ctx.values[item.name] = cached
            skipped.append(item.name)
            hashes[item.name] = item.digest
            continue

        started = time.perf_counter()
        value = st.fn(ctx)
        timings[item.name] = time.perf_counter() - started

        ctx.values[item.name] = value
        STORE.put(item.name, item.digest, value)
        hashes[item.name] = item.digest
        ran.append(item.name)

    write_hashes(out_dir, hashes)
    (out_dir / "spec.json").write_text(
        spec.model_dump_json(indent=2), encoding="utf-8")

    return RunResult(values=ctx.values, ran=ran, skipped=skipped,
                     timings=timings, out_dir=out_dir)
