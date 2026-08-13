"""Where things live, and how that changes inside a packaged executable.

A source checkout and a one-file build disagree about three questions, and each
disagreement is a bug rather than an inconvenience:

* **Where the code is.** PyInstaller unpacks the bundle into a temporary
  directory and points `sys._MEIPASS` at it. Anything computed from
  `Path(__file__).parents[...]` lands there instead of a repository.
* **Where the user's data goes.** That temporary directory is *deleted when the
  process exits*. A run written next to the code — which is what
  `parents[2] / "runs"` means in a checkout — would disappear the moment the
  app closes, and the history drawer would come back empty every launch.
* **Whether the source exists at all.** `pipeline/cache.py` hashes every `.py`
  and `.yaml` to derive `CODE_VERSION`, which is what stops a warm process
  serving a result computed by code that has since changed. A frozen build
  ships bytecode, so that scan finds nothing and the hash would be a constant —
  reintroducing precisely the stale-cache bug the constant exists to prevent.

None of this is hypothetical for a desktop build; it is the difference between
an executable that works and one that loses the user's runs on exit.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: The build stamp a frozen bundle carries in place of readable source.
BUILD_ID_FILE = "_build_id.txt"


def frozen() -> bool:
    """True inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_root() -> Path:
    """The directory holding read-only data that ships with the app.

    Samples, the KPI library and the built front end. In a checkout this is the
    repository; in a bundle it is the unpacked temporary tree.
    """
    if frozen():
        return Path(sys._MEIPASS)                              # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def user_data_dir() -> Path:
    """Somewhere writable that survives the process, per platform convention.

    `MASTERBI_DATA_DIR` overrides it — a portable install on a USB stick wants
    its data beside the executable, and the tests want it in a tmp directory.
    """
    override = os.environ.get("MASTERBI_DATA_DIR")
    if override:
        return Path(override)

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        # XDG, with the specification's own fallback rather than a guess.
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "MasterBI"


def runs_dir() -> Path:
    """Where runs are written.

    A checkout keeps them in `runs/` beside the code, which is what every
    existing path and the `.gitignore` entry assume. A packaged app cannot:
    that directory is inside a tree the operating system deletes on exit.
    """
    override = os.environ.get("MASTERBI_RUNS_DIR")
    if override:
        return Path(override)
    if frozen():
        return user_data_dir() / "runs"
    return Path(__file__).resolve().parents[1] / "runs"


def user_library_dir() -> Path:
    """Where a user's own KPIs are saved.

    Same split, same reason. In a checkout this stays inside the package, where
    it has always been and where `kpi/library/user/` is already ignored; in a
    bundle the package is read-only and temporary, so a KPI "kept for later"
    would not survive the session that saved it.
    """
    if frozen():
        return user_data_dir() / "kpi-library"
    return Path(__file__).resolve().parent / "kpi" / "library" / "user"


def build_id() -> str | None:
    """The stamp written at freeze time, or None in a checkout.

    Read from the bundle rather than baked into a module so that rebuilding
    does not require editing source. `installer/build.py` writes it.
    """
    stamp = resource_root() / "kpi_maker" / BUILD_ID_FILE
    if not stamp.exists():
        stamp = resource_root() / BUILD_ID_FILE
    if stamp.exists():
        text = stamp.read_text(encoding="utf-8").strip()
        return text or None
    return None
