"""The question bank (ROADMAP M3).

Design rules, enforced by `tests/test_survey.py::test_every_question_branches`
— which for a long time this docstring named and *no such test existed*. A
claim of enforcement nobody had written is worse than no claim, because it is
the reason nobody checks. It exists now, along with one asserting that every
option a user can click builds a profile at all; three countries crashed the
survey outright when that was first run.

Rules:

  * 14 core questions maximum before the user sees a result.
  * Every question must FILL a profile field or BRANCH the pipeline. If two
    different answers produce an identical dashboard, the question is deleted.
  * Bands, never free-text numbers — people answer bands honestly and abandon
    open number fields.
  * Intent is asked EARLY. The objective is the strongest single predictor of
    which KPIs matter, and asking it first primes better answers after it.
  * Every question offers "I don't know" -> fills from the sector prior and
    tags provenance, so the report can footnote the assumption.

Served to the UI as JSON; the UI renders it generically and never hard-codes a
question. Adding a question here makes it appear in the survey.
"""
from __future__ import annotations

from typing import Any, Dict, List

DONT_KNOW = {"value": "__unknown__", "label": "I don't know — use the sector average"}


def _q(qid: str, text: str, helptext: str, options: List[Dict[str, str]],
       fills: str, default: str, group: str, multi: bool = False,
       allow_unknown: bool = True, optional: bool = False,
       unlocks: str = "") -> Dict[str, Any]:
    opts = list(options)
    if allow_unknown:
        opts.append(DONT_KNOW)
    return {
        "id": qid, "text": text, "help": helptext, "options": opts,
        "fills": fills, "default": default, "group": group, "multi": multi,
        # Optional questions sit after the core 14 and can be skipped wholesale.
        # `unlocks` tells the user what accuracy they buy by answering — without
        # that, an optional question is just a longer form.
        "optional": optional, "unlocks": unlocks,
    }


# Every sector the profile schema declares, in the order the survey offers
# them: the two with their own content first, then the rest.
SECTOR_LABELS: List[Dict[str, str]] = [
    {"value": "saas", "label": "Software / subscription"},
    {"value": "ecommerce", "label": "E-commerce or D2C"},
    {"value": "retail", "label": "Retail (physical)"},
    {"value": "services", "label": "Professional services"},
    {"value": "manufacturing", "label": "Manufacturing"},
    {"value": "marketplace", "label": "Marketplace or platform"},
    {"value": "distribution", "label": "Distribution or wholesale"},
    {"value": "hospitality", "label": "Hospitality and food service"},
    {"value": "logistics", "label": "Logistics and transport"},
    {"value": "healthcare", "label": "Healthcare services"},
]


def _sector_options() -> List[Dict[str, Any]]:
    """The sector list, each entry marked exact or approximate.

    Derived from `profile/sectors.py` rather than hand-maintained, so a sector
    gaining its own pack stops being badged the moment the pack lands and
    nobody has to remember to edit this file too.
    """
    from ..profile import sectors

    options: List[Dict[str, Any]] = []
    for entry in SECTOR_LABELS:
        option = dict(entry)
        if sectors.is_approximate(entry["value"]):
            option["approximate"] = True
            option["note"] = "cross-sector pack"
        options.append(option)
    return options


