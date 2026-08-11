"""The running screen used to lie twice, and both lies were in the engine.

Progress was theatre: the server set one step label, submitted the whole
pipeline, and appended the other four *after* it returned, so the user watched
"Validating profile" for the entire run. Cancel was theatre too: the UI cleared
its poll timer and navigated home while the job ran to completion, holding one
of only two worker threads.

These tests are about the runner's contract, because that is where both fixes
live. The last one is the important one: `on_progress` and `cancel` observe and
stop, and must not be able to change what a run produces.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.pipeline.cache import STORE  # noqa: E402
from kpi_maker.pipeline.graph import STAGES  # noqa: E402
from kpi_maker.pipeline.runner import RunCancelled, execute  # noqa: E402
from kpi_maker.spec.schema import RunSpec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "northwind_saas.json"

# The whole graph is slow and none of these tests are about rendering. The
# dashboard pulls the entire compute spine — resolve through visualise — which
# is every stage that can be reused, cancelled or misreported.
ARTIFACTS = ["dashboard"]


def _spec() -> RunSpec:
    return RunSpec.for_profile(load_profile(SAMPLE))


class Recorder:
    """Collects progress events in the order they were emitted."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def __call__(self, event: Dict[str, Any]) -> None:
        self.events.append(dict(event))

    def states(self, stage: str) -> List[str]:
        return [e["state"] for e in self.events if e["stage"] == stage]

    @property
    def stages(self) -> List[str]:
        seen: List[str] = []
        for e in self.events:
            if e["stage"] not in seen:
                seen.append(e["stage"])
        return seen


def _digests(out: Path) -> Dict[str, str]:
    """Hash every artifact, ignoring the run's own bookkeeping.

    Same exclusions as `tests/spine.py`: `spec.json` and the stage hashes are
    the cache's own records, not output.
    """
    skip = {"spec.json", ".stage_hashes.json"}
    return {
        str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name not in skip
    }


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cold(tmp_path_factory):
    """One cold run, recorded. Reused by the reporting tests — it is not cheap."""
    STORE.clear()
    out = tmp_path_factory.mktemp("progress") / "cold"
    rec = Recorder()
    result = execute(_spec(), out, artifacts=ARTIFACTS, on_progress=rec)
    return rec, result, out


def test_every_stage_reports_running_then_a_terminal_state(cold):
    rec, result, _ = cold
    assert rec.events, "no progress events at all"
    for stage in rec.stages:
        assert rec.states(stage) in (["running", "done"], ["running", "reused"]), \
            f"{stage} reported {rec.states(stage)}"


def test_reported_stages_are_exactly_the_stages_that_ran(cold):
    rec, result, _ = cold
    assert set(rec.stages) == set(result.ran) | set(result.skipped)


def test_events_arrive_in_dependency_order(cold):
    """A stage must not be announced before something it needs.

    The order is the runner's plan, which is `topological_order`, so this also
    catches a future refactor that emits events from somewhere else.
    """
    rec, _, _ = cold
    position = {name: i for i, name in enumerate(rec.stages)}
    for name, at in position.items():
        for need in STAGES[name].needs:
            if need in position:
                assert position[need] < at, f"{name} announced before {need}"


def test_index_and_total_are_consistent(cold):
    rec, _, _ = cold
    total = rec.events[0]["total"]
    assert total == len(rec.stages)
    for event in rec.events:
        assert event["total"] == total
        assert 1 <= event["index"] <= total
    # The index is the position in the plan, so it counts up without gaps.
    assert [e["index"] for e in rec.events if e["state"] == "running"] == \
        list(range(1, total + 1))


def test_the_estimate_falls_and_the_labels_are_human(cold):
    rec, _, _ = cold
    etas = [e["eta_seconds"] for e in rec.events]
    assert etas == sorted(etas, reverse=True), "the estimate went back up"
    assert etas[-1] <= etas[0]
    for event in rec.events:
        assert event["label"] and event["label"] == STAGES[event["stage"]].label
        assert event["elapsed"] >= 0


