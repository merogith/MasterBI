"""History used to be a guess reconstructed from a directory listing.

`_STATE` is a module-level dict, so a restart took every run's mode, start time
and outcome with it. What survived was whatever `list_runs` could infer by
globbing `runs/*/summary.json`, and that scan could only ever see runs that
finished. Three things followed, and they are the first three tests here:

  * every recovered run reported mode "restored", so sample, survey, upload and
    re-run collapsed into one word describing the recovery rather than the run;
  * `started_at` was `None` and the list sorts on it, so everything from before
    the restart sank below everything after it, in filesystem order;
  * cancelled and failed runs disappeared completely — 0.6 stopped writing
    `summary.json` for a cancelled run, correctly, because that file is what
    makes a run look finished, but the recovery scan keyed on exactly that file.
    The user was told their completed stages were kept and then could not find
    them.

The last one is the one that matters most: it is a regression 0.6 introduced,
and the promise it breaks is one the UI makes out loud.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.store import reset_cache, store  # noqa: E402
from kpi_maker.store.runs import _MIGRATIONS, Store  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The API module with its run directory redirected into the tmp tree.

    `reset_cache` on the way out because the store holds an open SQLite handle;
    leaving it open across the tmp directory's removal fails the teardown on
    Windows, where an open file cannot have its directory deleted.
    """
    from kpi_maker.api import server as api

    monkeypatch.setattr(api, "RUNS_DIR", tmp_path / "runs")
    api.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    api._STATE.clear()
    api._CANCEL.clear()
    yield api
    api._STATE.clear()
    reset_cache()


def _finished_run(api, run_id: str, *, mode: str, company: str,
                  started_at: str) -> Path:
    """A run that ran to completion, artifacts and all, the way `_execute` does."""
    run_dir = api.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"run_id": run_id, "company": company, "seconds": 1.5,
                    "stages_ran": ["ingest"], "stages_reused": []}),
        encoding="utf-8")
    api._set(run_id, status="queued", mode=mode, company=company,
             started_at=started_at, progress=None)
    api._set(run_id, status="done", finished_at="2026-01-01T00:00:10+00:00",
             seconds=1.5, stages_ran=["ingest"], stages_reused=[])
    return run_dir


def _restart(api) -> None:
    """Everything a process restart destroys, and nothing it does not."""
    api._STATE.clear()
    api._CANCEL.clear()


# --------------------------------------------------------------------------
# The four things a restart used to lose
# --------------------------------------------------------------------------

def test_mode_and_start_time_survive_a_restart(api):
    """A survey run must still say "survey" after a restart, at its real time.

    Before the index this read `{"mode": "restored", "started_at": None}` — the
    word described how the run was recovered, not how it was started, and the
    missing timestamp sorted it below every run in the current session.
    """
    _finished_run(api, "aaa", mode="survey", company="Acme",
                  started_at="2026-01-01T00:00:00+00:00")
    _restart(api)

    row = next(r for r in api.list_runs() if r["run_id"] == "aaa")
    assert row["mode"] == "survey"
    assert row["started_at"] == "2026-01-01T00:00:00+00:00"
    assert row["status"] == "done"


def test_a_cancelled_run_is_still_listed_after_a_restart(api):
    """The 0.6 regression: cancelling a run erased it.

    A cancelled run writes no `summary.json` — deliberately, it did not finish —
    and the old recovery scan globbed exactly that file, so the run vanished
    along with the completed stages 0.6 kept on disk to make resuming cheap.
    The stage it stopped before is part of the answer: that is what a resume
    starts from, and what the drawer needs in order to offer one.
    """
    run_dir = api.RUNS_DIR / "bbb"
    run_dir.mkdir(parents=True)
    api._set("bbb", status="queued", mode="sample", company="Northwind",
             started_at="2026-01-02T00:00:00+00:00")
    api._set("bbb", status="cancelled", cancelled_stage="visualise",
             error="cancelled before visualise",
             finished_at="2026-01-02T00:00:05+00:00")
    _restart(api)

    listed = {r["run_id"]: r for r in api.list_runs()}
    assert "bbb" in listed, "a cancelled run must not disappear from history"
    assert listed["bbb"]["status"] == "cancelled"
    assert listed["bbb"]["cancelled_stage"] == "visualise"
    assert listed["bbb"]["mode"] == "sample"


