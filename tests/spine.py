"""Tests for the RunSpec contract and the stage graph.

`stress.py` asks "does the product lie?". This asks a different question:
"does adjusting the pipeline do exactly what it says, and nothing else?"

Four properties, each of which has a specific way of going wrong:

  1. **Neutrality.** An empty spec reproduces the pre-RunSpec pipeline. If this
     ever fails, the configuration layer has changed the product rather than
     exposing it.
  2. **Invalidation.** Editing one section rebuilds exactly the stages that
     read it. Too few and the user's edit silently does nothing; too many and
     the studio is slow for no reason. Both are quiet failures, which is why
     the expected sets are written out longhand here.
  3. **Equivalence.** A warm partial re-run produces the same artifacts as a
     cold full run. Caching that changes the answer is worse than no caching.
  4. **Controls.** Pin, exclude, override and artifact selection actually take
     effect, and bad input is refused rather than ignored.

    python -m tests.spine
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile
from kpi_maker.pipeline.cache import STORE
from kpi_maker.pipeline.graph import STAGES, required_stages
from kpi_maker.pipeline.runner import execute, plan_rerun
from kpi_maker.spec.schema import ALL_ARTIFACTS, KpiOverride, RunSpec

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "northwind_saas.json"

# Artifacts cheap enough to rebuild repeatedly. The PNG export and the three
# print deliverables are ~75% of a run and prove nothing extra here.
FAST = ["dashboard", "facts_csv", "json_dumps", "csv_bundle"]

_failures: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok   ' if ok else 'FAIL '}  {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(f"{name}: {detail}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts_digest(out: Path) -> Dict[str, str]:
    """Hash everything except the run's own bookkeeping."""
    skip = {"spec.json", ".stage_hashes.json"}
    return {
        str(p.relative_to(out)): digest(p)
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name not in skip
    }


def fresh_spec() -> RunSpec:
    spec = RunSpec.for_profile(load_profile(SAMPLE))
    spec.outputs.artifacts = list(FAST)
    return spec


# --------------------------------------------------------------------------

def test_graph_is_well_formed() -> None:
    order = required_stages(ALL_ARTIFACTS)
    check("graph: every stage reachable", len(order) == len(STAGES),
          f"{len(order)} of {len(STAGES)} reachable from the full artifact set")

    placed: Set[str] = set()
    ok = True
    for name in order:
        if any(dep not in placed for dep in STAGES[name].needs):
            ok = False
        placed.add(name)
    check("graph: topological order respects needs", ok)

    # Every section a stage claims to read must exist on the spec, or the cache
    # key silently hashes nothing and the stage never goes dirty.
    spec = fresh_spec()
    unknown = sorted({r for s in STAGES.values() for r in s.reads
                      if not hasattr(spec, r)})
    check("graph: declared reads exist on the spec", not unknown, f"unknown: {unknown}")

    # Pruning has to actually prune.
    only_csv = required_stages(["csv_bundle"])
    check("graph: unused branches pruned",
          "metrics" not in only_csv and "visualise" not in only_csv,
          f"csv_bundle pulled in {sorted(only_csv)}")


def test_empty_spec_is_neutral(tmp: Path) -> None:
    """Property 1 — the configuration layer must not change the product."""
    spec = fresh_spec()
    a = tmp / "neutral_a"
    execute(spec, a)

    STORE.clear()
    b = tmp / "neutral_b"
    execute(RunSpec(**json.loads(spec.model_dump_json())), b)

    check("neutral: spec round-trip produces identical artifacts",
          artifacts_digest(a) == artifacts_digest(b))


def test_invalidation(tmp: Path) -> None:
    """Property 2 — an edit rebuilds exactly what reads it."""
    out = tmp / "invalidate"
    spec = fresh_spec()
    execute(spec, out)

    cases: List[tuple] = [
        ("nothing", lambda s: None, set()),
        ("design.theme", lambda s: setattr(s.design, "theme", "dark"),
         {"visualise", "dashboard"}),
        ("metrics.excluded",
         lambda s: setattr(s.metrics, "excluded", ["cac_payback_months"]),
         {"select", "metrics", "analyse", "narrate", "visualise", "dashboard",
          "facts_csv", "json_dumps"}),
        ("source.generator.seed",
         lambda s: setattr(s.source.generator, "seed", 4242),
         {"source", "clean", "model", "metrics", "analyse", "narrate",
          "visualise", "dashboard", "facts_csv", "json_dumps", "csv_bundle"}),
        # json_dumps writes findings.json, so it is correctly downstream of a
        # change to how many findings there are.
        ("analysis.max_findings",
         lambda s: setattr(s.analysis, "max_findings", 5),
         {"analyse", "narrate", "dashboard", "json_dumps"}),
        # The AI section reaches narration and the renderers that carry its
        # prose, and nothing else.
        ("ai.enabled", lambda s: setattr(s.ai, "enabled", True),
         {"narrate", "dashboard"}),
        ("ai.model", lambda s: setattr(s.ai, "model", "claude-sonnet-5"),
         {"narrate", "dashboard"}),
        # The counterpart property, and the one that pays for the stage
        # reading `ai` and `profile` but deliberately NOT `design`: restyling a
        # report must not re-buy its prose. `visualise` and `dashboard` go
        # dirty; `narrate` does not.
        ("design.brand.primary — narration is reused",
         lambda s: setattr(s.design.brand, "primary", "#7C3AED"),
         {"visualise", "dashboard"}),
    ]

    for label, mutate, expected in cases:
        trial = RunSpec(**json.loads(spec.model_dump_json()))
        mutate(trial)
        dirty = set(plan_rerun(trial, out)["dirty"])
        check(f"invalidate: {label}", dirty == expected,
              f"expected {sorted(expected)}, got {sorted(dirty)}")