QUESTIONS: List[Dict[str, Any]] = [
    # ---- Intent first -----------------------------------------------------
    _q("objective",
       "What matters most over the next 12 months?",
       "This is the single biggest driver of which KPIs you get. A growth "
       "dashboard and a cash dashboard look very different.",
       [
           {"value": "growth", "label": "Grow revenue"},
           {"value": "margin_expansion", "label": "Improve margins"},
           {"value": "retention", "label": "Keep the customers we have"},
           {"value": "cash_liquidity", "label": "Protect cash and extend runway"},
           {"value": "fundraise", "label": "Raise capital"},
           {"value": "cost_reduction", "label": "Cut cost"},
           {"value": "exit_prep", "label": "Prepare for a sale"},
           {"value": "quality", "label": "Improve quality and delivery"},
       ],
       fills="intent.primary_objective", default="growth", group="Your goal",
       allow_unknown=False),

    _q("audience",
       "Who will read this?",
       "Changes the weighting between financial, customer and operational views.",
       [
           {"value": "board", "label": "The board"},
           {"value": "exec", "label": "The leadership team"},
           {"value": "investor", "label": "Investors"},
           {"value": "bank", "label": "A bank or lender"},
           {"value": "department_head", "label": "A department head"},
           {"value": "operational_team", "label": "An operational team"},
       ],
       fills="intent.audience", default="exec", group="Your goal",
       allow_unknown=False),

    # ---- Identity ---------------------------------------------------------
    _q("country",
       "Where is the business based?",
       "Sets currency, fiscal calendar and which benchmark region applies.",
       [
           {"value": "US", "label": "United States"},
           {"value": "GB", "label": "United Kingdom"},
           {"value": "DE", "label": "Germany"},
           {"value": "TR", "label": "Türkiye"},
           {"value": "NL", "label": "Netherlands"},
           {"value": "FR", "label": "France"},
           {"value": "CA", "label": "Canada"},
           {"value": "AU", "label": "Australia"},
       ],
       fills="identity.country", default="US", group="The business",
       allow_unknown=False),

    # Every sector the schema declares is offered. Two have their own generator
    # archetype and reviewed KPI pack; the rest run on the nearest archetype and
    # the cross-sector `general` pack, and are badged `approximate` so the user
    # chooses that knowingly. `profile/sectors.py` owns which is which, and the
    # badge is derived from it rather than hand-maintained here — the previous
    # list marked two sectors "Soon" and offered options that could not be
    # picked at all, which is a worse answer than a labelled approximation.
    _q("business_model",
       "What kind of business is it?",
       "Selects the KPI library and the data model.",
       _sector_options(),
       fills="business_model.type", default="saas", group="The business",
       allow_unknown=False),

    _q("customer_type",
       "Who buys from you?",
       "B2B and B2C businesses need different retention and funnel metrics.",
       [
           {"value": "B2B", "label": "Other businesses"},
           {"value": "B2C", "label": "Consumers"},
           {"value": "B2B2C", "label": "Both, via partners"},
           {"value": "B2G", "label": "Government and public sector"},
       ],
       fills="business_model.customer_type", default="B2B", group="The business"),

    _q("sales_motion",
       "How do you win customers?",
       "Determines whether pipeline and sales-cycle metrics apply at all.",
       [
           {"value": "self_serve", "label": "They sign up themselves"},
           {"value": "inside", "label": "An inside sales team"},
           {"value": "field", "label": "Field sales / account executives"},
           {"value": "channel", "label": "Partners and resellers"},
           {"value": "tender", "label": "Tenders and public bids"},
       ],
       fills="business_model.sales_motion", default="inside", group="The business"),

    # ---- Size -------------------------------------------------------------
    _q("revenue_band",
       "Roughly what is annual revenue?",
       "A band is fine — it selects the benchmark cohort, nothing more.",
       [
           {"value": "under_1m", "label": "Under 1M"},
           {"value": "1m_5m", "label": "1M – 5M"},
           {"value": "5m_10m", "label": "5M – 10M"},
           {"value": "10m_50m", "label": "10M – 50M"},
           {"value": "50m_plus", "label": "Over 50M"},
       ],
       fills="financials.revenue", default="5m_10m", group="Size and shape"),

    _q("headcount_band",
       "How many people work there?",
       "Drives per-employee productivity metrics and the cost base.",
       [
           {"value": "1_10", "label": "1 – 10"},
           {"value": "11_50", "label": "11 – 50"},
           {"value": "51_200", "label": "51 – 200"},
           {"value": "201_500", "label": "201 – 500"},
           {"value": "500_plus", "label": "Over 500"},
       ],
       fills="size.headcount_total", default="51_200", group="Size and shape"),

    _q("stage",
       "Where is the business in its life?",
       "Sets expectations for growth, margin and churn — a 60% growth rate is "
       "healthy at one stage and alarming at another.",
       [
           {"value": "early", "label": "Early — finding product-market fit"},
           {"value": "growth", "label": "Growing fast"},
           {"value": "established", "label": "Established and steady"},
           {"value": "mature", "label": "Mature, optimising"},
           {"value": "turnaround", "label": "In turnaround"},
       ],
       fills="size.stage", default="growth", group="Size and shape"),

    _q("customer_mix",
       "What size are your customers?",
       "The single most important input for retention: blended churn is "
       "meaningless when segments differ, and they always do.",
       [
           {"value": "smb_heavy", "label": "Mostly small businesses"},
           {"value": "balanced", "label": "A mix of sizes"},
           {"value": "enterprise_heavy", "label": "Mostly large organisations"},
       ],
       fills="market.segments", default="balanced", group="Customers"),

    _q("reach",
       "Where are your customers?",
       "Affects concentration risk and which benchmark region applies.",
       [
           {"value": "domestic", "label": "Mostly our home country"},
           {"value": "regional", "label": "Across the region"},
           {"value": "global", "label": "Worldwide"},
       ],
       fills="market.geographies", default="regional", group="Customers"),

    # ---- Operating context ------------------------------------------------
    _q("data_maturity",
       "How do you track numbers today?",
       "We only propose KPIs you could realistically produce.",
       [
           {"value": "none", "label": "We mostly don't"},
           {"value": "spreadsheets", "label": "Spreadsheets"},
           {"value": "bi_tool", "label": "A BI tool"},
           {"value": "warehouse", "label": "A data warehouse"},
           {"value": "data_team", "label": "A dedicated data team"},
       ],
       fills="org_culture.data_maturity", default="spreadsheets", group="How you work"),

    _q("kpi_experience",
       "How comfortable is the audience with metrics?",
       "A dashboard nobody can read has no value however correct it is — this "
       "caps how many KPIs we show.",
       [
           {"value": "low", "label": "Not very — keep it simple"},
           {"value": "medium", "label": "Reasonably comfortable"},
           {"value": "high", "label": "Very — give us everything"},
       ],
       fills="org_culture.kpi_experience", default="medium", group="How you work"),

    _q("contract_terms",
       "How do customers commit and pay?",
       "Billings, deferred revenue and contracted-revenue metrics are all "
       "meaningless without this — a monthly-billed business has almost no "
       "deferred revenue, an annual-prepay business carries a year of it.",
       [
           {"value": "monthly", "label": "Month to month"},
           {"value": "annual", "label": "Annual contracts, paid up front"},
           {"value": "multi_year", "label": "Multi-year contracts"},
           {"value": "mixed", "label": "A mix"},
       ],
       fills="business_model.contract_terms", default="mixed", group="How you work"),

    # ---- Optional deep dive ----------------------------------------------
    # Everything below can be skipped entirely. Each answer replaces a derived
    # assumption with a fact, which is why `unlocks` is stated plainly.
    _q("growth_rate",
       "Roughly how fast is revenue growing?",
       "If you skip this we infer growth from your company stage, which puts "
       "every 'growing' business on the same curve.",
       [
           {"value": "shrinking", "label": "Shrinking"},
           {"value": "flat", "label": "Roughly flat"},
           {"value": "slow", "label": "Up to 15% a year"},
           {"value": "steady", "label": "15–40% a year"},
           {"value": "fast", "label": "40–100% a year"},
           {"value": "hyper", "label": "More than doubling"},
       ],
       fills="financials.growth_rate_yoy", default="steady", group="Sharpen the result",
       optional=True,
       unlocks="Growth rate, Rule of 40 and cRPO growth stop being estimates"),

    _q("gross_margin",
       "What is your gross margin?",
       "Sets the ceiling on every efficiency metric downstream.",
       [
           {"value": "low", "label": "Under 60%"},
           {"value": "mid", "label": "60–75%"},
           {"value": "high", "label": "75–85%"},
           {"value": "very_high", "label": "Over 85%"},
       ],
       fills="financials.gross_margin_pct", default="mid", group="Sharpen the result",
       optional=True,
       unlocks="Gross margin, CAC payback and LTV/CAC become yours rather than the sector's"),

    _q("churn_level",
       "How much of your customer base leaves each year?",
       "The single biggest driver of retention metrics, and the number most "
       "often assumed wrongly.",
       [
           {"value": "very_low", "label": "Under 5%"},
           {"value": "low", "label": "5–10%"},
           {"value": "moderate", "label": "10–20%"},
           {"value": "high", "label": "20–35%"},
           {"value": "very_high", "label": "Over 35%"},
       ],
       fills="market.segments", default="moderate", group="Sharpen the result",
       optional=True,
       unlocks="NRR, GRR, churn and quick ratio reflect your actual retention"),

    _q("ownership",
       "Who owns the business?",
       "Shapes which objectives and reporting conventions apply.",
       [
           {"value": "founder", "label": "Founders and employees"},
           {"value": "vc_backed", "label": "Venture backed"},
           {"value": "pe_backed", "label": "Private equity backed"},
           {"value": "public", "label": "Publicly listed"},
           {"value": "bootstrapped", "label": "Bootstrapped, no outside capital"},
       ],
       fills="size.ownership", default="vc_backed", group="Sharpen the result",
       optional=True,
       unlocks="Reporting conventions and benchmark cohort match your ownership"),

    _q("cadence",
       "How often do you review performance?",
       "Sets the reporting cadence referenced in the report.",
       [
           {"value": "weekly", "label": "Weekly"},
           {"value": "monthly", "label": "Monthly"},
           {"value": "quarterly", "label": "Quarterly"},
       ],
       fills="org_culture.decision_cadence", default="monthly",
       group="Sharpen the result", optional=True,
       unlocks="Report cadence matches how you actually run the business"),
]

CORE_QUESTIONS = [q for q in QUESTIONS if not q["optional"]]
OPTIONAL_QUESTIONS = [q for q in QUESTIONS if q["optional"]]

QUESTION_BY_ID = {q["id"]: q for q in QUESTIONS}


def groups() -> List[str]:
    seen: List[str] = []
    for q in QUESTIONS:
        if q["group"] not in seen:
            seen.append(q["group"])
    return seen


def as_json() -> Dict[str, Any]:
    return {
        "questions": QUESTIONS,
        "groups": groups(),
        "count": len(QUESTIONS),
        "core_count": len(CORE_QUESTIONS),
        "optional_count": len(OPTIONAL_QUESTIONS),
        "optional_group": "Sharpen the result",
    }
