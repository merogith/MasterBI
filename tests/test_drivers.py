"""The value-driver tree, and the two ways it was not real.

`driver_parent` is authored on **56 of the 80** shipped record sheets — fifty-six
careful statements about how a business works, ARR driven by NRR driven by GRR
driven by activation — and `grep -rn driver_parent kpi_maker/` returned exactly
one hit before `kpi/drivers.py`: the field declaration in `schema.py`.
`ARCHITECTURE.md` promises drill-down, a decomposition waterfall and a
defensible answer to "why is this metric on here?", all three built on it.

Building the graph exposed two problems the declaration alone could not:

1. **Two of the three packs barely had a tree.** E-commerce declared 4 parents
   across 20 KPIs — sixteen roots and a depth of one, so drill-down would have
   done nothing at all for eight of the ten sectors. The missing edges are
   authored now, and `test_a_pack_is_a_tree_not_a_list` is what stops a pack
   shipping with a tree that is all roots.
2. **A selection is not a library.** A run picks 25 of the 44 SaaS sheets, so a
   KPI's declared parent is frequently not on the scorecard —
   `rnd_pct_revenue` points at `arr_per_fte`, which was not selected. Built
   naively, a real run's tree shattered into **seven roots with seven dangling
   parents**. Lifting each child to its nearest *selected* ancestor gives one
   root and none.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.kpi.drivers import build, validate  # noqa: E402
from kpi_maker.kpi.schema import KPI  # noqa: E402
from kpi_maker.kpi.selection import load_library, select  # noqa: E402
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402

PACKS = ["saas", "ecommerce", "general"]


def _library(pack: str):
    return load_library([pack], include_user=False)


class _Fake:
    """A KPI-shaped stand-in, for graphs no library would ever contain."""

    def __init__(self, kpi_id: str, parent=None):
        self.id = kpi_id
        self.name = kpi_id
        self.driver_parent = parent


# --------------------------------------------------------------------------
# Every pack
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pack", PACKS)
def test_every_driver_parent_resolves_inside_its_own_pack(pack):
    """Resolving against the whole library would pass and still be broken.

    A `general.yaml` KPI whose parent lives in `saas.yaml` is fine for a
    software company and a dangling reference for the retailer who actually
    runs on that pack — and eight of the ten sectors run on `general`.
    """
    problems = validate(_library(pack))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("pack", PACKS)
def test_a_pack_is_a_tree_not_a_list(pack):
    """One root and real depth, or the drill-down has nothing to drill.

    E-commerce declared four parents across twenty KPIs before this item:
    sixteen roots, depth one. A graph that shape is a list wearing a tree's
    field name, and no assertion existed to notice.
    """
    library = _library(pack)
    tree = build(library)

    assert tree.roots == [tree.roots[0]], \
        f"{pack} has {len(tree.roots)} roots: {tree.roots}"
    assert max(node.depth for node in tree.nodes.values()) >= 3, \
        f"{pack} is only {max(n.depth for n in tree.nodes.values())} deep"

    orphans = [k.id for k in library if not k.driver_parent]
    assert len(orphans) == 1, \
        f"{pack} leaves {len(orphans)} KPIs outside the tree: {orphans}"


@pytest.mark.parametrize("pack", PACKS)
def test_every_path_reaches_the_root(pack):
    library = _library(pack)
    tree = build(library)
    root = tree.roots[0]
    for kpi in library:
        path = tree.path_to_root(kpi.id)
        assert path[0] == root, f"{kpi.id} climbs to {path[0]}, not {root}"
        assert path[-1] == kpi.id


@pytest.mark.parametrize("sector", [m.value for m in BusinessModel])
def test_every_declared_sector_gets_a_usable_tree(sector):
    """Ten sectors are offered, so ten sectors need a decomposition."""
    packs = list(sectors.resolve_packs(sector).value)
    library = load_library(packs, include_user=False)
    assert not validate(library)
    assert len(build(library).roots) == 1


# --------------------------------------------------------------------------
# A selection is not a library
# --------------------------------------------------------------------------

def test_a_selection_keeps_its_shape_through_the_gaps():
    """The lift, which is what makes a scorecard's tree readable.

    Measured on a real `northwind_saas` selection: seven roots and seven
    dangling parents without it, one root and none with it.
    """
    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    chosen = select(profile).kpis
    ancestry = {k.id: k.driver_parent
                for k in load_library(["saas"], include_user=False)}

    naive = build(chosen)
    assert len(naive.roots) > 1 and naive.dangling, \
        "the selection no longer has gaps, so this test proves nothing"

    lifted = build(chosen, ancestry)
    assert lifted.roots == ["arr"], lifted.roots
    assert not lifted.dangling, lifted.dangling
    assert lifted.path_to_root("rnd_pct_revenue")[0] == "arr"


def test_a_selected_kpi_is_never_dropped_from_the_tree():
    """Whatever its parent situation. Losing one silently would be the worst
    of the three options."""
    kpis = [_Fake("a"), _Fake("b", "nowhere"), _Fake("c", "a")]
    tree = build(kpis)
    assert set(tree.nodes) == {"a", "b", "c"}
    assert tree.dangling == {"b": "nowhere"}
    assert "b" in tree.roots


# --------------------------------------------------------------------------
# Graphs a library should never contain
# --------------------------------------------------------------------------

def test_a_cycle_is_reported_rather_than_hung_on():
    """Anything walking a looping tree hangs instead of failing, so the
    walkers guard and the validator names the loop."""
    kpis = [_Fake("a", "b"), _Fake("b", "a")]
    problems = validate(kpis)
    assert any("loops" in p for p in problems), problems

    tree = build(kpis)                       # must terminate
    assert tree.path_to_root("a")[:1] in ([ "a" ], ["b"])


def test_a_self_parent_is_reported():
    problems = validate([_Fake("a", "a")])
    assert any("its own driver_parent" in p for p in problems), problems


# --------------------------------------------------------------------------
# What a run actually serves
# --------------------------------------------------------------------------

def test_the_run_payload_carries_the_tree(tmp_path):
    """The first consumer of a field that has had none."""
    from kpi_maker.api.server import _driver_tree

    profile = load_profile(ROOT / "samples" / "northwind_saas.json")
    chosen = select(profile)
    kpi_set = json.loads(chosen.model_dump_json())

    tree = _driver_tree(kpi_set, profile)
    assert tree["roots"] == ["arr"]
    assert not tree["dangling"]
    assert tree["nodes"]["grr"]["parent"] == "nrr"


def test_ancestry_is_never_taken_from_the_merged_library():
    """Ids are unique within the packs a profile loads *together*, not globally.

    `gross_margin_pct` is in both `saas.yaml` and `ecommerce.yaml`,
    deliberately, because no company loads both — so a merged
    `load_library()` shadows one with the other and would lift a SaaS metric
    onto a retailer's parent. This is the check that the collision is real and
    that the run does not walk into it.
    """
    saas = {k.id: k.driver_parent for k in load_library(["saas"], include_user=False)}
    ecom = {k.id: k.driver_parent
            for k in load_library(["ecommerce"], include_user=False)}
    shared = set(saas) & set(ecom)
    assert shared, "no ids collide any more, so the hazard is gone"
    assert any(saas[i] != ecom[i] for i in shared), \
        f"colliding ids agree on their parent, so nothing could go wrong: {shared}"

    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    from kpi_maker.api.server import _driver_tree
    kpi_set = json.loads(select(profile).model_dump_json())
    tree = _driver_tree(kpi_set, profile)
    assert not tree["dangling"], tree["dangling"]


def test_the_library_still_validates_as_a_whole_where_ids_allow():
    """Belt and braces: the shipped sheets parse into real KPI objects."""
    library = load_library(["saas"], include_user=False)
    assert all(isinstance(k, KPI) for k in library)
    assert sum(1 for k in library if k.driver_parent) >= 40
