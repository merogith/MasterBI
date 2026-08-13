"""Executes the stage graph.

The runner is deliberately small: work out what needs doing, do it in
dependency order, record the hashes so the next call can work it out again.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..spec.schema import RunSpec
from . import stages as _stages  # noqa: F401  (registers every stage)
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
    # Caveats this run carries: Tier 2 identity misses on uploaded data, and a
    # sector simulated by a neighbouring archetype. Never fatal, always
    # reported. A list rather than None so any stage can append without first
    # checking whether an earlier one already did — it used to be assigned
    # rather than appended to, so a second writer would have clobbered the first.
    gate_warnings: List[str] = field(default_factory=list)
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
    # Caveats collected during the run. Carried here rather than left on the
    # context so a caller that only holds the result can still report them.
    warnings: List[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return round(sum(self.timings.values()), 3)


class RunCancelled(Exception):
    """Raised when the caller's cancel event was set between two stages.

    Deliberately not a subclass of anything the pipeline already catches: a
    cancelled run is not a failed run, and the two must not end up looking the
    same to the user. Carries the stage the run stopped before, because "we
    stopped at Rendering the PDF report" is the only useful thing to say.
    """

    def __init__(self, stage: str, done: int, total: int) -> None:
        super().__init__(f"cancelled before {stage} ({done} of {total} stages done)")
        self.stage = stage
        self.done = done
        self.total = total


def execute(spec: RunSpec, out_dir: Path, *,
            artifacts: Optional[Sequence[str]] = None,
            say: Optional[Callable[[str], None]] = None,
            uploads_dir: Optional[Path] = None,
            force: bool = False,
            on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
            cancel: Optional[Event] = None) -> RunResult:
    """Run every stage that needs running, in dependency order.

    `on_progress` is called before and after every stage — including reused
    ones, because distinguishing "rebuilt" from "reused" is the informative
    part of a warm re-run. `cancel` is a `threading.Event` checked between
    stages; a stage is never interrupted part-way, so the worst-case latency is
    one stage (`charts_png`, at roughly two seconds).

    Both are optional and neither can change what the run produces: the
    callback only observes, and the event only stops the loop.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    speak = say or (lambda msg: None)
    ctx = RunContext(spec=spec, out_dir=out_dir, say=speak,
                     uploads_dir=uploads_dir)

    plan = _plan(spec, out_dir, artifacts)
    hashes = read_hashes(out_dir)
    ran: List[str] = []
    skipped: List[str] = []
    timings: Dict[str, float] = {}

    total = len(plan)
    began = time.perf_counter()
    # What the run still has to build, in seconds. Only the dirty stages count:
    # a warm re-run that reuses fourteen of seventeen stages should not quote
    # the cold-start number. Decremented as each dirty stage finishes so the
    # estimate falls monotonically instead of being recomputed from a guess.
    remaining = sum(COST_HINT.get(p.name, 0.1) for p in plan if p.dirty)

    def emit(item: StagePlan, index: int, state: str) -> None:
        if on_progress is None:
            return
        on_progress({
            "stage": item.name,
            "label": STAGES[item.name].label,
            "index": index,
            "total": total,
            "state": state,
            "elapsed": round(time.perf_counter() - began, 2),
            "eta_seconds": round(max(remaining, 0.0), 1),
        })

    try:
        for position, item in enumerate(plan):
            if cancel is not None and cancel.is_set():
                raise RunCancelled(item.name, position, total)

            st = STAGES[item.name]
            cached = None if force else STORE.get(item.name, item.digest)

            # A clean stage still has to produce its value if it is not in the
            # store, because something downstream may be about to read it.
            # Skipping on "unchanged" alone would hand the next stage a hole.
            reuse = cached is not None and not item.dirty
            emit(item, position + 1, "running")

            if reuse:
                ctx.values[item.name] = cached
                skipped.append(item.name)
                hashes[item.name] = item.digest
                emit(item, position + 1, "reused")
                continue

            started = time.perf_counter()
            value = st.fn(ctx)
            timings[item.name] = time.perf_counter() - started

            ctx.values[item.name] = value
            STORE.put(item.name, item.digest, value)
            hashes[item.name] = item.digest
            ran.append(item.name)
            remaining -= COST_HINT.get(item.name, 0.1)
            emit(item, position + 1, "done")
    finally:
        # A cancelled run's finished stages are genuinely finished — a stage
        # either ran to completion or it did not — so recording their hashes
        # lets the next attempt resume instead of starting over. This must
        # still happen exactly once on the success path, or the warm-partial
        # equals cold-full guarantee breaks.
        #
        # `spec.json` goes with them: it is the run's input contract, and it is
        # what the server reads to re-run a directory. Written only on success,
        # the hashes above would be unreachable — the kept work would exist and
        # nothing could ask for it. Neither file is an artifact, which is why
        # `tests/spine.py` excludes both from its byte comparison.
        write_hashes(out_dir, hashes)
        (out_dir / "spec.json").write_text(
            spec.model_dump_json(indent=2), encoding="utf-8")

    return RunResult(values=ctx.values, ran=ran, skipped=skipped,
                     timings=timings, out_dir=out_dir,
                     warnings=list(ctx.gate_warnings))
