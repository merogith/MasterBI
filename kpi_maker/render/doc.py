"""Editable report (DOCX).

Same eight sections as the PDF. The difference is intent: the PDF is the record,
the DOCX is what a client edits before circulating it internally. So this one
uses real Word heading styles (so the navigation pane and any generated table of
contents work) rather than hand-styled text.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from ..design.palette import heading_accent
from ..insight.detectors import Finding
from ..kpi.schema import KPISet
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
from ..viz.theme import TOKENS
from .sections import SectionContent, SectionContext, build as build_sections

# Where the reader should turn a page. A property of this format: the deck has
# no pages, and the PDF decides from its own y cursor.
PAGE_BREAK_BEFORE = {"diagnostic", "deep_dives", "benchmarks", "appendix"}

# Inches, against Word's default page width at these margins.
TABLE_WIDTHS = {"scorecard": [2.0, 0.9, 0.9, 0.9, 1.0, 0.8],
                "actions": [0.3, 2.6, 1.1, 0.7, 0.7, 0.6]}


def _rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _style(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Segoe UI"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(6)
    for name, size, color in (("Heading 1", 18, "text_primary"),
                              ("Heading 2", 13, "text_primary"),
                              ("Heading 3", 11, "text_primary")):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        st.font.name = "Segoe UI"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = _rgb(doc.t[color])


def _muted(doc: Document, text: str, size: int = 9, italic: bool = False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.italic = italic
    run.font.color.rgb = _rgb(doc.t["muted"])
    return p


def _table(doc: Document, headers: List[str], rows: List[List[str]],
           widths: Optional[List[float]] = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(9)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(val))
            run.font.size = Pt(9)
    if widths:
        for i, w in enumerate(widths):
            for row in table.rows:
                row.cells[i].width = Inches(w)
    return table


def _exhibit(doc: Document, png: Optional[bytes], title: str, caption: str = ""):
    if png is None:
        return
    doc.add_heading(title, level=3)
    doc.add_picture(io.BytesIO(png), width=Inches(6.4))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if caption:
        _muted(doc, caption, size=8, italic=True)


def _draw_cover(doc: Document, c: SectionContent) -> None:
    if getattr(doc, "logo", None) is not None:
        doc.add_picture(io.BytesIO(doc.logo.data), height=Inches(0.5))
    _muted(doc, "PERFORMANCE REVIEW", size=10)
    doc.add_heading(c.title, level=0)
    _muted(doc, c.intro, size=11)
    for block in c.blocks:
        _muted(doc, block.title, size=9)
        # Styled text, not a heading. This document's whole reason for using
        # real Word styles is a working navigation pane, and "$12.0M" is not a
        # section anyone wants to navigate to.
        run = doc.add_paragraph().add_run(block.lines[0])
        run.bold = True
        run.font.size = Pt(28)
        run.font.color.rgb = _rgb(heading_accent(doc.t))
    for note in c.notes:
        _muted(doc, note)
    doc.add_paragraph()


def _draw_section(doc: Document, c: SectionContent,
                  images: Dict[str, bytes]) -> None:
    """One drawer for all eight.

    Word has no page geometry to fight, so every section is the same shape:
    heading, intro, then whichever of the parts the section filled in. The PDF
    needs a function per section; this does not, and pretending otherwise would
    be eight copies of the same six lines.
    """
    doc.add_heading(c.title, level=1)
    if c.intro:
        doc.add_paragraph(c.intro)

    for b in c.bullets:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(f"{b.lead}. ").bold = True
        p.add_run(b.text)

    for e in c.exhibits:
        _exhibit(doc, images.get(e.id), e.title, e.caption)
        if e.note:
            _muted(doc, e.note, size=8, italic=True)
        for label, text in (("Observation", e.observation),
                            ("Implication", e.implication)):
            if not text:
                continue
            p = doc.add_paragraph()
            p.add_run(f"{label}. ").bold = True
            p.add_run(text)

    for table in c.tables:
        if table.group:
            doc.add_heading(table.group, level=3)
        headers, rows = table.headers, table.rows
        if c.id == "actions":
            # The editable report numbers its actions; the PDF renders them as
            # numbered blocks instead. Same rows, same order.
            headers = ["#"] + headers
            rows = [[str(i)] + row for i, row in enumerate(rows, 1)]
        _table(doc, headers, rows, widths=TABLE_WIDTHS.get(c.id))
        if table.group:
            doc.add_paragraph()

    for block in c.blocks:
        doc.add_heading(block.title, level=min(block.level, 3))
        if block.intro:
            doc.add_paragraph(block.intro)
        for line in block.lines:
            doc.add_paragraph(line, style="List Bullet" if block.level <= 2
                              else None)
        if block.detail:
            doc.add_paragraph(block.detail)
        for line, tint in block.tinted:
            p = doc.add_paragraph()
            p.add_run(line).font.color.rgb = _rgb(doc.t[tint])

    for note in c.notes:
        _muted(doc, note, italic=True)

    if c.is_empty and c.empty:
        doc.add_paragraph(c.empty)


def render_doc(path: Path, profile: CompanyProfile, kpi_set: KPISet,
               results: List[MetricResult], findings: List[Finding],
               images: Dict[str, bytes], specs: List, checks: List[str],
               anomaly_notes: List[str], period: str = "",
               tokens: Optional[Dict[str, str]] = None,
               section_order: Optional[List[str]] = None,
               logo=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.logo = logo
    # The token set rides on the document, matching `pdf.t` and `self.t` in the
    # other two renderers. Threading it through `_muted`'s call sites would say
    # the same thing many more times.
    doc.t = dict(tokens or TOKENS["light"])
    _style(doc)

    ctx = SectionContext(
        profile=profile, kpi_set=kpi_set, results=results, findings=findings,
        images=images, specs=specs, checks=checks,
        anomaly_notes=anomaly_notes, period=period)

    for content in build_sections(ctx, section_order):
        if content.id == "cover":
            _draw_cover(doc, content)
            continue
        if content.id in PAGE_BREAK_BEFORE:
            doc.add_page_break()
        _draw_section(doc, content, images)

    doc.save(str(path))
    return path
