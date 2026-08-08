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
from typing import Dict, List, Optional, Tuple

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from ..fmt import fmt_value, is_missing
from ..insight.detectors import Finding
from ..kpi.schema import KPISet, Perspective
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
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

STATUS_WORD = {"green": "On track", "amber": "Watch", "red": "Off track",
               "unscored": "No target", "unknown": "No data"}
SEVERITY_WORD = {"critical": "Critical", "high": "High", "medium": "Medium",
                 "low": "Low", "positive": "Strength"}


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
        import io
        self.image(io.BytesIO(png), w=self.w - 36)
        self.ln(1.5)
        if note:
            self.note(note)


# --------------------------------------------------------------------------

def _top_findings(findings: List[Finding], exclude_positive: bool = False,
                  limit: int = 5) -> List[Finding]:
    pool = [f for f in findings if not (exclude_positive and f.severity == "positive")]
    return pool[:limit]


def _cover(pdf: PDFReport, profile: CompanyProfile, results: List[MetricResult],
           kpi_set: KPISet, period: str) -> None:
    pdf.add_page()
    pdf.set_y(58)
    pdf.set_font(pdf.family, "", 9)
    pdf.set_text_color(*_rgb(pdf.t["muted"]))
    pdf.cell(0, 5, pdf.clean("PERFORMANCE REVIEW"))
    pdf.ln(11)

    pdf.set_font(pdf.family, "B", 27)
    pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
    pdf.multi_cell(0, 11, pdf.clean(profile.identity.name))
    pdf.ln(3)

    pdf.set_font(pdf.family, "", 11)
    pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
    pdf.multi_cell(0, 5.6, pdf.clean(
        f"{profile.business_model.type.value.upper()} · "
        f"{profile.business_model.customer_type.value} · "
        f"{profile.identity.country} · {period}"
    ))
    pdf.ln(14)

    north = next((r for r in results if r.kpi.id == kpi_set.north_star and r.computed), None)
    if north:
        pdf.set_font(pdf.family, "", 9)
        pdf.set_text_color(*_rgb(pdf.t["muted"]))
        pdf.cell(0, 5, pdf.clean(north.kpi.name.upper()))
        pdf.ln(8)
        pdf.set_font(pdf.family, "B", 34)
        pdf.set_text_color(*_rgb(heading_accent(pdf.t)))
        pdf.cell(0, 14, pdf.clean(
            fmt_value(north.current, north.kpi.unit, profile.identity.currency)))
        pdf.ln(20)

    pdf.set_y(pdf.h - 56)
    pdf.rule()
    pdf.set_font(pdf.family, "", 8)
    pdf.set_text_color(*_rgb(pdf.t["muted"]))
    pdf.multi_cell(0, 4.2, pdf.clean(
        f"Prepared for the {profile.intent.audience.value} · "
        f"primary objective: {profile.intent.primary_objective.value.replace('_', ' ')} · "
        f"horizon {profile.intent.horizon_months} months\n"
        f"Profile confidence {profile.confidence:.0%} · "
        f"{len(kpi_set.kpis)} KPIs selected from the {profile.business_model.type.value} library\n"
        f"Benchmarks in this report are illustrative placeholders, not a licensed "
        f"dataset. See the appendix before acting on any peer comparison."
    ))
    pdf.cover_done = True


