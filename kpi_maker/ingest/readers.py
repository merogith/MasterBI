"""Read whatever the user actually has.

Real exports are not tidy CSVs. They arrive with a title block above the
header, semicolons instead of commas, European decimals, a byte-order mark,
cp1252 encoding from an old Excel, and four sheets of which one is the data.
Guessing all of that correctly is most of the work of "bring your data", and
guessing it *wrong* silently is worse than refusing the file.

So every inference here is reported alongside the frame, and the user can
override each one.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Tried in order. utf-8-sig first because it strips the BOM Excel writes and
# otherwise leaves a zero-width character welded to the first column's name —
# which then fails to match any mapping and is invisible in every error message.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

MAX_HEADER_SCAN = 12       # rows to consider as the real header
SNIFF_BYTES = 64 * 1024


class ReadError(ValueError):
    """A file that cannot be read as tabular data."""


@dataclass
class ReadResult:
    frame: pd.DataFrame
    sheet: Optional[str] = None
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    header_row: int = 0
    notes: List[str] = field(default_factory=list)
    sheets_available: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sheet": self.sheet, "encoding": self.encoding,
            "delimiter": self.delimiter, "header_row": self.header_row,
            "notes": self.notes, "sheets_available": self.sheets_available,
            "rows": int(len(self.frame)),
            "columns": [str(c) for c in self.frame.columns],
        }


def read_any(path: Path, sheet: Optional[str] = None,
             header_row: Optional[int] = None) -> ReadResult:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        return _read_excel(path, sheet, header_row)
    if suffix in (".csv", ".tsv", ".txt"):
        return _read_delimited(path, header_row)
    raise ReadError(f"Unsupported file type {suffix!r}. Use CSV, TSV or Excel.")


# --------------------------------------------------------------------------

def _read_delimited(path: Path, header_row: Optional[int]) -> ReadResult:
    raw, encoding, notes = _decode(path)
    delimiter = _sniff_delimiter(raw)

    probe = _probe_grid(raw, delimiter)
    if probe.empty:
        raise ReadError("The file has no rows.")

    row = header_row if header_row is not None else _find_header_row(probe)
    if row > 0:
        notes.append(f"Skipped {row} row(s) above the header — a title block, "
                     f"by the look of it.")

    frame = pd.read_csv(io.StringIO(raw), sep=delimiter, skiprows=row, dtype=str,
                        keep_default_na=False, na_values=["", "NA", "N/A", "null", "-"])
    frame, more = _tidy(frame)
    notes += more

    return ReadResult(frame=frame, encoding=encoding, delimiter=delimiter,
                      header_row=row, notes=notes)


def _read_excel(path: Path, sheet: Optional[str],
                header_row: Optional[int]) -> ReadResult:
    try:
        book = pd.ExcelFile(path)
    except Exception as exc:                                # noqa: BLE001
        raise ReadError(f"Could not open the workbook: {exc}") from exc

    available = list(book.sheet_names)
    chosen = sheet or _pick_sheet(book, available)
    notes: List[str] = []
    if sheet is None and len(available) > 1:
        notes.append(f"Read the {chosen!r} sheet; {len(available) - 1} other "
                     f"sheet(s) available.")

    probe = book.parse(chosen, header=None, dtype=str)
    if probe.empty:
        raise ReadError(f"Sheet {chosen!r} is empty.")
    row = header_row if header_row is not None else _find_header_row(probe)
    if row > 0:
        notes.append(f"Skipped {row} row(s) above the header.")

    frame = book.parse(chosen, skiprows=row, dtype=str)
    frame, more = _tidy(frame)
    notes += more

    return ReadResult(frame=frame, sheet=chosen, header_row=row, notes=notes,
                      sheets_available=available)


# --------------------------------------------------------------------------

def _decode(path: Path):
    data = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        notes = []
        if encoding != "utf-8-sig":
            notes.append(f"Read as {encoding}; it is not valid UTF-8.")
        return text, encoding, notes
    raise ReadError("Could not decode the file in any supported encoding.")


def _sniff_delimiter(raw: str) -> str:
    sample = raw[:SNIFF_BYTES]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # The sniffer gives up on short or irregular files. Counting is crude
        # but never raises, and a wrong guess is visible immediately as a
        # one-column frame.
        counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def _probe_grid(raw: str, delimiter: str) -> pd.DataFrame:
    """The first few rows as a ragged grid, for header detection.

    Not `pd.read_csv(header=None)`: a title block has one field per line while
    the data has many, and the C parser refuses that outright with "Expected 1
    fields, saw 6" — on exactly the files this function exists to handle. The
    csv module tolerates ragged rows, and a grid of the first dozen lines is
    all the header sniffer needs.
    """
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows: List[List[str]] = []
    for line in reader:
        rows.append(line)
        if len(rows) >= MAX_HEADER_SCAN:
            break
    if not rows:
        return pd.DataFrame()
    width = max(len(r) for r in rows)
    return pd.DataFrame([r + [None] * (width - len(r)) for r in rows])


def _find_header_row(probe: pd.DataFrame) -> int:
    """The first row that looks like column names rather than data.

    A header row is mostly non-empty, mostly text, and has no repeats. Exports
    from finance systems commonly carry a report title, a date range and a
    blank line first, and reading those as the header names every column
    'Unnamed: 3'.
    """
    best_row, best_score = 0, -1.0
    for i in range(min(MAX_HEADER_SCAN, len(probe))):
        values = [v for v in probe.iloc[i].tolist()
                  if v is not None and str(v).strip() not in ("", "nan")]
        if len(values) < 2:
            continue
        filled = len(values) / max(len(probe.columns), 1)
        texty = sum(1 for v in values if not _looks_numeric(v)) / len(values)
        unique = len(set(map(str, values))) / len(values)
        score = filled + texty + unique
        # A later row must be clearly better to displace an earlier one;
        # otherwise a data row of text beats a real header further up.
        if score > best_score + 0.35:
            best_row, best_score = i, score
    return best_row


def _looks_numeric(value) -> bool:
    text = str(value).strip().replace(" ", "")
    for junk in ("$", "€", "£", "%", ","):
        text = text.replace(junk, "")
    try:
        float(text)
        return True
    except ValueError:
        return False


def _tidy(frame: pd.DataFrame):
    """Drop the debris every export carries: blank rows, blank columns, dupes."""
    notes: List[str] = []
    before_rows, before_cols = len(frame), len(frame.columns)

    frame = frame.dropna(axis=1, how="all")
    unnamed = [c for c in frame.columns if str(c).startswith("Unnamed:")]
    if unnamed:
        empty_unnamed = [c for c in unnamed if frame[c].isna().all()]
        frame = frame.drop(columns=empty_unnamed)

    frame = frame.dropna(axis=0, how="all")
    frame.columns = [str(c).strip() for c in frame.columns]

    # Duplicate header names silently shadow each other on every later lookup.
    seen: Dict[str, int] = {}
    renamed = []
    for column in frame.columns:
        if column in seen:
            seen[column] += 1
            renamed.append(f"{column}_{seen[column]}")
        else:
            seen[column] = 0
            renamed.append(column)
    if renamed != list(frame.columns):
        notes.append("Renamed duplicate column headers so they stop shadowing "
                     "each other.")
        frame.columns = renamed

    if len(frame) < before_rows:
        notes.append(f"Dropped {before_rows - len(frame)} entirely blank row(s).")
    if len(frame.columns) < before_cols:
        notes.append(f"Dropped {before_cols - len(frame.columns)} entirely blank column(s).")

    return frame.reset_index(drop=True), notes


def _pick_sheet(book: pd.ExcelFile, available: List[str]) -> str:
    """The sheet with the most data-shaped content.

    Workbooks routinely lead with a cover sheet or a parameters tab, so "the
    first sheet" is the wrong default more often than it is right.
    """
    best, best_score = available[0], -1
    for name in available:
        try:
            probe = book.parse(name, header=None, nrows=40, dtype=str)
        except Exception:                                   # noqa: BLE001
            continue
        score = int(probe.notna().to_numpy().sum())
        if score > best_score:
            best, best_score = name, score
    return best
