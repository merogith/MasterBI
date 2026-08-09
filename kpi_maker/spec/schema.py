"""RunSpec — the configuration contract for a single run.

`CompanyProfile` answers "who is this company?". `RunSpec` answers "what should
the pipeline do about it?" — which data, which cleaning, which KPIs, which
analysis, which design, which artifacts.

Before this existed, all seven of those answers were implicit: derived inside
the code from the profile, with no way for a caller to see or change them. That
is the whole reason a user could not adjust a run, and the reason a preset went
straight from click to finished output with no knob in between.

Two rules make the migration safe:

  1. **Every field is optional and every default is None-means-derive.** An
     empty RunSpec must reproduce exactly what the pipeline did before it
     existed. `resolve_*` helpers below are the single place that derivation
     happens, so "what does the default actually do?" has one answer.
  2. **The spec is data, never code.** It round-trips through JSON, so it can be
     diffed, stored beside the run, patched over HTTP, and (later) emitted by an
     agent and reviewed by a human before it executes.

Python 3.9 compatible: typing.Optional/List/Dict, not PEP 604 unions.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..profile.schema import CompanyProfile

SPEC_VERSION = 1


class SpecModel(BaseModel):
    """Base for every spec block.

    `validate_assignment` matters more here than on most models: a spec is
    edited field by field — by a PATCH from the studio, by a CLI flag, later by
    an agent — not just parsed once at the boundary. Without it, assigning the
    string "dark" to an enum field is accepted silently and only fails later,
    somewhere else, as an AttributeError on `.value`.
    """
    model_config = ConfigDict(validate_assignment=True)


# --------------------------------------------------------------------------
# Source — where the numbers come from
# --------------------------------------------------------------------------

class SourceKind(str, Enum):
    synthetic = "synthetic"
    upload = "upload"          # P2 — ingestion


class GeneratorParams(SpecModel):
    """Knobs on the synthetic generator.

    Only `seed` was reachable before, and only from the CLI. The rest were
    constants inside `datagen/saas.py`, which meant "generate me a choppier
    book" or "give me five years of history" were code edits.
    """
    archetype: Optional[str] = None        # None -> from business_model.type
    seed: Optional[int] = None             # None -> profile.seed
    history_months: Optional[int] = None   # None -> profile.history_months
    seasonality_amplitude: float = Field(default=1.0, ge=0.0, le=3.0)
    volatility: float = Field(default=1.0, ge=0.0, le=3.0)
    inject_anomalies: bool = True


class SourceSpec(SpecModel):
    kind: SourceKind = SourceKind.synthetic
    generator: GeneratorParams = Field(default_factory=GeneratorParams)
    # Uploaded file ids, resolved against the run's upload directory.
    uploads: List[str] = Field(default_factory=list)

    # Fact tables the user has explicitly agreed to synthesise because their
    # upload does not contain them. Empty by default, and deliberately so: the
    # honest answer to a missing headcount roster is to drop the KPIs that need
    # one, not to invent it. Every KPI computed from a filled table reports
    # `basis: modelled` and is labelled as such wherever it appears.
    fill_gaps: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Cleaning — the transformation recipe
# --------------------------------------------------------------------------

class CleaningStep(SpecModel):
    """One registered operation. Ordered; the list IS the recipe.

    Kept deliberately loose (`params` is a free dict) because the op registry
    owns the per-op parameter schema — see `prep/ops.py` in P2. Validating it
    twice would mean two places to update when an op gains an option.
    """
    op: str
    table: Optional[str] = None            # None -> every table
    params: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    note: str = ""


class CleaningRecipe(SpecModel):
    steps: List[CleaningStep] = Field(default_factory=list)

    @property
    def active(self) -> List[CleaningStep]:
        return [s for s in self.steps if s.enabled]


# --------------------------------------------------------------------------
# Model — the shape the metrics engine is entitled to assume
# --------------------------------------------------------------------------

class CalculatedColumn(SpecModel):
    """A user-defined column, evaluated row-wise by the formula engine (P1)."""
    table: str
    name: str
    expression: str
    description: str = ""


class ModelSpec(SpecModel):
    # P2: {fact_table: {canonical_field: source_column}}
    mapping: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    calculated_columns: List[CalculatedColumn] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Metrics — which KPIs, and the user's own
# --------------------------------------------------------------------------

class KpiOverride(SpecModel):
    """Per-KPI adjustments layered over the library record sheet.

    None means "leave the library value alone" — distinct from a user setting
    the value to zero, which is a real instruction.
    """
    target: Optional[float] = None
    alert_green: Optional[float] = None
    alert_red: Optional[float] = None
    tier: Optional[int] = None


class MetricsSpec(SpecModel):
    packs: Optional[List[str]] = None                  # None -> from business model
    pinned: List[str] = Field(default_factory=list)    # forced in, like `core: true`
    excluded: List[str] = Field(default_factory=list)  # forced out, with a reason
    overrides: Dict[str, KpiOverride] = Field(default_factory=dict)
    # Full KPI record sheets carrying a `compute` block (P1). Dicts rather than
    # KPI models so a spec with a malformed custom KPI still parses and can
    # report the validation error against that one entry.
    custom: List[Dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Analysis — the deterministic detectors
# --------------------------------------------------------------------------

class AnalysisSpec(SpecModel):
    detectors: Optional[List[str]] = None              # None -> all registered
    disabled: List[str] = Field(default_factory=list)
    min_severity: Optional[str] = None                 # drop findings below this
    max_findings: Optional[int] = None
    params: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Design — how the deliverables look
# --------------------------------------------------------------------------

class ThemeMode(str, Enum):
    light = "light"
    dark = "dark"
    auto = "auto"


class BrandSpec(SpecModel):
    """None everywhere means "use the validated default palette".

    A brand colour is not free: an arbitrary hex can fail contrast against the
    surface and produce an unreadable chart. P3 adds the WCAG check; the field
    exists here so the contract does not change when it lands.
    """
    primary: Optional[str] = None
    accent: Optional[str] = None
    logo_path: Optional[str] = None
    font_stack: Optional[str] = None
    footer_text: Optional[str] = None


class DesignSpec(SpecModel):
    theme: ThemeMode = ThemeMode.light
    brand: BrandSpec = Field(default_factory=BrandSpec)
    locale: Optional[str] = None                       # None -> identity.language
    page_size: str = "A4"
    sections: Optional[List[str]] = None               # None -> the fixed 8
    exhibits: Optional[List[str]] = None               # None -> every chart that builds
    # id -> "half" | "full". Selection and order live in `exhibits`; this only
    # changes how much of the row a chart takes, which is a layout decision
    # separate from whether it appears at all.
    exhibit_widths: Dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# AI — the one part of the pipeline that is not deterministic
# --------------------------------------------------------------------------

# The narrator's default beat. Sections whose content is a table or a chart
# grid do not want a paragraph in front of them, and the appendix is a
# reference rather than an argument. Narrating everything was the obvious first
# guess and it reads as padding.
NARRATABLE_SECTIONS = ["exec_summary", "diagnostic", "benchmarks", "risks",
                       "actions"]


class AISpec(SpecModel):
    """Whether, and how, a model is allowed near this run.

    **Off by default, and that is a contract rather than a shy default.** A
    preset click, a survey run and a CLI invocation all cost zero tokens unless
    somebody deliberately turns this on, which is what keeps ROADMAP M7's
    "Modes 1 and 2 stay at zero" literally true. It is also what makes the
    no-regression gate provable: with `enabled` false the client is never
    constructed, so every artifact is byte-identical to the run before this
    block existed.

    The two agents this gates are deliberately unequal in reach. The narrator
    writes prose that is checked number by number against `facts.csv` before it
    is allowed into a deliverable. The planner only ever proposes a patch to
    this document, which the user reviews hunk by hunk. Neither can move a
    number.
    """
    enabled: bool = False
    model: str = "claude-opus-5"
    # None -> NARRATABLE_SECTIONS. A list selects; an empty list narrates
    # nothing, which is a legitimate way to buy the planner without the
    # narrator.
    narrate_sections: Optional[List[str]] = None
    max_paragraphs: int = Field(default=1, ge=1, le=3)
    # A hard ceiling on what one run may spend, checked against the pre-flight
    # estimate before the first call rather than discovered afterwards.
    max_tokens_per_run: int = Field(default=150_000, ge=1_000)

    def resolve_narrate_sections(self) -> List[str]:
        if self.narrate_sections is None:
            return list(NARRATABLE_SECTIONS)
        return list(self.narrate_sections)


# --------------------------------------------------------------------------
# Outputs — which artifacts to actually produce
# --------------------------------------------------------------------------

# Render is ~80% of a run's wall time, and the static chart export alone is
# ~37%. Letting a caller ask for just the dashboard is therefore the single
# largest speedup available, which is why this is a first-class part of the
# contract rather than a CLI flag.
ALL_ARTIFACTS = [
    "dashboard", "workbook", "report_pdf", "deck_pptx", "doc_docx",
    "charts_png", "csv_bundle", "facts_csv", "json_dumps",
]

# Artifacts that need the static PNG export. Requesting none of them skips
# kaleido entirely.
PRINT_ARTIFACTS = {"report_pdf", "deck_pptx", "doc_docx"}


class OutputSpec(SpecModel):
    artifacts: Optional[List[str]] = None              # None -> all of them

    def resolved(self) -> List[str]:
        if self.artifacts is None:
            return list(ALL_ARTIFACTS)
        wanted = [a for a in self.artifacts if a in ALL_ARTIFACTS]
        # The print deliverables embed PNGs, so asking for the PDF implicitly
        # asks for the export even if the caller did not list it.
        if any(a in PRINT_ARTIFACTS for a in wanted) and "charts_png" not in wanted:
            wanted.append("charts_png")
        return wanted


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------

class RunSpec(SpecModel):
    """Everything a run needs, beyond the profile itself."""

    version: int = SPEC_VERSION
    profile: CompanyProfile

    source: SourceSpec = Field(default_factory=SourceSpec)
    cleaning: CleaningRecipe = Field(default_factory=CleaningRecipe)
    model: ModelSpec = Field(default_factory=ModelSpec)
    metrics: MetricsSpec = Field(default_factory=MetricsSpec)
    analysis: AnalysisSpec = Field(default_factory=AnalysisSpec)
    design: DesignSpec = Field(default_factory=DesignSpec)
    outputs: OutputSpec = Field(default_factory=OutputSpec)
    ai: AISpec = Field(default_factory=AISpec)

    # -- derivation: the one place a None default becomes a real value -----

    @classmethod
    def for_profile(cls, profile: CompanyProfile) -> "RunSpec":
        """The spec that reproduces the pre-RunSpec pipeline exactly."""
        return cls(profile=profile)

    def resolve_seed(self) -> int:
        seed = self.source.generator.seed
        return self.profile.seed if seed is None else seed

    def resolve_history_months(self) -> int:
        months = self.source.generator.history_months
        return self.profile.history_months if months is None else months

    def resolve_archetype(self) -> str:
        archetype = self.source.generator.archetype
        return self.profile.business_model.type.value if archetype is None else archetype

    def resolve_packs(self) -> List[str]:
        packs = self.metrics.packs
        return [self.profile.business_model.type.value] if packs is None else list(packs)

    def resolve_currency(self) -> str:
        return self.profile.identity.currency

    def resolve_theme(self) -> str:
        # "auto" is a browser-side concept; anything rendered server-side has to
        # commit to one, and light is what every print deliverable assumes.
        return "light" if self.design.theme == ThemeMode.auto else self.design.theme.value

    def wants(self, artifact: str) -> bool:
        return artifact in self.outputs.resolved()

    # -- hashing support ---------------------------------------------------

    def section(self, name: str) -> Any:
        """One named slice, for per-stage cache keys.

        `profile` is a section too: a stage that reads the profile must go dirty
        when the profile changes, and several of them do.
        """
        return getattr(self, name)


# Sections an automated patch is allowed to touch.
#
# `profile` is excluded on purpose, and it is the most important line in this
# module. The planner's job is to change what the pipeline DOES; changing who
# the company is would change the numbers, and the one rule this codebase does
# not bend is that the model never produces a number. `version` is excluded for
# the duller reason that a schema migration is not a suggestion.
PATCHABLE_SECTIONS = frozenset({
    "source", "cleaning", "model", "metrics", "analysis", "design", "outputs",
})