def test_a_failed_run_is_still_listed_after_a_restart(api):
    """Same erasure, same fix, and the message is why the run is worth listing."""
    (api.RUNS_DIR / "ccc").mkdir(parents=True)
    api._set("ccc", status="queued", mode="upload", company="Widgets",
             started_at="2026-01-03T00:00:00+00:00")
    api._set("ccc", status="error", error="no numeric columns",
             finished_at="2026-01-03T00:00:02+00:00")
    _restart(api)

    listed = {r["run_id"]: r for r in api.list_runs()}
    assert listed["ccc"]["status"] == "error"
    assert api._store().get("ccc")["error"] == "no numeric columns"


def test_an_unfinished_run_is_addressable_after_a_restart(api):
    """`GET /api/runs/{id}` used to 404 for a run whose stages were on disk.

    The drawer can list it; opening it must not then say it never existed.
    """
    (api.RUNS_DIR / "ddd").mkdir(parents=True)
    api._set("ddd", status="cancelled", mode="rerun", company="Acme",
             cancelled_stage="render", started_at="2026-01-04T00:00:00+00:00")
    _restart(api)

    got = api.get_run("ddd")
    assert got["status"] == "cancelled"
    assert got["cancelled_stage"] == "render"


# --------------------------------------------------------------------------
# The index against the directories it indexes
# --------------------------------------------------------------------------

def test_a_run_whose_directory_was_deleted_reads_as_missing(api):
    """Deleted artifacts make a run "missing", not absent.

    A row with nothing behind it is a fact worth reporting — the user deleted
    the folder, or a sync did. Dropping it silently is how history lies.
    """
    _finished_run(api, "eee", mode="sample", company="Acme",
                  started_at="2026-01-05T00:00:00+00:00")
    _restart(api)
    import shutil
    shutil.rmtree(api.RUNS_DIR / "eee")

    row = next(r for r in api.list_runs() if r["run_id"] == "eee")
    assert row["status"] == "missing"


def test_reconcile_indexes_directories_the_index_never_saw(api):
    """Delete `runs.db` and you lose the history, not the work.

    Also the CLI's case: it writes the same directory layout without ever
    touching the index, and those runs still belong in the drawer.
    """
    run_dir = api.RUNS_DIR / "fff"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({"company": "Legacy Co", "seconds": 3.0,
                    "stages_ran": ["ingest", "render"], "stages_reused": []}),
        encoding="utf-8")

    listed = {r["run_id"]: r for r in api.list_runs()}
    assert listed["fff"]["company"] == "Legacy Co"
    # No record of how it was started ever existed for these, and inventing one
    # would be the same lie in a new place.
    assert listed["fff"]["mode"] == "restored"
    # A real ordering, from the file's mtime, rather than the None that used to
    # sink every recovered run to the bottom of the list.
    assert listed["fff"]["started_at"] is not None


def test_a_live_run_beats_the_index(api):
    """Mid-flight state is fresher than the last committed row."""
    (api.RUNS_DIR / "ggg").mkdir(parents=True)
    api._set("ggg", status="queued", mode="sample", company="Acme",
             started_at="2026-01-06T00:00:00+00:00")
    api._store().upsert("ggg", status="queued")
    api._set("ggg", status="running")

    row = next(r for r in api.list_runs() if r["run_id"] == "ggg")
    assert row["status"] == "running"


def test_requeuing_clears_the_previous_attempts_outcome(api, monkeypatch):
    """A resumed run must stop advertising the cancellation it recovered from.

    Caught by booting the server rather than by a unit test: cancel a run,
    restart, re-run it to completion, and the history row read `"status":
    "done"` next to `"cancelled_stage": "charts_png"`.
    """
    (api.RUNS_DIR / "ooo").mkdir(parents=True)
    api._set("ooo", status="cancelled", mode="sample", company="Acme",
             cancelled_stage="charts_png", error="cancelled before charts_png",
             started_at="2026-01-09T00:00:00+00:00")

    # The queueing half of `_submit` is what is under test; the pipeline it
    # would start is not.
    monkeypatch.setattr(api._POOL, "submit", lambda *a, **k: None)
    api._submit("ooo", spec=None)

    row = next(r for r in api.list_runs() if r["run_id"] == "ooo")
    assert row["cancelled_stage"] is None
    assert api._store().get("ooo")["error"] is None