def _exec_summary(pdf: PDFReport, findings: List[Finding], results: List[MetricResult],
                  profile: CompanyProfile) -> None:
    pdf.add_page()
    pdf.h1("1. Executive summary")
    pdf.body(
        "Every statement below is computed directly from the underlying data. "
        "Figures in this section reconcile to the scorecard that follows."
    )
    pdf.ln(1)
    for f in _top_findings(findings, limit=5):
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(f"{SEVERITY_WORD.get(f.severity, '')} · {f.title}"))
        pdf.set_font(pdf.family, "", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
        pdf.multi_cell(0, 4.6, pdf.clean(f.statement))
        pdf.ln(2.5)


def _scorecard(pdf: PDFReport, results: List[MetricResult], kpi_set: KPISet,
               profile: CompanyProfile) -> None:
    pdf.add_page()
    pdf.h1("2. Scorecard")
    pdf.body(
        f"Grouped by Balanced Scorecard perspective. Coverage is constrained to "
        f"between two and seven KPIs per perspective, so no single view of the "
        f"business can dominate. Leading indicators are "
        f"{kpi_set.leading_share:.0%} of the set."
    )
    cur = profile.identity.currency
    widths = [58, 24, 24, 24, 26, 18]
    for perspective in Perspective:
        group = [r for r in results if r.kpi.perspective == perspective and r.computed]
        if not group:
            continue
        pdf.h2(perspective.value.replace("_", " ").title())
        rows, colors = [], []
        for r in sorted(group, key=lambda x: int(x.kpi.tier)):
            k = r.kpi
            rows.append([
                k.short_name or k.name,
                fmt_value(r.current, k.unit, cur),
                fmt_value(r.prior_year, k.unit, cur),
                fmt_value(r.target, k.unit, cur),
                fmt_value(k.benchmark.p50, k.unit, cur) if k.benchmark else "—",
                STATUS_WORD.get(r.status, "—"),
            ])
            colors.append({"green": "good", "amber": "serious",
                           "red": "critical"}.get(r.status, "muted"))
        pdf.table(["KPI", "Current", "12mo ago", "Target", "Cohort med.", "Status"],
                  rows, widths, ["L", "R", "R", "R", "R", "L"], colors)


def _diagnostic(pdf: PDFReport, images: Dict[str, bytes], findings: List[Finding],
                specs_by_id: Dict[str, object]) -> None:
    pdf.add_page()
    pdf.h1("3. Diagnostic")
    pdf.body(
        "Where the period's performance actually came from. The bridge decomposes "
        "the movement in the North Star; the cohort view shows how much of it "
        "persists."
    )
    bridge = next((f for f in findings if f.id == "arr_bridge"), None)
    pdf.exhibit(images.get("arr_bridge"), "ARR bridge, last 12 months",
                "Blue adds, red subtracts",
                bridge.statement if bridge else "")
    pdf.exhibit(images.get("cohort_heatmap"),
                "Revenue retention by acquisition cohort",
                "Percentage of each cohort's initial ARR still held",
                "Rows below 100% at month 12 are cohorts where churn outran expansion.")


def _deep_dives(pdf: PDFReport, images: Dict[str, bytes], specs: List,
                findings: List[Finding]) -> None:
    """Exhibit -> observation -> implication, one block per chart."""
    pdf.add_page()
    pdf.h1("4. Deep dives")

    shown = {"arr_bridge", "cohort_heatmap"}
    by_kpi: Dict[str, List[Finding]] = {}
    for f in findings:
        for kid in f.kpi_ids:
            by_kpi.setdefault(kid, []).append(f)

    for spec in specs:
        if spec.id in shown or spec.id not in images:
            continue
        related = []
        for f in findings:
            if f.id.endswith(spec.id) or spec.id in f.id:
                related.append(f)
        if not related:
            for kid, fs in by_kpi.items():
                if kid in spec.id or spec.id in kid:
                    related.extend(fs)
        observation = related[0].statement if related else ""
        implication = related[0].recommendation if related and related[0].recommendation else ""
        pdf.exhibit(images[spec.id], spec.title, spec.subtitle, spec.note)
        if observation:
            pdf.set_font(pdf.family, "B", 9)
            pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
            pdf.multi_cell(0, 4.5, pdf.clean("Observation"))
            pdf.body(observation)
        if implication:
            pdf.set_font(pdf.family, "B", 9)
            pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
            pdf.multi_cell(0, 4.5, pdf.clean("Implication"))
            pdf.body(implication)
        pdf.ln(2)


def _benchmarks(pdf: PDFReport, images: Dict[str, bytes],
                results: List[MetricResult], profile: CompanyProfile) -> None:
    pdf.add_page()
    pdf.h1("5. Benchmarks")
    pdf.body(
        "Position against the peer cohort median. Direction is normalised so that "
        "positive always means better, whichever way the underlying metric runs."
    )
    pdf.exhibit(images.get("benchmark_position"),
                "Position against the peer cohort median", "")
    cur = profile.identity.currency
    rows = []
    for r in results:
        if not r.computed or r.kpi.benchmark is None:
            continue
        b = r.kpi.benchmark
        rows.append([
            r.kpi.short_name or r.kpi.name,
            fmt_value(r.current, r.kpi.unit, cur),
            fmt_value(b.p25, r.kpi.unit, cur),
            fmt_value(b.p50, r.kpi.unit, cur),
            fmt_value(b.p75, r.kpi.unit, cur),
            (r.benchmark_position or "—").replace("_", " "),
        ])
    if rows:
        pdf.table(["KPI", "You", "P25", "P50", "P75", "Position"],
                  rows, [52, 22, 22, 22, 22, 34], ["L", "R", "R", "R", "R", "L"])
    pdf.note(
        "Peer figures are illustrative placeholders assembled from public "
        "commentary, not a licensed benchmark dataset. They are suitable for "
        "calibration, not for external reporting."
    )


def _risks(pdf: PDFReport, findings: List[Finding]) -> None:
    pdf.add_page()
    pdf.h1("6. Risks and watch-list")
    risks = [f for f in findings if f.severity in ("critical", "high")]
    if not risks:
        pdf.body("No critical or high-severity issues were detected this period.")
        return
    for f in risks:
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["critical"] if f.severity == "critical"
                                 else pdf.t["serious"]))
        pdf.multi_cell(0, 4.8, pdf.clean(f"{SEVERITY_WORD[f.severity]} · {f.title}"))
        pdf.body(f.statement)
        pdf.ln(1)


