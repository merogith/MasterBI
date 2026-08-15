"""Turn uploaded files into the fact-table contract.

This is the seam ARCHITECTURE §3 always described: uploaded data enters the
*same* fact tables synthetic data does, so nothing downstream learns which it
got. The metrics engine, the detectors, the charts and every renderer are
unchanged.

Order matters, and it is not the obvious one:

    read -> (clean) -> map -> gate

Cleaning happens on the user's own column names, before mapping, because those
are the names they recognise and the ones the profiler reported problems
against. Mapping to canonical names last means the recipe stays readable and a
re-map does not invalidate the cleaning.

**Which fact table a file becomes is decided here; which column becomes which
field is not.** The two used to be one unanswered question, and the result was
the quietest failure in the project: `load_uploads` keyed every file by its own
stem, so a clean 24-month P&L called `finance_export_2025.csv` arrived as a
table named `finance_export_2025`. Nothing raised. The gate passed, the run
finished, nine artifacts were written, and **every one of the eighteen KPIs
reported "needs the monthly_financials table, which this run does not have"** —
a board pack with nothing in it and no error anywhere. Measured, not inferred.

`ingest/shapes.py` and `ingest/mapping.py` existed the whole time and were
never called by this module. They are now: `plan_uploads` scores the file's
contents against every shape and takes the winner's target table when it is
usable. That is not the filename guess the old docstring rightly refused —
it is a content match with a confidence and a stated reason, and an explicit
assignment always beats it.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..contract.gate import run_gate
from ..contract.schemas import FACT_SCHEMAS
from ..datagen.saas import GeneratedData
from .mapping import CONFIDENT, MappingProposal, detect_shape
from .profiler import profile_table
from .readers import read_any

MEASURED = "measured"
MODELLED = "modelled"


@dataclass
class UploadPlan:
    """What one uploaded file was decided to be, and why.

    Carried out of `build_from_uploads` rather than kept internal because every
    part of it is something the user is owed an explanation of: a file read as
    the wrong table produces an empty dashboard, and "we guessed" is only
    acceptable when the guess is visible and reversible.
    """
    filename: str
    table: str
    shape: Optional[str] = None
    confidence: float = 0.0
    mapping: Dict[str, str] = dataclass_field(default_factory=dict)
    note: str = ""
    read_fixes: List[str] = dataclass_field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename, "table": self.table, "shape": self.shape,
            "confidence": round(self.confidence, 3), "mapping": self.mapping,
            "note": self.note, "read_fixes": self.read_fixes,
        }


# The profiler's suggestions split cleanly in two, and only one half belongs
# here. `cast` and `parse_dates` are **reading** the file: `readers.py` loads
# every column as text on purpose, because 1.234,56 read as Anglo is a
# plausible number wrong by three orders of magnitude, so the convention has to
# be detected rather than assumed — and the profiler detects it. Applying its
# answer is finishing the read.
#
# `fill_missing`, `normalize_case` and `trim` are judgements about someone's
# data, and they stay in the Clean panel where the user can see and undo them.
#
# Without this an upload arrives as strings in every column and *nothing*
# computes: `revenue - cogs` on text raises, and a formula that happens not to
# raise returns concatenated digits. The suggestion existed, was correct, and
# was shown to a user who had no reason to know the run depended on it.
_READ_COMPLETING_OPS = ("cast", "parse_dates")


def _finish_reading(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Apply the profiler's own type fixes. Returns (frame, what was done)."""
    from ..prep.recipe import apply_recipe
    from ..spec.schema import CleaningRecipe, CleaningStep

    profile = profile_table(frame)
    steps = [CleaningStep(op=s["op"], params=s["params"])
             for column in profile.columns for s in column.suggestions
             if s["op"] in _READ_COMPLETING_OPS]
    if not steps:
        return frame, []

    tables, lineage = apply_recipe({"upload": frame}, CleaningRecipe(steps=steps))
    return tables["upload"], [entry.sentence for entry in lineage.entries]


def _plan_for(path: Path, frame: pd.DataFrame,
              assigned: Optional[str]) -> UploadPlan:
    """Decide the fact table this file becomes, most trustworthy rule first."""
    if assigned:
        return UploadPlan(path.name, assigned, note="you assigned this file")

    # Someone whose file is called `monthly_financials.csv` has already
    # answered the question. Checked before detection so an exact statement is
    # never overruled by a score.
    if path.stem in FACT_SCHEMAS:
        return UploadPlan(path.name, path.stem,
                          note="the file is named for the table it becomes")

    proposals = detect_shape(frame, profile_table(frame))
    best: Optional[MappingProposal] = proposals[0] if proposals else None
    if best is not None and best.usable:
        required = [m for m in best.matches if m.required]
        confidence = (sum(m.confidence for m in required) / len(required)
                      if required else 0.0)
        strength = "matches" if confidence >= CONFIDENT else "looks most like"
        return UploadPlan(
            path.name, best.target_table, shape=best.shape,
            confidence=confidence, mapping=best.to_spec(),
            note=(f"{strength} the {best.shape.replace('_', ' ')} shape "
                  f"({confidence:.0%} on its required fields)"))

    # Nothing fits. Keep the stem — the old behaviour — but say so, because
    # this is the case where the run will be narrow and the user needs to know
    # it was the file, not the product.
    missing = ", ".join(best.missing_required) if best else ""
    return UploadPlan(
        path.name, path.stem,
        note=(f"no shape matched — the closest needs {missing}, which no column "
              f"in this file supplies" if missing else
              "no shape matched this file's columns"))


