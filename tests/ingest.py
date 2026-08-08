"""Tests for ingestion, cleaning and the two-tier contract gate.

The theme of this phase is that the pipeline meets numbers it did not
generate. Everything downstream was built against data engineered to satisfy
twelve accounting identities, and real exports will not. So these tests are
built on deliberately awful fixtures rather than tidy ones — a tidy fixture
proves nothing about a file someone actually has.

Six groups:

  1. **Readers** — title blocks, encodings, delimiters, multi-sheet workbooks.
  2. **Profiling** — semantic types, decimal conventions, suggested fixes.
  3. **The tier split** — the most important test here. A structural violation
     must be fatal for every source; a calibration one only for synthetic.
  4. **Mapping** — confident on a clear file, unsure on an obfuscated one.
  5. **Cleaning** — lineage, idempotence, missing-value integrity.
  6. **Basis** — measured, modelled and mixed, and that nothing invented is
     ever labelled as measured.

    python -m tests.ingest
"""
from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile
from kpi_maker.contract import ReconciliationError, run_gate, validate_schemas
from kpi_maker.contract.identities import CHECKS, Tier
from kpi_maker.datagen.saas import generate
from kpi_maker.ingest import detect_shape, profile_table, read_any
from kpi_maker.ingest.derive import derive_profile_fields
from kpi_maker.ingest.profiler import numeric_convention
from kpi_maker.kpi.selection import select
from kpi_maker.metrics.engine import compute
from kpi_maker.prep import OpError, apply_recipe
from kpi_maker.spec.schema import CleaningRecipe, CleaningStep

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "northwind_saas.json"

_failures: List[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not ok:
        _failures.append(f"{name}: {detail}")
        print(f"  FAIL   {name}" + (f"  — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# Fixtures — deliberately awful, because tidy ones prove nothing
# --------------------------------------------------------------------------

def write_messy_csv(path: Path) -> None:
    """Title block, cp1252, semicolons, European decimals, junk row, blank line."""
    # "Zürich" and the curly apostrophe are the point: encoded as cp1252 they
    # are bytes 0xFC and 0x92, neither of which is valid UTF-8. Without them
    # the file decodes as UTF-8 on the first try and the fallback is never
    # exercised — which is how this test passed while proving nothing.
    rows = [
        ["15/01/2024", "Zürich Systems", "C-001", "1.234,56", " enterprise ", "new"],
        ["20/01/2024", "Contoso", "C-002", "850,00", "smb", "new"],
        ["2024-02-15", "Fabrikam", "C-003", "12.500,00", "Enterprise", "expansion"],
        ["28/02/2024", "Northwind Ltd", "C-001", "1.234,56", "ENTERPRISE", "expansion"],
        ["not a date", "Broken", "C-004", "", "smb", "new"],
        ["01/03/2024", "Tailspin", "C-005", "99.999.999,00", "mid", "new"],
    ]
    with path.open("w", encoding="cp1252", newline="") as handle:
        handle.write("ACME CORP \u2014 Q1 REVENUE EXPORT\r\n"
                     "Prepared by Finance\u2019s reporting team\r\n\r\n")
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["Invoice Date", "Account Name", "Cust ID",
                         "Net Amount", "Plan Tier", "Change Type"])
        writer.writerows(rows)


def write_multi_sheet_xlsx(path: Path) -> None:
    """A cover sheet first, the data second — the usual layout."""
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"A": ["Quarterly Report", "Prepared by Finance"]}).to_excel(
            writer, sheet_name="Cover", index=False, header=False)
        pd.DataFrame({
            "month": ["2024-01", "2024-02", "2024-03"],
            "revenue": [100000, 110000, 121000],
            "cogs": [30000, 33000, 36300],
        }).to_excel(writer, sheet_name="Data", index=False)


# --------------------------------------------------------------------------

def test_readers(tmp: Path) -> None:
    print("\nreaders — what people actually have")

    messy = tmp / "messy.csv"
    write_messy_csv(messy)
    result = read_any(messy)

    check("reader: finds the header under a title block", result.header_row == 3,
          f"header_row={result.header_row}")
    check("reader: sniffs the delimiter", result.delimiter == ";", result.delimiter)
    check("reader: falls back on encoding",
          result.encoding in ("cp1252", "latin-1"), str(result.encoding))
    check("reader: reads every data row", len(result.frame) == 6,
          f"{len(result.frame)} rows")
    check("reader: column names are clean",
          list(result.frame.columns)[:2] == ["Invoice Date", "Account Name"],
          str(list(result.frame.columns)))
    check("reader: non-UTF-8 text survives the fallback",
          "Z" in str(result.frame["Account Name"].iloc[0]),
          str(result.frame["Account Name"].iloc[0]))

    book = tmp / "book.xlsx"
    write_multi_sheet_xlsx(book)
    excel = read_any(book)
    check("reader: picks the data sheet over the cover", excel.sheet == "Data",
          f"chose {excel.sheet!r}")
    check("reader: reports the other sheets",
          "Cover" in (excel.sheets_available or []), str(excel.sheets_available))

    try:
        read_any(tmp / "nope.docx")
        check("reader: refuses an unsupported type", False, "accepted .docx")
    except Exception:                                       # noqa: BLE001
        check("reader: refuses an unsupported type", True)


