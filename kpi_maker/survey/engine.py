"""Survey answers -> CompanyProfile.

The whole of Mode 2 is this function. Once it returns a valid profile the rest
of the pipeline is identical to Mode 1 — that is the point of the profile
contract, and if this module ever needs to touch anything downstream, the
contract has leaked.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

from ..profile.schema import CompanyProfile
from . import defaults as D
from .questions import QUESTION_BY_ID, QUESTIONS

UNKNOWN = "__unknown__"

# Provenance for a figure read off the user's own file. The exact `ingested:`
# tag naming the file is set by `ingest/derive.py`; this is the fallback for a
# value that reached the solve without one, and it still has to read as
# measured rather than assumed — the appendix draws that line.
_measured_provenance = "ingested:upload"

# Name parts for generated companies. Deliberately fictional-sounding: a
# generated report must never look like it describes a real business.
_PREFIX = ["North", "Cobalt", "Vertex", "Lumen", "Harbor", "Quill", "Aster",
           "Ridge", "Beacon", "Ember", "Sable", "Terra", "Onyx", "Vale"]
_SUFFIX = ["wind", "line", "point", "works", "field", "stack", "wave", "core",
           "gate", "path", "scope", "bridge"]
_KIND = ["Analytics", "Systems", "Labs", "Software", "Technologies", "Data",
         "Platform", "Cloud"]


def is_visible(question: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    """Whether this question applies to the business described so far.

    `show_if` is `{question_id: [answers that keep it]}`, every entry having to
    match. Data rather than an expression, so the browser and this module can
    each decide it in five lines without two parsers drifting apart.

    A condition whose own question is unanswered keeps the question visible:
    the survey is answered top to bottom, and hiding something because a
    later-but-unanswered question has not decided yet would make questions
    appear and disappear as the user works.
    """
    for other, allowed in (question.get("show_if") or {}).items():
        given = answers.get(other)
        if given in (None, "", UNKNOWN):
            continue
        if given not in allowed:
            return False
    return True


def visible_questions(answers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The questions this respondent should actually be asked."""
    return [q for q in QUESTIONS if is_visible(q, answers)]


def _answer(answers: Dict[str, Any], qid: str) -> Tuple[Any, bool]:
    """Return (value, was_defaulted).

    An answer to a question this respondent should never have seen is
    discarded. Otherwise changing "software" to "retail" halfway through would
    leave the contract-terms answer behind, and the profile would carry a fact
    about the business that the user was never asked to confirm and cannot see.
    """
    q = QUESTION_BY_ID[qid]
    raw = answers.get(qid)
    if not is_visible(q, answers):
        return q["default"], True
    if raw is None or raw == UNKNOWN or raw == "":
        return q["default"], True
    return raw, False


def _explicit(answers: Dict[str, Any], qid: str) -> Optional[Any]:
    """The answer only if the user actually gave one, and was asked.

    Optional questions must not fall back to a default: the entire point of
    the deep-dive block is that an unanswered question leaves the derived
    assumption in place (and correctly flagged as derived).
    """
    if not is_visible(QUESTION_BY_ID[qid], answers):
        return None
    raw = answers.get(qid)
    if raw is None or raw == UNKNOWN or raw == "":
        return None
    return raw


