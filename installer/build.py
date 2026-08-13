"""Build the one-file executable.

    python -m installer.build

Named `installer/` and not `packaging/`, which is what it obviously wants to be
called: the repository root is on `sys.path`, and a top-level `packaging`
package shadows the PyPI library of that name — which PyInstaller imports on
startup. The build failed with `No module named packaging.requirements` until
this was renamed.

Two things happen before PyInstaller runs, and both are failures that would
otherwise only appear in the finished binary:

* **The front end must already be built.** `kpi_maker/ui_dist/` is a build
  artifact; a bundle without it serves the "no front end" 500 that
  `render.yaml` used to.
* **A build id is stamped in.** `pipeline/cache.py` derives `CODE_VERSION` by
  hashing every source file, and a frozen build has none to hash — the fallback
  is this stamp, and without it the cache would serve results computed by code
  that has since changed.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / "kpi_maker" / "_build_id.txt"


def _source_digest() -> str:
    """The same inputs `_code_version` hashes, so the stamp means the same thing."""
    digest = hashlib.sha256()
    sources = sorted((ROOT / "kpi_maker").rglob("*.py"))
    sources += sorted((ROOT / "kpi_maker" / "kpi" / "library").glob("*.yaml"))
    for path in sources:
        if "__pycache__" in path.parts or path.name == "_build_id.txt":
            continue
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def main() -> int:
    dist = ROOT / "kpi_maker" / "ui_dist" / "index.html"
    if not dist.exists():
        print("No front end. Run `npm --prefix web ci && npm --prefix web run "
              "build` first — a bundle without it serves a 500.", file=sys.stderr)
        return 1

    stamp = f"{_source_digest()} {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    STAMP.write_text(stamp + "\n", encoding="utf-8")
    print(f"build id: {stamp}")

    return subprocess.call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        str(ROOT / "installer" / "masterbi.spec"),
    ], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
