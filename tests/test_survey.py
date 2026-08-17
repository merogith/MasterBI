"""The survey's own rules, actually enforced.

`survey/questions.py` opens with "Design rules, enforced by
`test_every_question_branches`" — and **no such test has ever existed**. A false
claim of enforcement is worse than no claim: it is the reason nobody checked.
This file is that test, plus the one the crash below demanded.

Two things are checked here, and they are different in kind:

* **Every answer must build a profile.** Not "should" — a survey answer that
  raises is a 422 in the middle of a form, and it shipped: answering "Worldwide"
  to *Where are your customers?* crashed for **GB, CA and AU**, three of the
  eight countries offered, because the global market split was a dict literal
  whose keys collided.
* **Every question must earn its place.** The ROADMAP rule is "if two answers
  produce the same dashboard, delete the question". The honest reading of
  "dashboard" is the whole profile, not the KPI list — `revenue_band` changes
  every number without changing which KPIs are chosen, and deleting it would be
  absurd.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.kpi.selection import select  # noqa: E402
from kpi_maker.profile.taxonomy import load as load_taxonomy  # noqa: E402
from kpi_maker.survey import build_profile, visible_questions  # noqa: E402
from kpi_maker.survey.defaults import geographies_for  # noqa: E402
from kpi_maker.survey.questions import QUESTIONS  # noqa: E402

# The sector list moved into `profile/taxonomy.yaml` in 4.1, so these read
# it rather than a hand-maintained copy in the survey module.
SECTORS = [s.id for s in load_taxonomy().sectors]

UNKNOWN = "__unknown__"

# A transactional consumer business — the profile least like the SaaS one the
# survey was written around, and therefore the one most likely to be asked
# something that does not apply to it.
B2C = {
    "objective": "growth", "audience": "exec", "country": "GB",
    "business_model": "ecommerce", "customer_type": "B2C",
    "sales_motion": "self_serve", "revenue_band": "5m_10m",
    "headcount_band": "51_200", "stage": "growth", "customer_mix": "smb_heavy",
    "reach": "domestic", "data_maturity": "spreadsheets",
    "kpi_experience": "medium", "contract_terms": "mixed",
}

COUNTRIES = [o["value"] for q in QUESTIONS if q["id"] == "country"
             for o in q["options"] if o["value"] != UNKNOWN]


def _answerable(question) -> list:
    return [o["value"] for o in question["options"] if o["value"] != UNKNOWN]


# --------------------------------------------------------------------------
# Nothing a user can click may raise
# --------------------------------------------------------------------------

@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("reach", ["domestic", "regional", "global"])
def test_every_market_split_sums_to_one(country, reach):
    """`CompanyProfile` requires exactly 1.0, and three countries gave 0.750.

    The global split repeated a market key — `{GB: .45, US: .25, DE: .18,
    US: .12}` — and the later entry won, so 0.25 vanished. Additive now, and
    normalised, so a future edit to the weights cannot bring it back.
    """
    shares = geographies_for(reach, country)
    assert sum(shares.values()) == pytest.approx(1.0), shares
    assert all(share > 0 for share in shares.values()), shares


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q["id"])
def test_every_answer_to_every_question_builds_a_profile(question):
    """A survey option that raises is a dead end wearing a radio button."""
    for value in _answerable(question) + [UNKNOWN]:
        try:
            build_profile({**B2C, question["id"]: value})
        except Exception as exc:                            # noqa: BLE001
            pytest.fail(f"{question['id']}={value!r} raised {type(exc).__name__}: "
                        f"{str(exc)[:200]}")


@pytest.mark.parametrize("sector", SECTORS)
def test_every_offered_sector_produces_a_scorecard(sector):
    """The survey offers ten sectors, so ten sectors have to work end to end."""
    kpis = select(build_profile({**B2C, "business_model": sector}))
    assert len(kpis.kpis) >= 8, f"{sector} produced {len(kpis.kpis)} KPIs"
    assert kpis.north_star in {k.id for k in kpis.kpis}


def test_an_empty_answer_set_is_a_complete_profile():
    """Every question skipped is still a valid, fully defaulted run."""
    profile = build_profile({})
    assert profile.market.customer_count > 0
    assert profile.provenance, "nothing recorded how any of this was decided"


# --------------------------------------------------------------------------
# Every question earns its place
# --------------------------------------------------------------------------

def _fingerprint(answers) -> str:
    """What this set of answers actually produces.

    The whole profile, not the KPI list. `revenue_band` changes every number in
    the pack without changing which KPIs are chosen, and a rule that called
    that "no effect" would recommend deleting it.
    """
    profile = build_profile(answers)
    return profile.model_dump_json(exclude={"provenance", "seed"})


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q["id"])
def test_every_question_branches(question):
    """The rule `questions.py` has always said was enforced, now enforcing it.

    "If two answers produce an identical dashboard, the question is deleted."
    A question whose every answer yields the same profile is a form field that
    costs a user thirty seconds and buys them nothing.

    **Judged against a respondent who is actually asked it.** Branching and this
    rule interact, and getting the interaction backwards would be worse than
    having neither: `contract_terms` produces one identical profile for a
    retailer *because it is correctly hidden from them*, and a test that read
    that as "delete the question" would delete the question subscription
    businesses need. The rule is "every question changes something for the
    people it is put to", and hiding it from everyone else is the other half of
    the same rule rather than a violation of it.
    """
    base = dict(B2C)
    for other, allowed in (question.get("show_if") or {}).items():
        base[other] = allowed[0]

    reference = _fingerprint(base)
    outcomes = {_fingerprint({**base, question["id"]: value})
                for value in _answerable(question)}
    outcomes.add(reference)

    assert len(outcomes) > 1, (
        f"{question['id']!r} produces the same profile whatever it is answered, "
        f"even for a respondent who is asked it — it fills nothing and branches "
        f"nothing, so it should be deleted or wired to something")


def test_the_question_ids_are_unique():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)), "a duplicate id shadows a question"


def test_every_question_offers_a_way_out():
    """"I don't know" everywhere, or an option set nobody can fail to answer.

    Objective, audience, country and sector have no unknown branch on purpose:
    they are choices only the user can make, and defaulting them silently would
    produce a report about a business nobody described.
    """
    deliberate = {"objective", "audience", "country", "business_model"}
    for question in QUESTIONS:
        has_unknown = any(o["value"] == UNKNOWN for o in question["options"])
        assert has_unknown or question["id"] in deliberate, \
            f"{question['id']} has no 'I don't know' and is not one of the four " \
            f"questions that deliberately require an answer"


# --------------------------------------------------------------------------
# Branching
# --------------------------------------------------------------------------

def test_a_retailer_is_not_asked_about_contract_terms():
    """The two questions with no possible effect on a transactional business.

    `contract_terms` fills `annual_prepay_share` and `avg_contract_months`, and
    `grep` finds exactly two readers, both inside `datagen/subscription.py`.
    `sales_motion` is read there and by seven `applies_when` clauses, all in
    `saas*.yaml`. Verified before branching by varying every answer against an
    e-commerce profile: identical generated data, identical scorecard. A
    retailer was answering into a void, twice.
    """
    asked = {q["id"] for q in visible_questions({**B2C, "business_model": "retail"})}
    assert "contract_terms" not in asked
    assert "sales_motion" not in asked

    saas = {q["id"] for q in visible_questions({**B2C, "business_model": "saas"})}
    assert {"contract_terms", "sales_motion"} <= saas, \
        "the questions vanished for the businesses they exist for"


def test_an_unanswered_condition_keeps_the_question():
    """Nothing is hidden before the question it depends on has been answered.

    The survey is answered top to bottom. Hiding something because a condition
    is merely undecided would make questions appear and disappear as the user
    works, which is worse than one extra question.
    """
    asked = {q["id"] for q in visible_questions({})}
    assert {"contract_terms", "sales_motion"} <= asked


def test_a_hidden_answer_never_reaches_the_profile():
    """Change "software" to "retail" halfway and the stale answer must go.

    Otherwise the profile carries a fact about the business that the user was
    never asked to confirm and can no longer see or correct.
    """
    switched = build_profile({**B2C, "business_model": "retail",
                              "contract_terms": "multi_year",
                              "sales_motion": "tender"})
    default = build_profile({**B2C, "business_model": "retail"})

    assert switched.business_model.contract_terms == default.business_model.contract_terms
    assert switched.business_model.sales_motion == default.business_model.sales_motion
    assert switched.provenance["business_model.contract_terms"].startswith(
        "benchmark_default"), "a discarded answer was recorded as the user's"


def test_the_gate_follows_the_archetype_registry():
    """`show_if` lists are derived from `sectors.py`, never hand-written.

    When a sector gains its own subscription archetype it must start being
    asked these questions with nothing in `questions.py` to remember to edit —
    which is only true while the list is generated. This is the check that it
    still is.
    """
    from kpi_maker.profile import sectors
    from kpi_maker.survey.questions import QUESTION_BY_ID

    expected = sorted(sector for sector in SECTORS
                      if sectors.resolve_archetype(sector).value == "saas")
    for qid in ("contract_terms", "sales_motion"):
        gate = QUESTION_BY_ID[qid]["show_if"]["business_model"]
        assert sorted(gate) == expected, (
            f"{qid} is gated on {sorted(gate)} but the subscription archetype "
            f"now covers {expected}")


def test_the_two_evaluators_agree():
    """`is_visible` exists twice — in Python and in TypeScript — on purpose.

    The browser has to decide visibility as the user answers and the server has
    to decide it again when the answers arrive. A shared expression language
    would be two parsers to keep in step; `show_if` is data, so each side is
    five lines. This asserts the *rules* those five lines implement have not
    drifted.
    """
    source = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "export function isVisible" in source, \
        "the browser lost its evaluator; every question would be shown to everyone"
    # The unanswered-condition rule is the one that is easy to drop, and
    # dropping it changes behaviour rather than breaking a build.
    assert "UNANSWERED.includes(given)" in source, \
        "the browser no longer treats an unanswered condition as 'keep showing'"
    assert "'__unknown__'" in source, \
        "the browser's unanswered set has lost the survey's own UNKNOWN value"


def test_branching_does_not_strand_a_respondent_mid_survey():
    """Every sector must still leave enough questions to build a profile."""
    for sector in SECTORS:
        asked = visible_questions({**B2C, "business_model": sector})
        ids = {q["id"] for q in asked}
        assert len(ids) >= 15, f"{sector} was left with only {len(ids)} questions"
        assert {"objective", "audience", "business_model", "revenue_band"} <= ids, \
            f"{sector} lost a question the profile cannot be built without"
