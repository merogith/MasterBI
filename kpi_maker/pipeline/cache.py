"""Content-hash cache keys, and the store that holds stage outputs.

A stage's hash is a function of three things:

    the code that implements it   (CODE_VERSION)
  + the hashes of its inputs      (so a dirty upstream dirties everything after)
  + the RunSpec sections it reads (so an unrelated setting does not)

That last clause is the point. Changing a brand colour must not regenerate the
company, and changing the seed must not be ignored. Declaring `reads` per stage
is what buys both.

**On persistence.** The store is in-memory, bounded, and process-local. Stage
hashes, by contrast, are written to disk. That asymmetry is deliberate: the
hashes are small and let `plan_rerun` answer "what would change?" honestly after
a restart, while the artifacts include plotly figures and pandas objects whose
serialization would be a real subsystem for little gain — the upstream stages
they would save total ~10% of a run's wall time (measured), against ~80% in
render, which is already gated by OutputSpec. Re-running the cheap half after a
restart is the right trade.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .. import paths


def _code_version() -> str:
    """A digest of the engine's own source, as half of every stage's cache key.

    This used to be the literal string `"1"`, bumped by hand "when a stage's
    semantics change". That is a rule nobody can follow reliably: change the
    arithmetic in a metric, forget the bump, and a warm process serves the old
    number — a bug that presents as "my edit did nothing", which is exactly the
    failure the constant existed to prevent.

    The KPI library counts as source. A record sheet's formula, benchmark or
    alert bands change what `metrics` produces just as surely as Python does,
    and a YAML edit that did not invalidate the cache would be the same bug
    wearing a different hat.

    Cost is one pass over ~100 small files at import, once per process. The
    price of getting it wrong is a wrong number in a board pack, so this is not
    a close call. If it ever does matter, cache it in a build artifact — do not
    go back to a hand-maintained string.
    """
    # A frozen build ships bytecode, so this scan finds nothing and the hash
    # would be a constant for every version ever released — which is exactly
    # the stale-result bug the constant exists to prevent, wearing the disguise
    # of working code. The build stamps an id in instead; refusing to guess is
    # the only safe third option.
    stamp = paths.build_id()
    if stamp:
        return hashlib.sha256(stamp.encode()).hexdigest()[:16]

    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    sources = sorted(root.rglob("*.py")) + sorted((root / "kpi" / "library").glob("*.yaml"))
    if not sources:
        raise RuntimeError(
            "cannot derive CODE_VERSION: no source files found and no build id "
            "was stamped. A constant here serves results computed by code that "
            "has since changed — see installer/build.py.")
    for path in sources:
        if "__pycache__" in path.parts:
            continue
        # The path is hashed as well as the bytes, so moving a file to a new
        # name is a change even when its contents are identical.
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


CODE_VERSION = _code_version()

HASH_FILE = ".stage_hashes.json"

# Runs are small (tens of MB of DataFrames at the top end) but a long-lived
# server must not accumulate them without bound.
MAX_ENTRIES = 64


def _canonical(value: Any) -> str:
    """Stable JSON for hashing. Key order must not change the hash."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stage_hash(name: str, reads: Dict[str, Any], upstream: List[str]) -> str:
    h = hashlib.sha256()
    h.update(CODE_VERSION.encode())
    h.update(name.encode())
    for dep_hash in sorted(upstream):
        h.update(dep_hash.encode())
    for key in sorted(reads):
        h.update(key.encode())
        h.update(_canonical(reads[key]).encode())
    return h.hexdigest()[:16]


class ArtifactStore:
    """Bounded LRU of stage outputs, keyed by (stage, hash)."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._max = max_entries

    @staticmethod
    def _key(stage: str, digest: str) -> str:
        return f"{stage}:{digest}"

    def get(self, stage: str, digest: str) -> Any:
        key = self._key(stage, digest)
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def has(self, stage: str, digest: str) -> bool:
        return self._key(stage, digest) in self._items

    def put(self, stage: str, digest: str, value: Any) -> None:
        key = self._key(stage, digest)
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


# One store per process. Runs execute on a thread pool inside a single server,
# so sharing it is what makes an interactive re-run fast.
STORE = ArtifactStore()


def read_hashes(run_dir: Path) -> Dict[str, str]:
    path = run_dir / HASH_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # A corrupt hash file must degrade to "everything is dirty", never to a
        # crash — the artifacts themselves are still perfectly good.
        return {}


def write_hashes(run_dir: Path, hashes: Dict[str, str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / HASH_FILE).write_text(
        json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def previous_spec(run_dir: Path) -> Optional[Dict[str, Any]]:
    path = run_dir / "spec.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
