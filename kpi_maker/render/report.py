"""Executive PDF report.

Structure is fixed across all sectors (ARCHITECTURE §4.3):

    1 Executive summary   — "so what" in five bullets, each carrying a number
    2 Scorecard           — North Star + KPIs, RAG vs target and vs cohort
    3 Diagnostic          — where performance came from
    4 Deep dives          — exhibit -> observation -> implication
    5 Benchmarks          — position against the peer cohort
    6 Risks & watch-list
    7 Recommended actions — prioritised by impact x effort, each with an owner
    8 Appendix            — definitions, assumptions, provenance, methodology

Built on fpdf2: pure Python, no GTK, no browser, no system libraries. That
matters for a tool meant to run on a laptop rather than a build server.
"""
from __future__ import annotations

from pathlib import Path
import io
from typing import Dict, List, Optional, Tuple

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from ..insight.detectors import Finding
from ..kpi.schema import KPISet
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
from .sections import (DIAGNOSTIC_EXHIBITS, SectionContent, SectionContext,  # noqa: F401
                       build as build_sections)
from ..design.palette import heading_accent
from ..viz.theme import TOKENS

FONT_CANDIDATES = [
    ("Segoe UI", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf",
     "C:/Windows/Fonts/segoeuii.ttf"),
    ("Arial", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
     "C:/Windows/Fonts/ariali.ttf"),
    ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
]