def build_profile(answers: Dict[str, Any], *, name: Optional[str] = None,
                  seed: Optional[int] = None,
                  measured: Optional[Dict[str, Any]] = None) -> CompanyProfile:
    """Turn survey answers into a validated, internally consistent profile.

    `measured` carries figures read off an uploaded file, by profile path, and
    it has to arrive **here** rather than be patched on afterwards. The
    consistency step below *solves* customer count from revenue so the profile's
    cross-block validator passes by construction; overwriting revenue after that
    breaks the equation it just satisfied. Measured on a real 12-month export:
    `revenue does not reconcile with the customer book: 312 customers x 24,000
    blended ACV = 7,488,000, but financials.revenue is 1,270,200 (490% apart)` —
    a 422 on a perfectly good file, from a validator that was right.
    """
    rng = random.Random(seed if seed is not None else 20250806)
    provenance: Dict[str, str] = {}
    measured = dict(measured or {})

    def take(qid: str, path: str) -> Any:
        value, defaulted = _answer(answers, qid)
        provenance[path] = (
            f"benchmark_default:{qid}={value}" if defaulted else "user_survey"
        )
        return value

    objective = take("objective", "intent.primary_objective")
    audience = take("audience", "intent.audience")
    country = take("country", "identity.country")
    model = take("business_model", "business_model.type")
    customer_type = take("customer_type", "business_model.customer_type")
    sales_motion = take("sales_motion", "business_model.sales_motion")
    revenue_band = take("revenue_band", "financials.revenue")
    headcount_band = take("headcount_band", "size.headcount_total")
    stage = take("stage", "size.stage")
    mix_key = take("customer_mix", "market.segments")
    reach = take("reach", "market.geographies")
    data_maturity = take("data_maturity", "org_culture.data_maturity")
    kpi_experience = take("kpi_experience", "org_culture.kpi_experience")

    revenue = D.REVENUE_BANDS.get(revenue_band, 7_500_000)
    headcount = D.HEADCOUNT_BANDS.get(headcount_band, 110)

    if "financials.revenue" in measured:
        revenue = float(measured.pop("financials.revenue"))
        provenance["financials.revenue"] = _measured_provenance
    measured.pop("size.revenue_band", None)   # a band is a coarser restatement

    # --- The consistency step -------------------------------------------
    # Segment mix gives us a blended ACV. Customer count is then SOLVED, not
    # guessed, so `customers x blended_acv == revenue` holds exactly and the
    # profile's cross-block validator passes by construction.
    segments, blended_acv = D.segments_for(mix_key, revenue_band, stage)
    customer_count = max(3, round(revenue / blended_acv)) if blended_acv else 0
    provenance["market.customer_count"] = (
        f"derived:revenue/blended_acv={blended_acv:,.0f}"
    )

    if "market.customer_count" in measured:
        # Both sides measured, so the assumption that has to yield is the one
        # nobody measured: the segment ACVs. Scaling them keeps the identity
        # exact instead of letting two true numbers fail a validator between
        # them.
        counted = max(1, int(measured.pop("market.customer_count")))
        implied = revenue / counted
        if blended_acv > 0:
            scale = implied / blended_acv
            segments = [{**s, "avg_acv": round(s["avg_acv"] * scale)}
                        for s in segments]
        customer_count = counted
        provenance["market.customer_count"] = _measured_provenance
        provenance["market.segments.avg_acv"] = (
            "derived:revenue/measured customer count")

    # --- Optional deep-dive answers override the derived assumptions -------
    churn_answer = _explicit(answers, "churn_level")
    if churn_answer is not None:
        segments = D.rescale_churn(segments, D.CHURN_BANDS[churn_answer])
        provenance["market.segments.logo_churn_annual"] = "user_survey"

    growth_answer = _explicit(answers, "growth_rate")
    growth_rate = D.GROWTH_BANDS.get(growth_answer) if growth_answer else None
    if growth_rate is not None:
        provenance["financials.growth_rate_yoy"] = "user_survey"
    else:
        provenance["financials.growth_rate_yoy"] = f"benchmark_default:stage={stage}"

    contract_answer = _explicit(answers, "contract_terms") or "mixed"
    contract = D.CONTRACT_SHAPES.get(contract_answer, D.CONTRACT_SHAPES["mixed"])
    provenance["business_model.contract_terms"] = (
        "user_survey" if _explicit(answers, "contract_terms") else "benchmark_default:mixed"
    )

    ownership_answer = _explicit(answers, "ownership")
    ownership = D.OWNERSHIP_MAP.get(ownership_answer or "", "vc_backed")
    provenance["size.ownership"] = (
        "user_survey" if ownership_answer else "benchmark_default:vc_backed"
    )

    cadence_answer = _explicit(answers, "cadence")
    cadence = cadence_answer or "monthly"

    # Both priors branch on the archetype as well as the stage: the tables in
    # `defaults.py` were measured on subscription software, and applied to a
    # consultancy they produce a 72% gross margin and a 0.7% EBITDA margin. See
    # the note there. Resolved through `sectors` rather than off the sector id,
    # so a sector moving onto its own archetype picks up that archetype's
    # priors with nothing here to edit.
    from ..profile import sectors

    archetype = sectors.resolve_archetype(model).value
    opex = D.opex_for(archetype, stage)

    margin_answer = _explicit(answers, "gross_margin")
    if margin_answer is not None:
        gross_margin = D.GROSS_MARGIN_BANDS[margin_answer]
        provenance["financials.gross_margin_pct"] = "user_survey"
    else:
        gross_margin = D.gross_margin_for(archetype, stage)
        provenance["financials.gross_margin_pct"] = (
            f"benchmark_default:archetype={archetype},stage={stage}")

    cash = revenue / 12.0 * D.CASH_MONTHS_BY_STAGE.get(stage, 6.0)
    provenance["financials.opex_split"] = (
        f"benchmark_default:archetype={archetype},stage={stage}")
    provenance["financials.cash"] = f"benchmark_default:stage={stage}"
    provenance["size.headcount_by_function"] = "benchmark_default:saas_function_mix"

    # Data availability: survey respondents never have survey/support exports
    # wired up, so the KPIs needing them are dropped with a recorded reason
    # rather than shown as empty.
    has = ["billing", "crm", "gl", "hris", "product_analytics"]
    missing = ["survey", "support_desk"]
    if data_maturity in ("none", "spreadsheets"):
        missing.append("marketing_automation")
    else:
        has.append("marketing_automation")

    company_name = name or generate_name(rng)

    profile = CompanyProfile(
        identity={
            "name": company_name,
            "country": country,
            "currency": D.currency_for(country),
            "fiscal_year_start": "01-01",
            "language": "en",
        },
        industry={
            "taxonomy": "internal",
            "internal_sector": f"{model}.general",
            "vertical_tags": [customer_type.lower()],
        },
        business_model={
            "type": model,
            "customer_type": customer_type,
            "revenue_model": ["subscription"],
            "sales_motion": sales_motion,
            "contract_terms": contract_answer,
            "annual_prepay_share": contract["annual_prepay_share"],
            "avg_contract_months": contract["avg_contract_months"],
        },
        size={
            "headcount_total": headcount,
            "headcount_by_function": D.headcount_split(headcount),
            "revenue_band": revenue_band,
            "stage": stage,
            "age_years": {"early": 3, "growth": 7, "established": 14,
                          "mature": 22, "turnaround": 16}.get(stage, 8),
            "ownership": ownership,
        },
        market={
            "geographies": D.geographies_for(reach, country),
            "segments": segments,
            "customer_count": customer_count,
            "concentration_top10_pct": {"smb_heavy": 0.12, "balanced": 0.24,
                                        "enterprise_heavy": 0.44}.get(mix_key, 0.25),
            "seasonality": "b2b_software" if customer_type != "B2C" else "retail_q4",
        },
        financials={
            "revenue": revenue,
            "gross_margin_pct": gross_margin,
            "opex_split": opex,
            "cash": cash,
            "net_debt": 0.0,
            "growth_rate_yoy": growth_rate,
        },
        org_culture={
            "data_maturity": data_maturity,
            "decision_cadence": cadence,
            "planning_horizon": "annual",
            "risk_appetite": "balanced",
            "kpi_experience": kpi_experience,
        },
        intent={
            "primary_objective": objective,
            "secondary": [],
            "horizon_months": 12,
            "audience": audience,
        },
        data_availability={"has": has, "missing": missing},
        provenance=provenance,
        seed=seed if seed is not None else 20250806,
        history_months=36,
    )

    # Whatever the file said that did not feed the consistency solve — history
    # length, gross margin, currency, the segment list. Applied last and
    # re-validated, so a measured figure that *does* break an identity is
    # rejected here rather than surfacing as a wrong number in a board pack.
    return _overlay(profile, measured) if measured else profile


