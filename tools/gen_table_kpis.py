"""Derive "which fact table unlocks which KPIs" from the engine that knows.

    python tools/gen_table_kpis.py           # rewrite the generated module
    python tools/gen_table_kpis.py --check   # fail if it is stale

`ingest/quality.py` answers the single most motivating question a partial
upload raises: *if I go and find the headcount roster, what do I get for it?*
That answer used to come from a hand-written dict of SaaS KPI ids. Two things
were wrong with that, and only one of them was visible:

* **Visibly**, an e-commerce or cross-sector profile intersected with a SaaS-only
  map to nothing, so a retailer uploading a P&L was told it unlocked *zero*
  KPIs — the exact discouragement the report exists to remove.
* **Invisibly**, the map is a second statement of a fact the metrics engine
  already owns, so it was wrong the moment anyone added a KPI. It was: `orders`,
  `traffic`, `inventory` and `buyers` were not keys at all, and thirteen shipped
  KPIs read a table the map did not connect them to.

So derive it. The definition used here is the one the user is actually asking
about: **a table unlocks the KPIs that stop computing when you take it away.**
Not "mentions the table" — `MetricResult.tables_used` over-reports, because a
metric may probe an optional table and carry on without it. Removing the table
and re-running is the experiment that matches the question, and it needs no
declaration from anybody.

One limit, stated rather than hidden: a KPI that does not compute on the sample
profile even with every table present cannot be attributed to a table by this
experiment, so it is absent from the map. `cash_runway_months` is the current
example. Absent means the report stays quiet about it, which is the same
"no opinion" answer `_kpis_needing` gives — never a claim of zero.

Generated rather than computed at import, following `tools/gen_tokens.py`:
deriving it costs a synthetic dataset per archetype (~9s), which is fine in a
test and not fine on the request that renders a quality report. Staleness is
caught by `test_the_generated_table_map_matches_the_engine`, which re-derives
and compares, so the file cannot rot without a red build; `--check` is here for
running the same question by hand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.kpi.schema import KPISet  # noqa: E402
from kpi_maker.kpi.selection import load_library  # noqa: E402
from kpi_maker.metrics.engine import compute  # noqa: E402
from kpi_maker.profile import sectors  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402

TARGET = ROOT / "kpi_maker" / "ingest" / "table_kpis.py"

# One profile per archetype, chosen only for its shape — the KPI universe below
# is deliberately every pack, not this profile's selection, so the map covers
# KPIs this particular company would never be shown.
PROFILES: Dict[str, Path] = {
    "saas": ROOT / "samples" / "northwind_saas.json",
    "ecommerce": ROOT / "samples" / "kestrel_retail.json",
    "project": ROOT / "samples" / "halberd_consulting.json",
    "production": ROOT / "samples" / "orbis_works.json",
}


def _packs_for_archetype(archetype: str) -> List[str]:
    """Every pack any sector simulated by this archetype can select from.

    Over every *declared* sector, not `sectors.supported_sectors()` — that
    returns only the two with their own pack, which would drop `general`, the
    pack eight of the ten sectors actually run on. Read through `sectors` rather
    than listed here, so a sector gaining its own pack widens this
    automatically, which is the whole point of that module.
    """
    packs: List[str] = []
    for sector in (m.value for m in BusinessModel):
        if sectors.resolve_archetype(sector).value != archetype:
            continue
        for pack in sectors.resolve_packs(sector).value:
            if pack not in packs:
                packs.append(pack)
    return packs


def derive() -> Dict[str, List[str]]:
    """{fact table: [kpi ids that stop computing without it]}."""
    unlocks: Dict[str, Set[str]] = {}

    for archetype, sample in sorted(PROFILES.items()):
        profile = load_profile(sample)
        data = GENERATORS[archetype](profile)

        library = load_library(_packs_for_archetype(archetype), include_user=False)
        # `north_star` is required by the model and unread by `compute`; the
        # universe is what matters here, not the scorecard shape.
        universe = KPISet(north_star=library[0].id, kpis=library)

        tables = dict(data.tables)
        computable = {r.kpi.id for r in compute(universe, dict(tables), profile)
                      if r.computed}

        for table in sorted(tables):
            without = {k: v for k, v in tables.items() if k != table}
            survives = {r.kpi.id for r in compute(universe, without, profile)
                        if r.computed}
            lost = computable - survives
            if lost:
                unlocks.setdefault(table, set()).update(lost)

    return {table: sorted(ids) for table, ids in sorted(unlocks.items())}


HEADER = '''"""Which KPIs each fact table unlocks. Generated — do not edit.

    python tools/gen_table_kpis.py

Derived by removing one table at a time from a full synthetic dataset and
recording which KPIs stop computing, across every archetype and every pack. See
`tools/gen_table_kpis.py` for why this is derived rather than declared, and
`kpi_maker/ingest/quality.py` for what reads it.
"""
from __future__ import annotations

from typing import Dict, List

TABLE_KPIS: Dict[str, List[str]] = {
'''


def render(unlocks: Dict[str, List[str]]) -> str:
    lines = [HEADER]
    for table, ids in unlocks.items():
        lines.append(f'    "{table}": [\n')
        for kpi_id in ids:
            lines.append(f'        "{kpi_id}",\n')
        lines.append("    ],\n")
    lines.append("}\n")
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the generated file is stale")
    args = parser.parse_args()

    rendered = render(derive())
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""

    if args.check:
        if rendered != current:
            print(f"{TARGET.relative_to(ROOT)} is stale — run "
                  f"`python tools/gen_table_kpis.py`", file=sys.stderr)
            return 1
        print(f"{TARGET.relative_to(ROOT)} is current")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
