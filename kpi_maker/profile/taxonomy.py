"""The sector list, read from one file instead of stated in three.

Before this, twenty sectors' worth of facts lived in three places that had to
agree and had no way of noticing when they did not: `BusinessModel` in
`profile/schema.py`, `ARCHETYPE_EXACT`/`ARCHETYPE_APPROXIMATION`/`PACKS_EXACT`
in `profile/sectors.py`, and `SECTOR_LABELS` in `survey/questions.py`. Adding a
sector meant editing all three, and forgetting one of them fails differently in
each: an enum value with no archetype degrades silently, a label with no enum
value 422s the survey, an archetype with no label is unreachable.

`taxonomy.yaml` is now the source and every one of those reads it. The enum
still exists — it is the type the rest of the codebase branches on and mypy
checks — but `tests/test_taxonomy.py` fails if it and the file disagree, which
is the same generated-not-declared pattern as `tools/gen_tokens.py` and
`tools/gen_table_kpis.py`.

**The official codes are not load-bearing, deliberately.** NACE and NAICS are
carried for credibility and for the benchmark lookup 4.4 needs — Eurostat SBS
is keyed by NACE, SEC/XBRL frames and Census data by NAICS — while the
archetype, the packs and the search all work off `id`, `label` and `aliases`.
A wrong code is then a wrong label rather than a wrong scorecard. What was and
was not verified is recorded in the file itself; Eurostat is unreachable from
this environment, and saying so beats implying a check that did not happen.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

TAXONOMY_PATH = Path(__file__).with_name("taxonomy.yaml")


@dataclass(frozen=True)
class Code:
    """One sector's place in one official classification."""
    system: str
    code: str
    title: str
    #: NACE only: the section letter the division sits under.
    section: Optional[str] = None
    section_title: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.code} {self.title}"


@dataclass(frozen=True)
class Sector:
    id: str
    label: str
    aliases: Tuple[str, ...]
    archetype: str
    packs: Tuple[str, ...]
    #: Why this archetype is a fair approximation. None iff the sector has its
    #: own archetype, which is what `exact_archetype` is derived from.
    why: Optional[str]
    nace: Optional[Code]
    naics: Optional[Code]

    @property
    def exact_archetype(self) -> bool:
        return self.why is None

    @property
    def exact_packs(self) -> bool:
        return self.packs != ("general",)

    @property
    def codes(self) -> List[Code]:
        return [c for c in (self.nace, self.naics) if c is not None]

    def classification(self) -> str:
        """One line naming this sector's official codes, for the appendix.

        Both systems, because a Turkish or German reader knows NACE and an
        American reader knows NAICS, and printing only one makes the other
        wonder whether the classification was guessed.
        """
        parts = []
        if self.nace is not None:
            parts.append(f"NACE {self.nace.section}{self.nace.code} "
                         f"({self.nace.title})")
        if self.naics is not None:
            parts.append(f"NAICS {self.naics.code} ({self.naics.title})")
        return " · ".join(parts)


@dataclass(frozen=True)
class Taxonomy:
    version: int
    systems: Dict[str, Dict[str, str]]
    sectors: Tuple[Sector, ...]

    #: Built once in `load`. A `lru_cache`d method would need the whole
    #: taxonomy to be hashable, and it carries a dict of systems.
    index: Dict[str, Sector]

    def get(self, sector_id: str) -> Optional[Sector]:
        return self.index.get(sector_id)

    def ids(self) -> List[str]:
        return [s.id for s in self.sectors]

    def search(self, query: str) -> List[Sector]:
        """Sectors matching a typed query, best first.

        Twenty options is past the point where a list is a choice, so the
        survey needs a search box, and a search box needs to match what people
        actually type. `aliases` carries the words a user reaches for — "shop",
        "gym", "haulage", "dtc" — rather than the label the taxonomy happens to
        use. Ranked so an exact alias beats a prefix beats a substring; below
        that the taxonomy's own order stands, which puts the two sectors with
        their own content first.
        """
        needle = query.strip().lower()
        if not needle:
            return list(self.sectors)

        ranked: List[Tuple[int, int, Sector]] = []
        for position, sector in enumerate(self.sectors):
            haystack = [sector.label.lower(), sector.id.replace("_", " ")]
            haystack += [a.lower() for a in sector.aliases]
            score = None
            for text in haystack:
                if text == needle:
                    score = 0
                elif text.startswith(needle):
                    score = min(1 if score is None else score, 1)
                elif needle in text:
                    score = min(2 if score is None else score, 2)
            if score is not None:
                ranked.append((score, position, sector))

        ranked.sort(key=lambda row: (row[0], row[1]))
        return [sector for _, _, sector in ranked]


def _code(raw: Optional[dict], system: str) -> Optional[Code]:
    if not raw:
        return None
    return Code(system=system, code=str(raw["division" if system == "nace" else "code"]),
                title=str(raw["division_title" if system == "nace" else "title"]),
                section=raw.get("section"), section_title=raw.get("section_title"))


@lru_cache(maxsize=1)
def load() -> Taxonomy:
    """Parsed once. The file is shipped inside the package and never edited at
    runtime, so caching it costs nothing and saves a YAML parse per call."""
    raw = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    sectors = tuple(
        Sector(
            id=entry["id"],
            label=entry["label"],
            aliases=tuple(entry.get("aliases", ())),
            archetype=entry["archetype"],
            packs=tuple(entry.get("packs", ["general"])),
            why=entry.get("why"),
            nace=_code(entry.get("nace"), "nace"),
            naics=_code(entry.get("naics"), "naics"),
        )
        for entry in raw["sectors"]
    )
    return Taxonomy(version=int(raw["version"]), systems=raw["systems"],
                    sectors=sectors, index={s.id: s for s in sectors})
