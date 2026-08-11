"""SQLite index over the run directories.

Why this exists: `_STATE` in the API is a module-level dict, so a restart takes
every run's mode, start time and outcome with it. What survived was whatever
could be guessed by globbing `runs/*/summary.json` — which reports a mode of
"restored" for everything, no start time at all, and, since 0.6 stopped writing
`summary.json` for a run the user cancelled, nothing whatsoever for cancelled
and failed runs. The stages 0.6 preserved on disk were unreachable.

The database sits *beside* the run directories rather than replacing them. Delete
`runs.db` and you lose the history, not the work: the reconcile pass rebuilds a
row for every directory that finished.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Columns of `runs`, in creation order. `upsert` refuses anything not named
# here rather than silently dropping it — a typo'd field that vanishes into a
# no-op is exactly the kind of quiet data loss this module exists to end.
COLUMNS = (
    "run_id", "company", "mode", "status", "started_at", "finished_at",
    "seconds", "stages_ran", "stages_reused", "error", "cancelled_stage",
)

# Stored as JSON text. SQLite has no array type and a separate table for two
# lists of stage names would be three joins in service of nothing.
_JSON_COLUMNS = frozenset({"stages_ran", "stages_reused"})


def _create_runs(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE runs (
            run_id          TEXT PRIMARY KEY,
            company         TEXT,
            mode            TEXT,
            status          TEXT,
            started_at      TEXT,
            finished_at     TEXT,
            seconds         REAL,
            stages_ran      TEXT,
            stages_reused   TEXT,
            error           TEXT,
            cancelled_stage TEXT
        )
    """)
    # The history drawer's only query is "newest first".
    conn.execute("CREATE INDEX runs_started_at ON runs (started_at DESC)")


# Append-only. The desktop build ships to machines that already have a
# `runs.db`, so a schema change without an upgrade path is a broken install,
# not an inconvenience. `PRAGMA user_version` records how many have run.
_MIGRATIONS: List[Callable[[sqlite3.Connection], None]] = [
    _create_runs,
]


def _encode(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS and value is not None:
        return json.dumps(value)
    return value


def _decode(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    for column in _JSON_COLUMNS:
        raw = out.get(column)
        if isinstance(raw, str):
            try:
                out[column] = json.loads(raw)
            except json.JSONDecodeError:
                out[column] = None
    return out


class Store:
    """One SQLite file, one connection, one lock.

    Not a connection per thread: FastAPI runs sync endpoints on an anyio worker
    pool whose threads are recycled, so thread-local connections would leak a
    file handle per thread that ever served a request. A single connection with
    `check_same_thread=False` behind a lock is correct for the handful of writes
    a run produces, and WAL keeps the reads out of the writer's way.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()

    # -- schema ------------------------------------------------------------

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(_MIGRATIONS)):
            _MIGRATIONS[index](self._conn)
            # PRAGMA takes no parameters; the value is a list index, not input.
            self._conn.execute(f"PRAGMA user_version = {index + 1}")
        self._conn.commit()

    @property
    def version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    # -- rows --------------------------------------------------------------

    def upsert(self, run_id: str, **fields: Any) -> None:
        """Create or update a run's row, touching only the columns given.

        Partial by design: the API learns a run's outcome long after it learns
        its mode, and neither call should have to restate the other's fields.
        """
        unknown = set(fields) - set(COLUMNS)
        if unknown:
            raise KeyError(f"not columns of `runs`: {sorted(unknown)}")
        fields.pop("run_id", None)

        columns = list(fields)
        values = [_encode(c, fields[c]) for c in columns]
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        with self._lock:
            if columns:
                assignments = ", ".join(f"{c}=excluded.{c}" for c in columns)
                self._conn.execute(
                    f"INSERT INTO runs (run_id, {', '.join(columns)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT(run_id) DO UPDATE SET {assignments}",
                    [run_id, *values])
            else:
                self._conn.execute(
                    "INSERT INTO runs (run_id) VALUES (?) "
                    "ON CONFLICT(run_id) DO NOTHING", [run_id])
            self._conn.commit()

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", [run_id]).fetchone()
        return _decode(row) if row is not None else None

    def list(self) -> List[Dict[str, Any]]:
        """Every run, newest first. Rows with no start time sort last."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs "
                "ORDER BY started_at IS NULL, started_at DESC").fetchall()
        return [_decode(r) for r in rows]

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM runs WHERE run_id = ?", [run_id])
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- recovery ----------------------------------------------------------

    def reconcile(self, runs_dir: Path) -> int:
        """Index finished run directories that have no row, and report how many.

        Covers two cases that are not restarts: a `runs.db` deleted or moved
        while the directories stayed, and runs produced by the CLI, which writes
        the same directory layout without ever touching this index. Cheap enough
        to call on every history read — a glob over a directory of run ids.
        """
        with self._lock:
            known = {r[0] for r in
                     self._conn.execute("SELECT run_id FROM runs").fetchall()}

        added = 0
        for path in sorted(Path(runs_dir).glob("*/summary.json")):
            run_id = path.parent.name
            if run_id in known:
                continue
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # The file's mtime is when the run finished. It is a real ordering
            # even though it is not the real start time, which beats the `None`
            # that used to drop every recovered run to the bottom of the list.
            stamp = datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            self.upsert(
                run_id,
                status="done",
                company=summary.get("company"),
                # Genuinely unknown: `summary.json` never recorded how the run
                # was started. Rows this server created carry their real mode,
                # so "restored" now means only "found on disk, origin unknown".
                mode="restored",
                started_at=stamp,
                finished_at=stamp,
                seconds=summary.get("seconds"),
                stages_ran=summary.get("stages_ran"),
                stages_reused=summary.get("stages_reused"),
            )
            added += 1
        return added


# One Store per database path. Keyed by resolved path rather than held in a
# module global because `api.RUNS_DIR` is rebound at runtime — the Pages build
# points it at the site tree, and tests point it at a tmp dir. Binding a path at
# import would write a stray `runs.db` into whatever tree the build was writing.
_CACHE: Dict[Path, Store] = {}
_CACHE_LOCK = threading.Lock()


def store(runs_dir: Path) -> Store:
    """The Store for `runs_dir`, opening and migrating it on first use."""
    path = Path(runs_dir).resolve() / "runs.db"
    with _CACHE_LOCK:
        existing = _CACHE.get(path)
        if existing is None:
            existing = Store(path)
            _CACHE[path] = existing
        return existing


def reset_cache() -> None:
    """Drop every open Store. For tests that rebuild a tmp runs directory."""
    with _CACHE_LOCK:
        for open_store in _CACHE.values():
            with contextlib.suppress(sqlite3.Error):
                open_store.close()
        _CACHE.clear()