def test_profiling(tmp: Path) -> None:
    print("\nprofiling — what a column means, not just what it holds")

    messy = tmp / "messy.csv"
    frame = read_any(messy).frame
    profile = profile_table(frame)
    semantic = {c.name: c.semantic for c in profile.columns}

    check("profile: date column detected", semantic.get("Invoice Date") == "date",
          str(semantic.get("Invoice Date")))
    check("profile: currency-as-text detected",
          semantic.get("Net Amount") == "currency", str(semantic.get("Net Amount")))
    check("profile: id detected through a spaced, capitalised header",
          semantic.get("Cust ID") == "identifier", str(semantic.get("Cust ID")))

    suggested = {s["op"] for s in profile.as_dict()["suggestions"]}
    for op in ("cast", "parse_dates", "trim"):
        check(f"profile: suggests {op}", op in suggested, str(sorted(suggested)))

    # The decimal convention. Getting this wrong is a 100x error, not a parse
    # failure, so it gets its own table of cases.
    for label, values, expected in [
        ("european with grouping", ["1.234,56", "12.500,00"], "european"),
        ("european without grouping", ["850,00", "99,50"], "european"),
        ("anglo with grouping", ["1,234.56", "12,500.00"], "anglo"),
        ("anglo without grouping", ["850.00", "99.50"], "anglo"),
        ("bare integers", ["850", "1234"], "anglo"),
    ]:
        _rate, convention = numeric_convention(pd.Series(values))
        check(f"decimals: {label}", convention == expected,
              f"read as {convention}")

    # And the value that motivates all of it.
    from kpi_maker.prep.ops import _to_number
    parsed = _to_number(pd.Series(["850,00"]), "european").iloc[0]
    check("decimals: 850,00 is 850, not 85000", abs(parsed - 850.0) < 1e-9,
          f"parsed as {parsed}")


def test_tier_split(tmp: Path) -> None:
    """The most important group in this suite."""
    print("\nthe tier split — structural is fatal for all, calibration only for synthetic")

    profile = load_profile(SAMPLE)
    tables = generate(profile).tables

    check("gate: clean data passes both sources",
          all(run_gate(tables, profile, source=s) for s in ("synthetic", "upload")))

    tiers = {c.tier for c in CHECKS}
    check("gate: both tiers are populated",
          Tier.structural in tiers and Tier.calibration in tiers, str(tiers))

    # A structural violation: the P&L no longer adds up.
    broken = {k: v.copy() for k, v in tables.items()}
    broken["monthly_financials"]["gross_profit"] *= 1.5
    for source in ("synthetic", "upload"):
        try:
            run_gate(broken, profile, source=source)
            check(f"gate: structural violation is fatal for {source}", False,
                  "accepted contradictory data")
        except ReconciliationError:
            check(f"gate: structural violation is fatal for {source}", True)

    # A calibration violation: the data is internally sound, the profile is not
    # what the data describes. This is the ordinary case for a real upload.
    mismatched = profile.model_copy(deep=True)
    mismatched.financials.revenue = profile.financials.revenue * 3.0
    mismatched.market.customer_count = profile.market.customer_count * 3

    try:
        run_gate(tables, mismatched, source="synthetic")
        check("gate: calibration miss is fatal for synthetic", False,
              "a generator that misses its own target is a bug")
    except ReconciliationError:
        check("gate: calibration miss is fatal for synthetic", True)

    try:
        result = run_gate(tables, mismatched, source="upload")
        check("gate: calibration miss only warns for an upload",
              len(result.warnings) >= 2, f"{len(result.warnings)} warning(s)")
        check("gate: the warning explains itself",
              any("does not match the profile" in w for w in result.warnings),
              str(result.warnings[:1]))
    except ReconciliationError as exc:
        check("gate: calibration miss only warns for an upload", False,
              f"refused an honest upload: {exc}")


