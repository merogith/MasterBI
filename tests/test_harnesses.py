"""Run the standalone harnesses under pytest, without rewriting them.

The eight modules beside this file are not unit tests and should not become
them. `stress.py` asks "does the product lie?", `spine.py` asks "does adjusting
the pipeline do exactly what it says?", `formula.py` tries to escape the
sandbox, `sector.py` checks a generated archetype cannot pass the gate on an
empty set. They are the most valuable thing in the repository and they are
worth keeping in the shape their authors chose.

What they lacked was a way for CI to run them. Each is `python -m tests.<name>`
and returns an exit code, so nothing ran unless a human typed eight commands —
and `.github/workflows/` had no test job at all. This file is the adapter: one
parametrised test per harness, so `pytest` runs all of them, reports which one
failed, and a CI job is three lines.

Each harness is invoked in a **subprocess**, deliberately. They keep failure
counts in module-level globals (`_failures`, `_checks`) and print to stdout, so
importing several into one interpreter would let one suite's state leak into
the next and would collapse eight independent reports into one. A subprocess
per harness also means a segfault in kaleido fails one test rather than the
whole session.

`stress.py` runs `--quick` by default here: the full 23-case matrix takes
minutes and belongs in a nightly job, while the three scale extremes catch the
same class of bug in seconds. Set `KPI_STRESS_FULL=1` for the whole matrix.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Ordered cheapest-first so a broken formula sandbox is reported before the
# suite that spends two minutes rendering PDFs.
HARNESSES = [
    "spine",
    "formula",
    "ingest",
    "sector",
    "design",
    "ai",
    "stress",
]


def _args(name: str) -> list:
    if name == "stress" and not os.environ.get("KPI_STRESS_FULL"):
        return ["--quick"]
    return []


@pytest.mark.parametrize("name", HARNESSES)
def test_harness(name: str) -> None:
    """The harness must exit 0. Its own output is the failure message."""
    proc = subprocess.run(
        [sys.executable, "-m", f"tests.{name}", *_args(name)],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # The harnesses print a numbered failure list; showing it beats a bare
        # "exited 1", and pytest only displays this for the tests that failed.
        pytest.fail(
            f"tests.{name} exited {proc.returncode}\n\n"
            f"--- stdout ---\n{proc.stdout[-8000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}",
            pytrace=False,
        )