def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class PDFReport(FPDF):
    def __init__(self, profile: CompanyProfile,
                 tokens: Optional[Dict[str, str]] = None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.profile = profile
        # Every drawing call reads `self.t`. It used to read a module constant
        # bound to the light palette at import, which meant no per-run brand
        # could ever reach the page. Defaulting to that same palette is what
        # keeps an unbranded report byte-identical.
        self.t = dict(tokens or TOKENS["light"])
        self.logo = None
        self.images: Dict[str, bytes] = {}
        self.unicode = False
        self.family = "helvetica"
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(auto=True, margin=20)
        self._load_font()
        self.cover_done = False

    # -- setup ------------------------------------------------------------
    def _load_font(self) -> None:
        """A Unicode TTF, or fall back to a core font and transliterate.

        Company names, currency symbols and typographic dashes all live outside
        latin-1, so the core fonts are a fallback rather than a default.

        Every style this class uses must be registered, even when the system is
        missing that face. Debian's `fonts-dejavu-core` is the common case: it
        ships Regular and Bold but no Oblique, and fpdf2 raises "Undefined font"
        rather than substituting. Aliasing the missing style onto a face that
        does exist costs the slant on a few captions; not aliasing it costs the
        whole report.
        """
        for name, regular, bold, italic in FONT_CANDIDATES:
            if not Path(regular).exists():
                continue
            try:
                self.add_font(name, "", regular)
                self.add_font(name, "B", bold if Path(bold).exists() else regular)
                self.add_font(name, "I", italic if Path(italic).exists() else regular)
                self.family = name
                self.unicode = True
                return
            except Exception:                          # noqa: BLE001
                continue

    def clean(self, text: Optional[str]) -> str:
        text = "" if text is None else str(text)
        if self.unicode:
            return text
        replacements = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"',
                        "”": '"', "…": "...", "×": "x", "≥": ">=", "≤": "<=",
                        "€": "EUR ", "£": "GBP ", "₺": "TRY ", "•": "-", "→": "->"}
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        return text.encode("latin-1", "replace").decode("latin-1")

    # -- chrome -----------------------------------------------------------
    def header(self) -> None:
        if not self.cover_done or self.page_no() == 1:
            return
        self.set_font(self.family, "", 7.5)
        self.set_text_color(*_rgb(self.t["muted"]))
        self.set_y(10)
        # Explicit half-widths. Two zero-width cells would leave x parked at the
        # right margin, and any multi_cell that resumes after an auto page break
        # then has no room to render — fpdf2 raises rather than recovering.
        half = (self.w - 36) / 2
        self.cell(half, 4, self.clean(self.profile.identity.name), align="L")
        self.cell(half, 4, self.clean("Performance review"), align="R")
        self.set_draw_color(*_rgb(self.t["grid"]))
        self.set_line_width(0.2)
        self.line(18, 15.5, self.w - 18, 15.5)
        self.set_y(22)

    def footer(self) -> None:
        if not self.cover_done:
            return
        self.set_y(-14)
        self.set_font(self.family, "", 7.5)
        self.set_text_color(*_rgb(self.t["muted"]))
        half = (self.w - 36) / 2
        self.cell(half, 4, self.clean(
            "Benchmarks are illustrative — see appendix"), align="L")
        self.cell(half, 4, f"{self.page_no()}", align="R")

    # -- typography -------------------------------------------------------
    def _flow(self) -> None:
        """Return the cursor to the left margin before flowing text."""
        self.set_x(self.l_margin)

    def multi_cell(self, w=0, h=None, text="", **kwargs):  # type: ignore[override]
        """Flow text back to the left margin by default.

        fpdf2's multi_cell leaves x at the RIGHT edge of the cell. A following
        `multi_cell(0, ...)` therefore computes zero available width and fpdf2
        raises "Not enough horizontal space" rather than wrapping. Since every
        call in this module wants normal top-to-bottom text flow, fixing the
        default here is safer than remembering the keyword at 30 call sites.
        """
        kwargs.setdefault("new_x", XPos.LMARGIN)
        kwargs.setdefault("new_y", YPos.NEXT)
        if not w:
            self.set_x(self.l_margin)
        return super().multi_cell(w, h, text, **kwargs)

    def h1(self, text: str) -> None:
        self._flow()
        self.ln(3)
        self.set_font(self.family, "B", 16)
        self.set_text_color(*_rgb(self.t["text_primary"]))
        self.multi_cell(0, 7, self.clean(text))
        self.ln(1.5)

    def h2(self, text: str) -> None:
        self._flow()
        self.ln(2.5)
        self.set_font(self.family, "B", 11.5)
        self.set_text_color(*_rgb(self.t["text_primary"]))
        self.multi_cell(0, 5.5, self.clean(text))
        self.ln(0.8)

    def body(self, text: str, size: float = 9.5, color: str = "text_secondary") -> None:
        self._flow()
        self.set_font(self.family, "", size)
        self.set_text_color(*_rgb(self.t[color]))
        self.multi_cell(0, 4.6, self.clean(text))
        self.ln(1.2)

    def bullet(self, text: str, marker: str = "•") -> None:
        self._flow()
        self.set_font(self.family, "", 9.5)
        self.set_text_color(*_rgb(self.t["text_secondary"]))
        x = self.get_x()
        self.cell(4.5, 4.6, self.clean(marker))
        self.set_x(x + 4.5)
        # Explicit width: a zero width would trip the override's left-margin
        # reset above and flatten the hanging indent.
        self.multi_cell(self.w - self.r_margin - (x + 4.5), 4.6, self.clean(text))
        self.ln(0.8)

    def note(self, text: str) -> None:
        self._flow()
        self.set_font(self.family, "I", 8.5)
        self.set_text_color(*_rgb(self.t["muted"]))
        self.multi_cell(0, 4, self.clean(text))
        self.ln(1)

    def rule(self) -> None:
        self.set_draw_color(*_rgb(self.t["grid"]))
        self.set_line_width(0.2)
        y = self.get_y()
        self.line(18, y, self.w - 18, y)
        self.ln(2)

    # -- components -------------------------------------------------------
    def table(self, headers: List[str], rows: List[List[str]], widths: List[float],
              aligns: Optional[List[str]] = None,
              row_colors: Optional[List[Optional[str]]] = None) -> None:
        aligns = aligns or ["L"] * len(headers)
        self.set_font(self.family, "B", 7.5)
        self.set_text_color(*_rgb(self.t["muted"]))
        self.set_fill_color(*_rgb(self.t["page"]))
        for h, w, a in zip(headers, widths, aligns):
            self.cell(w, 6, self.clean(h.upper()), align=a, fill=True)
        self.ln()

        self.set_font(self.family, "", 8.5)
        for i, row in enumerate(rows):
            if self.get_y() > self.h - 28:
                self.add_page()
                self.set_font(self.family, "B", 7.5)
                self.set_text_color(*_rgb(self.t["muted"]))
                self.set_fill_color(*_rgb(self.t["page"]))
                for h, w, a in zip(headers, widths, aligns):
                    self.cell(w, 6, self.clean(h.upper()), align=a, fill=True)
                self.ln()
                self.set_font(self.family, "", 8.5)

            tint = row_colors[i] if row_colors else None
            for j, (val, w, a) in enumerate(zip(row, widths, aligns)):
                if tint and j == len(row) - 1:
                    self.set_text_color(*_rgb(self.t[tint]))
                else:
                    self.set_text_color(*_rgb(self.t["text_primary"] if j == 0
                                              else self.t["text_secondary"]))
                self.cell(w, 5.6, self.clean(str(val)), align=a)
            self.ln()
            self.set_draw_color(*_rgb(self.t["grid"]))
            self.set_line_width(0.1)
            y = self.get_y()
            self.line(18, y, 18 + sum(widths), y)
        self.ln(2)

    def exhibit(self, png: Optional[bytes], title: str, subtitle: str = "",
                note: str = "") -> None:
        if png is None:
            return
        needed = 76 + (10 if note else 0)
        if self.get_y() + needed > self.h - 24:
            self.add_page()
        self.set_font(self.family, "B", 10)
        self.set_text_color(*_rgb(self.t["text_primary"]))
        self.multi_cell(0, 5, self.clean(title))
        if subtitle:
            self.set_font(self.family, "", 8.5)
            self.set_text_color(*_rgb(self.t["muted"]))
            self.multi_cell(0, 4, self.clean(subtitle))
        self.ln(1)
        self.image(io.BytesIO(png), w=self.w - 36)
        self.ln(1.5)
        if note:
            self.note(note)