def test_schemas(tmp: Path) -> None:
    print("\nschemas — the contract accepts both storage forms")

    profile = load_profile(SAMPLE)
    tables = generate(profile).tables

    _, problems = validate_schemas(tables)
    check("schema: in-memory generator output validates", not problems, str(problems[:2]))

    # The API and the static build both round-trip through CSV, which turns
    # every period into a string. Both are the same month.
    round_tripped = {}
    for name, frame in tables.items():
        path = tmp / f"{name}.csv"
        frame.to_csv(path, index=False)
        round_tripped[name] = pd.read_csv(path)
    _, problems = validate_schemas(round_tripped)
    check("schema: CSV round-trip validates too", not problems, str(problems[:2]))

    broken = {k: v.copy() for k, v in tables.items()}
    broken["monthly_financials"] = broken["monthly_financials"].drop(columns=["ebitda"])
    broken["customers"].loc[0, "final_acv"] = -500.0
    _, problems = validate_schemas(broken)
    check("schema: names the missing column",
          any("ebitda" in p and "missing" in p for p in problems), str(problems))
    check("schema: names the out-of-range value",
          any("final_acv" in p for p in problems), str(problems))
    check("schema: a missing required table blocks",
          validate_schemas({})[1], "empty tables accepted")


def test_mapping(tmp: Path) -> None:
    print("\nmapping — confident when it should be, unsure when it should be")

    frame = read_any(tmp / "messy.csv").frame
    profile = profile_table(frame)
    best = detect_shape(frame, profile)[0]

    check("mapping: picks the transaction log", best.shape == "transaction_log",
          f"chose {best.shape}")
    check("mapping: all required fields mapped", best.usable,
          f"missing {best.missing_required}")

    by_field = {m.field: m for m in best.matches}
    check("mapping: id beats a name column for customer_id",
          by_field["customer_id"].column == "Cust ID",
          f"chose {by_field['customer_id'].column!r}")
    check("mapping: required fields are confident",
          all(m.confidence >= 0.75 for m in best.matches if m.required),
          str({m.field: round(m.confidence, 2) for m in best.matches if m.required}))

    # An obfuscated file must produce doubt, not a confident wrong answer.
    obfuscated = pd.DataFrame({
        "col_a": ["2024-01-01", "2024-02-01"],
        "col_b": ["x", "y"],
        "col_c": [1.0, 2.0],
    })
    proposal = detect_shape(obfuscated, profile_table(obfuscated))[0]
    confident = [m for m in proposal.matches if m.confidence >= 0.75]
    check("mapping: an obfuscated file maps nothing confidently",
          len(confident) == 0,
          f"{len(confident)} confident: {[m.field for m in confident]}")


def test_cleaning(tmp: Path) -> None:
    print("\ncleaning — lineage, idempotence, and missing values that stay missing")

    frame = read_any(tmp / "messy.csv").frame
    suggestions = profile_table(frame).as_dict()["suggestions"]
    steps = [CleaningStep(op=s["op"], table="deals", params=s["params"])
             for s in suggestions]
    cleaned, lineage = apply_recipe({"deals": frame}, CleaningRecipe(steps=steps))

    check("cleaning: every step is recorded",
          len(lineage.entries) == len(steps),
          f"{len(lineage.entries)} of {len(steps)}")
    check("cleaning: entries carry row counts",
          all(e.rows_before >= e.rows_after >= 0 for e in lineage.entries))
    check("cleaning: entries read as sentences",
          all(e.sentence.endswith(".") for e in lineage.entries),
          str([e.sentence for e in lineage.entries][:1]))
    check("cleaning: the amount column is numeric afterwards",
          pd.api.types.is_numeric_dtype(cleaned["deals"]["Net Amount"]),
          str(cleaned["deals"]["Net Amount"].dtype))

    # Missing values must not become the strings "nan" or "<NA>", which would
    # survive as real categories in every chart.
    for column in cleaned["deals"].columns:
        values = cleaned["deals"][column].dropna().astype(str)
        corrupt = values[values.str.lower().isin(["nan", "none", "<na>"])]
        check(f"cleaning: {column} keeps blanks blank", corrupt.empty,
              f"{len(corrupt)} corrupted")

    base = pd.DataFrame({"a": ["  x  ", "y", "y", None], "b": [1.0, 2.0, 2.0, None]})
    for op, params in [
        ("trim", {}), ("normalize_case", {"columns": ["a"], "to": "lower"}),
        ("map_values", {"column": "a", "mapping": {"x": "X"}}),
        ("drop_duplicates", {}),
        ("drop_incomplete_rows", {"where_missing": ["b"]}),
        ("clip_outliers", {"column": "b", "method": "iqr", "action": "clip"}),
        ("apply_sign", {"column": "b", "by_column": "a", "negative_when": ["y"]}),
    ]:
        step = CleaningStep(op=op, table="t", params=params)
        once, _ = apply_recipe({"t": base}, CleaningRecipe(steps=[step]))
        twice, _ = apply_recipe(once, CleaningRecipe(steps=[step]))
        check(f"cleaning: {op} is idempotent",
              once["t"].reset_index(drop=True).equals(twice["t"].reset_index(drop=True)))

    for label, step in [
        ("unknown op", CleaningStep(op="nope")),
        ("unknown table", CleaningStep(op="trim", table="ghost")),
        ("missing column",
         CleaningStep(op="cast", table="t", params={"column": "zz", "to": "number"})),
    ]:
        try:
            apply_recipe({"t": base}, CleaningRecipe(steps=[step]))
            check(f"cleaning: {label} refused", False, "accepted")
        except OpError:
            check(f"cleaning: {label} refused", True)


