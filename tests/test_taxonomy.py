"""Twenty sectors, stated once.

The same facts used to live in three places with nothing to notice when they
disagreed: `BusinessModel` in `profile/schema.py`, the archetype and pack maps
in `profile/sectors.py`, and `SECTOR_LABELS` in `survey/questions.py`. Each way
of missing one fails differently — an enum value with no archetype degrades
silently, a label with no enum value 422s the survey, an archetype with no
label is unreachable — which is why none of them was obvious.

`profile/taxonomy.yaml` is the source now, and the checks below are what keeps
it the source rather than a fourth copy.

**On the official codes.** They are carried for credibility and for the
benchmark lookup 4.4 needs, and they are deliberately not load-bearing: the
archetype, the packs and the search work off `id`, `label` and `aliases`, so a
wrong code is a wrong label rather than a wrong scorecard. The NAICS two-digit
sector list was checked against census.gov; Eurostat is unreachable from this
environment, so the NACE titles are transcribed and the file says so. These
tests therefore check *shape and consistency*, and do not claim to have
verified the titles — a test that asserted a transcription against itself would
be worse than no test.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.profile import sectors, taxonomy  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402
from kpi_maker.survey.questions import QUESTION_BY_ID  # noqa: E402

TAXONOMY = taxonomy.load()

#: NACE section letters. 21 of them, A-U, confirmed independently.
NACE_SECTIONS = set("ABCDEFGHIJKLMNOPQRSTU")

#: NAICS 2022 two-digit sectors, three of which are ranges. Checked against
#: census.gov: 11, 21, 22, 23, 31-33, 42, 44-45, 48-49, 51-56, 61, 62, 71, 72,
#: 81, 92. A four-digit industry code has to start with one of them.
NAICS_SECTORS = {"11", "21", "22", "23", "31", "32", "33", "42", "44", "45",
                 "48", "49", "51", "52", "53", "54", "55", "56", "61", "62",
                 "71", "72", "81", "92"}


# --------------------------------------------------------------------------
# One source, three readers
# --------------------------------------------------------------------------

def test_the_enum_and_the_taxonomy_declare_the_same_sectors():
    """The enum is spelled out on purpose — it is the type mypy checks and the
    codebase branches on — so this is the seam that keeps it honest."""
    assert sorted(m.value for m in BusinessModel) == sorted(TAXONOMY.ids())


def test_the_survey_offers_every_sector_and_no_others():
    options = QUESTION_BY_ID["business_model"]["options"]
    offered = [o["value"] for o in options if not o["value"].startswith("__")]
    assert offered == TAXONOMY.ids(), \
        "the survey's sector list has drifted from the taxonomy's order"


def test_sectors_py_no_longer_keeps_its_own_maps():
    """The drift check. Two of these maps existed and had to agree with the
    file; a third copy is how the first two got out of step."""
    source = (ROOT / "kpi_maker" / "profile" / "sectors.py").read_text(encoding="utf-8")
    for gone in ("ARCHETYPE_EXACT: Dict", "ARCHETYPE_APPROXIMATION: Dict",
                 "PACKS_EXACT: Dict"):
        assert gone not in source, f"{gone} is back — the taxonomy is not the source"


# --------------------------------------------------------------------------
# Every sector resolves to something that exists
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_every_sector_resolves_to_a_registered_archetype(sector):
    assert sector.archetype in GENERATORS, \
        f"{sector.id} names archetype {sector.archetype!r}, which does not exist"


@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_every_pack_a_sector_names_exists(sector):
    library = ROOT / "kpi_maker" / "kpi" / "library"
    for pack in sector.packs:
        assert (library / f"{pack}.yaml").exists(), \
            f"{sector.id} names pack {pack!r}, which has no file"


@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_an_approximated_sector_says_why(sector):
    """The reason renders to the user, so it has to be an argument rather than
    an apology — and a sector that approximates silently is the failure this
    whole mechanism exists to prevent."""
    if sector.exact_archetype:
        assert sector.why is None
        return
    assert sector.why and len(sector.why) > 30, \
        f"{sector.id} approximates its archetype without a stated reason"
    note = sectors.resolve_archetype(sector.id).note
    assert note and sector.id in note and sector.why.split()[0] in note


def test_only_the_two_finished_sectors_claim_to_be_exact():
    """Twenty sectors is breadth, not twenty finished sectors. Anything else
    claiming exactness would be the "quietly borrowed another's content" bug
    the sector suite was written to catch."""
    assert sectors.supported_sectors() == ["ecommerce", "saas"]


# --------------------------------------------------------------------------
# The official codes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_every_sector_carries_both_classifications(sector):
    """A sector with only one is worse than one with neither: it looks
    classified while being unusable for half the benchmark sources."""
    assert sector.nace is not None, f"{sector.id} has no NACE code"
    assert sector.naics is not None, f"{sector.id} has no NAICS code"

    assert sector.nace.section in NACE_SECTIONS, sector.nace.section
    assert re.fullmatch(r"\d{2}", sector.nace.code), sector.nace.code
    assert sector.naics.code[:2] in NAICS_SECTORS, sector.naics.code
    assert re.fullmatch(r"\d{2}(-\d{2})?|\d{3,6}", sector.naics.code), \
        sector.naics.code
    assert sector.nace.title and sector.naics.title


def test_the_file_says_what_was_verified_and_what_was_not():
    """An uncited benchmark is worse than none, and the same holds for a code.

    Eurostat is blocked from this environment, so the NACE titles are
    transcribed. Recording that is the difference between a limitation and a
    quiet claim — and it is what tells the next person which half to re-check.
    """
    systems = TAXONOMY.systems
    for name in ("nace", "naics"):
        assert systems[name]["url"].startswith("https://")
        assert systems[name]["authority"]
        assert systems[name]["verification"], f"{name} does not say how it was checked"
    assert "not machine-checked" in systems["nace"]["verification"].lower() \
        or "transcribed" in systems["nace"]["verification"].lower(), \
        "the NACE entry implies a verification that did not happen"


def test_the_classification_line_names_both_systems():
    line = sectors.classification("retail")
    assert line and "NACE" in line and "NAICS" in line, line
    assert sectors.classification("not_a_sector") is None


# --------------------------------------------------------------------------
# Search — twenty options is past the point where a list is a choice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("typed,expected", [
    ("gym", "fitness"),
    ("shop", "retail"),
    ("haulage", "logistics"),
    ("dtc", "ecommerce"),
    ("consulting", "services"),
    ("restaurant", "food_service"),
    ("brewery", "food_production"),
    ("msp", "it_services"),
    ("architecture", "engineering"),
    ("wholesale", "distribution"),
])
def test_search_finds_the_sector_by_what_a_user_would_type(typed, expected):
    """`aliases` carries the words people reach for rather than the label the
    taxonomy happens to use — nobody types "Distribution or wholesale"."""
    hits = TAXONOMY.search(typed)
    assert hits, f"{typed!r} matched nothing"
    assert hits[0].id == expected, \
        f"{typed!r} ranked {hits[0].id} first, expected {expected}"


def test_an_empty_search_is_the_whole_list_in_order():
    assert [s.id for s in TAXONOMY.search("  ")] == TAXONOMY.ids()


def test_search_ranks_an_exact_alias_above_a_substring():
    """"media" is an alias of one sector and a substring of nothing else here,
    while "service" appears in several — the ranking has to prefer the sector
    that owns the word."""
    assert TAXONOMY.search("media")[0].id == "media"
    assert TAXONOMY.search("food service")[0].id == "food_service"
