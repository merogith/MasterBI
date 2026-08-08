"""Content digests for artifacts that are not byte-reproducible.

The no-regression gate compares artifact bytes against a captured baseline.
Four of the nine cannot take part in that, because they stamp themselves with
the moment they were written:

    report.pdf      /CreationDate and /ModDate in the document catalog
    report.docx     zip entry timestamps
    deck.pptx       zip entry timestamps
    workbook.xlsx   zip entry timestamps and docProps/core.xml

That exclusion was harmless while nothing touched those renderers. P3 rewires
all three of them, so it stopped being harmless: the phase's central safety
claim — a default DesignSpec changes nothing — was unverifiable for exactly the
deliverables most at risk. This module restores the check by hashing what is
*in* the file rather than the file.

The stripping is deliberately narrow. Only fields that provably carry a clock
are removed; anything else that moves is a real change and should fail.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Dict, List

# Fixed-width date strings, so removing them does not shift the byte offsets
# recorded in the PDF cross-reference table.
_PDF_DATES = re.compile(rb"/(?:CreationDate|ModDate)\s*\(D:[^)]*\)")
_PDF_ID = re.compile(rb"/ID\s*\[<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\]")

# Office members whose entire job is to record who wrote the file and when.
_VOLATILE_MEMBERS = {"docProps/core.xml"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_digest(path: Path) -> str:
    return _digest(_PDF_ID.sub(b"", _PDF_DATES.sub(b"", Path(path).read_bytes())))


def zip_digest(path: Path) -> str:
    """Hash the members and their names, ignoring entry timestamps and metadata.

    Sorted by name so that a change in write order — which the reader never
    sees — does not read as a regression.
    """
    accumulator = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name in _VOLATILE_MEMBERS:
                continue
            accumulator.update(name.encode("utf-8"))
            accumulator.update(archive.read(name))
    return accumulator.hexdigest()


_HANDLERS = {".pdf": pdf_digest, ".docx": zip_digest, ".pptx": zip_digest,
             ".xlsx": zip_digest}


def content_digest(path: Path) -> str:
    """A stable digest for any artifact, timestamped format or not."""
    path = Path(path)
    handler = _HANDLERS.get(path.suffix.lower())
    return handler(path) if handler else _digest(path.read_bytes())


def digest_tree(root: Path) -> Dict[str, str]:
    """Every artifact under `root`, keyed by its path relative to it."""
    root = Path(root)
    return {str(p.relative_to(root)): content_digest(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".cache" not in p.parts}


def compare(baseline: Dict[str, str], current: Dict[str, str]) -> List[str]:
    """Differences, as sentences. Empty means the two trees are equivalent."""
    problems = []
    for name in sorted(set(baseline) - set(current)):
        problems.append(f"{name}: missing now")
    for name in sorted(set(current) - set(baseline)):
        problems.append(f"{name}: new, not in the baseline")
    for name in sorted(set(baseline) & set(current)):
        if baseline[name] != current[name]:
            problems.append(f"{name}: content changed")
    return problems