def _actions(pdf: PDFReport, findings: List[Finding],
             results: List[MetricResult]) -> None:
    pdf.add_page()
    pdf.h1("7. Recommended actions")
    pdf.body(
        "Prioritised by impact against effort. Each action is tied to the KPI it "
        "is meant to move and to the role that owns that KPI."
    )
    owner_by_kpi = {r.kpi.id: r.kpi.owner_role for r in results}
    order = {"high": 0, "medium": 1, "low": 2, None: 3}
    actionable = sorted(
        [f for f in findings if f.recommendation],
        key=lambda f: (order.get(f.impact, 3), order.get(f.effort, 3)),
    )
    if not actionable:
        pdf.body("No actions were generated — no finding crossed an action threshold.")
        return
    rows = []
    for f in actionable[:12]:
        owner = next((owner_by_kpi.get(k) for k in f.kpi_ids if owner_by_kpi.get(k)), "—")
        rows.append([
            f.recommendation,
            ", ".join(f.kpi_ids[:2]) or "—",
            owner,
            (f.impact or "—").title(),
            (f.effort or "—").title(),
        ])
    # Recommendations are long; render as blocks rather than a cramped table.
    for i, row in enumerate(rows, 1):
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(f"{i}. {row[0]}"))
        pdf.set_font(pdf.family, "", 8.5)
        pdf.set_text_color(*_rgb(pdf.t["muted"]))
        pdf.multi_cell(0, 4.2, pdf.clean(
            f"Moves: {row[1]}   ·   Owner: {row[2]}   ·   "
            f"Impact: {row[3]}   ·   Effort: {row[4]}"))
        pdf.ln(2)