def plan_uploads(paths: List[Path],
                 assignments: Optional[Dict[str, str]] = None,
                 ) -> Tuple[Dict[str, pd.DataFrame], List[UploadPlan], Dict[str, Any]]:
    """Read each upload and decide the fact table it becomes.

    `assignments` maps a file name to a target table and always wins: shape
    detection is a proposal, and a caller who has said otherwise has said so.
    The pipeline passes none — there the override is `spec.model.mapping` —
    but the API's upload routes know which file the user pointed at.
    """
    tables: Dict[str, pd.DataFrame] = {}
    plans: List[UploadPlan] = []
    detail: Dict[str, Any] = {}

    for path in paths:
        result = read_any(path)
        # Types first: shape detection scores on semantic type as well as name,
        # and every column arriving as text makes a currency field look like
        # one more piece of prose.
        frame, fixes = _finish_reading(result.frame)
        plan = _plan_for(path, frame, (assignments or {}).get(path.name))
        plan.read_fixes = fixes
        # Two files landing on one table would silently drop the first. Keep
        # both, under the second's own name, rather than lose data quietly.
        if plan.table in tables:
            plan.table = path.stem
            plan.note += " — another file already took that table, so this one "\
                         "keeps its own name"
        tables[plan.table] = frame
        detail[plan.table] = result.as_dict()
        plans.append(plan)
    return tables, plans, detail


