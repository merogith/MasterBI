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
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..contract.gate import run_gate
from ..datagen.saas import GeneratedData
from .readers import read_any

MEASURED = "measured"
MODELLED = "modelled"


def load_uploads(paths: List[Path],
                 assignments: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Read each upload into a frame keyed by the fact table it will become.

    `assignments` maps a file name to a target table. Without one, the file is
    keyed by its own stem and the mapping stage decides — which is the honest
    default, since guessing a target from a filename is worse than asking.
    """
    tables: Dict[str, pd.DataFrame] = {}
    detail: Dict[str, Any] = {}

    for path in paths:
        result = read_any(path)
        key = (assignments or {}).get(path.name) or path.stem
        tables[key] = result.frame
        detail[key] = result.as_dict()
    return tables, detail


def fill_missing_tables(tables: Dict[str, pd.DataFrame], profile,
                        fill: List[str]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """Synthesise the tables the user explicitly agreed to fill.

    Opt-in only. Filling by default would put invented numbers beside measured
    ones with nothing marking the difference, which is the failure mode this
    whole path is built to avoid.
    """
    origins = {name: MEASURED for name in tables}
    if not fill:
        return tables, origins

    from ..datagen.saas import generate
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


def build_from_uploads(paths: List[Path], profile, spec) -> Tuple[GeneratedData, Dict[str, str]]:
    """Uploads -> the same GeneratedData shape the generator returns.

    Returning the generator's own type rather than a parallel one is what keeps
    every downstream stage ignorant of the source, which was the point.
    """
    tables, _detail = load_uploads(paths)
    tables, origins = fill_missing_tables(tables, profile, spec.source.fill_gaps)

    checks: List[str] = []
    for name, kind in sorted(origins.items()):
        if kind == MODELLED:
            checks.append(
                f"{name}: MODELLED — no data supplied, generated at your request; "
                f"every KPI reading it is marked accordingly")
        else:
            checks.append(f"{name}: measured from your upload")

    return GeneratedData(tables=tables, anomalies=[], checks=checks), origins


def gate_uploaded(tables: Dict[str, pd.DataFrame], profile) -> List[str]:
    """Run the contract gate in upload mode. Returns the Tier 2 warnings.

    Tier 1 still raises: data that contradicts itself cannot be rendered
    whatever its source. Tier 2 becomes a warning here because the data is the
    truth and the profile is the guess.
    """
    result = run_gate(tables, profile, source="upload")
    return result.warnings
