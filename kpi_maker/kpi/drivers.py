"""The value-driver tree the record sheets have always described.

`driver_parent` is populated on **56 of the 80** shipped record sheets, and
until this module existed `grep -rn driver_parent kpi_maker/` returned exactly
one hit: the field declaration in `schema.py`. Fifty-six authored statements
about how a business actually works — ARR is driven by NRR, which is driven by
GRR, which is driven by activation — recorded carefully by whoever wrote each
sheet and read by nothing.

`ARCHITECTURE.md` promises three things built on it: dashboard drill-down, the
diagnostic section's decomposition waterfall, and a defensible answer to "why is
this metric on here?". All three need the same object first, which is this one.

Two properties are worth stating because they are what the validation enforces:

* **A parent must resolve inside the pack that declares the child.** Resolving
  against the whole library would pass while breaking for a real user: a
  `general.yaml` KPI whose parent lives in `saas.yaml` is fine for a software
  company and a dangling reference for the retailer who actually runs on that
  pack. The check that matters is per-pack, and the one that would have been
  written by accident is not.
* **No cycles.** A driver tree that loops is not a decomposition, and anything
  walking it would hang rather than fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .schema import KPI


@dataclass
class DriverNode:
    kpi_id: str
    name: str
    parent: Optional[str]
    children: List[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class DriverTree:
    """A forest, honestly named as one.

    A pack *usually* has a single root — `arr` for subscriptions — but a
    partial selection has as many roots as it has orphaned branches, and
    pretending otherwise would mean inventing a parent to hang them from.
    """
    nodes: Dict[str, DriverNode] = field(default_factory=dict)
    roots: List[str] = field(default_factory=list)
    #: `{child: parent}` pairs whose parent is not in this set of KPIs.
    dangling: Dict[str, str] = field(default_factory=dict)

    def __contains__(self, kpi_id: str) -> bool:
        return kpi_id in self.nodes

    def children_of(self, kpi_id: str) -> List[str]:
        node = self.nodes.get(kpi_id)
        return list(node.children) if node else []

    def path_to_root(self, kpi_id: str) -> List[str]:
        """From the root down to this KPI, inclusive.

        The order a person reads it in — "ARR → NRR → GRR" says what the metric
        contributes to, which is the question being asked.
        """
        path: List[str] = []
        seen: set = set()
        current: Optional[str] = kpi_id
        while current and current in self.nodes and current not in seen:
            seen.add(current)
            path.append(current)
            current = self.nodes[current].parent
        return list(reversed(path))

    def descendants(self, kpi_id: str) -> List[str]:
        """Everything below this KPI, breadth first."""
        out: List[str] = []
        queue = list(self.children_of(kpi_id))
        while queue:
            current = queue.pop(0)
            if current in out:
                continue
            out.append(current)
            queue.extend(self.children_of(current))
        return out

    def as_dict(self) -> Dict[str, object]:
        return {
            "roots": self.roots,
            "dangling": self.dangling,
            "nodes": {
                node.kpi_id: {
                    "name": node.name, "parent": node.parent,
                    "children": node.children, "depth": node.depth,
                }
                for node in self.nodes.values()
            },
        }


def build(kpis: Iterable[KPI],
          ancestry: Optional[Dict[str, Optional[str]]] = None) -> DriverTree:
    """The tree for exactly this set of KPIs.

    `ancestry` is the **whole library's** `{id: parent}` map, and supplying it
    is what keeps a *selection* readable. A scorecard is a subset — 25 of the
    44 SaaS sheets on a real run — so a KPI's declared parent is often simply
    not on it: `rnd_pct_revenue` points at `arr_per_fte`, which was not
    selected. Without the map that child becomes a root and the tree shatters
    into seven fragments; with it, the child is lifted to its nearest *selected*
    ancestor and the decomposition keeps its shape through the gap.

    A parent that cannot be resolved even then is recorded in `dangling` and
    the child becomes a root, rather than being dropped. A selected KPI must
    appear whatever its parent situation — losing it silently because its
    driver was not selected would be the worst of the three options.
    """
    kpis = list(kpis)
    tree = DriverTree()
    known = {kpi.id for kpi in kpis}

    for kpi in kpis:
        parent = kpi.driver_parent
        if parent and parent not in known and ancestry:
            parent = _nearest_selected(parent, ancestry, known)
        if parent and parent not in known:
            tree.dangling[kpi.id] = kpi.driver_parent
            parent = None
        tree.nodes[kpi.id] = DriverNode(
            kpi_id=kpi.id, name=kpi.name, parent=parent)

    for node in tree.nodes.values():
        if node.parent:
            tree.nodes[node.parent].children.append(node.kpi_id)
        else:
            tree.roots.append(node.kpi_id)

    for node in tree.nodes.values():
        node.children.sort()
    tree.roots.sort()

    for root in tree.roots:
        _set_depth(tree, root, 0, set())
    return tree


def _nearest_selected(start: str, ancestry: Dict[str, Optional[str]],
                      known: set) -> Optional[str]:
    """Walk up the library's tree until something in `known` appears.

    `seen` guards a looping library rather than a looping selection —
    `validate` reports cycles instead of raising, so a caller may be walking a
    graph it already knows is broken in order to find out where.
    """
    seen: set = set()
    current: Optional[str] = start
    while current and current not in seen:
        if current in known:
            return current
        seen.add(current)
        current = ancestry.get(current)
    return None


def _set_depth(tree: DriverTree, kpi_id: str, depth: int, seen: set) -> None:
    """Depth-first, refusing to loop.

    `seen` is not defensive decoration: `validate` reports cycles rather than
    raising, so a caller may legitimately build a looping tree in order to be
    told where the loop is, and this must terminate while they do.
    """
    if kpi_id in seen:
        return
    seen.add(kpi_id)
    tree.nodes[kpi_id].depth = depth
    for child in tree.nodes[kpi_id].children:
        _set_depth(tree, child, depth + 1, seen)


def validate(kpis: Sequence[KPI]) -> List[str]:
    """Every problem with this set's driver graph, as sentences. Empty is good.

    Returned rather than raised: the authoring tools want the whole list, and a
    run wants to carry the ones that matter as warnings rather than stopping.
    """
    problems: List[str] = []
    tree = build(kpis)

    for child, parent in sorted(tree.dangling.items()):
        problems.append(
            f"{child}: driver_parent {parent!r} is not in this pack — the "
            f"decomposition breaks for anyone who runs on it")

    for kpi_id in sorted(tree.nodes):
        seen: set = set()
        current: Optional[str] = kpi_id
        while current:
            if current in seen:
                problems.append(
                    f"{kpi_id}: driver_parent chain loops through {current!r}")
                break
            seen.add(current)
            current = tree.nodes[current].parent

    self_parents = sorted(k.id for k in kpis if k.driver_parent == k.id)
    for kpi_id in self_parents:
        problems.append(f"{kpi_id}: is its own driver_parent")

    return problems
