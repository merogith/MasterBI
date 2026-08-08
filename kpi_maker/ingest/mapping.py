"""Match the columns someone uploaded to the fields we need.

Deterministic and explainable, on purpose. Three signals, combined:

  * **name similarity** — exact alias, then normalised token overlap, then
    fuzzy ratio. `Invoice Date` matching `month` is an alias hit;
    `cust_id` matching `customer_id` is fuzzy.
  * **semantic compatibility** — the profiler already decided whether a column
    holds dates, money, identifiers or categories. A currency field must not
    map to a date column however similar the names.
  * **value plausibility** — a `month` candidate whose values do not parse as
    dates is not a month, whatever it is called.

Confidence is reported per field and every proposal is overridable. A confident
wrong guess is worse than an unsure one, so the scorer is deliberately
reluctant: no name match at all caps confidence low even when the type fits.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .profiler import ColumnProfile, TableProfile
from .shapes import SHAPES, Field, Shape

# Below this, a proposal is shown as a question rather than an answer.
CONFIDENT = 0.75
PLAUSIBLE = 0.45

# Which semantic types can satisfy which field type. Deliberately narrow —
# widening it produces confident nonsense.
COMPATIBLE: Dict[str, set] = {
    "date": {"date"},
    "currency": {"currency", "measure"},
    "measure": {"measure", "currency", "boolean"},
    "identifier": {"identifier", "text", "category", "measure"},
    "category": {"category", "text", "identifier", "boolean"},
    "text": {"text", "category", "identifier"},
}


@dataclass
class FieldMatch:
    field: str
    column: Optional[str]
    confidence: float
    required: bool
    reason: str
    alternatives: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field, "column": self.column,
            "confidence": round(self.confidence, 3), "required": self.required,
            "reason": self.reason, "alternatives": self.alternatives,
            "status": ("confident" if self.confidence >= CONFIDENT else
                       "uncertain" if self.confidence >= PLAUSIBLE else "unmapped"),
        }


@dataclass
class MappingProposal:
    shape: str
    target_table: str
    matches: List[FieldMatch]
    unmapped_columns: List[str] = field(default_factory=list)

    @property
    def missing_required(self) -> List[str]:
        return [m.field for m in self.matches if m.required and m.column is None]

    @property
    def usable(self) -> bool:
        return not self.missing_required

    def to_spec(self) -> Dict[str, str]:
        """The `{canonical_field: source_column}` form ModelSpec.mapping holds."""
        return {m.field: m.column for m in self.matches if m.column}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "shape": self.shape, "target_table": self.target_table,
            "matches": [m.as_dict() for m in self.matches],
            "unmapped_columns": self.unmapped_columns,
            "missing_required": self.missing_required,
            "usable": self.usable,
        }


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _tokens(name: str) -> set:
    return {t for t in _normalise(name).split("_") if t}


def name_score(column: str, spec: Field) -> float:
    """0..1 on the name alone."""
    target = _normalise(column)
    canonical = _normalise(spec.name)
    aliases = [canonical] + [_normalise(a) for a in spec.aliases]

    if target in aliases:
        return 1.0

    # A column called `account_name` or `customer_name` is a label for an
    # entity, not a key to it. Without this, `Account Name` outscores `Cust ID`
    # for customer_id — the alias "account" is a substring of "account_name",
    # while "cust_id" is not a substring of "customer_id". The result is a
    # mapping that joins on a display name, which duplicates every renamed
    # company and silently splits their history.
    if spec.semantic == "identifier" and re.search(r"(^|_)name$", target):
        return 0.15

    column_tokens = _tokens(column)
    best = 0.0
    for alias in aliases:
        alias_tokens = {t for t in alias.split("_") if t}
        if not alias_tokens:
            continue
        overlap = len(column_tokens & alias_tokens) / len(column_tokens | alias_tokens)
        fuzzy = difflib.SequenceMatcher(None, target, alias).ratio()
        # Containment matters: `invoice_amount` should match `amount` strongly
        # even though the token sets differ.
        contained = 0.8 if (alias in target or target in alias) else 0.0
        best = max(best, overlap, fuzzy * 0.9, contained)
    return min(best, 0.99)


def value_score(profile: ColumnProfile, spec: Field) -> float:
    """0..1 on whether the values could plausibly be this field."""
    allowed = COMPATIBLE.get(spec.semantic, {spec.semantic})
    if profile.semantic in allowed:
        # An exact semantic hit is worth more than a merely permitted one.
        return 1.0 if profile.semantic == spec.semantic else 0.7
    return 0.0


def match_shape(frame: pd.DataFrame, profile: TableProfile,
                shape: Shape) -> MappingProposal:
    by_name = {c.name: c for c in profile.columns}
    matches: List[FieldMatch] = []
    taken: set = set()

    # Required fields choose first: on a file where one column could serve two
    # fields, the one that blocks the run should win it.
    ordered = sorted(shape.fields, key=lambda f: (not f.required, f.name))

    for spec in ordered:
        scored: List[tuple] = []
        for column, column_profile in by_name.items():
            if column in taken:
                continue
            names = name_score(column, spec)
            values = value_score(column_profile, spec)
            if values == 0.0:
                continue                       # wrong kind of data, at any name
            # A type-compatible column with no name resemblance is a guess, not
            # a match — cap it below the confident threshold.
            combined = (0.65 * names + 0.35 * values) if names > 0.25 else values * 0.4
            scored.append((combined, names, values, column))

        scored.sort(reverse=True)
        if not scored:
            matches.append(FieldMatch(spec.name, None, 0.0, spec.required,
                                      "no column holds this kind of value"))
            continue

        confidence, names, values, column = scored[0]
        taken.add(column)
        matches.append(FieldMatch(
            field=spec.name, column=column, confidence=confidence,
            required=spec.required,
            reason=_reason(names, values, column, spec),
            alternatives=[c for _, _, _, c in scored[1:4]],
        ))

    mapped = {m.column for m in matches if m.column}
    return MappingProposal(
        shape=shape.id, target_table=shape.target_table, matches=matches,
        unmapped_columns=[c.name for c in profile.columns if c.name not in mapped],
    )


def _reason(names: float, values: float, column: str, spec: Field) -> str:
    if names >= 0.99:
        return f"{column!r} is a known name for {spec.name}"
    if names > 0.6:
        return f"{column!r} closely resembles {spec.name} and holds the right type"
    if names > 0.25:
        return f"{column!r} partly resembles {spec.name}; check this one"
    return (f"{column!r} holds the right kind of value but the name does not "
            f"match — confirm before running")


def detect_shape(frame: pd.DataFrame, profile: TableProfile) -> List[MappingProposal]:
    """Score the file against every shape, best fit first.

    Returned rather than decided: on an ambiguous file the user should choose,
    and seeing the runner-up is what makes that choice possible.
    """
    proposals = [match_shape(frame, profile, shape) for shape in SHAPES.values()]

    def rank(proposal: MappingProposal) -> tuple:
        required = [m for m in proposal.matches if m.required]
        required_score = (sum(m.confidence for m in required) / len(required)
                          if required else 0.0)
        optional = [m for m in proposal.matches if not m.required and m.column]
        optional_score = sum(m.confidence for m in optional) / max(len(proposal.matches), 1)
        # Missing a required field is close to disqualifying: that shape cannot
        # produce its fact table at all.
        return (proposal.usable, required_score, optional_score)

    return sorted(proposals, key=rank, reverse=True)
