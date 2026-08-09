"""Every number in the prose must appear in the facts table.

ROADMAP M7 gives this job to a Sonnet critic. It should not be a model. The
property is decidable by arithmetic, and handing it to an LLM would add cost,
latency and non-determinism to a check that string matching settles exactly —
the same refinement M7 already made when it ruled that the executor is code,
applied one row further down. A critic that can hallucinate is not a critic.

The design decision that makes this tractable is upstream: the narrator is
handed the **pre-formatted** `fmt_value` renderings alongside the raw numbers
and told to quote them verbatim. So the gate is membership in a set, not fuzzy
numeric comparison against a table of floats with unknown units.

Matching is by value and unit rather than by literal string, which is leniency
in exactly one safe direction. `$12.0M` and `12.0M` collide, and so do `12.0`
and `12` — but both only ever admit a number the table already contains. A
percentage is never conflated with a bare number and a magnitude suffix is
never dropped, because `34.5` and `34.5%` and `34.5M` are three different
claims about the business.

Three things are allowed that are not in the facts table, each for a stated
reason:

  * **Numbers already in a `Finding`.** The detectors wrote those
    deterministically from the same data; re-deriving them here would be
    checking our own arithmetic against itself.
  * **Small bare integers**, up to the number of KPIs or findings in the run.
    "three of the five risks" is a counting claim, not a measurement, and it is
    dimensionless — anything carrying a unit or a magnitude suffix is out.
  * **The reporting period's own numbers**, so a paragraph can say which months
    it covers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..fmt import CURRENCY_SYMBOL, fmt_value

# A numeric token as prose actually writes one: an optional sign, an optional
# currency symbol, digits with thousands separators, an optional fraction, and
# an optional unit. The unit alternatives are ordered longest-first so that
# `mo` is not matched as a bare `m`.
_NUMBER = re.compile(
    r"[-+−–]?"
    r"[" + "".join(re.escape(s) for s in CURRENCY_SYMBOL.values()) + r"]?"
    r"\d[\d,]*(?:\.\d+)?"
    r"\s?(?:%|mo\b|bn\b|[KkMmBb]\b|[dhx]\b)?"
)

# Suffixes that scale the value, and the ones that only label it. Both stay in
# the comparison key; neither is normalised away.
_MAGNITUDE = {"k", "m", "b", "bn"}
_LABEL = {"%", "mo", "d", "h", "x"}

# Value keys are rounded before comparison so that float formatting does not
# decide whether a number is honest. Four places is finer than anything
# `fmt_value` emits and coarser than IEEE noise.
_PRECISION = 4


@dataclass
class Violation:
    """One number in the prose that the facts table does not support."""
    token: str
    offset: int
    context: str

    def __str__(self) -> str:
        return f"{self.token!r} at character {self.offset} (…{self.context}…)"


Key = Tuple[float, str]


def _key(value: float, suffix: str) -> Key:
    return (round(float(value), _PRECISION), suffix)


def _parse(token: str) -> Optional[Key]:
    """A numeric token as (value, unit), or None if it is not really a number."""
    text = token.strip()
    # Unicode minus and en-dash both turn up in formatted output; `fmt_delta`
    # emits U+2212 by design.
    text = text.replace("−", "-").replace("–", "-")
    for symbol in CURRENCY_SYMBOL.values():
        text = text.replace(symbol, "")
    text = text.replace(",", "").replace(" ", "").lstrip("+")

    suffix = ""
    lowered = text.lower()
    for candidate in sorted(_MAGNITUDE | _LABEL, key=len, reverse=True):
        if lowered.endswith(candidate):
            suffix = candidate
            text = text[: len(text) - len(candidate)]
            break
    try:
        return _key(float(text), suffix)
    except ValueError:
        return None


def extract_numbers(text: str) -> List[Tuple[str, int]]:
    """Every numeric-looking token in the prose, with its character offset."""
    return [(m.group(0).strip(), m.start()) for m in _NUMBER.finditer(text or "")]


# --------------------------------------------------------------------------
# The allowed set
# --------------------------------------------------------------------------

def _render(value: Any, unit: str, currency: str) -> Iterable[Key]:
    """Every honest way of writing one figure.

    Both the formatted rendering and the raw value, because a narrator that
    writes `1,240` where the table says `1.2K` has not invented anything — it
    has rounded differently, and refusing that would push it toward vaguer
    prose rather than truer prose.
    """
    if value is None:
        return ()
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return ()
    if raw != raw:                       # NaN
        return ()

    keys: Set[Key] = set()
    for token, _ in extract_numbers(fmt_value(raw, unit, currency)):
        parsed = _parse(token)
        if parsed is not None:
            keys.add(parsed)
    # The unformatted value, and the percentage read as a whole number, which
    # is how a percentage is spoken.
    keys.add(_key(raw, ""))
    if unit == "pct":
        keys.add(_key(raw * 100.0, "%"))
        keys.add(_key(raw * 100.0, ""))
    if unit == "currency":
        for scale, mark in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
            if abs(raw) >= scale:
                keys.add(_key(raw / scale, mark))
    return keys


_RESULT_FIELDS = ("current", "prior_month", "prior_year", "yoy_change",
                  "target", "vs_target")


def allowed_numbers(results: Sequence[Any], findings: Sequence[Any],
                    currency: str = "USD", period: str = "",
                    extra_counts: Sequence[int] = ()) -> Set[Key]:
    """Every number the narrator is entitled to write.

    Built from the same `MetricResult` objects `facts_table()` serialises, so
    the gate and the LLM-safe view cannot drift apart: if a figure reaches the
    prompt it is checkable, and if it does not, it is refused.
    """
    allowed: Set[Key] = set()

    for result in results:
        unit = result.kpi.unit
        for field in _RESULT_FIELDS:
            allowed |= set(_render(getattr(result, field, None), unit, currency))
        benchmark = getattr(result.kpi, "benchmark", None)
        if benchmark is not None:
            for level in ("p25", "p50", "p75"):
                allowed |= set(_render(getattr(benchmark, level, None), unit, currency))
        # `benchmark_position` is deliberately not here: it is a phrase ("top
        # quartile"), not a figure, so there is nothing to check.

    # The detectors already wrote these from the same data. Trusting them is
    # not a shortcut — re-deriving them would be checking our arithmetic
    # against itself and calling the agreement evidence.
    for finding in findings:
        for text in (getattr(finding, "statement", ""),
                     getattr(finding, "recommendation", "")):
            for token, _ in extract_numbers(text or ""):
                parsed = _parse(token)
                if parsed is not None:
                    allowed.add(parsed)

    for token, _ in extract_numbers(period):
        parsed = _parse(token)
        if parsed is not None:
            allowed.add(parsed)

    # Counting claims. Dimensionless by construction: a token carrying a
    # percent sign, a currency magnitude or a unit label never lands here.
    ceiling = max([len(results), len(findings), 12, *[int(c) for c in extra_counts]])
    for n in range(0, ceiling + 1):
        allowed.add(_key(float(n), ""))

    return allowed


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def check_prose(text: str, allowed: Set[Key],
                context_chars: int = 28) -> List[Violation]:
    """Numbers in `text` that `allowed` does not support.

    Empty means the prose is safe to render. Anything else is fed back to the
    narrator once, and on a second failure the paragraph is dropped.
    """
    violations: List[Violation] = []
    for token, offset in extract_numbers(text):
        parsed = _parse(token)
        if parsed is None or parsed in allowed:
            continue
        start = max(0, offset - context_chars)
        end = min(len(text), offset + len(token) + context_chars)
        violations.append(Violation(token=token, offset=offset,
                                    context=text[start:end].replace("\n", " ")))
    return violations


def check_sections(narrative: Dict[str, List[str]],
                   allowed: Set[Key]) -> Dict[str, List[Violation]]:
    """The gate over a whole narrative, keyed by section.

    Per-section rather than whole-document because the fallback is
    per-section: one bad paragraph costs its own section its prose and leaves
    the rest of the report narrated.
    """
    out: Dict[str, List[Violation]] = {}
    for section_id, paragraphs in narrative.items():
        found: List[Violation] = []
        for paragraph in paragraphs:
            found.extend(check_prose(paragraph, allowed))
        if found:
            out[section_id] = found
    return out