# --------------------------------------------------------------------------
# Drawing. What goes in each section is decided in `sections.py`; everything
# below is how fpdf puts it on a page.
# --------------------------------------------------------------------------

# Column widths are a property of an A4 page at these margins, not of the
# content, so they stay here rather than in the registry.
WIDTHS = {
    "scorecard": [58, 24, 24, 24, 26, 18],
    "benchmarks": [52, 22, 22, 22, 22, 34],
}


def _draw_narrative(pdf: PDFReport, c: SectionContent) -> None:
    """The AI paragraph, in ordinary body style.

    Deliberately not boxed, tinted or badged. Every figure in it was checked
    against the same facts table the scorecard is built from, so setting it
    apart would imply a lower standard of evidence than the sentence beside it
    — and a disclaimer box in the middle of a board report reads as a defect.
    Where it came from is disclosed in the methodology appendix, alongside the
    measured-versus-modelled note, which is where a reader goes to ask that
    kind of question.
    """
    for paragraph in c.narrative:
        pdf.body(paragraph)
    if c.narrative:
        pdf.ln(1)


def _draw_cover(pdf: PDFReport, c: SectionContent) -> None:
    pdf.add_page()
    if pdf.logo is not None:
        # Above the block, not beside it: a logo of unknown aspect ratio
        # alongside the title would collide with a wide one. Height is fixed
        # and the width follows, so any shape behaves.
        pdf.image(io.BytesIO(pdf.logo.data), x=18, y=26, h=14)
    pdf.set_y(58)
    pdf.set_font(pdf.family, "", 9)
    pdf.set_text_color(*_rgb(pdf.t["muted"]))
    pdf.cell(0, 5, pdf.clean("PERFORMANCE REVIEW"))
    pdf.ln(11)

    pdf.set_font(pdf.family, "B", 27)
    pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
    pdf.multi_cell(0, 11, pdf.clean(c.title))
    pdf.ln(3)

    pdf.set_font(pdf.family, "", 11)
    pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
    pdf.multi_cell(0, 5.6, pdf.clean(c.intro))
    pdf.ln(14)

    for block in c.blocks:
        pdf.set_font(pdf.family, "", 9)
        pdf.set_text_color(*_rgb(pdf.t["muted"]))
        pdf.cell(0, 5, pdf.clean(block.title))
        pdf.ln(8)
        pdf.set_font(pdf.family, "B", 34)
        pdf.set_text_color(*_rgb(heading_accent(pdf.t)))
        pdf.cell(0, 14, pdf.clean(block.lines[0]))
        pdf.ln(20)

    pdf.set_y(pdf.h - 56)
    pdf.rule()
    pdf.set_font(pdf.family, "", 8)
    pdf.set_text_color(*_rgb(pdf.t["muted"]))
    pdf.multi_cell(0, 4.2, pdf.clean("\n".join(c.notes)))
    pdf.cover_done = True


