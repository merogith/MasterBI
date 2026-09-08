"""Load the golden sets, score them, report rates.

A case is `{input, expected, rubric}` as the brief asks, spelled here as
`output` / `expect` / `why` — `why` because a case nobody can read the purpose
of is a case nobody will maintain, and every failure mode in this corpus is
named in the case that probes it.

**Fixtures come from real runs, not from hand-typed numbers.** A narrator case
has to be graded against `allowed_numbers`, which is built from actual
`MetricResult` objects; typing a fake facts table would make the corpus grade
itself. So one sample is run once per process and every case for that sample
scores against it — the same "generated, not declared" move as the design
tokens, the table-to-KPI map and the benchmark bands.

That also makes the corpus **grow for free**: `mutants.py` derives adversarial
prose from whatever figures the run really produced, so adding a sector or
changing a formatter changes the cases rather than leaving them stale.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES = Path(__file__).resolve().parent / "cases"

# Pin the calendar before anything generates data, for the reason
# `tests/conftest.py` gives: generated history ends at the last completed
# month, so a seasonal sample's figures depend on when the corpus is run, and
# a red February would look like a code change nobody made. This corpus is
# *derived* from those figures, which makes it more exposed than an ordinary
# test rather than less.
#
# Set here rather than inherited from `conftest.py`, because `python -m evals`
# is a documented entry point and pytest is not in the loop. That distinction
# is not hypothetical: building this harness is what showed `python -m
# tests.ai` had been failing standalone for exactly this reason while passing
# under pytest, which is the shape of "green because of the harness around it"
# that 7.4c's gate fix was also about.
_conftest = ROOT / "tests" / "conftest.py"
if _conftest.exists():
    import re as _re

    from kpi_maker.datagen.base import HISTORY_END_ENV

    # Read the value rather than restate it: two pinned months would be two
    # descriptions of the same data, and the corpus quotes figures the rest of
    # the suite also quotes.
    _pin = _re.search(r'PINNED_HISTORY_END = "([^"]+)"',
                      _conftest.read_text(encoding="utf-8"))
    if _pin:
        os.environ.setdefault(HISTORY_END_ENV, _pin.group(1))

#: The floor each suite must clear. Not 100% by accident — 100% is the right
#: number for a *correctness* gate, and every scorer here is one. A rate that
#: is allowed to sit below 1.0 is a rate nobody looks at, and the moment a
#: threshold is set to "current minus a bit" it stops being a gate and becomes
#: a record of the last regression.
THRESHOLDS = {"narrator": 1.0, "planner": 1.0}


@dataclass
class Result:
    suite: str
    case_id: str
    ok: bool
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def load(suite: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every case, or one suite's. Sorted, so a report reads the same twice."""
    cases: List[Dict[str, Any]] = []
    for path in sorted(CASES.rglob("*.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        for case in (loaded if isinstance(loaded, list) else [loaded]):
            case.setdefault("id", path.stem)
            case.setdefault("suite", path.parent.name)
            case["_file"] = str(path.relative_to(ROOT))
            if suite is None or case["suite"] == suite:
                cases.append(case)
    return cases


# --------------------------------------------------------------------------
# Fixtures — one real run per sample, built once
# --------------------------------------------------------------------------

_FIXTURES: Dict[str, Dict[str, Any]] = {}


def fixture(sample: str) -> Dict[str, Any]:
    """A real run of `sample`, and everything the scorers need from it.

    Cached per process because the pipeline is the expensive part and every
    case for a sample wants the identical view. Only the cheap half of the
    graph is walked — `facts_csv` and `json_dumps` stop before the renderers,
    which is the same trick `tests/ai.py::compute_spine` uses and the reason
    this suite runs in seconds rather than minutes.
    """
    if sample in _FIXTURES:
        return _FIXTURES[sample]

    import tempfile

    from kpi_maker.ai import planner as ai_planner
    from kpi_maker.ai import verify as ai_verify
    from kpi_maker.cli import load_profile
    from kpi_maker.pipeline.cache import STORE
    from kpi_maker.pipeline.runner import execute
    from kpi_maker.spec.schema import RunSpec

    profile = load_profile(ROOT / "samples" / f"{sample}.json")
    spec = RunSpec.for_profile(profile)
    STORE.clear()
    with tempfile.TemporaryDirectory() as tmp:
        result = execute(spec, Path(tmp) / "run",
                         artifacts=["facts_csv", "json_dumps"])
    values = result.values

    resolved = values["resolve"]
    built = {
        "spec": spec,
        "profile": resolved,
        "results": values["metrics"],
        "findings": values["analyse"],
        "catalog": ai_planner.catalog(resolved),
        "allowed": ai_verify.allowed_numbers(
            values["metrics"], values["analyse"],
            currency=resolved.identity.currency,
            period=result.period),
    }
    _FIXTURES[sample] = built
    return built


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def run(suite: Optional[str] = None) -> List[Result]:
    from evals import mutants, scorers

    cases = load(suite)
    cases.extend(mutants.derive(suite))

    out: List[Result] = []
    for case in cases:
        fix = fixture(case.get("sample", "northwind_saas"))
        if case["suite"] == "narrator":
            scored = scorers.score_narrator(case, fix["allowed"])
        elif case["suite"] == "planner":
            scored = scorers.score_planner(case, fix["spec"], fix["catalog"])
        else:
            scored = {"ok": False,
                      "detail": f"no scorer for suite {case['suite']!r}"}
        out.append(Result(suite=case["suite"], case_id=case["id"],
                          ok=scored["ok"], detail=scored.get("detail", ""),
                          extra={k: v for k, v in scored.items()
                                 if k not in ("ok", "detail")}))
    return out


def report(results: List[Result]) -> int:
    """Print per-suite rates, return an exit code."""
    suites = sorted({r.suite for r in results})
    failed = False

    for suite in suites:
        rows = [r for r in results if r.suite == suite]
        passed = sum(1 for r in rows if r.ok)
        rate = passed / len(rows) if rows else 0.0
        floor = THRESHOLDS.get(suite, 1.0)
        mark = "ok " if rate >= floor else "FAIL"
        print(f"\n{mark} {suite}: {passed}/{len(rows)} = {rate:.1%} "
              f"(floor {floor:.0%})")
        for row in rows:
            if not row.ok:
                failed = True
                print(f"       {row.case_id}: {row.detail}")

        # Reported, never gated — see `score_planner` on why a no-op is a
        # quantity problem rather than a correctness one.
        no_ops = sum(len(r.extra.get("no_ops", [])) for r in rows)
        if suite == "planner":
            print(f"       no-op changes proposed across the corpus: {no_ops}")
        if rate < floor:
            failed = True

    print(f"\n{len(results)} cases, "
          f"{sum(1 for r in results if r.ok)} passed")
    return 1 if failed else 0