def _appendix(pdf: PDFReport, results: List[MetricResult], kpi_set: KPISet,
              profile: CompanyProfile, checks: List[str],
              anomaly_notes: List[str]) -> None:
    pdf.add_page()
    pdf.h1("8. Appendix")

    pdf.h2("Methodology")
    pdf.body(
        "KPIs were selected by evaluating each metric's applicability expression "
        "against this company profile, then scoring on objective alignment, "
        "Balanced Scorecard coverage, leading/lagging balance and audience fit. "
        "Redundant metric pairs were suppressed. Every figure in this report is "
        "computed by deterministic code from the underlying fact tables; no "
        "number in this document was produced by a language model."
    )

    pdf.h2("Assumptions and provenance")
    defaulted = [(p, s) for p, s in sorted(profile.provenance.items())
                 if s.startswith("benchmark_default")]
    if defaulted:
        for path, src in defaulted:
            pdf.bullet(f"{path} — {src}")
    else:
        pdf.body("No profile fields were filled from benchmark defaults.")

    pdf.h2("Data quality")
    pdf.body(f"{len(checks)} reconciliation assertions passed before this report "
             f"was produced. The pipeline refuses to render on data that fails them.")
    for c in checks:
        pdf.bullet(c, marker="-")

    if anomaly_notes:
        pdf.h2("Known events in this dataset")
        for n in anomaly_notes:
            pdf.bullet(n)

    pdf.add_page()
    pdf.h2("KPI definitions")
    for r in sorted(results, key=lambda x: (x.kpi.perspective.value, int(x.kpi.tier))):
        k = r.kpi
        if pdf.get_y() > pdf.h - 45:
            pdf.add_page()
        pdf.set_font(pdf.family, "B", 9.5)
        pdf.set_text_color(*_rgb(pdf.t["text_primary"]))
        pdf.multi_cell(0, 4.8, pdf.clean(f"{k.name}  ({k.perspective.value}, {k.timing.value})"))
        pdf.set_font(pdf.family, "", 8.5)
        pdf.set_text_color(*_rgb(pdf.t["text_secondary"]))
        pdf.multi_cell(0, 4.2, pdf.clean(f"Formula: {k.formula}"))
        pdf.multi_cell(0, 4.2, pdf.clean(
            f"Owner: {k.owner_role}   ·   Sources: {', '.join(k.source_systems) or 'n/a'}"
            f"   ·   Frequency: {k.frequency}"))
        if k.pitfalls:
            pdf.set_text_color(*_rgb(pdf.t["serious"]))
            pdf.multi_cell(0, 4.2, pdf.clean(f"Pitfall: {k.pitfalls}"))
        if k.benchmark:
            pdf.set_text_color(*_rgb(pdf.t["muted"]))
            pdf.multi_cell(0, 4.2, pdf.clean(f"Benchmark source: {k.benchmark.source}"))
        pdf.ln(2)

    if kpi_set.dropped:
        pdf.h2("KPIs considered but not selected")
        for kid, reason in sorted(kpi_set.dropped.items()):
            pdf.bullet(f"{kid} — {reason}", marker="-")


def render_report(path: Path, profile: CompanyProfile, kpi_set: KPISet,
                  results: List[MetricResult], findings: List[Finding],
                  images: Dict[str, bytes], specs: List, checks: List[str],
                  anomaly_notes: List[str], period: str = "",
                  tokens: Optional[Dict[str, str]] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = PDFReport(profile, tokens)
    specs_by_id = {s.id: s for s in specs}

    _cover(pdf, profile, results, kpi_set, period)
    _exec_summary(pdf, findings, results, profile)
    _scorecard(pdf, results, kpi_set, profile)
    _diagnostic(pdf, images, findings, specs_by_id)
    _deep_dives(pdf, images, specs, findings)
    _benchmarks(pdf, images, results, profile)
    _risks(pdf, findings)
    _actions(pdf, findings, results)
    _appendix(pdf, results, kpi_set, profile, checks, anomaly_notes)

    pdf.output(str(path))
    return path
