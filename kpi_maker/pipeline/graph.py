"""The stage registry and its dependency graph.

A stage declares three things and the runner does the rest:

    needs     upstream stage names — the edges of the DAG
    reads     RunSpec sections it depends on — the cache key's other half
    artifact  the OutputSpec name that gates it, if any

`artifact` is what makes "choose your outputs" and "don't redo work" the same
mechanism rather than two. Asking for only the dashboard prunes the PDF, the
deck, the doc and the static PNG export from the graph — measured at ~80% of a
run's wall time — without a single `if` in the rendering code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

STAGES: Dict[str, Stage] = {}


@dataclass
class Stage:
    name: str
    fn: Callable
    needs: Sequence[str] = field(default_factory=tuple)
    reads: Sequence[str] = field(default_factory=tuple)
    artifact: Optional[str] = None
    label: str = ""


def stage(name: str, *, needs: Sequence[str] = (), reads: Sequence[str] = (),
          artifact: Optional[str] = None, label: str = ""):
    def wrap(fn: Callable) -> Callable:
        if name in STAGES:
            raise ValueError(f"duplicate stage {name!r}")
        STAGES[name] = Stage(name=name, fn=fn, needs=tuple(needs),
                             reads=tuple(reads), artifact=artifact,
                             label=label or name)
        return fn
    return wrap


def topological_order(names: Optional[Sequence[str]] = None) -> List[str]:
    """Dependency order, deterministic across runs.

    Ties break alphabetically rather than by registration order: a cache key
    that depended on which module imported first would be a miserable bug.
    """
    wanted = set(names) if names is not None else set(STAGES)

    # Pull in everything the wanted set transitively depends on.
    pending = list(wanted)
    while pending:
        current = pending.pop()
        for dep in STAGES[current].needs:
            if dep not in wanted:
                wanted.add(dep)
                pending.append(dep)

    ordered: List[str] = []
    placed = set()
    while len(ordered) < len(wanted):
        ready = sorted(
            n for n in wanted
            if n not in placed and all(d in placed for d in STAGES[n].needs)
        )
        if not ready:
            stuck = sorted(wanted - placed)
            raise ValueError(f"cycle or missing dependency among stages: {stuck}")
        for name in ready:
            ordered.append(name)
            placed.add(name)
    return ordered


def required_stages(artifacts: Sequence[str]) -> List[str]:
    """The stages needed to produce exactly this set of artifacts, and no more.

    Only artifact-bearing stages seed the walk; the compute spine is pulled in
    transitively by whatever actually needs it. Seeding with every stage that
    merely lacks an `artifact` would keep computing metrics for a caller who
    asked only for the raw CSV bundle.
    """
    wanted = set(artifacts)
    leaves = [s.name for s in STAGES.values() if s.artifact in wanted]
    return topological_order(leaves)
