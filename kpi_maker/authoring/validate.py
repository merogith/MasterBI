"""Is one record sheet coherent?

Everything a `KPI` model can express is already checked by pydantic when the
pack loads — required fields, enum values, alert bands that match the
direction, and since 3.6 the target rule too. Repeating any of that here would
be a second place stating the same rule, which is the bug this repo tests for.

What is left is what a model of one sheet cannot see: whether the things it
*names* exist. A `driver_parent` names another sheet. A `kind: builtin` names
an implementation. An `applies_when` names profile fields. An `expression`
names fact-table columns. Each of those is a reference into something outside
the sheet, and each fails silently — the KPI drops with a reason nobody reads,
or renders as an empty row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

from ..formula.errors import FormulaError
from ..kpi.expr import ExpressionError
from ..kpi.schema import KPI

#: Severity, worst first. `error` fails the lint; `warn` is reported and does
#: not; `info` is a count worth seeing.
LEVELS = ("error", "warn", "info")


@dataclass(frozen=True)
class Finding:
    level: str
    rule: str
    message: str
    pack: str = ""
    kpi_id: str = ""

    def __str__(self) -> str:
        where = f"{self.pack}:{self.kpi_id}" if self.kpi_id else self.pack
        return f"{self.level.upper():5} {where or '-':38} {self.rule}: {self.message}"


def _reference_profile():
    """A fully defaulted profile, to evaluate `applies_when` against.

    `kpi/expr.py` offers evaluation and not a bare parse, and evaluating is the
    stronger check anyway: parsing proves the grammar, evaluating proves the
    *field paths* resolve. `business_model.revenu_model` parses perfectly and
    is a typo that hides the KPI from every profile forever.

    Every question skipped, so nothing here depends on a company — the paths a
    gate names are a fact about `CompanyProfile`, not about the business.
    """
    from ..survey import build_profile

    return build_profile({})


def _field_paths(expression: str) -> List[str]:
    """Every dotted profile path an expression names."""
    import ast

    tree = ast.parse(expression.strip(), mode="eval")
    paths: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts, cur = [], node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            paths.append(".".join(reversed(parts)))
    # Only the longest chain of each — `a.b.c` also walks as `a.b`.
    return [p for p in paths if not any(o != p and o.startswith(p + ".")
                                        for o in paths)]


def _unknown_path(path: str, model) -> Optional[str]:
    """None if this dotted path exists on the profile model, else the part that
    does not.

    Walked structurally rather than evaluated, and that distinction is the
    whole check. `kpi/expr.py` resolves a missing attribute to `None` and
    `_compare` turns `None` into `False` — deliberately, so that "a KPI which
    depends on data we don't have quietly does not apply". At runtime that is
    right. At authoring time it means a typo is indistinguishable from an
    absent field: `business_model.revenu_model` evaluates cleanly to False and
    hides the KPI from every profile forever, with nothing raised anywhere.
    """
    from pydantic import BaseModel

    current = model
    for part in path.split("."):
        fields = getattr(current, "model_fields", None)
        if fields is None:
            # Reached a scalar, a list or a dict — anything below it is data
            # rather than schema, and the linter has nothing to check against.
            return None
        if part not in fields:
            return part
        annotation = fields[part].annotation
        current = annotation if isinstance(annotation, type) \
            and issubclass(annotation, BaseModel) else object
    return None


def _evaluates(expression: str, profile) -> Optional[str]:
    """None if `applies_when` is well formed and names real fields."""
    from ..kpi import expr

    try:
        expr.evaluate(expression, profile)
    except (ExpressionError, SyntaxError, ValueError, AttributeError) as exc:
        return str(exc)

    from ..profile.schema import CompanyProfile

    for path in _field_paths(expression):
        bad = _unknown_path(path, CompanyProfile)
        if bad is not None:
            return (f"names {path!r}, and `{bad}` is not a field on the company "
                    f"profile — the expression evaluates to False for every "
                    f"company rather than raising")
    return None


def _objective_values() -> List[str]:
    from ..profile.schema import Objective

    return [o.value for o in Objective]


def _unknown_objectives(kpi: KPI) -> List[str]:
    """Objectives this sheet claims to serve that no profile can ever state.

    `serves_objectives` is `List[str]` rather than `List[Objective]`, and that
    is not an oversight to correct in the model: a stored user KPI posted to
    `/api/catalog/kpis` would then 422 on a value that costs it nothing but
    half a rank, and a spec saved before an objective was renamed would stop
    loading. The looseness is right and the silence is not.

    `selection._score` matches this field by string against `Objective`, at the
    heaviest weight in the function (5.0 primary, 2.0 secondary), and
    `_explain` reads it again for the rationale the user sees. So a value that
    is not an enum member is worth exactly nothing, twice, and looks like
    intent both times. Measured when this check was written: `efficiency` on
    e-commerce's inventory cover and `digital_transformation` on the general
    pack's R&D intensity had been shipping since those packs were authored.
    """
    valid = set(_objective_values())
    return sorted({o for o in kpi.serves_objectives if o not in valid})


def validate_sheet(kpi: KPI, *, pack: str = "", known_ids: Optional[Set[str]] = None,
                   registry: Optional[Set[str]] = None,
                   profile=None) -> List[Finding]:
    """Every reference this sheet makes, checked against what exists.

    `known_ids` is every id in the *load group*, not the file — see the module
    docstring in `authoring/__init__.py` for why that distinction is the whole
    difference between eight dangling parents and none.
    """
    known_ids = known_ids or set()
    if registry is None:
        from ..metrics.engine import _REGISTRY

        registry = set(_REGISTRY)

    out: List[Finding] = []

    def add(level: str, rule: str, message: str) -> None:
        out.append(Finding(level, rule, message, pack=pack, kpi_id=kpi.id))

    if kpi.driver_parent and kpi.driver_parent not in known_ids:
        add("error", "driver-parent",
            f"points at {kpi.driver_parent!r}, which no pack in this group "
            f"defines — the tree breaks here and drill-down stops")

    if kpi.driver_parent == kpi.id:
        add("error", "driver-parent", "is its own parent")

    if kpi.applies_when:
        why = _evaluates(kpi.applies_when, profile
                         if profile is not None else _reference_profile())
        if why:
            add("error", "applies-when",
                f"{why}. A sheet whose gate cannot be evaluated is skipped "
                f"for every profile")

    if not kpi.is_formula and kpi.compute_ref not in registry:
        # Not automatically wrong: `nps` and `support_first_response_hours`
        # describe metrics this build has no data model for, and they say so
        # with `requires_data`. Selection drops them with a readable reason.
        # A builtin claiming no source system and having no implementation is
        # the real defect — it will be selected and render as a broken row,
        # which is exactly what shipped before 0.2.
        if kpi.requires_data:
            add("info", "unimplemented",
                f"declares `kind: builtin` with no registered @metric, and "
                f"needs {', '.join(kpi.requires_data)} — selection drops it "
                f"with a reason")
        else:
            add("error", "unimplemented",
                "declares `kind: builtin`, nothing registers a @metric for it, "
                "and it names no source system that would explain the gap")

    if kpi.is_formula and not (kpi.compute.expression or "").strip():
        add("error", "formula", "declares `kind: formula` with no expression")

    unknown_objectives = _unknown_objectives(kpi)
    if unknown_objectives:
        add("error", "objective",
            f"serves {', '.join(unknown_objectives)}, which is not an "
            f"`Objective` — selection matches this field by string against the "
            f"enum, and intent is its heaviest weight, so the sheet loses "
            f"5 points it was authored to earn and `_explain` omits the "
            f"reason. Valid: {', '.join(_objective_values())}")

    if kpi.benchmark is not None and not (kpi.benchmark.source or "").strip():
        add("error", "benchmark", "has a benchmark with no citation")

    if kpi.benchmark is not None and _is_a_prior(kpi.benchmark):
        add("info", "benchmark-placeholder",
            "cites an internal prior rather than a published distribution")

    if not (kpi.interpretation or "").strip():
        add("warn", "interpretation",
            "has no `interpretation`, so its record sheet cannot say how to "
            "read it — 3.5 renders that beside the number")

    return out


def _is_a_prior(benchmark) -> bool:
    """A band nobody measured, whatever it calls itself.

    Two phrasings now: the record sheets' "Illustrative composite" and 4.4's
    derived cost-structure bands, which say so at greater length. Counted rather
    than gated — 4.4 could not reach a published distribution from this
    environment, and failing a pack over work that is blocked on a network is
    how an author learns to ignore a linter.
    """
    source = (benchmark.source or "")
    return ("llustrative" in source
            or "not a published distribution" in source
            or "internal prior" in (benchmark.vintage or ""))


def _entity_grain_tables() -> Set[str]:
    """Fact tables with no `month` column, read from the schemas that declare it.

    `customers`, `projects` and `suppliers` are one row per entity, so a monthly
    aggregate over them cannot be computed at all — `SUM(projects.actual_hours)`
    raises *"projects has no month column, so it cannot be aggregated by
    month"*. Derived rather than listed, so an archetype adding an entity-grain
    table is covered without anyone remembering this function exists.
    """
    from ..contract.schemas import SCHEMAS_BY_ARCHETYPE

    monthly: Dict[str, bool] = {}
    for schemas in SCHEMAS_BY_ARCHETYPE.values():
        for name, schema in schemas.items():
            monthly[name] = monthly.get(name, False) or "month" in schema.columns
    return {name for name, has in monthly.items() if not has}


def aggregates_a_time_series(kpi: KPI) -> Optional[str]:
    """None if every aggregate in this sheet reads a table that has months.

    **The gap 4.3b found by running a pack the linter had passed.** Two project
    record sheets validated cleanly — the syntax parses, the references resolve,
    the functions exist — and then computed nothing on every run, because
    `SUM()` groups by month and `projects` is one row per engagement. The
    general pack's own header has warned about this for `customers` since 0.1;
    what was missing was anything that checked.

    It is exactly the failure `validate.py` exists to catch: invisible in a
    diff, silent at run time, and surfacing as a KPI that is simply absent.
    """
    if not kpi.is_formula:
        return None
    from ..formula.introspect import aggregate_columns

    try:
        columns = aggregate_columns(kpi.compute.expression or "")
    except Exception:                                     # noqa: BLE001
        return None            # a broken expression is `compiles`'s to report
    entity = _entity_grain_tables()
    bad = sorted({ref.split(".")[0] for ref in columns
                  if ref.split(".")[0] in entity})
    if not bad:
        return None
    return (f"aggregates {', '.join(bad)}, which is one row per entity and has "
            f"no month column — the metric cannot be computed at all, and will "
            f"be absent from every run rather than wrong on one")


def compiles(kpi: KPI, universe: Iterable[str]) -> Optional[str]:
    """None if a formula KPI's expression validates, else why not.

    Split from `validate_sheet` because it needs the run-time universe of names
    — other KPI ids and `table.column` references — which the caller assembles
    once per group rather than per sheet.
    """
    if not kpi.is_formula:
        return None
    from ..formula.evaluate import validate

    try:
        report = validate(kpi.compute.expression or "")
    except FormulaError as exc:
        return str(exc)
    unknown = [name for name in report["references"]
               if "." not in name and name not in universe]
    if unknown:
        return f"references {', '.join(sorted(unknown))}, which no KPI defines"
    return None