def load_uploads(paths: List[Path],
                 assignments: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Read each upload into a frame keyed by the fact table it will become.

    Kept as the two-value form `plan_uploads` grew out of; the plans are what
    callers wanting the reasoning should ask for.
    """
    tables, _plans, detail = plan_uploads(paths, assignments)
    return tables, detail


# The P&L columns every schema requires that are pure arithmetic on the lines a
# real export actually contains. Each is a **Tier 1 structural identity** in
# `contract/identities.py`, which is why deriving them is not modelling: the
# gate would reject any other value for them, so there is exactly one right
# answer and refusing to compute it just withholds it.
#
# Nobody's accounting export has a column called `net_burn`, and `pnl_export`
# is right not to ask for one — but every schema and half the cross-sector pack
# wants `gross_profit`, `total_opex` and `ebitda`. Without this an uploaded P&L
# reached the metrics engine and produced six `has no column` errors on a file
# containing everything needed to compute all six.
_DERIVED_PL = ("gross_profit", "total_opex", "ebitda", "gross_margin_pct")


def derive_pl_columns(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Fill the definitional P&L columns an export never carries.

    Returns (frame, names added). Only ever *adds*: a column the user supplied
    is theirs, even where we would have computed a different number, because
    their ledger is the record and ours is a restatement of it.

    Stops at arithmetic. `capex`, `free_cash_flow` and `net_burn` are **not**
    derived, though the generator emits all three — it gets them from a working
    capital model (capex as a fixed share of revenue, inventory build from the
    change in COGS) that is a reasonable simulation and would be an invention
    on someone's real accounts. A KPI that needs them says so, which is true.
    """
    out = frame.copy()
    added: List[str] = []

    def have(*columns: str) -> bool:
        return all(c in out.columns for c in columns)

    # gross_profit = revenue - cogs
    if "gross_profit" not in out.columns and have("revenue", "cogs"):
        out["gross_profit"] = out["revenue"] - out["cogs"]
        added.append("gross_profit")

    # total_opex = sum of opex lines. A missing line is zero rather than a
    # refusal: an export with no R&D column is a business with no R&D line,
    # not an unanswerable question.
    opex_lines = ["sales_cost", "marketing_cost", "rnd_cost", "ga_cost"]
    if "total_opex" not in out.columns and any(c in out.columns for c in opex_lines):
        present = [c for c in opex_lines if c in out.columns]
        out["total_opex"] = out[present].sum(axis=1)
        added.append("total_opex")

    # ebitda = gross_profit - total_opex
    if "ebitda" not in out.columns and have("gross_profit", "total_opex"):
        out["ebitda"] = out["gross_profit"] - out["total_opex"]
        added.append("ebitda")

    if "gross_margin_pct" not in out.columns and have("gross_profit", "revenue"):
        out["gross_margin_pct"] = (out["gross_profit"] / out["revenue"]
                                   .replace(0, pd.NA)).fillna(0.0)
        added.append("gross_margin_pct")

    return out, added


def fill_missing_tables(tables: Dict[str, pd.DataFrame], profile,
                        fill: List[str]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """Synthesise the tables the user explicitly agreed to fill.

    Opt-in only. Filling by default would put invented numbers beside measured
    ones with nothing marking the difference, which is the failure mode this
    whole path is built to avoid.
    """
    origins = dict.fromkeys(tables, MEASURED)
    if not fill:
        return tables, origins

    # Route through the registry the rest of the pipeline uses, not the SaaS
    # module by name. Gap-filling an e-commerce upload with `datagen.saas`
    # synthesised subscription tables — MRR movements for a retailer — and
    # labelled them MODELLED, which made an invented number look like a
    # deliberate one rather than a wrong one.
    from ..datagen import GENERATORS
    from ..profile.sectors import resolve_archetype

    archetype = resolve_archetype(profile.business_model.type.value).value
    generate = GENERATORS.get(archetype) or GENERATORS["saas"]
    synthetic = generate(profile)

    out = dict(tables)
    for name in fill:
        if name in out and not out[name].empty:
            continue                      # the user supplied it after all
        frame = synthetic.tables.get(name)
        if frame is None:
            continue
        out[name] = frame
        origins[name] = MODELLED
    return out, origins


def apply_mapping(tables: Dict[str, pd.DataFrame],
                  mapping: Dict[str, Dict[str, str]]) -> Dict[str, pd.DataFrame]:
    """Rename source columns to canonical fact-table names.

    `mapping` is `{fact_table: {canonical_field: source_column}}`. The source
    key may be the fact table itself (a file already assigned to it) or the
    file stem, so a user can map `deals.csv` onto `mrr_movements` without
    renaming the file.
    """
    if not mapping:
        return tables

    out = dict(tables)
    for target, fields in mapping.items():
        source_key = target if target in out else _source_key(out, fields)
        if source_key is None:
            continue
        frame = out[source_key]
        renames = {column: canonical for canonical, column in fields.items()
                   if column in frame.columns and column != canonical}
        renamed = frame.rename(columns=renames).copy()

        if source_key != target:
            out.pop(source_key)
        out[target] = renamed
    return out


def _source_key(tables: Dict[str, pd.DataFrame],
                fields: Dict[str, str]) -> Optional[str]:
    """The uploaded table whose columns this mapping refers to."""
    wanted = set(fields.values())
    best, best_hits = None, 0
    for name, frame in tables.items():
        hits = len(wanted & set(map(str, frame.columns)))
        if hits > best_hits:
            best, best_hits = name, hits
    return best if best_hits else None


def detected_mapping(plans: List[UploadPlan],
                     explicit: Optional[Dict[str, Dict[str, str]]] = None,
                     ) -> Dict[str, Dict[str, str]]:
    """The column mapping shape detection proposes, under anything explicit.

    Merged per *field*, not per table: a user who corrected `revenue` should
    keep the seven fields they did not correct, and replacing the whole table's
    mapping with a one-field override would silently discard them.
    """
    merged: Dict[str, Dict[str, str]] = {}
    for plan in plans:
        if plan.mapping:
            merged[plan.table] = dict(plan.mapping)
    for table, fields in (explicit or {}).items():
        merged[table] = {**merged.get(table, {}), **fields}
    return merged


def build_from_uploads(paths: List[Path], profile, spec,
                       ) -> Tuple[GeneratedData, Dict[str, str], List[UploadPlan]]:
    """Uploads -> the same GeneratedData shape the generator returns.

    Returning the generator's own type rather than a parallel one is what keeps
    every downstream stage ignorant of the source, which was the point.

    The plans also ride on `GeneratedData.upload_plans` — see the comment there
    for why they must be part of the stage's cached output rather than set on
    the context — and are returned here as well so a direct caller does not have
    to reach into the result to find them.
    """
    # No assignments here on purpose. A user who disagrees with the detected
    # table says so through `spec.model.mapping`, and `apply_mapping` relocates
    # by column overlap in the `model` stage — the override path that already
    # exists. Adding a second one would be a spec field with one consumer and
    # two ways to answer the same question.
    tables, plans, _detail = plan_uploads(paths)
    tables, origins = fill_missing_tables(tables, profile, spec.source.fill_gaps)

    checks: List[str] = []
    for plan in plans:
        checks.append(f"{plan.filename} -> {plan.table}: {plan.note}")
    for name, kind in sorted(origins.items()):
        if kind == MODELLED:
            checks.append(
                f"{name}: MODELLED — no data supplied, generated at your request; "
                f"every KPI reading it is marked accordingly")
        else:
            checks.append(f"{name}: measured from your upload")

    return (GeneratedData(tables=tables, anomalies=[], checks=checks,
                          upload_plans=plans),
            origins, plans)


def gate_uploaded(tables: Dict[str, pd.DataFrame], profile) -> List[str]:
    """Run the contract gate in upload mode. Returns the Tier 2 warnings.

    Tier 1 still raises: data that contradicts itself cannot be rendered
    whatever its source. Tier 2 becomes a warning here because the data is the
    truth and the profile is the guess.
    """
    result = run_gate(tables, profile, source="upload")
    return result.warnings