def _overlay(profile: CompanyProfile, measured: Dict[str, Any]) -> CompanyProfile:
    """Set measured values by dotted path and re-validate the whole profile."""
    dump = profile.model_dump(mode="json")
    for path, value in measured.items():
        node, parts = dump, path.split(".")
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                node = None
                break
            node = nxt
        if node is None:
            continue                     # a path this profile does not have
        node[parts[-1]] = value
        dump["provenance"][path] = profile.provenance.get(path, _measured_provenance)
    return CompanyProfile(**dump)


def generate_name(rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    return f"{rng.choice(_PREFIX)}{rng.choice(_SUFFIX)} {rng.choice(_KIND)}"


def random_answers(seed: Optional[int] = None) -> Dict[str, Any]:
    """"Surprise me" — a complete, plausible set of answers.

    Draws only from real options (never "I don't know"), so a surprise company
    is fully specified rather than mostly defaulted.
    """
    rng = random.Random(seed)
    answers: Dict[str, Any] = {}
    for q in QUESTIONS:
        choices = [o["value"] for o in q["options"]
                   if o["value"] != UNKNOWN and not o.get("disabled")]
        if choices:
            answers[q["id"]] = rng.choice(choices)
    # Only the SaaS generator exists; picking anything else would fail loudly
    # further down, which is a worse experience than quietly staying in scope.
    answers["business_model"] = "saas"
    return answers


def surprise_profile(seed: Optional[int] = None) -> CompanyProfile:
    rng = random.Random(seed)
    actual_seed = seed if seed is not None else rng.randint(1, 10_000_000)
    return build_profile(
        random_answers(actual_seed),
        name=generate_name(random.Random(actual_seed)),
        seed=actual_seed,
    )
