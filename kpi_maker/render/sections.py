"""What goes in each report section, decided once.

The PDF, the DOCX and the deck are meant to be one report in three formats.
They were three independent implementations of the same eight sections, and
each re-derived the same content. The actions section repeated this in all
three:

    order = {"high": 0, "medium": 1, "low": 2, None: 3}
    owner_by_kpi = {r.kpi.id: r.kpi.owner_role for r in results}
    actionable = sorted([f for f in findings if f.recommendation], key=...)

Change the sort or the owner rule and two of the three silently disagree. They
already had: the deep-dives matcher in `report.py` accepted `f.id.endswith(id)`
and `doc.py` did not. On today's three samples the two rules happen to pick the
same finding for all nineteen exhibits, which is precisely why it had gone
unnoticed.

**Content is shared here; drawing is not.** fpdf, python-docx and python-pptx
genuinely differ, and the per-format polish in the PDF is worth keeping. What
stops being duplicated is the selection, the sorting and the caps.

Per-format limits are an explicit argument rather than a constant buried in
each renderer, so the deck's smaller action table (8 rows against 12) is
recorded intent instead of drift nobody can tell from a bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..fmt import fmt_value
from ..insight.detectors import Finding
from ..kpi.schema import KPISet, Perspective
from ..metrics.engine import MetricResult
from ..profile.schema import CompanyProfile

SEVERITY_WORD = {"critical": "Critical", "high": "High", "medium": "Medium",
                 "low": "Low", "positive": "Strength"}
STATUS_WORD = {"green": "On track", "amber": "Watch", "red": "Off track",
               "unscored": "No target", "unknown": "No data"}

# Impact and effort are ranked words, not numbers. `None` sorts last so a
# finding that declined to estimate does not jump the queue.
_RANK = {"high": 0, "medium": 1, "low": 2, None: 3}

# These two carry the diagnostic section; the deep dives must not repeat them.
DIAGNOSTIC_EXHIBITS = ("arr_bridge", "cohort_heatmap")


# --------------------------------------------------------------------------
# The shapes a section can contain
# --------------------------------------------------------------------------

@dataclass
class Bullet:
    """A severity label, a title and a sentence — kept apart on purpose.

    The report and the editable doc want "High · Churn is concentrated"; the
    deck's risks slide wants the title alone, because the slide headline
    already says these are the risks. Storing the composed string would force
    the deck to unpick it.

    `tint` names a token, for renderers that colour the lead-in.
    """
    label: str = ""
    title: str = ""
    text: str = ""
    tint: str = ""

    @property
    def lead(self) -> str:
        return f"{self.label} · {self.title}" if self.label else self.title


@dataclass
class Table:
    headers: List[str]
    rows: List[List[str]]
    group: str = ""                      # subheading above this table
    aligns: List[str] = field(default_factory=list)
    # One token name per row, for the status column in the PDF scorecard.
    row_tints: List[str] = field(default_factory=list)


@dataclass
class Exhibit:
    id: str
    title: str
    caption: str = ""                    # the chart's own subtitle
    note: str = ""                       # the chart's own footnote
    observation: str = ""
    implication: str = ""


@dataclass
class Block:
    """A titled run of prose — an appendix subsection, or one KPI definition.

    `level` distinguishes the two: 2 is a subheading inside the section, 3 is
    an entry underneath one. Renderers map that onto whatever they call a
    heading; nothing here knows about point sizes.
    """
    title: str = ""
    level: int = 2
    # Prose that stands on its own, as against `lines`, which are list items.
    # The distinction carries the "nothing to list" case: an empty `lines` with
    # an `intro` is how a block says "no profile fields were defaulted" rather
    # than printing an empty bullet list.
    intro: str = ""
    lines: List[str] = field(default_factory=list)
    marker: str = "•"
    # Long-form prose the editable report includes and the PDF omits for
    # length. A recorded per-format decision, like the deck's smaller action
    # table — not something a renderer decides for itself by inspecting text.
    detail: str = ""
    # (line, token name) for lines a renderer should colour — a pitfall in
    # amber, a benchmark source in grey.
    tinted: List[tuple] = field(default_factory=list)
    # The KPI definitions run long enough to want a page to themselves. A hint,
    # not an instruction: the deck has no pages and ignores it.
    page_break_before: bool = False


@dataclass
class SectionContent:
    id: str
    title: str
    intro: str = ""
    # AI-written connective prose, empty unless `spec.ai.enabled` (P5). It sits
    # between the deterministic `intro` and the section's content, and it is
    # additive by construction: every bullet, table and exhibit below is
    # unchanged, so dropping a paragraph that failed the number check costs the
    # section nothing but the framing.
    narrative: List[str] = field(default_factory=list)
    bullets: List[Bullet] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    exhibits: List[Exhibit] = field(default_factory=list)
    blocks: List[Block] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # What to print when the section found nothing. A section with an empty
    # message still renders its heading — "no critical issues" is a result the
    # reader wants, not an absence to hide.
    empty: str = ""
    # How many items existed before the per-format cap. The deck's risks slide
    # is headlined "8 issues need a decision" while showing the top five; a
    # renderer that counted what it was handed would say five and understate
    # the problem.
    total: Optional[int] = None

    @property
    def is_empty(self) -> bool:
        return not (self.bullets or self.tables or self.exhibits or self.blocks)


@dataclass
class SectionContext:
    """Everything a section may read. Deliberately not the RunContext.

    Sections see finished results, never the pipeline — so a section cannot
    trigger work, and the same context can be built in a test from three
    literals.
    """
    profile: CompanyProfile
    kpi_set: KPISet
    results: List[MetricResult]
    findings: List[Finding]
    images: Dict[str, bytes] = field(default_factory=dict)
    specs: List[Any] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)
    anomaly_notes: List[str] = field(default_factory=list)
    period: str = ""
    # {table: "measured" | "modelled"} for uploads the user asked to gap-fill.
    # Empty for a synthetic run, where there is no gap to fill and every table
    # is as real as any other.
    origins: Dict[str, str] = field(default_factory=dict)
    # Section ids that carry AI-written prose (P5). Empty on every run that did
    # not enable it, which is the default — and the appendix says nothing new
    # when it is empty, so an unconfigured report is unchanged.
    narrated: List[str] = field(default_factory=list)
    # Sentences from the AI layer about what it could not do: a dropped
    # narrative, a refusal, a missing key. Reported rather than swallowed.
    ai_notes: List[str] = field(default_factory=list)
    # Caveats about the run itself: a sector simulated by a neighbouring
    # archetype, a scorecard built on the cross-sector pack, a Tier 2 identity
    # miss on uploaded data. These belong in the emailed PDF, not only in the
    # console — a reader who cannot see them cannot weigh the numbers.
    caveats: List[str] = field(default_factory=list)
    # Separator convention for every number this report prints. `None` means
    # English, which is what every call site did before the field existed.
    locale: Optional[str] = None

    @property
    def currency(self) -> str:
        return self.profile.identity.currency

    @property
    def computed(self) -> List[MetricResult]:
        return [r for r in self.results if r.computed]

    def value(self, result: MetricResult, which: str = "current") -> str:
        return fmt_value(getattr(result, which), result.kpi.unit, self.currency,
                         locale=self.locale)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

@dataclass
class Section:
    id: str
    title: str
    order: int
    fn: Callable[..., SectionContent]
    numbered: bool = True                # the cover is not "0. Cover"

    def build(self, ctx: SectionContext, **kwargs) -> SectionContent:
        return self.fn(ctx, **kwargs)


REGISTRY: Dict[str, Section] = {}


def section(id: str, title: str, order: int, numbered: bool = True):
    def wrap(fn):
        REGISTRY[id] = Section(id=id, title=title, order=order, fn=fn,
                               numbered=numbered)
        return fn
    return wrap


class UnknownSection(ValueError):
    """A section id that is not registered."""


def default_order() -> List[str]:
    return [s.id for s in sorted(REGISTRY.values(), key=lambda s: s.order)]


def resolve_order(requested: Optional[Sequence[str]]) -> List[str]:
    """The section ids to render, in order.

    `None` means the shipped eight in their shipped order — which is what keeps
    an unconfigured run identical. A list both selects and orders: anything
    omitted is disabled. An unknown id is refused rather than skipped, because
    a typo that silently drops a section from a board report is worse than an
    error message.
    """
    if requested is None:
        return default_order()
    unknown = [s for s in requested if s not in REGISTRY]
    if unknown:
        raise UnknownSection(
            f"unknown section(s): {', '.join(unknown)}. "
            f"Available: {', '.join(default_order())}")
    seen, out = set(), []
    for sid in requested:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def build(ctx: SectionContext, requested: Optional[Sequence[str]] = None,
          limits: Optional[Dict[str, Dict[str, Any]]] = None,
          narrative: Optional[Dict[str, List[str]]] = None
          ) -> List[SectionContent]:
    """Every selected section's content, in order, with its heading number set.

    Numbers come from position, so reordering renumbers rather than leaving a
    report whose section 5 follows section 7.

    `narrative` is the AI overlay, applied here rather than inside each section
    function for two reasons: a section builder should not have to know the
    layer exists, and applying it in one place is what guarantees the PDF, the
    DOCX and the deck carry identical prose. A section the narrator said
    nothing about, or whose prose failed the number check, simply gets an empty
    list — which is what it has always had.
    """
    limits = limits or {}
    narrative = narrative or {}
    out, number = [], 0
    for sid in resolve_order(requested):
        spec = REGISTRY[sid]
        content = spec.build(ctx, **limits.get(sid, {}))
        if spec.numbered:
            number += 1
            content.title = f"{number}. {content.title}"
        paragraphs = narrative.get(sid)
        if paragraphs:
            content.narrative = list(paragraphs)
        out.append(content)
    return out


# --------------------------------------------------------------------------
# The sections
# --------------------------------------------------------------------------

@section("cover", title="Cover", order=0, numbered=False)
def _cover(ctx: SectionContext) -> SectionContent:
    p = ctx.profile
    north = next((r for r in ctx.results
                  if r.kpi.id == ctx.kpi_set.north_star and r.computed), None)
    content = SectionContent(id="cover", title=p.identity.name)
    content.intro = (f"{p.business_model.type.value.upper()} · "
                     f"{p.business_model.customer_type.value} · "
                     f"{p.identity.country} · {ctx.period}")
    if north:
        content.blocks.append(Block(title=north.kpi.name.upper(),
                                    lines=[ctx.value(north)]))
    content.notes = [
        f"Prepared for the {p.intent.audience.value} · primary objective: "
        f"{p.intent.primary_objective.value.replace('_', ' ')} · "
        f"horizon {p.intent.horizon_months} months",
        f"Profile confidence {p.confidence:.0%} · {len(ctx.kpi_set.kpis)} KPIs "
        f"selected from the {p.business_model.type.value} library",
        "Benchmarks in this report are illustrative placeholders, not a "
        "licensed dataset. See the appendix before acting on any peer comparison.",
    ]
    return content


@section("exec_summary", title="Executive summary", order=1)
def _exec_summary(ctx: SectionContext, limit: int = 5) -> SectionContent:
    content = SectionContent(
        id="exec_summary", title="Executive summary",
        intro="Every statement below is computed directly from the underlying "
              "data. Figures in this section reconcile to the scorecard that "
              "follows.")
    content.bullets = [
        Bullet(label=SEVERITY_WORD.get(f.severity, ""), title=f.title,
               text=f.statement)
        for f in ctx.findings[:limit]
    ]
    return content


@section("scorecard", title="Scorecard", order=2)
def _scorecard(ctx: SectionContext) -> SectionContent:
    content = SectionContent(
        id="scorecard", title="Scorecard",
        intro=f"Grouped by Balanced Scorecard perspective. Coverage is "
              f"constrained to between two and seven KPIs per perspective, so "
              f"no single view of the business can dominate. Leading "
              f"indicators are {ctx.kpi_set.leading_share:.0%} of the set.")
    tint = {"green": "good", "amber": "serious", "red": "critical"}
    for perspective in Perspective:
        group = [r for r in ctx.computed if r.kpi.perspective == perspective]
        if not group:
            continue
        rows, tints = [], []
        for r in sorted(group, key=lambda x: int(x.kpi.tier)):
            k = r.kpi
            rows.append([
                k.short_name or k.name,
                ctx.value(r), ctx.value(r, "prior_year"), ctx.value(r, "target"),
                fmt_value(k.benchmark.p50, k.unit, ctx.currency, locale=ctx.locale) if k.benchmark else "—",
                STATUS_WORD.get(r.status, "—"),
            ])
            tints.append(tint.get(r.status, "muted"))
        content.tables.append(Table(
            group=perspective.value.replace("_", " ").title(),
            headers=["KPI", "Current", "12mo ago", "Target", "Cohort med.", "Status"],
            rows=rows, aligns=["L", "R", "R", "R", "R", "L"], row_tints=tints))
    return content


@section("diagnostic", title="Diagnostic", order=3)
def _diagnostic(ctx: SectionContext) -> SectionContent:
    content = SectionContent(
        id="diagnostic", title="Diagnostic",
        intro="Where the period's performance actually came from. The bridge "
              "decomposes the movement in the North Star; the cohort view "
              "shows how much of it persists.")
    bridge = next((f for f in ctx.findings if f.id == "arr_bridge"), None)
    content.exhibits = [
        Exhibit(id="arr_bridge", title="ARR bridge, last 12 months",
                caption="Blue adds, red subtracts",
                # The bridge's own finding is its footnote here rather than a
                # separate observation — the diagnostic section is two named
                # exhibits, not the deep-dive treatment.
                note=bridge.statement if bridge else ""),
        Exhibit(id="cohort_heatmap",
                title="Revenue retention by acquisition cohort",
                caption="Percentage of each cohort's initial ARR still held",
                note="Rows below 100% at month 12 are cohorts where churn "
                     "outran expansion."),
    ]
    return content


@section("deep_dives", title="Deep dives", order=4)
def _deep_dives(ctx: SectionContext, limit: Optional[int] = None) -> SectionContent:
    """Exhibit, observation, implication — one block per chart.

    The matching rule is the union of what the two renderers were doing
    separately: a finding whose id contains or ends with the exhibit id, else
    any finding attached to a KPI whose id overlaps it. `report.py` had the
    `endswith` clause and `doc.py` did not, so keeping the broader rule is the
    conservative choice — it can only find a finding where the narrower one
    found none.
    """
    content = SectionContent(id="deep_dives", title="Deep dives")
    by_kpi: Dict[str, List[Finding]] = {}
    for f in ctx.findings:
        for kid in f.kpi_ids:
            by_kpi.setdefault(kid, []).append(f)

    for spec in ctx.specs:
        if spec.id in DIAGNOSTIC_EXHIBITS or spec.id not in ctx.images:
            continue
        related = [f for f in ctx.findings
                   if f.id.endswith(spec.id) or spec.id in f.id]
        if not related:
            for kid, group in by_kpi.items():
                if kid in spec.id or spec.id in kid:
                    related.extend(group)
        first = related[0] if related else None
        content.exhibits.append(Exhibit(
            id=spec.id, title=spec.title, caption=spec.subtitle, note=spec.note,
            observation=first.statement if first else "",
            implication=(first.recommendation if first and first.recommendation
                         else "")))
        if limit is not None and len(content.exhibits) >= limit:
            break
    return content


@section("benchmarks", title="Benchmarks", order=5)
def _benchmarks(ctx: SectionContext) -> SectionContent:
    content = SectionContent(
        id="benchmarks", title="Benchmarks",
        intro="Position against the peer cohort median. Direction is "
              "normalised so that positive always means better, whichever way "
              "the underlying metric runs.")
    content.exhibits = [Exhibit(
        id="benchmark_position",
        title="Position against the peer cohort median")]
    rows = []
    for r in ctx.computed:
        b = r.kpi.benchmark
        if b is None:
            continue
        rows.append([
            r.kpi.short_name or r.kpi.name, ctx.value(r),
            fmt_value(b.p25, r.kpi.unit, ctx.currency, locale=ctx.locale),
            fmt_value(b.p50, r.kpi.unit, ctx.currency, locale=ctx.locale),
            fmt_value(b.p75, r.kpi.unit, ctx.currency, locale=ctx.locale),
            (r.benchmark_position or "—").replace("_", " "),
        ])
    if rows:
        content.tables.append(Table(
            headers=["KPI", "You", "P25", "P50", "P75", "Position"], rows=rows,
            aligns=["L", "R", "R", "R", "R", "L"]))
    content.notes = [
        "Peer figures are illustrative placeholders assembled from public "
        "commentary, not a licensed benchmark dataset. They are suitable for "
        "calibration, not for external reporting."]
    return content


@section("risks", title="Risks and watch-list", order=6)
def _risks(ctx: SectionContext, limit: Optional[int] = None) -> SectionContent:
    content = SectionContent(
        id="risks", title="Risks and watch-list",
        empty="No critical or high-severity issues were detected this period.")
    risks = [f for f in ctx.findings if f.severity in ("critical", "high")]
    content.total = len(risks)
    if limit is not None:
        risks = risks[:limit]
    content.bullets = [
        Bullet(label=SEVERITY_WORD[f.severity], title=f.title, text=f.statement,
               tint="critical" if f.severity == "critical" else "serious")
        for f in risks]
    return content


@section("actions", title="Recommended actions", order=7)
def _actions(ctx: SectionContext, limit: int = 12) -> SectionContent:
    """Prioritised by impact against effort.

    `limit` is per format on purpose: the deck passes 8 because a slide holds
    fewer rows than a page. That was already true and written nowhere.
    """
    content = SectionContent(
        id="actions", title="Recommended actions",
        intro="Prioritised by impact against effort. Each action is tied to "
              "the KPI it is meant to move and to the role that owns that KPI.",
        empty="No finding crossed an action threshold this period.")
    owner_by_kpi = {r.kpi.id: r.kpi.owner_role for r in ctx.results}
    actionable = sorted([f for f in ctx.findings if f.recommendation],
                        key=lambda f: (_RANK.get(f.impact, 3),
                                       _RANK.get(f.effort, 3)))
    content.total = len(actionable)
    if not actionable:
        return content
    rows = []
    for f in actionable[:limit]:
        owner = next((owner_by_kpi.get(k) for k in f.kpi_ids
                      if owner_by_kpi.get(k)), "—")
        rows.append([f.recommendation, ", ".join(f.kpi_ids[:2]) or "—", owner,
                     (f.impact or "—").title(), (f.effort or "—").title()])
    content.tables.append(Table(
        headers=["Action", "Moves", "Owner", "Impact", "Effort"], rows=rows))
    return content


def _basis_block(ctx: SectionContext) -> Optional[Block]:
    """Which numbers came from the user's data and which the generator supplied.

    A modelled number presented as measured is the worst thing this product can
    do, and the print deliverables were the one place the marker had not
    reached — the dashboard, facts.csv and the workbook all carried it.

    Returns None when there is nothing to say. That is the common case: a
    synthetic run has no gaps to fill, so every table is as real as any other
    and a paragraph explaining the distinction would only teach the reader to
    skip it.
    """
    # Computed results only. A KPI that could not be computed at all has no
    # basis worth reporting, and counting one as measured is how the first
    # version of this paragraph came to say "18 of 25 KPIs were computed
    # entirely from your own data" about a run where 18 were not computed at
    # all and every one of the remaining 7 was modelled. A reassuring number
    # that is wrong is worse here than no number.
    computed = ctx.computed
    modelled = sorted(t for t, origin in (ctx.origins or {}).items()
                      if origin and origin != "measured")
    affected = [r for r in computed
                if getattr(r, "basis", "measured") != "measured"]
    if not modelled and not affected:
        return None

    block = Block(
        title="Measured and modelled figures",
        intro=("Some figures in this report were not computed from your data. "
               "You asked for the tables below to be filled in by the "
               "generator, because your upload did not contain them; every "
               "number that reads from one is marked here and wherever else "
               "it appears."))
    for table in modelled:
        block.lines.append(
            f"{table} — supplied by the generator, not present in your upload")

    for label, kind in (("Modelled", "modelled"), ("Part modelled", "mixed")):
        names = sorted(r.kpi.name for r in affected
                       if getattr(r, "basis", "") == kind)
        if names:
            block.lines.append(f"{label}: {', '.join(names)}")

    if computed:
        clean = len(computed) - len(affected)
        block.lines.append(
            f"{clean} of the {len(computed)} KPIs on this report "
            f"{'was' if len(computed) == 1 else 'were'} computed entirely from "
            f"your own data.")
    return block


def _narration_block(ctx: SectionContext) -> Optional[Block]:
    """Which sections were narrated by a model, and what was checked.

    The methodology paragraph has always ended "no number in this document was
    produced by a language model", and that stays true — the narrator copies
    figures, it does not compute them. But leaning on the precise wording of
    that sentence to cover AI-written prose would be exactly the kind of
    technically-accurate reassurance the measured-versus-modelled paragraph
    exists to avoid. So when a model wrote anything, the report says so, names
    where, and states the check that was applied.

    Returns None on every run that did not enable it, which keeps the appendix
    byte-identical by default.
    """
    if not ctx.narrated and not ctx.ai_notes:
        return None
    block = Block(
        title="AI-written narrative",
        intro=("The connective paragraphs in the sections below were written "
               "by a language model. It was shown the computed KPI table and "
               "the findings, never the underlying data, and every figure it "
               "wrote was checked against that table before this report was "
               "produced — prose containing a number the table does not "
               "support is discarded rather than printed. The bullets, tables "
               "and exhibits are unchanged deterministic output."))
    # Listed in the order the reader will meet them, not alphabetically by id
    # — sorting on the id gave "Recommended actions, Benchmarks, Diagnostic,
    # Executive summary", which looks like no order at all.
    narrated = set(ctx.narrated)
    for section_id in default_order():
        if section_id in narrated:
            block.lines.append(REGISTRY[section_id].title)
    for section_id in sorted(narrated - set(default_order())):
        block.lines.append(section_id)
    for note in ctx.ai_notes:
        block.tinted.append((note, "serious"))
    return block


@section("appendix", title="Appendix", order=8)
def _appendix(ctx: SectionContext) -> SectionContent:
    p = ctx.profile
    content = SectionContent(id="appendix", title="Appendix")
    content.blocks.append(Block(title="Methodology", intro=(
        "KPIs were selected by evaluating each metric's applicability "
        "expression against this company profile, then scoring on objective "
        "alignment, Balanced Scorecard coverage, leading/lagging balance and "
        "audience fit. Redundant metric pairs were suppressed. Every figure in "
        "this report is computed by deterministic code from the underlying "
        "fact tables; no number in this document was produced by a language "
        "model.")))

    # Immediately after methodology, before anything reassuring. A caveat that
    # changes how the whole report should be read cannot sit below sixty KPI
    # definitions.
    if ctx.caveats:
        content.blocks.append(Block(
            title="Scope and limitations",
            intro="This run had to approximate part of its content. What "
                  "follows is accurate for what was modelled; these are the "
                  "limits of what that was.",
            tinted=[(c, "serious") for c in ctx.caveats]))

    defaulted = [f"{path} — {src}" for path, src in sorted(p.provenance.items())
                 if src.startswith("benchmark_default")]
    content.blocks.append(Block(
        title="Assumptions and provenance", lines=defaulted,
        intro="" if defaulted else
              "No profile fields were filled from benchmark defaults."))

    content.blocks.append(Block(
        title="Data quality",
        intro=f"{len(ctx.checks)} reconciliation assertions passed before this "
              f"report was produced. The pipeline refuses to render on data "
              f"that fails them.",
        lines=list(ctx.checks), marker="-"))

    basis = _basis_block(ctx)
    if basis is not None:
        content.blocks.append(basis)

    narration = _narration_block(ctx)
    if narration is not None:
        content.blocks.append(narration)

    if ctx.anomaly_notes:
        content.blocks.append(Block(title="Known events in this dataset",
                                    lines=list(ctx.anomaly_notes)))

    content.blocks.append(Block(title="KPI definitions", page_break_before=True))
    for r in sorted(ctx.results,
                    key=lambda x: (x.kpi.perspective.value, int(x.kpi.tier))):
        k = r.kpi
        entry = Block(
            level=3,
            title=f"{k.name}  ({k.perspective.value}, {k.timing.value})",
            lines=[f"Formula: {k.formula}",
                   f"Owner: {k.owner_role}   ·   Sources: "
                   f"{', '.join(k.source_systems) or 'n/a'}"
                   f"   ·   Frequency: {k.frequency}"],
            detail=k.interpretation or "")
        if k.pitfalls:
            entry.tinted.append((f"Pitfall: {k.pitfalls}", "serious"))
        if k.benchmark:
            entry.tinted.append((f"Benchmark source: {k.benchmark.source}", "muted"))
        content.blocks.append(entry)

    if ctx.kpi_set.dropped:
        content.blocks.append(Block(
            title="KPIs considered but not selected",
            marker="-",
            lines=[f"{kid} — {reason}"
                   for kid, reason in sorted(ctx.kpi_set.dropped.items())]))
    return content
