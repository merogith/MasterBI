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
    country = country.upper()
    neighbours = {
        "US": ["CA", "GB"], "GB": ["US", "DE"], "DE": ["NL", "FR"],
        "TR": ["DE", "GB"], "NL": ["DE", "GB"], "FR": ["DE", "ES"],
    }.get(country, ["US", "GB"])

    if reach == "domestic":
        return {country: 1.0}
    if reach == "regional":
        return {country: 0.7, neighbours[0]: 0.2, neighbours[1]: 0.1}
    return {country: 0.45, neighbours[0]: 0.25, neighbours[1]: 0.18, "US": 0.12} \
        if country != "US" else {country: 0.55, "GB": 0.18, "DE": 0.15, "AU": 0.12}