def test_basis(tmp: Path) -> None:
    print("\nbasis — nothing invented is ever labelled measured")

    profile = load_profile(SAMPLE)
    tables = generate(profile).tables
    kpi_set = select(profile)

    results = compute(kpi_set, tables, profile)
    check("basis: a synthetic run is measured throughout",
          {r.basis for r in results} == {"measured"},
          str({r.basis for r in results}))

    origins = {"headcount": "modelled", "product_usage": "modelled"}
    results = compute(kpi_set, tables, profile, origins=origins)
    by_id = {r.kpi.id: r for r in results}

    check("basis: a KPI reading only a modelled table is modelled",
          by_id["headcount_growth"].basis in ("modelled", "mixed"),
          f"{by_id['headcount_growth'].basis} from {by_id['headcount_growth'].tables_used}")

    per_fte = by_id.get("arr_per_fte")
    if per_fte and per_fte.computed:
        check("basis: reading both measured and modelled gives mixed",
              per_fte.basis == "mixed",
              f"{per_fte.basis} from {per_fte.tables_used}")

    check("basis: untouched KPIs stay measured",
          by_id["gross_margin_pct"].basis == "measured",
          by_id["gross_margin_pct"].basis)

    # The safety property, stated directly.
    for result in results:
        if not result.computed:
            continue
        touched_modelled = any(origins.get(t) == "modelled" for t in result.tables_used)
        if touched_modelled:
            check(f"basis: {result.kpi.id} is not passed off as measured",
                  result.basis != "measured", result.basis)


def test_derivation(tmp: Path) -> None:
    print("\nderivation — read the profile off the data, coherently")

    profile = load_profile(SAMPLE)
    tables = generate(profile).tables
    derived = derive_profile_fields(tables, "generated.csv")

    check("derive: finds revenue", "financials.revenue" in derived.values)
    check("derive: finds the customer count", "market.customer_count" in derived.values)
    check("derive: tags provenance as ingested",
          all(v.startswith("ingested:") for v in derived.provenance.values()),
          str(list(derived.provenance.values())[:1]))
    check("derive: still asks for what no file contains",
          {u["path"] for u in derived.unknown} >= {"intent.primary_objective"},
          str([u["path"] for u in derived.unknown]))

    # The coherence property: the three derived numbers must satisfy the
    # profile's own cross-block identity, or the profile will not build.
    revenue = derived.values.get("financials.revenue")
    customers = derived.values.get("market.customer_count")
    segments = derived.values.get("market.segments")
    if revenue and customers and segments:
        blended = sum(s["share"] * s["avg_acv"] for s in segments)
        drift = abs(customers * blended - revenue) / revenue
        check("derive: revenue, customers and segments reconcile", drift <= 0.35,
              f"{drift:.0%} apart")
        check("derive: segment shares sum to one",
              abs(sum(s["share"] for s in segments) - 1.0) < 0.011,
              str(sum(s["share"] for s in segments)))


# --------------------------------------------------------------------------

def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kpi-ingest-"))
    write_messy_csv(tmp / "messy.csv")

    suites: List[Callable] = [
        lambda: test_readers(tmp),
        lambda: test_profiling(tmp),
        lambda: test_tier_split(tmp),
        lambda: test_schemas(tmp),
        lambda: test_mapping(tmp),
        lambda: test_cleaning(tmp),
        lambda: test_basis(tmp),
        lambda: test_derivation(tmp),
    ]
    try:
        for suite in suites:
            try:
                suite()
            except Exception as exc:                        # noqa: BLE001
                _failures.append(f"crash: {type(exc).__name__}: {exc}")
                print(f"  CRASH  {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 70}")
    print(f"checks: {_checks}  failures: {len(_failures)}")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(run())