def test_deleting_a_run_deletes_its_row(api):
    _finished_run(api, "hhh", mode="sample", company="Acme",
                  started_at="2026-01-07T00:00:00+00:00")
    api.delete_run("hhh")
    _restart(api)

    assert api._store().get("hhh") is None
    assert all(r["run_id"] != "hhh" for r in api.list_runs())


def test_progress_events_do_not_write_to_the_index(api):
    """Stage reports arrive several times a second and carry no durable field.

    `_set` writes through only what the index has a column for, so the common
    case never reaches SQLite. If this fails, every run is doing hundreds of
    pointless commits.
    """
    api._set("iii", status="running", mode="sample", company="Acme",
             started_at="2026-01-08T00:00:00+00:00")
    before = api.RUNS_DIR.joinpath("runs.db").stat().st_mtime_ns
    for index in range(50):
        api._set("iii", progress={"current": {"stage": f"s{index}"}})
    assert api.RUNS_DIR.joinpath("runs.db").stat().st_mtime_ns == before


# --------------------------------------------------------------------------
# What each set of artifacts was built from
# --------------------------------------------------------------------------

@pytest.fixture
def spec_run(api, monkeypatch):
    """A run directory with a real spec on disk and no pipeline behind it."""
    from kpi_maker.cli import load_profile
    from kpi_maker.spec.schema import RunSpec

    monkeypatch.setattr(api._POOL, "submit", lambda *a, **k: None)
    spec = RunSpec.for_profile(
        load_profile(ROOT / "samples" / "northwind_saas.json"))
    run_dir = api.RUNS_DIR / "ppp"
    run_dir.mkdir(parents=True)
    (run_dir / "spec.json").write_text(
        spec.model_dump_json(indent=2), encoding="utf-8")
    return "ppp"


def test_a_rerun_keeps_the_spec_it_is_about_to_replace(api, spec_run):
    """A re-run overwrites the artifacts; the edit already overwrote `spec.json`.

    Between the two there was no way left to say what the previous dashboard
    was built from — which is the foundation compare and undo need, and what
    "this month vs last month" rests on.
    """
    api.rerun(spec_run)

    edited = json.loads((api.RUNS_DIR / spec_run / "spec.json").read_text())
    edited["design"]["theme"] = "dark"
    api.put_spec(spec_run, edited)
    api.rerun(spec_run)

    versions = api._store().versions(spec_run, with_spec=True)
    assert [v["seq"] for v in versions] == [1, 2]
    assert versions[0]["spec"]["design"]["theme"] == "light"
    assert versions[1]["spec"]["design"]["theme"] == "dark"
    assert all(v["author"] == "user" for v in versions)


def test_a_rerun_that_rebuilds_nothing_is_not_a_version(api, spec_run, monkeypatch):
    """Found by pressing the button twice: "re-run, rebuilding 0 stages".

    A re-run with a clean cache produces the same artifacts it started with, so
    it replaced no spec and there is nothing to attribute. Recording it anyway
    fills the history with rows that differ only in their timestamp.
    """
    monkeypatch.setattr(api, "plan_rerun",
                        lambda *a, **k: {"dirty": [], "reused": ["resolve"]})
    api.rerun(spec_run)

    assert api._store().versions(spec_run) == []


def test_studio_edits_are_not_versions(api, spec_run):
    """Every spec write funnels through `put_spec`, including the studio's
    debounced per-keystroke PATCH. Versioning those would bury the handful of
    rows that mean something under hundreds that do not, and 7.1 would then
    have to add drafts just to make the history readable again.
    """
    current = json.loads((api.RUNS_DIR / spec_run / "spec.json").read_text())
    for theme in ("dark", "light", "dark"):
        current["design"]["theme"] = theme
        api.put_spec(spec_run, current)

    assert api._store().versions(spec_run) == []


def test_an_accepted_plan_is_recorded_as_the_planners(api, spec_run):
    """Once `spec.json` is overwritten, the paths are the only record of what
    the model changed — and of the fact that a model, not the user, changed it.
    """
    api.ai_apply(spec_run, api.ApplyRequest(changes=[
        {"path": "design.theme", "value": "dark"}]))

    versions = api._store().versions(spec_run, with_spec=True)
    assert len(versions) == 1
    assert versions[0]["author"] == "planner"
    assert versions[0]["message"] == "design.theme"
    assert versions[0]["spec"]["design"]["theme"] == "dark"


