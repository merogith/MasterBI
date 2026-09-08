"""Run the scored eval suites, the same way `test_harnesses.py` runs the others.

A subprocess for the same reasons that file gives: the harness keeps state in
module globals, prints its own report, and a crash in one suite should not take
the session with it.

**Why this exists as a gate rather than a thing somebody remembers to run.**
7.4c shipped four accessibility gates into a module no CI job named, so they
ran nowhere and the run was green anyway. An eval corpus is exactly the sort of
thing that ends up in that position — valuable, slow-ish, easy to leave out of
a job list — so it gets a test on the day it is written rather than later.
`test_packaging.py` asserts separately that every browser module is named by a
workflow; this one is plain pytest, so the three-OS matrix picks it up with no
job list to forget.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_eval_suites_meet_their_thresholds() -> None:
    """Every suite at or above its floor, which for a correctness gate is 100%.

    The report itself is the failure message: it names the case, what was
    expected and what the scorer saw, which is what makes a red run actionable
    rather than a number that went down.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "evals"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"evals exited {proc.returncode}\n\n"
            f"--- stdout ---\n{proc.stdout[-8000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}",
            pytrace=False,
        )


def test_the_corpus_is_not_trivially_satisfiable() -> None:
    """A corpus of only-clean cases would score 100% against a gate that never
    fires, and a corpus of only-adversarial ones against a gate that always
    does.

    5.3e is the cautionary case: the concentration detector fired on 11 of 11
    dimensions and looked like a working detector until somebody asked whether
    it could ever say no. So the corpus has to contain both verdicts in
    quantity, asserted here rather than assumed — and the honest half is what
    catches a *false positive*, which is the failure mode a gate's own author
    is least likely to look for.
    """
    sys.path.insert(0, str(ROOT))
    from evals.harness import load
    from evals.mutants import derive

    cases = load() + derive()
    narrator = [c for c in cases if c["suite"] == "narrator"]
    clean = [c for c in narrator if c["expect"]["verdict"] == "clean"]
    refused = [c for c in narrator if c["expect"]["verdict"] == "refused"]

    assert len(clean) >= 5, f"only {len(clean)} clean cases; a gate that " \
                            f"refused everything would still score full marks"
    assert len(refused) >= 20, f"only {len(refused)} adversarial cases"

    planner = [c for c in cases if c["suite"] == "planner"]
    accepted = [c for c in planner
                if c.get("expect", {}).get("accepted")]
    assert accepted, "no planner case expects a change to be accepted, so a " \
                     "guard that refused every patch would score full marks"
