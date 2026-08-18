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
wrong code is a wrong label rather than a wrong scorecard.

They are now **checked against a published source**, which the first version of
this file could not do — every Eurostat host and national mirror is blocked by
this environment's egress policy, so the NACE titles were transcribed and these
tests only checked their shape. That was not good enough, and it hid a real
bug: four sectors carried a section title truncated to its first word, because
`{section_title: Arts, entertainment and recreation}` is a YAML flow mapping
that splits on its own commas. A shape check cannot see a wrong string.

The way through was **ISIC Rev. 4**, which is reachable: NACE Rev. 2 is the
European implementation of it and its sections and divisions are identical,
with NACE adding detail only below the level this taxonomy uses. The UN's
classification ships in the `isic` package on PyPI — one of the few hosts this
environment allows — and the twenty-one section and eighteen division titles
taken from it are vendored below. All twenty sectors match it exactly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.profile import sectors, taxonomy  # noqa: E402
from kpi_maker.profile.schema import BusinessModel  # noqa: E402
from kpi_maker.survey.questions import QUESTION_BY_ID  # noqa: E402

TAXONOMY = taxonomy.load()

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

# --------------------------------------------------------------------------
# The official codes, checked against a published source
# --------------------------------------------------------------------------

#: ISIC Rev. 4 sections, from the UN Statistics Division's classification as
#: published in the `isic` package on PyPI. **NACE Rev. 2's sections and
#: divisions are identical to ISIC Rev. 4's** — NACE is the European
#: implementation of ISIC and adds detail only at group and class level, below
#: anything this taxonomy uses — so checking against ISIC checks the NACE codes
#: at exactly the level they are stated.
#:
#: Vendored rather than imported: this is reference data that must not change
#: under the suite, and a runtime dependency on a third-party package for
#: twenty-one strings would be a worse trade than twenty-one strings.
NACE_SECTIONS = {
    "A": "Agriculture, forestry and fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam and air conditioning supply",
    "E": "Water supply; sewerage, waste management and remediation activities",
    "F": "Construction",
    "G": "Wholesale and retail trade; repair of motor vehicles and motorcycles",
    "H": "Transportation and storage",
    "I": "Accommodation and food service activities",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M": "Professional, scientific and technical activities",
    "N": "Administrative and support service activities",
    "O": "Public administration and defence; compulsory social security",
    "P": "Education",
    "Q": "Human health and social work activities",
    "R": "Arts, entertainment and recreation",
    "S": "Other service activities",
    "T": "Activities of households as employers; undifferentiated goods- and "
         "services-producing activities of households for own use",
    "U": "Activities of extraterritorial organizations and bodies",
}

#: Every division this taxonomy names, same source. Only the ones in use: a
#: transcription of all 88 would be 70 lines nothing checks.
NACE_DIVISIONS = {
    "10": "Manufacture of food products",
    "25": "Manufacture of fabricated metal products, except machinery and equipment",
    "41": "Construction of buildings",
    "46": "Wholesale trade, except of motor vehicles and motorcycles",
    "47": "Retail trade, except of motor vehicles and motorcycles",
    "49": "Land transport and transport via pipelines",
    "55": "Accommodation",
    "56": "Food and beverage service activities",
    "58": "Publishing activities",
    "62": "Computer programming, consultancy and related activities",
    "63": "Information service activities",
    "68": "Real estate activities",
    "70": "Activities of head offices; management consultancy activities",
    "71": "Architectural and engineering activities; technical testing and analysis",
    "73": "Advertising and market research",
    "85": "Education",
    "86": "Human health activities",
    "93": "Sports activities and amusement and recreation activities",
}


@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_every_nace_code_matches_the_published_classification(sector):
    """Not "the code looks well-formed" — the code and its title, against the
    source.

    The shape-only version of this check passed while **four sectors carried a
    truncated section title**: `{section_title: Arts, entertainment and
    recreation}` is a YAML flow mapping, so it split on its own commas and
    stored "Arts" with the rest as a junk key. Nothing noticed, because nothing
    compared the string to anything, and `classification()` does not print the
    section title — so it was invisible on screen too.
    """
    assert NACE_SECTIONS[sector.nace.section] == sector.nace.section_title, \
        f"{sector.id}: section {sector.nace.section} title does not match ISIC/NACE"
    assert NACE_DIVISIONS[sector.nace.code] == sector.nace.title, \
        f"{sector.id}: division {sector.nace.code} title does not match ISIC/NACE"


@pytest.mark.parametrize("sector", TAXONOMY.sectors, ids=lambda s: s.id)
def test_a_code_block_carries_exactly_the_fields_it_should(sector):
    """The other half of the same bug: the junk key existed because nothing
    said what a `nace:` block is allowed to contain."""
    raw = yaml.safe_load(
        (ROOT / "kpi_maker" / "profile" / "taxonomy.yaml").read_text(encoding="utf-8"))
    entry = next(e for e in raw["sectors"] if e["id"] == sector.id)
    assert set(entry["nace"]) == {"section", "section_title", "division",
                                  "division_title"}, entry["nace"]
    assert set(entry["naics"]) == {"code", "title"}, entry["naics"]


def test_every_classification_records_how_it_was_checked():
    """An uncited benchmark is worse than none, and the same holds for a code.

    The first version of this asserted the *opposite* claim — that the NACE
    entry admits to being transcribed rather than checked — which was the right
    assertion while it was true and the wrong one to keep. What has to hold in
    both worlds is that the file states its authority, its source and the
    check, so the next person knows what was actually done.
    """
    systems = TAXONOMY.systems
    for name in ("nace", "naics"):
        assert systems[name]["url"].startswith("https://")
        assert systems[name]["authority"]
        assert systems[name]["verification"], f"{name} does not say how it was checked"

    # NACE cannot be checked against Eurostat from here, so it says what it was
    # checked against instead. A claim of "verified" with no named source would
    # be the thing this file exists to avoid.
    assert "ISIC" in systems["nace"]["checked_against"], systems["nace"]
    assert "census.gov" in systems["naics"]["verification"]


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
