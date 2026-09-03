"""Benchmark priors and derivations — the "I don't know" engine.

A survey collects ~14 bands. A CompanyProfile needs ~40 fields. This module
closes that gap, and it is where the product's credibility lives: every value
it invents is tagged in `provenance` so the report can footnote it rather than
present it as fact.

**The derivation order matters.** Revenue, customer count and blended ACV must
satisfy `customers x blended_acv ~= revenue` or CompanyProfile refuses to
validate (that cross-check exists because these three are always collected
separately and always drift). So we never sample all three: we sample revenue
and the segment mix, then SOLVE for customer count. Consistency by construction
beats consistency by validation.

Sources for the priors are public and citable — see ROADMAP M4. They are
deliberately coarse: a band midpoint that is honest about being a band midpoint
is more useful than a precise number that is precisely wrong.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# --------------------------------------------------------------------------
# Band -> value. Midpoints, geometric where the band spans an order of magnitude.
# --------------------------------------------------------------------------

REVENUE_BANDS: Dict[str, float] = {
    "under_1m": 600_000,
    "1m_5m": 2_800_000,
    "5m_10m": 7_500_000,
    "10m_50m": 22_000_000,
    "50m_plus": 80_000_000,
}

HEADCOUNT_BANDS: Dict[str, int] = {
    "1_10": 7,
    "11_50": 30,
    "51_200": 110,
    "201_500": 320,
    "500_plus": 800,
}

# Function mix for a subscription software business. Ratios, not counts.
HEADCOUNT_MIX: Dict[str, float] = {
    "engineering": 0.35,
    "sales": 0.24,
    "customer_success": 0.16,
    "ga": 0.14,
    "marketing": 0.11,
}

# Opex as a share of revenue, by stage. Early-stage spends ahead of revenue;
# mature businesses have converted that spend into operating leverage.
OPEX_BY_STAGE: Dict[str, Dict[str, float]] = {
    "pre_revenue": {"sales": 0.30, "marketing": 0.20, "rnd": 0.35, "ga": 0.18},
    "early": {"sales": 0.28, "marketing": 0.18, "rnd": 0.30, "ga": 0.15},
    "growth": {"sales": 0.22, "marketing": 0.14, "rnd": 0.20, "ga": 0.12},
    "established": {"sales": 0.18, "marketing": 0.10, "rnd": 0.17, "ga": 0.11},
    "mature": {"sales": 0.15, "marketing": 0.08, "rnd": 0.14, "ga": 0.10},
    "turnaround": {"sales": 0.16, "marketing": 0.07, "rnd": 0.13, "ga": 0.12},
}

GROSS_MARGIN_BY_STAGE: Dict[str, float] = {
    "pre_revenue": 0.55, "early": 0.66, "growth": 0.72,
    "established": 0.76, "mature": 0.78, "turnaround": 0.70,
}

# --------------------------------------------------------------------------
# The same two priors, for the archetypes they were not measured on
# --------------------------------------------------------------------------
#
# Both tables above describe a subscription software business — 72% gross
# margin and 68% of revenue in operating cost at growth stage is a venture-
# funded SaaS company, and it was applied to every sector because for a long
# time there were only two and the second one shipped with its own sample
# profile rather than through the survey.
#
# It stopped being harmless when 4.1 took the sector list to twenty. Measured
# on the first project-archetype run: a 110-person consultancy on $7.5M came
# out at a **72% gross margin and a 0.7% EBITDA margin** — a firm that is
# simultaneously a software vendor and insolvent. Nothing was wrong with the
# generator; it honours the profile, and the profile was wrong.
#
# So the priors branch on the archetype, which is the level the shape of a P&L
# actually varies at: what a business sells decides its cost of sales, and
# twenty sector-specific tables would be nineteen invented numbers per column.
# `saas` is deliberately absent from both — it falls through to the tables
# above, unchanged, so nothing that ran before this moves.
#
# Coarse on purpose, like everything else here. 4.4 replaces them with
# Damodaran's industry margins, which is a distribution with a citation rather
# than a band midpoint with a reason.

ARCHETYPE_GROSS_MARGIN_BY_STAGE: Dict[str, Dict[str, float]] = {
    # Retail and distribution: gross margin is after landed product cost.
    "ecommerce": {
        "pre_revenue": 0.34, "early": 0.38, "growth": 0.42,
        "established": 0.45, "mature": 0.46, "turnaround": 0.38,
    },
    # Services: gross margin is after the cost of the people who did the work,
    # so it is bounded by utilisation and realisation and cannot run away.
    "project": {
        "pre_revenue": 0.30, "early": 0.34, "growth": 0.38,
        "established": 0.42, "mature": 0.44, "turnaround": 0.35,
    },
}

ARCHETYPE_OPEX_BY_STAGE: Dict[str, Dict[str, Dict[str, float]]] = {
    # A retailer's operating cost is marketing and stores, and almost none of
    # it is research.
    "ecommerce": {
        "pre_revenue":  {"sales": 0.10, "marketing": 0.22, "rnd": 0.040, "ga": 0.140},
        "early":        {"sales": 0.09, "marketing": 0.19, "rnd": 0.035, "ga": 0.125},
        "growth":       {"sales": 0.08, "marketing": 0.16, "rnd": 0.030, "ga": 0.110},
        "established":  {"sales": 0.07, "marketing": 0.13, "rnd": 0.025, "ga": 0.100},
        "mature":       {"sales": 0.06, "marketing": 0.11, "rnd": 0.020, "ga": 0.095},
        "turnaround":   {"sales": 0.065, "marketing": 0.12, "rnd": 0.020, "ga": 0.105},
    },
    # A services firm sells through its senior people and markets through its
    # reputation, so business development is large and marketing is small.
    "project": {
        "pre_revenue":  {"sales": 0.140, "marketing": 0.070, "rnd": 0.030, "ga": 0.200},
        "early":        {"sales": 0.130, "marketing": 0.060, "rnd": 0.025, "ga": 0.180},
        "growth":       {"sales": 0.110, "marketing": 0.050, "rnd": 0.020, "ga": 0.160},
        "established":  {"sales": 0.100, "marketing": 0.045, "rnd": 0.018, "ga": 0.145},
        "mature":       {"sales": 0.090, "marketing": 0.040, "rnd": 0.015, "ga": 0.135},
        "turnaround":   {"sales": 0.095, "marketing": 0.040, "rnd": 0.015, "ga": 0.145},
    },
}


def gross_margin_for(archetype: str, stage: str) -> float:
    """The gross-margin prior for a company nobody has told us about yet."""
    table = ARCHETYPE_GROSS_MARGIN_BY_STAGE.get(archetype, GROSS_MARGIN_BY_STAGE)
    return table.get(stage, table.get("growth", 0.72))


def opex_for(archetype: str, stage: str) -> Dict[str, float]:
    """The operating-cost prior, as shares of revenue."""
    table = ARCHETYPE_OPEX_BY_STAGE.get(archetype, OPEX_BY_STAGE)
    return dict(table.get(stage, table["growth"]))

# Cash as a multiple of monthly revenue — a proxy for runway.
CASH_MONTHS_BY_STAGE: Dict[str, float] = {
    "pre_revenue": 18.0, "early": 12.0, "growth": 6.5,
    "established": 3.5, "mature": 2.5, "turnaround": 1.5,
}

# Customer mix archetypes: segment shares, and each segment's ACV as a multiple
# of the SMB deal size.
CUSTOMER_MIX: Dict[str, Dict[str, Any]] = {
    "smb_heavy": {
        "label": "Mostly small businesses",
        "shares": [0.70, 0.25, 0.05],
        "acv_multiples": [1.0, 3.5, 12.0],
    },
    "balanced": {
        "label": "A mix of sizes",
        "shares": [0.45, 0.40, 0.15],
        "acv_multiples": [1.0, 4.0, 13.0],
    },
    "enterprise_heavy": {
        "label": "Mostly large organisations",
        "shares": [0.15, 0.35, 0.50],
        "acv_multiples": [1.0, 4.5, 15.0],
    },
}

SEGMENT_NAMES = ["SMB", "Mid-Market", "Enterprise"]

# Annual logo churn and expansion by segment. Small customers churn far more.
SEGMENT_CHURN = [0.28, 0.14, 0.07]
SEGMENT_EXPANSION = [0.05, 0.11, 0.18]

# Base SMB deal size by revenue band — bigger companies sell bigger deals.
SMB_ACV_BY_REVENUE: Dict[str, float] = {
    "under_1m": 3_000,
    "1m_5m": 4_500,
    "5m_10m": 6_000,
    "10m_50m": 8_000,
    "50m_plus": 12_000,
}

# Stage multiplier on churn: early-stage products churn harder.
CHURN_BY_STAGE: Dict[str, float] = {
    "pre_revenue": 1.6, "early": 1.35, "growth": 1.0,
    "established": 0.85, "mature": 0.75, "turnaround": 1.45,
}

COUNTRY_CURRENCY: Dict[str, str] = {
    "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR",
    "TR": "TRY", "ES": "EUR", "IT": "EUR", "SE": "SEK", "CA": "CAD",
    "AU": "AUD", "IN": "INR", "AE": "AED", "JP": "JPY",
}

GEO_MIX: Dict[str, Dict[str, float]] = {
    "domestic": {},                       # filled with the home country at 1.0
    "regional": {},                       # home 0.65 + neighbours
    "global": {},
}


# --------------------------------------------------------------------------
# Optional deep-dive answers -> values. Each of these REPLACES a derived
# assumption, which is the whole reason for asking.
# --------------------------------------------------------------------------

GROWTH_BANDS: Dict[str, float] = {
    "shrinking": -0.12, "flat": 0.02, "slow": 0.09,
    "steady": 0.27, "fast": 0.65, "hyper": 1.25,
}

GROSS_MARGIN_BANDS: Dict[str, float] = {
    "low": 0.52, "mid": 0.68, "high": 0.80, "very_high": 0.88,
}

# Blended annual logo churn, redistributed across segments in proportion to
# the library's baseline mix (SMB always churns hardest).
CHURN_BANDS: Dict[str, float] = {
    "very_low": 0.035, "low": 0.075, "moderate": 0.15,
    "high": 0.27, "very_high": 0.42,
}

CONTRACT_SHAPES: Dict[str, Dict[str, float]] = {
    "monthly": {"annual_prepay_share": 0.05, "avg_contract_months": 1.0},
    "annual": {"annual_prepay_share": 0.85, "avg_contract_months": 12.0},
    "multi_year": {"annual_prepay_share": 0.75, "avg_contract_months": 30.0},
    "mixed": {"annual_prepay_share": 0.50, "avg_contract_months": 12.0},
}

OWNERSHIP_MAP: Dict[str, str] = {
    "founder": "family", "vc_backed": "vc_backed", "pe_backed": "pe_backed",
    "public": "public", "bootstrapped": "family",
}


def currency_for(country: str) -> str:
    return COUNTRY_CURRENCY.get(country.upper(), "USD")


def rescale_churn(segments: List[dict], target_blended: float) -> List[dict]:
    """Scale segment churn so the share-weighted blend hits the stated rate.

    Scaling proportionally rather than setting every segment to the same value
    preserves the fact that small customers churn several times harder than
    enterprise ones — flattening that would destroy the segment-outlier
    detector, which is one of the most useful findings the product produces.
    """
    current = sum(s["share"] * s["logo_churn_annual"] for s in segments)
    if current <= 0 or target_blended <= 0:
        return segments
    factor = target_blended / current
    for s in segments:
        s["logo_churn_annual"] = round(min(0.85, s["logo_churn_annual"] * factor), 4)
    return segments


def segments_for(mix_key: str, revenue_band: str, stage: str) -> Tuple[List[dict], float]:
    """Build the segment list and return it with the blended ACV.

    Returning the blended ACV is what lets the caller solve for customer count
    instead of guessing it — see the module docstring.
    """
    mix = CUSTOMER_MIX.get(mix_key, CUSTOMER_MIX["balanced"])
    base_acv = SMB_ACV_BY_REVENUE.get(revenue_band, 6_000)
    churn_mult = CHURN_BY_STAGE.get(stage, 1.0)

    segments = []
    blended = 0.0
    for i, name in enumerate(SEGMENT_NAMES):
        share = mix["shares"][i]
        acv = base_acv * mix["acv_multiples"][i]
        if share <= 0:
            continue
        segments.append({
            "name": name,
            "share": share,
            "avg_acv": round(acv),
            "logo_churn_annual": round(min(0.60, SEGMENT_CHURN[i] * churn_mult), 3),
            "expansion_annual": SEGMENT_EXPANSION[i],
        })
        blended += share * acv

    # Normalise shares against what we actually kept.
    total_share = sum(s["share"] for s in segments)
    if total_share and abs(total_share - 1.0) > 1e-9:
        for s in segments:
            s["share"] = round(s["share"] / total_share, 4)
        # Rounding can leave a residue; push it onto the largest segment.
        drift = 1.0 - sum(s["share"] for s in segments)
        largest = max(segments, key=lambda s: s["share"])
        largest["share"] = round(largest["share"] + drift, 4)
        blended = sum(s["share"] * s["avg_acv"] for s in segments)

    return segments, blended


def headcount_split(total: int) -> Dict[str, int]:
    """Split a headcount total across functions, guaranteeing it sums back."""
    split = {fn: max(1, round(total * ratio)) for fn, ratio in HEADCOUNT_MIX.items()}
    drift = total - sum(split.values())
    if drift:
        # Absorb rounding drift in the largest function.
        biggest = max(split, key=lambda k: split[k])
        split[biggest] = max(1, split[biggest] + drift)
    return split


def geographies_for(reach: str, country: str) -> Dict[str, float]:
    """Market split by reach. Shares always sum to exactly 1.0.

    They did not, and the survey crashed. The global case was a dict literal
    holding `{country: .45, n0: .25, n1: .18, "US": .12}`, and for any country
    whose neighbour list already contains the US the key repeated — the later
    entry silently won, the shares came to 0.750, and `CompanyProfile` rejected
    the profile with "geographies shares sum to 0.750, expected 1.0". A 422 in
    the middle of the survey, for **GB, CA and AU** — three of the eight
    countries offered — on one of the three answers to "Where are your
    customers?".

    Written additively and normalised, so a repeated market accumulates instead
    of overwriting, and any future edit to the weights cannot reintroduce this.
    """
    country = country.upper()
    neighbours = {
        "US": ["CA", "GB"], "GB": ["US", "DE"], "DE": ["NL", "FR"],
        "TR": ["DE", "GB"], "NL": ["DE", "GB"], "FR": ["DE", "ES"],
    }.get(country, ["US", "GB"])

    if reach == "domestic":
        weights = [(country, 1.0)]
    elif reach == "regional":
        weights = [(country, 0.7), (neighbours[0], 0.2), (neighbours[1], 0.1)]
    elif country == "US":
        weights = [(country, 0.55), ("GB", 0.18), ("DE", 0.15), ("AU", 0.12)]
    else:
        weights = [(country, 0.45), (neighbours[0], 0.25),
                   (neighbours[1], 0.18), ("US", 0.12)]

    shares: Dict[str, float] = {}
    for market, share in weights:
        shares[market] = shares.get(market, 0.0) + share

    total = sum(shares.values())
    return {market: round(share / total, 4) for market, share in shares.items()}
