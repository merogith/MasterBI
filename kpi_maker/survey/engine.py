"""Survey answers -> CompanyProfile.

The whole of Mode 2 is this function. Once it returns a valid profile the rest
of the pipeline is identical to Mode 1 — that is the point of the profile
contract, and if this module ever needs to touch anything downstream, the
contract has leaked.
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional, Tuple

from ..profile.schema import CompanyProfile
from . import defaults as D
from .questions import QUESTION_BY_ID, QUESTIONS

UNKNOWN = "__unknown__"

# Name parts for generated companies. Deliberately fictional-sounding: a
# generated report must never look like it describes a real business.
_PREFIX = ["North", "Cobalt", "Vertex", "Lumen", "Harbor", "Quill", "Aster",
           "Ridge", "Beacon", "Ember", "Sable", "Terra", "Onyx", "Vale"]
_SUFFIX = ["wind", "line", "point", "works", "field", "stack", "wave", "core",
           "gate", "path", "scope", "bridge"]
_KIND = ["Analytics", "Systems", "Labs", "Software", "Technologies", "Data",
         "Platform", "Cloud"]


def _answer(answers: Dict[str, Any], qid: str) -> Tuple[Any, bool]:
    """Return (value, was_defaulted)."""
    q = QUESTION_BY_ID[qid]
    raw = answers.get(qid)
    if raw is None or raw == UNKNOWN or raw == "":
        return q["default"], True
    return raw, False


def _explicit(answers: Dict[str, Any], qid: str) -> Optional[Any]:
    """The answer only if the user actually gave one.

    Optional questions must not fall back to a default: the entire point of
    the deep-dive block is that an unanswered question leaves the derived
    assumption in place (and correctly flagged as derived).
    """
    raw = answers.get(qid)
    if raw is None or raw == UNKNOWN or raw == "":
        return None
    return raw


def build_profile(answers: Dict[str, Any], *, name: Optional[str] = None,
                  seed: Optional[int] = None) -> CompanyProfile:
    """Turn survey answers into a validated, internally consistent profile."""
    rng = random.Random(seed if seed is not None else 20250806)
    provenance: Dict[str, str] = {}

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

    # --- The consistency step -------------------------------------------
    # Segment mix gives us a blended ACV. Customer count is then SOLVED, not
    # guessed, so `customers x blended_acv == revenue` holds exactly and the
    # profile's cross-block validator passes by construction.
    segments, blended_acv = D.segments_for(mix_key, revenue_band, stage)
    customer_count = max(3, round(revenue / blended_acv)) if blended_acv else 0
    provenance["market.customer_count"] = (
        f"derived:revenue/blended_acv={blended_acv:,.0f}"
    )

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

    opex = dict(D.OPEX_BY_STAGE.get(stage, D.OPEX_BY_STAGE["growth"]))

    margin_answer = _explicit(answers, "gross_margin")
    if margin_answer is not None:
        gross_margin = D.GROSS_MARGIN_BANDS[margin_answer]
        provenance["financials.gross_margin_pct"] = "user_survey"
    else:
        gross_margin = D.GROSS_MARGIN_BY_STAGE.get(stage, 0.72)
        provenance["financials.gross_margin_pct"] = f"benchmark_default:stage={stage}"

    cash = revenue / 12.0 * D.CASH_MONTHS_BY_STAGE.get(stage, 6.0)
    provenance["financials.opex_split"] = f"benchmark_default:stage={stage}"
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

    return CompanyProfile(
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