def test_a_reused_stage_says_reused_not_done(tmp_path):
    """The informative half of a warm re-run, and the half the screen could
    never show: "9 reused, 3 rebuilt" is what the Studio's cost bar promises."""
    STORE.clear()
    out = tmp_path / "warm"
    spec = _spec()
    execute(spec, out, artifacts=ARTIFACTS)

    rec = Recorder()
    second = execute(spec, out, artifacts=ARTIFACTS, on_progress=rec)

    assert second.skipped, "nothing was reused on an unchanged re-run"
    for stage in second.skipped:
        assert rec.states(stage) == ["running", "reused"]
    for stage in second.ran:
        assert rec.states(stage) == ["running", "done"]


# --------------------------------------------------------------------------
# Cancel
# --------------------------------------------------------------------------

def test_cancel_set_before_the_run_stops_at_the_first_stage(tmp_path):
    STORE.clear()
    out = tmp_path / "precancelled"
    stop = threading.Event()
    stop.set()

    rec = Recorder()
    with pytest.raises(RunCancelled) as caught:
        execute(_spec(), out, artifacts=ARTIFACTS, on_progress=rec, cancel=stop)

    assert caught.value.done == 0
    assert rec.events == [], "a cancelled-before-start run announced a stage"
    assert not (out / "dashboard.html").exists()


def test_cancel_mid_run_stops_within_one_stage(tmp_path):
    """Set from another thread, exactly as the API server does it.

    The check is between stages, never inside one, so the guarantee is "at most
    one more stage", not "immediately" — and the UI says so rather than
    pretending otherwise.
    """
    STORE.clear()
    out = tmp_path / "midcancel"
    stop = threading.Event()
    rec = Recorder()

    def watch(event: Dict[str, Any]) -> None:
        rec(event)
        if event["stage"] == "source" and event["state"] == "done":
            stop.set()

    with pytest.raises(RunCancelled) as caught:
        execute(_spec(), out, artifacts=ARTIFACTS, on_progress=watch, cancel=stop)

    finished = [e["stage"] for e in rec.events if e["state"] in ("done", "reused")]
    assert "source" in finished
    # At most one stage ran after the flag went up, and it is the one the
    # exception names as the stage we stopped before.
    assert caught.value.done == len(finished)
    assert caught.value.stage not in finished
    assert len(finished) <= finished.index("source") + 2


def test_a_cancelled_run_writes_no_summary_and_no_deliverable(tmp_path):
    """A cancelled run must not look finished. `summary.json` is the file that
    makes it look finished, both to the results screen and to the disk scan
    that recovers runs after a server restart."""
    STORE.clear()
    out = tmp_path / "nosummary"
    stop = threading.Event()

    def watch(event: Dict[str, Any]) -> None:
        if event["stage"] == "select":
            stop.set()

    with pytest.raises(RunCancelled):
        execute(_spec(), out, artifacts=ARTIFACTS, on_progress=watch, cancel=stop)

    assert not (out / "summary.json").exists()
    assert not (out / "dashboard.html").exists()


def test_completed_stages_survive_a_cancel(tmp_path):
    """The hashes are written in a `finally` precisely for this: a stage either
    ran to completion or it did not, so a cancelled run's finished work is real
    and the next attempt should not redo it."""
    STORE.clear()
    out = tmp_path / "resume"
    spec = _spec()
    stop = threading.Event()

    def watch(event: Dict[str, Any]) -> None:
        if event["stage"] == "model" and event["state"] == "done":
            stop.set()

    with pytest.raises(RunCancelled):
        execute(spec, out, artifacts=ARTIFACTS, on_progress=watch, cancel=stop)

    hashes = json.loads((out / ".stage_hashes.json").read_text(encoding="utf-8"))
    assert {"source", "clean", "model"} <= set(hashes)
    # And the spec that produced them, or the kept work is unreachable: the
    # server re-runs a directory by reading this file back.
    assert (out / "spec.json").exists()

    resumed = execute(spec, out, artifacts=ARTIFACTS)
    assert {"source", "clean", "model"} <= set(resumed.skipped)
    assert (out / "dashboard.html").exists()


# --------------------------------------------------------------------------
# The guard that matters
# --------------------------------------------------------------------------

