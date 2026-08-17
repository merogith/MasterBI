"""Which part of the business moved the number.

"Churn is up" is a fact a reader already suspected. "Churn is up 2.1 points, of
which SMB contributes 1.8" is the sentence that decides what anyone does next,
and it is the single highest-value analytical addition the plan names.

It could not be written before 3.2 and 3.3: it needs a per-segment series for
every KPI (`MetricResult.by_segment`) and a value-driver graph to walk
(`kpi/drivers.py`). Both now exist.

**Two kinds of statement, and conflating them would be the whole failure.**

* **Contribution** — only for a metric whose segments genuinely sum to the
  blended figure. Revenue does; a *rate* does not. "SMB contributed 1.8 of the
  2.1 point fall" is arithmetic when the parts add up and fiction when they do
  not, so additivity is **verified numerically against this run's own numbers**
  rather than assumed from the unit. A metric that fails the check gets the
  other kind of statement instead.
* **Dispersion** — for everything else. "NRR is 103% blended, but SMB is at
  81%" claims only what it can show: the segments differ, and here is the
  outlier. No contribution arithmetic is attempted.

The check is deliberately strict. Half a percent of tolerance would let a
metric that *nearly* adds up produce a contribution sentence that is quietly
wrong, and a quietly wrong number in a board pack is the failure this project
treats as unacceptable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# How closely the parts must sum to the whole before a contribution claim is
# allowed. One part in a thousand: tight enough that only genuinely additive
# metrics pass, loose enough to survive float arithmetic over 36 months.
ADDITIVE_TOLERANCE = 1e-3

# A segment has to own enough of the move to be worth naming. Below this it is
# noise dressed as a cause.
MIN_SHARE_OF_MOVE = 0.35

# And the move itself has to be big enough to talk about, relative to the level.
MIN_RELATIVE_MOVE = 0.05


@dataclass
class Contribution:
    """One segment's part of a KPI's move, in the KPI's own units."""
    segment: str
    change: float
    share_of_move: float
    current: Optional[float]
    prior: Optional[float]


@dataclass
class Decomposition:
    kpi_id: str
    dimension: str
    #: "contribution" when the parts sum to the whole, "dispersion" otherwise.
    kind: str
    total_change: float
    parts: List[Contribution]

    @property
    def leader(self) -> Optional[Contribution]:
        """The segment that owns most of the move, or the biggest outlier."""
        return self.parts[0] if self.parts else None


def is_additive(blended: Optional[float], parts: Dict[str, Optional[float]]) -> bool:
    """Do this run's own segment values sum to its blended value?

    Measured, not inferred from the unit. `net_revenue` adds up; `aov` is a
    ratio and does not; and a KPI's unit alone cannot be trusted to say which,
    because a "currency" metric can be either a total or an average.
    """
    if blended is None or not parts:
        return False
    values = [v for v in parts.values() if v is not None]
    if len(values) != len(parts) or not values:
        return False
    scale = max(abs(blended), 1e-9)
    return abs(sum(values) - blended) / scale <= ADDITIVE_TOLERANCE


def _at(series, offset: int) -> Optional[float]:
    if series is None:
        return None
    clean = series.dropna()
    if len(clean) <= offset:
        return None
    return float(clean.iloc[-1 - offset])


def decompose(result, dimension: str, months_back: int = 12
              ) -> Optional[Decomposition]:
    """How each segment moved, and whether they add up to how the whole moved.

    `months_back` is a year by default: the comparison a board makes, and long
    enough that a seasonal business is compared against its own like period
    rather than against last month.
    """
    per_segment = result.by_segment.get(dimension) or {}
    if len(per_segment) < 2:
        return None

    blended_now = _at(result.series, 0)
    blended_then = _at(result.series, months_back)
    if blended_now is None or blended_then is None:
        return None

    total_change = blended_now - blended_then
    if abs(total_change) / max(abs(blended_then), 1e-9) < MIN_RELATIVE_MOVE:
        return None

    now = {name: _at(series, 0) for name, series in per_segment.items()}
    then = {name: _at(series, months_back) for name, series in per_segment.items()}

    additive = (is_additive(blended_now, now) and is_additive(blended_then, then))

    parts: List[Contribution] = []
    for name in per_segment:
        current, prior = now.get(name), then.get(name)
        if current is None or prior is None:
            continue
        change = current - prior
        parts.append(Contribution(
            segment=name, change=change,
            share_of_move=(change / total_change) if additive else 0.0,
            current=current, prior=prior))

    if not parts:
        return None

    if additive:
        parts.sort(key=lambda p: abs(p.change), reverse=True)
        kind = "contribution"
    else:
        # No contribution claim is possible, so rank by how far each segment
        # sits from the blended figure — which is the only thing the numbers
        # actually support saying.
        parts.sort(key=lambda p: abs((p.current or 0.0) - blended_now), reverse=True)
        kind = "dispersion"

    return Decomposition(kpi_id=result.kpi.id, dimension=dimension, kind=kind,
                         total_change=total_change, parts=parts)


def worth_reporting(decomposition: Decomposition) -> bool:
    """Is there a cause here, or just several segments drifting together?

    A move spread evenly across every segment is a company-level story, and
    naming the largest of five near-identical contributors as *the* cause would
    be picking a scapegoat out of noise.
    """
    leader = decomposition.leader
    if leader is None:
        return False
    if decomposition.kind == "contribution":
        return abs(leader.share_of_move) >= MIN_SHARE_OF_MOVE
    if leader.current is None:
        return False
    others = [p.current for p in decomposition.parts[1:] if p.current is not None]
    if not others:
        return False
    spread = max(others) - min(others) if len(others) > 1 else 0.0
    gap = abs(leader.current - (sum(others) / len(others)))
    return gap > max(spread, 1e-9)