def _draw_bullets(pdf: PDFReport, c: SectionContent) -> None:
    """Exec summary and risks: a bold lead-in, then the sentence."""
    pdf.add_page()
    pdf.h1(c.title)
    if c.intro:
        pdf.body(c.intro)
        pdf.ln(1)
    _draw_narrative(pdf, c)
    if not c.bullets:
        if c.empty:
            pdf.body(c.empty)
        return
    for b in c.bullets:
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t[b.tint or "text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(b.lead))
        if b.tint:
            # Risks put the statement in the standard body style; the exec
            # summary sets its own, slightly tighter, leading.
            pdf.body(b.text)
            pdf.ln(1)
        else:
            pdf.set_font(pdf.family, "", 9.5)
            pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
            pdf.multi_cell(0, 4.6, pdf.clean(b.text))
            pdf.ln(2.5)


def _draw_scorecard(pdf: PDFReport, c: SectionContent) -> None:
    pdf.add_page()
    pdf.h1(c.title)
    pdf.body(c.intro)
    _draw_narrative(pdf, c)
    for table in c.tables:
        if table.group:
            pdf.h2(table.group)
        pdf.table(table.headers, table.rows, WIDTHS["scorecard"], table.aligns,
                  table.row_tints or None)


def _draw_exhibits(pdf: PDFReport, c: SectionContent, gap: float = 0.0) -> None:
    """Picture, then what it says and what to do about it.

    `gap` is the space after each block. The deep dives are a run of
    exhibit-observation-implication units and need separating; the diagnostic
    is two named exhibits and does not.
    """
    pdf.add_page()
    pdf.h1(c.title)
    if c.intro:
        pdf.body(c.intro)
    _draw_narrative(pdf, c)
    for e in c.exhibits:
        pdf.exhibit(pdf.images.get(e.id), e.title, e.caption, e.note)
        for label, text in (("Observation", e.observation),
                            ("Implication", e.implication)):
            if not text:
                continue
            pdf.set_font(pdf.family, "B", 9)
            pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
            pdf.multi_cell(0, 4.5, pdf.clean(label))
            pdf.body(text)
        if gap:
            pdf.ln(gap)


def _draw_benchmarks(pdf: PDFReport, c: SectionContent) -> None:
    pdf.add_page()
    pdf.h1(c.title)
    pdf.body(c.intro)
    _draw_narrative(pdf, c)
    for e in c.exhibits:
        pdf.exhibit(pdf.images.get(e.id), e.title, e.caption, e.note)
    for table in c.tables:
        pdf.table(table.headers, table.rows, WIDTHS["benchmarks"], table.aligns)
    for note in c.notes:
        pdf.note(note)


def _draw_actions(pdf: PDFReport, c: SectionContent) -> None:
    pdf.add_page()
    pdf.h1(c.title)
    pdf.body(c.intro)
    _draw_narrative(pdf, c)
    if not c.tables:
        pdf.body(c.empty)
        return
    # Recommendations are long; render as blocks rather than a cramped table.
    for i, row in enumerate(c.tables[0].rows, 1):
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(f"{i}. {row[0]}"))
        pdf.set_font(pdf.family, "", 8.5)
        pdf.set_text_color(*_rgb(pdf.t["muted"]))
        pdf.multi_cell(0, 4.2, pdf.clean(
            f"Moves: {row[1]}   \u00b7   Owner: {row[2]}   \u00b7   "
            f"Impact: {row[3]}   \u00b7   Effort: {row[4]}"))
        pdf.ln(2)