def test_observing_a_run_cannot_change_what_it_produces(tmp_path):
    """`on_progress` and `cancel` are an observer and a flag. If supplying them
    changed a single byte of output they would not be either of those things,
    and every artifact the engine makes would depend on who was watching."""
    spec = _spec()
    # Four writers rather than one: HTML, the facts CSV, the JSON dumps and the
    # per-table CSV bundle. The print deliverables are excluded from the byte
    # comparison for the same reason `tests/spine.py` excludes them — fpdf2
    # stamps `/CreationDate` and a wall-clock `/ID` into every PDF, so two
    # renders of identical content are never identical files. Their *content*
    # is compared below, which is the part that could actually drift.
    wide = ["dashboard", "facts_csv", "json_dumps", "csv_bundle", "report_pdf"]

    STORE.clear()
    plain = tmp_path / "plain"
    execute(spec, plain, artifacts=wide, on_progress=None, cancel=None)

    STORE.clear()
    watched = tmp_path / "watched"
    execute(spec, watched, artifacts=wide,
            on_progress=Recorder(), cancel=threading.Event())

    before = {k: v for k, v in _digests(plain).items() if k != "report.pdf"}
    # Comparing two empty dicts would pass and prove nothing, which is exactly
    # how a vacuous test gets written.
    assert {"dashboard.html", "facts.csv"} <= set(before) and len(before) > 4
    assert before == {k: v for k, v in _digests(watched).items()
                      if k != "report.pdf"}

    from pypdf import PdfReader
    def pages(path: Path) -> List[str]:
        return [p.extract_text() or "" for p in PdfReader(path).pages]

    assert pages(plain / "report.pdf") == pages(watched / "report.pdf")


# --------------------------------------------------------------------------
# The server side of the same two lies
# --------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    """The API module with its run directory redirected into the tmp tree."""
    from kpi_maker.api import server as api

    monkeypatch.setattr(api, "RUNS_DIR", tmp_path / "runs")
    api.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    api._STATE.clear()
    api._CANCEL.clear()
    return api


def test_cancelling_an_unknown_run_is_idle_not_an_error(server):
    """Cancel on a run that already finished is a no-op, and must say so —
    reporting "cancelling" for a run nobody can stop is the same class of lie
    this item exists to remove."""
    assert server.cancel_run("nope")["status"] == "idle"


def test_a_queued_run_is_cancellable_before_it_starts(server, monkeypatch):
    """The pool has two workers, so a third run waits. Registering the cancel
    event inside the worker would leave that run uncancellable: the user
    presses Cancel, is told there is nothing to cancel, and then watches it run.
    """
    submitted = []
    monkeypatch.setattr(server._POOL, "submit",
                        lambda *args, **kw: submitted.append(args))

    server._submit("queued-run", _spec())
    assert server.cancel_run("queued-run")["status"] == "cancelling"

    # The event handed to the worker is the one the endpoint just set, so the
    # run stops at its first stage rather than starting properly.
    (_fn, _run_id, _spec_arg, event) = submitted[0]
    assert event.is_set()


def test_the_server_reports_cancelled_and_writes_no_summary(server):
    STORE.clear()
    stop = threading.Event()
    stop.set()
    server._set("stopped", status="queued")
    server._CANCEL["stopped"] = stop

    server._execute("stopped", _spec(), stop)

    state = server._get("stopped")
    assert state["status"] == "cancelled"
    assert not (server.RUNS_DIR / "stopped" / "summary.json").exists()
    # And the event is retired, so a re-run under the same id starts clean
    # instead of inheriting a flag that cancels it immediately.
    assert "stopped" not in server._CANCEL


def test_the_server_forwards_real_stage_progress(server):
    """`_execute` used to set one label, submit the whole pipeline, and append
    the other four *after* it returned. Every stage the engine reaches must
    appear in the state the poll endpoint reads."""
    STORE.clear()
    spec = _spec()
    # `artifacts`, not `only`: pydantic ignores unknown keys by default, so the
    # first draft of this line silently asked for all nine deliverables — and
    # dragged kaleido's native subprocess into a test that is about progress
    # events. That is the same shape of bug as a spec field nothing reads.
    spec = RunSpec(**{**spec.model_dump(), "outputs": {"artifacts": ["dashboard"]}})
    assert spec.outputs.resolved() == ["dashboard"]
    server._set("live", status="queued")
    server._execute("live", spec, threading.Event())

    state = server._get("live")
    assert state["status"] == "done", state.get("error")
    progress = state["progress"]
    assert progress["done"] == progress["total"] > 5
    assert {s["state"] for s in progress["stages"]} <= {"done", "reused"}
    assert progress["current"]["label"]
