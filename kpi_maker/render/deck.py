"""Board deck (PPTX).

The one rule that makes a deck read like a consulting deliverable: **the slide
title is the message, not the topic.** "Churn is concentrated in SMB" — never
"Churn analysis". Every exhibit slide here takes its headline from the finding
attached to that chart, so the titles say something by construction.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Optional

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from ..fmt import fmt_value
from ..insight.detectors import Finding
from ..kpi.schema import KPISet
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile
from ..viz.theme import TOKENS

FONT = "Segoe UI"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.6)


def _rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class Deck:
    def __init__(self, profile: CompanyProfile,
                 tokens: Optional[Dict[str, str]] = None):
        self.t = dict(tokens or TOKENS["light"])
        self.prs = Presentation()
        self.prs.slide_width = SLIDE_W
        self.prs.slide_height = SLIDE_H
        self.profile = profile
        self.blank = self.prs.slide_layouts[6]

    def _slide(self):
        return self.prs.slides.add_slide(self.blank)

    def _text(self, slide, text, left, top, width, height, size=14, bold=False,
              color="text_primary", align=PP_ALIGN.LEFT, wrap=True):
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = wrap
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = FONT
        run.font.color.rgb = _rgb(self.t[color])
        return box

    def _headline(self, slide, message: str, kicker: str = ""):
        """The message, not the topic."""
        if kicker:
            self._text(slide, kicker.upper(), MARGIN, Inches(0.42),
                       SLIDE_W - 2 * MARGIN, Inches(0.28), size=10, color="muted")
        self._text(slide, message, MARGIN, Inches(0.72),
                   SLIDE_W - 2 * MARGIN, Inches(0.9), size=23, bold=True)
        line = slide.shapes.add_shape(1, MARGIN, Inches(1.62),
                                      SLIDE_W - 2 * MARGIN, Emu(9525))
        line.fill.solid()
        line.fill.fore_color.rgb = _rgb(self.t["grid"])
        line.line.fill.background()
        line.shadow.inherit = False

    def _footer(self, slide, text: str):
        self._text(slide, text, MARGIN, SLIDE_H - Inches(0.52),
                   SLIDE_W - 2 * MARGIN, Inches(0.3), size=9, color="muted")

    # -- slide types ------------------------------------------------------
    def title_slide(self, results: List[MetricResult], kpi_set: KPISet, period: str):
        s = self._slide()
        p = self.profile
        self._text(s, "PERFORMANCE REVIEW", MARGIN, Inches(2.3),
                   Inches(8), Inches(0.3), size=11, color="muted")
        self._text(s, p.identity.name, MARGIN, Inches(2.8),
                   Inches(11), Inches(1.0), size=40, bold=True)
        self._text(s, f"{p.business_model.type.value.upper()} · "
                      f"{p.business_model.customer_type.value} · "
                      f"{p.identity.country} · {period}",
                   MARGIN, Inches(3.9), Inches(11), Inches(0.4), size=14,
                   color="text_secondary")
        north = next((r for r in results if r.kpi.id == kpi_set.north_star and r.computed), None)
        if north:
            self._text(s, north.kpi.name.upper(), MARGIN, Inches(4.9),
                       Inches(8), Inches(0.3), size=10, color="muted")
            self._text(s, fmt_value(north.current, north.kpi.unit, p.identity.currency),
                       MARGIN, Inches(5.2), Inches(8), Inches(0.9), size=34,
                       bold=True, color="series_1")
        self._footer(s, f"Prepared for the {p.intent.audience.value} · "
                        f"benchmarks are illustrative — see appendix")

    def bullets_slide(self, message: str, items: List[str], kicker: str = ""):
        s = self._slide()
        self._headline(s, message, kicker)
        top = Inches(2.0)
        box = s.shapes.add_textbox(MARGIN, top, SLIDE_W - 2 * MARGIN,
                                   SLIDE_H - top - Inches(0.8))
        tf = box.text_frame
        tf.word_wrap = True
        for i, item in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = p.add_run()
            run.text = item
            run.font.size = Pt(14)
            run.font.name = FONT
            run.font.color.rgb = _rgb(self.t["text_secondary"])
            p.space_after = Pt(11)
        return s

    def exhibit_slide(self, message: str, png: Optional[bytes], kicker: str = "",
                      caption: str = ""):
        if png is None:
            return None
        s = self._slide()
        self._headline(s, message, kicker)
        img_w = SLIDE_W - 2 * MARGIN
        # Charts export at 900x360 or 900x320; scale to width and centre.
        pic = s.shapes.add_picture(io.BytesIO(png), MARGIN, Inches(2.0), width=img_w)
        max_h = SLIDE_H - Inches(2.0) - Inches(0.9)
        if pic.height > max_h:
            ratio = max_h / pic.height
            pic.height = int(max_h)
            pic.width = int(pic.width * ratio)
            pic.left = int((SLIDE_W - pic.width) / 2)
        if caption:
            self._footer(s, caption)
        return s

    def table_slide(self, message: str, headers: List[str], rows: List[List[str]],
                    kicker: str = "", col_widths: Optional[List[float]] = None):
        s = self._slide()
        self._headline(s, message, kicker)
        rows = rows[:12]
        n_rows, n_cols = len(rows) + 1, len(headers)
        top = Inches(2.0)
        height = min(SLIDE_H - top - Inches(0.7), Inches(0.32) * n_rows)
        shape = s.shapes.add_table(n_rows, n_cols, MARGIN, top,
                                   SLIDE_W - 2 * MARGIN, height)
        table = shape.table
        if col_widths:
            total = SLIDE_W - 2 * MARGIN
            for i, frac in enumerate(col_widths):
                table.columns[i].width = Emu(int(total * frac))

        for c, head in enumerate(headers):
            cell = table.cell(0, c)
            cell.text = head
            para = cell.text_frame.paragraphs[0]
            para.runs[0].font.size = Pt(10)
            para.runs[0].font.bold = True
            para.runs[0].font.name = FONT
            para.runs[0].font.color.rgb = _rgb(self.t["text_primary"])

        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row):
                cell = table.cell(r, c)
                cell.text = str(val)
                para = cell.text_frame.paragraphs[0]
                para.runs[0].font.size = Pt(10)
                para.runs[0].font.name = FONT
                para.runs[0].font.color.rgb = _rgb(
                    self.t["text_primary"] if c == 0 else self.t["text_secondary"])
        return s

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(str(path))
        return path


SEVERITY_WORD = {"critical": "Critical", "high": "High", "medium": "Medium",
                 "low": "Low", "positive": "Strength"}
STATUS_WORD = {"green": "On track", "amber": "Watch", "red": "Off track",
               "unscored": "No target", "unknown": "No data"}


def render_deck(path: Path, profile: CompanyProfile, kpi_set: KPISet,
                results: List[MetricResult], findings: List[Finding],
                images: Dict[str, bytes], specs: List, period: str = "",
                tokens: Optional[Dict[str, str]] = None) -> Path:
    deck = Deck(profile, tokens)
    cur = profile.identity.currency
    computed = [r for r in results if r.computed]

    deck.title_slide(results, kpi_set, period)

    # Executive summary — the five findings, each already carrying its number.
    top = findings[:5]
    deck.bullets_slide(
        "What the numbers say",
        [f"{SEVERITY_WORD.get(f.severity, '')} · {f.title}\n{f.statement}" for f in top],
        kicker="Executive summary",
    )

    # Scorecard
    l1 = [r for r in computed if int(r.kpi.tier) <= 1]
    n_red = len([r for r in computed if r.status == "red"])
    n_amber = len([r for r in computed if r.status == "amber"])
    deck.table_slide(
        f"{n_red} KPI{'' if n_red == 1 else 's'} off track, {n_amber} on watch",
        ["KPI", "Current", "12mo ago", "Target", "Status"],
        [[r.kpi.short_name or r.kpi.name,
          fmt_value(r.current, r.kpi.unit, cur),
          fmt_value(r.prior_year, r.kpi.unit, cur),
          fmt_value(r.target, r.kpi.unit, cur),
          STATUS_WORD.get(r.status, "—")] for r in l1],
        kicker="Scorecard",
        col_widths=[0.36, 0.16, 0.16, 0.16, 0.16],
    )

    # Exhibits — headline comes from the finding attached to the chart.
    finding_for = {}
    for f in findings:
        for kid in f.kpi_ids:
            finding_for.setdefault(kid, f)

    exhibit_plan = [
        ("arr_trend", "arr", "Trajectory"),
        ("arr_bridge", "net_new_arr", "Diagnostic"),
        ("retention", "nrr", "Retention"),
        ("segment_churn", "logo_churn_rate", "Retention"),
        ("cohort_heatmap", "grr", "Retention"),
        ("cac_payback", "cac_payback_months", "Efficiency"),
        ("channel_cost", "blended_cac", "Efficiency"),
        ("indexed_growth", "arr_per_fte", "Leverage"),
        ("benchmark_position", None, "Benchmarks"),
    ]
    # Where no finding is attached, compute a headline rather than falling back
    # to the chart's topic. "Annual Recurring Revenue" tells a board nothing.
    by_id = {r.kpi.id: r for r in computed}
    fallbacks: Dict[str, str] = {}
    north = by_id.get(kpi_set.north_star)
    growth = by_id.get("arr_growth_yoy")
    if north and north.current is not None:
        headline = f"{north.kpi.name} reached {fmt_value(north.current, north.kpi.unit, cur)}"
        if growth and growth.current is not None:
            headline += f", up {growth.current:.0%} year on year"
        fallbacks["arr_trend"] = headline
    benched = [r for r in computed if r.benchmark_position]
    behind = [r for r in benched
              if r.benchmark_position in ("below_median", "bottom_quartile")]
    if benched:
        fallbacks["benchmark_position"] = (
            f"{len(behind)} of {len(benched)} benchmarked KPIs trail the cohort median"
        )

    specs_by_id = {s.id: s for s in specs}
    for spec_id, kpi_id, kicker in exhibit_plan:
        spec = specs_by_id.get(spec_id)
        if spec is None or spec_id not in images:
            continue
        f = finding_for.get(kpi_id) if kpi_id else None
        headline = f.title if f else fallbacks.get(spec_id, spec.title)
        deck.exhibit_slide(headline, images[spec_id], kicker=kicker,
                           caption=spec.note or spec.subtitle)

    # Risks
    risks = [f for f in findings if f.severity in ("critical", "high")]
    if risks:
        deck.bullets_slide(
            f"{len(risks)} issues need a decision this quarter",
            [f"{f.title}\n{f.statement}" for f in risks[:5]],
            kicker="Risks and watch-list",
        )

    # Actions
    order = {"high": 0, "medium": 1, "low": 2, None: 3}
    owner_by_kpi = {r.kpi.id: r.kpi.owner_role for r in results}
    actionable = sorted([f for f in findings if f.recommendation],
                        key=lambda f: (order.get(f.impact, 3), order.get(f.effort, 3)))
    if actionable:
        deck.table_slide(
            "Where to act first",
            ["Action", "Owner", "Impact", "Effort"],
            [[f.recommendation[:110],
              next((owner_by_kpi.get(k) for k in f.kpi_ids if owner_by_kpi.get(k)), "—"),
              (f.impact or "—").title(), (f.effort or "—").title()]
             for f in actionable[:8]],
            kicker="Recommended actions",
            col_widths=[0.58, 0.14, 0.14, 0.14],
        )

    deck.bullets_slide(
        "How to read this deck",
        [
            "Every figure is computed by deterministic code from the underlying "
            "fact tables. No number in this deck was produced by a language model.",
            f"{len(kpi_set.kpis)} KPIs were selected from the "
            f"{profile.business_model.type.value} library by scoring applicability, "
            f"objective alignment, Balanced Scorecard coverage and audience fit.",
            "Benchmarks are illustrative placeholders assembled from public "
            "commentary — not a licensed dataset. They are suitable for "
            "calibration, not for external reporting.",
            f"Profile confidence {profile.confidence:.0%}. Fields filled from "
            f"sector defaults rather than your data are listed in the appendix "
            f"of the full report.",
        ],
        kicker="Appendix",
    )

    return deck.save(path)