def _draw_appendix(pdf: PDFReport, c: SectionContent) -> None:
    pdf.add_page()
    pdf.h1(c.title)
    for block in c.blocks:
        if block.page_break_before:
            pdf.add_page()
        if block.level <= 2:
            if block.title:
                pdf.h2(block.title)
            if block.intro:
                pdf.body(block.intro)
            for line in block.lines:
                pdf.bullet(line, marker=block.marker)
            # `tinted` used to be read only by the KPI-definition branch below,
            # so a section-level block that set it lost the text with no error —
            # the DOCX printed it and the PDF did not. A field the registry
            # offers has to mean the same thing in every renderer.
            for line, tint in block.tinted:
                pdf.set_text_color(*_rgb(pdf.t[tint]))
                pdf.multi_cell(0, 4.6, pdf.clean(line))
                pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
            if block.tinted:
                pdf.ln(2)
            continue

        # A KPI definition. Keep the whole entry on one page where it fits.
        if pdf.get_y() > pdf.h - 45:
            pdf.add_page()
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(block.title))
        pdf.set_font(pdf.family, "", 8.5)
        pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
        for line in block.lines:
            pdf.multi_cell(0, 4.2, pdf.clean(line))
        for line, tint in block.tinted:
            pdf.set_text_color(*_rgb(pdf.t[tint]))
            pdf.multi_cell(0, 4.2, pdf.clean(line))
        pdf.ln(2)


DRAW = {
    "cover": _draw_cover,
    "exec_summary": _draw_bullets,
    "scorecard": _draw_scorecard,
    "diagnostic": _draw_exhibits,
    "deep_dives": lambda pdf, c: _draw_exhibits(pdf, c, gap=2.0),
    "benchmarks": _draw_benchmarks,
    "risks": _draw_bullets,
    "actions": _draw_actions,
    "appendix": _draw_appendix,
}


def render_report(path: Path, profile: CompanyProfile, kpi_set: KPISet,
                  results: List[MetricResult], findings: List[Finding],
                  images: Dict[str, bytes], specs: List, checks: List[str],
                  anomaly_notes: List[str], period: str = "",
                  tokens: Optional[Dict[str, str]] = None,
                  section_order: Optional[List[str]] = None,
                  logo=None, origins: Optional[Dict[str, str]] = None,
                  narrative: Optional[Dict[str, List[str]]] = None,
                  ai_notes: Optional[List[str]] = None,
                  caveats: Optional[List[str]] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = PDFReport(profile, tokens)
    pdf.images = images
    pdf.logo = logo

    ctx = SectionContext(
        profile=profile, kpi_set=kpi_set, results=results, findings=findings,
        images=images, specs=specs, checks=checks,
        anomaly_notes=anomaly_notes, period=period, origins=origins or {},
        narrated=sorted(narrative or {}), ai_notes=list(ai_notes or []),
        caveats=list(caveats or []))
    for content in build_sections(ctx, section_order, narrative=narrative):
        DRAW[content.id](pdf, content)

    pdf.output(str(path))
    return path