def test_warm_equals_cold(tmp: Path) -> None:
    """Property 3 — caching must not change the answer."""
    spec = fresh_spec()

    warm = tmp / "warm"
    execute(spec, warm)
    spec.metrics.excluded = ["cac_payback_months"]
    result = execute(spec, warm)          # partial: reuses the data stages
    reused = set(result.skipped)

    STORE.clear()
    cold = tmp / "cold"
    execute(spec, cold)                   # same spec, nothing cached

    check("warm/cold: partial re-run reused the data stages",
          {"source", "clean", "model"} <= reused, f"reused {sorted(reused)}")
    check("warm/cold: artifacts identical",
          artifacts_digest(warm) == artifacts_digest(cold))


def test_controls(tmp: Path) -> None:
    """Property 4 — the knobs do something, and refuse nonsense."""
    out = tmp / "controls"

    spec = fresh_spec()
    spec.metrics.excluded = ["cac_payback_months"]
    spec.metrics.pinned = ["quick_ratio"]
    spec.metrics.overrides = {"gross_margin_pct": KpiOverride(target=0.9)}
    result = execute(spec, out)

    kpi_set = result.values["select"]
    ids = {k.id for k in kpi_set.kpis}
    check("control: excluded KPI is gone", "cac_payback_months" not in ids)
    check("control: exclusion records a reason",
          kpi_set.dropped.get("cac_payback_months") == "excluded by user",
          repr(kpi_set.dropped.get("cac_payback_months")))
    check("control: pinned KPI is present", "quick_ratio" in ids)

    gm = next(r for r in result.values["metrics"] if r.kpi.id == "gross_margin_pct")
    check("control: target override wins over the benchmark", gm.target == 0.9,
          f"target is {gm.target}")

    # Artifact selection writes what was asked for, and nothing else.
    lean = fresh_spec()
    lean.outputs.artifacts = ["facts_csv"]
    lean_out = tmp / "lean"
    execute(lean, lean_out)
    written = {p.name for p in lean_out.iterdir()}
    check("control: only requested artifacts written",
          written == {"facts.csv", "spec.json", ".stage_hashes.json"},
          f"got {sorted(written)}")

    # Bad input is refused rather than silently dropped.
    for label, mutate in [
        ("unknown excluded id", lambda s: setattr(s.metrics, "excluded", ["nope"])),
        ("unknown pinned id", lambda s: setattr(s.metrics, "pinned", ["nope"])),
    ]:
        bad = fresh_spec()
        mutate(bad)
        try:
            execute(bad, tmp / "bad")
            check(f"control: {label} rejected", False, "accepted silently")
        except ValueError:
            check(f"control: {label} rejected", True)

    for label, mutate in [
        ("invalid theme", lambda s: setattr(s.design, "theme", "neon")),
        ("out-of-range volatility",
         lambda s: setattr(s.source.generator, "volatility", 99)),
    ]:
        bad = fresh_spec()
        try:
            mutate(bad)
            check(f"control: {label} rejected", False, "accepted silently")
        except Exception:                                  # noqa: BLE001
            check(f"control: {label} rejected", True)


# --------------------------------------------------------------------------

def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kpi-spine-"))
    suites: List[Callable] = [
        lambda: test_graph_is_well_formed(),
        lambda: test_empty_spec_is_neutral(tmp),
        lambda: test_invalidation(tmp),
        lambda: test_warm_equals_cold(tmp),
        lambda: test_controls(tmp),
    ]
    try:
        for suite in suites:
            try:
                suite()
            except Exception as exc:                       # noqa: BLE001
                _failures.append(f"crash: {type(exc).__name__}: {exc}")
                print(f"  CRASH  {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 70}")
    print(f"failures: {len(_failures)}")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(run())
