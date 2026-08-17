"""How findings are ordered, and what is deliberately not in the score.

Ordering was `(severity, id)` — so within a severity band the list was
alphabetical by detector id, which is to say arbitrary. A reader takes the top
three findings seriously and skims the rest, so an arbitrary order inside the
band that matters is a real cost: the biggest miss and the smallest one sit
wherever their ids happen to fall.

The augmented-analytics literature ranks on **magnitude x significance x
recency x whether the user watches that metric**. Three of those four are
computable here. The fourth is not, and it is not faked:

* **Magnitude** — how far the number is from whatever the finding compares it
  to. Every detector puts both sides in `evidence` because the statement quotes
  them, so this needs no new plumbing and cannot drift from the prose.
* **Recency** — a finding about last month outranks one about a change six
  months ago. Only the detectors that genuinely describe a past event set
  `month`; the rest describe the latest state, which *is* current, so their
  weight is 1.0 rather than a guess.
* **Watched** — a KPI the user pinned in the Studio is a KPI they have said
  they care about. Nothing else in the product knows that.
* **Significance is missing on purpose.** A p-value needs a null model, and
  the detectors have nothing of the kind: a RAG breach is a threshold
  comparison and a benchmark gap is a quartile lookup. Multiplying by a number
  computed to look statistical would make the ranking *feel* more principled
  while being less honest, which is the opposite of the trade this project
  makes everywhere else. When Phase 4's benchmark distributions land there is a
  real `n` and a real spread to work with, and it can be added then.

Severity stays dominant. It is the detector's own judgement about how much a
thing matters, made with knowledge of the metric that no generic scorer has,
and the score sorts *within* it rather than against it.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

# The reference each detector compares its `current` against, in the order a
# finding is most likely to carry them. Read from `evidence` rather than
# declared per detector so a new detector is ranked without registering
# anything — and so the number ranked is the number the statement quotes.
_REFERENCES = ("threshold", "benchmark_p50", "expected", "prior_year",
               "prior", "blended")

# How much a full band of severity is worth, so magnitude can reorder within a
# band without ever lifting a "medium" above a "critical".
_SEVERITY_WEIGHT = {
    "critical": 100.0, "high": 80.0, "medium": 50.0,
    "low": 25.0, "positive": 10.0,
}

#: A pinned KPI counts for a little over half a magnitude point.
WATCHED_BOOST = 1.6

#: Months after which an event stops competing with this month's news.
RECENCY_HALF_LIFE = 6.0


def magnitude(evidence: Dict[str, Any]) -> float:
    """Relative distance between the finding's two numbers, 0..1.

    Clamped rather than unbounded: a metric that moved 40x would otherwise
    dominate every list forever, and past a point "very large" is the same
    editorial fact as "enormous".
    """
    current = evidence.get("current")
    if not isinstance(current, (int, float)):
        return 0.0

    for key in _REFERENCES:
        reference = evidence.get(key)
        if not isinstance(reference, (int, float)):
            continue
        scale = max(abs(float(reference)), 1e-9)
        return min(abs(float(current) - float(reference)) / scale, 1.0)

    return 0.0


def recency(months_ago: Optional[float]) -> float:
    """1.0 for now, halving every `RECENCY_HALF_LIFE` months.

    `None` means the finding describes the current state rather than a past
    event, which is not the same as "we do not know when" — so it scores 1.0
    rather than being penalised for a field it had no reason to set.
    """
    if months_ago is None or months_ago <= 0:
        return 1.0
    return float(0.5 ** (months_ago / RECENCY_HALF_LIFE))


def score(finding, watched: Optional[Set[str]] = None) -> float:
    """The finding's rank. Higher is more worth reading first."""
    watched = watched or set()
    base = _SEVERITY_WEIGHT.get(finding.severity, 30.0)

    size = magnitude(finding.evidence or {})
    age = recency(getattr(finding, "months_ago", None))
    pinned = any(kpi_id in watched for kpi_id in (finding.kpi_ids or []))

    # Additive inside the band, multiplicative across recency: an old finding
    # should fade, but a large one should not be able to jump a whole band.
    within_band = (1.0 + size) * age * (WATCHED_BOOST if pinned else 1.0)
    return round(base + min(within_band, 3.0), 6)


def rank_all(findings: Iterable, watched: Optional[Set[str]] = None) -> list:
    """Score every finding and return them worst-first, deterministically.

    `id` is the final tie-break so two findings that genuinely score the same
    do not swap places between runs — `tests/spine.py` compares artifacts byte
    for byte, and an unstable sort would make every re-run look like a change.
    """
    findings = list(findings)
    for finding in findings:
        finding.score = score(finding, watched)
    findings.sort(key=lambda f: (f.rank, -f.score, f.id))
    return findings