def test_versions_are_reachable_over_the_api(api, spec_run):
    """A table nothing can read is the pattern this phase exists to stop."""
    api.rerun(spec_run)

    listed = api.list_spec_versions(spec_run)
    assert [v["seq"] for v in listed] == [1]
    assert "spec" not in listed[0], "the list is metadata; specs are large"
    assert api.get_spec_version(spec_run, 1)["spec"]["design"]["theme"] == "light"


def test_deleting_a_run_deletes_its_versions(api, spec_run):
    api.rerun(spec_run)
    api.delete_run(spec_run)
    assert api._store().versions(spec_run) == []


# --------------------------------------------------------------------------
# The database itself
# --------------------------------------------------------------------------

def test_the_store_binds_no_path_at_import(api, tmp_path):
    """`RUNS_DIR` is rebound at runtime and the store must follow it.

    `tools/build_pages.py` points it at the site tree and the tests point it at
    a tmp directory. A handle captured at import would drop a `runs.db` into
    whichever tree ran first — into the published Pages site, in the build's case.
    """
    first = api.RUNS_DIR
    api._store().upsert("jjj", status="done")
    assert (first / "runs.db").exists()

    second = tmp_path / "elsewhere"
    second.mkdir()
    api.RUNS_DIR = second
    api._store().upsert("kkk", status="done")

    assert (second / "runs.db").exists()
    assert store(second).get("jjj") is None, "the two trees must not share an index"
    assert store(first).get("kkk") is None


def test_migrations_run_once_and_keep_their_rows(tmp_path):
    """The desktop build ships to machines that already have a `runs.db`.

    A schema change with no upgrade path is a broken install, so the version
    counter has to advance exactly as far as the migration list and reopening
    must not re-run anything.
    """
    first = Store(tmp_path / "runs.db")
    first.upsert("lll", status="done", company="Acme")
    assert first.schema_version == len(_MIGRATIONS)
    first.close()

    second = Store(tmp_path / "runs.db")
    assert second.schema_version == len(_MIGRATIONS)
    assert second.get("lll")["company"] == "Acme"
    second.close()


def test_a_database_at_version_zero_upgrades(tmp_path):
    """An empty file is what a fresh install and a botched copy both look like."""
    path = tmp_path / "runs.db"
    raw = sqlite3.connect(str(path))
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()

    opened = Store(path)
    assert opened.schema_version == len(_MIGRATIONS)
    opened.upsert("mmm", status="done")
    assert opened.get("mmm")["status"] == "done"
    opened.close()


def test_two_threads_can_write_at_once(tmp_path):
    """The pool has two workers, and both can finish a run at the same moment.

    Guards two failures at once: SQLITE_BUSY from concurrent writers, and
    "objects created in a thread can only be used in that same thread" from
    sharing a connection without `check_same_thread=False`.
    """
    opened = Store(tmp_path / "runs.db")

    def write(worker: int) -> None:
        for index in range(40):
            opened.upsert(f"{worker}-{index}", status="done",
                          company=f"Co {worker}", stages_ran=["ingest"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(write, 0), pool.submit(write, 1)]:
            future.result()

    assert len(opened.list()) == 80
    assert opened.get("1-39")["stages_ran"] == ["ingest"]
    opened.close()


def test_unknown_columns_are_refused_not_dropped(tmp_path):
    """A typo'd field that vanishes into a no-op is the bug this module ends."""
    opened = Store(tmp_path / "runs.db")
    with pytest.raises(KeyError):
        opened.upsert("nnn", statuss="done")
    opened.close()


def test_list_is_newest_first_with_undated_rows_last(tmp_path):
    opened = Store(tmp_path / "runs.db")
    opened.upsert("old", started_at="2026-01-01T00:00:00+00:00")
    opened.upsert("new", started_at="2026-06-01T00:00:00+00:00")
    opened.upsert("undated")

    assert [r["run_id"] for r in opened.list()] == ["new", "old", "undated"]
    opened.close()
