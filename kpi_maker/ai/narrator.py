"""Prose over the facts table, and nothing else.

The narrator sees `facts_table()` — the compact view `metrics/engine.py` has
described as "the LLM-safe view" since long before there was an LLM to give it
to — plus the findings the detectors wrote and a one-line brief on what each
section already contains. It never sees a fact table row, a customer, or a
month of raw revenue.

Two design decisions carry the safety:

**It is handed the formatted strings.** Every figure arrives as `fmt_value`
already rendered it, next to its raw value, and the prompt says to copy them
verbatim. That turns the downstream check from "is this number approximately
right" into set membership, which is the difference between a gate and a
heuristic.

**It writes the connective paragraph, not the analysis.** The bullets, tables
and exhibits underneath are deterministic and stay exactly as they are; the
narrator frames them. If its paragraph is dropped the section still says
everything it said before — which is what makes dropping it an acceptable
failure mode rather than a hole in the report.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..fmt import fmt_value
from ..metrics.engine import facts_table
from .client import AIUnavailable, build_client
from .meter import Meter
from .verify import allowed_numbers, check_prose

PROMPTS = Path(__file__).parent / "prompts"

# How many findings and KPIs reach the prompt. The facts table for a large run
# is a few KB, which is affordable, but a narrator handed forty rows writes
# about the wrong five. The cap is on what it is asked to weigh, not on what it
# is allowed to cite — the verifier's allowed set is built from every result.
MAX_FACT_ROWS = 40
MAX_FINDINGS = 15


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# What the model is shown
# --------------------------------------------------------------------------

def facts_block(results: Sequence[Any], currency: str) -> str:
    """The facts table as the narrator reads it: raw value and rendering.

    Built through `facts_table()` rather than off the results directly, so the
    prompt and the artifact on disk cannot drift. The `formatted` columns are
    the addition — they are what the prompt tells the model to copy, and what
    `verify.allowed_numbers` builds its set from.
    """
    frame = facts_table(list(results))
    if frame.empty:
        return "(no KPIs were computed)"

    units = {r.kpi.id: r.kpi.unit for r in results}
    lines = ["kpi_id | name | unit | current | 12mo ago | vs target | "
             "peer median | status | basis",
             "--- | --- | --- | --- | --- | --- | --- | --- | ---"]
    shown = frame[frame["computed"]] if "computed" in frame else frame
    for _, row in shown.head(MAX_FACT_ROWS).iterrows():
        unit = units.get(row["kpi_id"], "number")

        def show(column: str) -> str:
            return fmt_value(row.get(column), unit, currency)

        lines.append(" | ".join([
            str(row["kpi_id"]), str(row["name"]), unit,
            show("current"), show("prior_year"), show("vs_target"),
            show("benchmark_p50"),
            str(row.get("status") or "—"), str(row.get("basis") or "—"),
        ]))

    dropped = int((~frame["computed"]).sum()) if "computed" in frame else 0
    if dropped:
        lines.append("")
        lines.append(f"({dropped} further KPIs were selected but could not be "
                     f"computed from the available data.)")
    return "\n".join(lines)


def findings_block(findings: Sequence[Any]) -> str:
    if not findings:
        return "(no findings)"
    lines = []
    for f in list(findings)[:MAX_FINDINGS]:
        lines.append(f"- [{f.severity}] {f.title}: {f.statement}"
                     + (f" — recommended: {f.recommendation}"
                        if getattr(f, "recommendation", None) else ""))
    return "\n".join(lines)


def _section_brief(content: Any) -> str:
    """One line on what a section already contains, so the prose complements it."""
    parts: List[str] = []
    if content.bullets:
        parts.append(f"{len(content.bullets)} bullets: "
                     + "; ".join(b.title for b in content.bullets[:5]))
    if content.tables:
        parts.append(f"{len(content.tables)} table(s)")
    if content.exhibits:
        parts.append("exhibits: " + ", ".join(e.title for e in content.exhibits[:5]))
    if content.blocks:
        parts.append(f"{len(content.blocks)} prose block(s)")
    return " · ".join(parts) or "(empty)"


def build_request(profile: Any, results: Sequence[Any], findings: Sequence[Any],
                  contents: Sequence[Any], period: str,
                  max_paragraphs: int = 1) -> Dict[str, str]:
    """The system and user text for one narration pass.

    Separated from the call so `POST /api/ai/estimate` can price exactly the
    request that would be sent, rather than a guess at its size.
    """
    identity, intent = profile.identity, profile.intent
    briefs = "\n".join(
        f"- `{c.id}` — {c.title.split('. ', 1)[-1]} — {_section_brief(c)}"
        for c in contents)

    user = f"""# The company

{identity.name} · {profile.business_model.type.value} · \
{profile.business_model.customer_type.value} · {identity.country} · \
{identity.currency}
Reporting period: {period or "unknown"}
Audience: {intent.audience.value}
Primary objective: {intent.primary_objective.value.replace('_', ' ')} \
over {intent.horizon_months} months

# Facts table

Copy figures from these cells exactly as written.

{facts_block(results, identity.currency)}

# Findings

Numbers inside a finding's statement are also safe to quote.

{findings_block(findings)}

# Sections to narrate

Write at most {max_paragraphs} paragraph(s) for each. The listed content is \
already in the report underneath your paragraph — frame it, do not restate it.

{briefs}
"""
    return {"system": _prompt("narrator"), "user": user}


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "paragraphs"],
            },
        },
    },
    "required": ["sections"],
}


# --------------------------------------------------------------------------
# The call, and the one retry
# --------------------------------------------------------------------------

def _collect(payload: Dict[str, Any], wanted: Sequence[str],
             max_paragraphs: int) -> Dict[str, List[str]]:
    """Model output, trimmed to what was asked for.

    A section id we did not request is dropped rather than rendered: the
    renderers key on section id, and a paragraph attached to a section that is
    not in this report has nowhere honest to go.
    """
    allowed = set(wanted)
    out: Dict[str, List[str]] = {}
    for entry in payload.get("sections") or []:
        section_id = str(entry.get("id", ""))
        if section_id not in allowed:
            continue
        paragraphs = [str(p).strip() for p in (entry.get("paragraphs") or [])]
        paragraphs = [p for p in paragraphs if p][:max_paragraphs]
        if paragraphs:
            out[section_id] = paragraphs
    return out


def _retry_message(user: str, first: Dict[str, List[str]],
                   problems: Dict[str, List[Any]]) -> str:
    """The original request plus what went wrong, which is the whole retry.

    The violations are quoted with their surrounding words so the model can see
    which sentence to fix, rather than being told a count and asked to guess.
    """
    lines = ["", "# Your previous attempt was rejected", "",
             "These numbers do not appear in the facts table. Every one of "
             "them must be removed or replaced with a figure copied exactly "
             "from a cell above.", ""]
    for section_id, violations in sorted(problems.items()):
        lines.append(f"In `{section_id}`:")
        for v in violations:
            lines.append(f"  - {v}")
        lines.append("")
    lines.append("Your previous text, for reference:")
    for section_id, paragraphs in sorted(first.items()):
        lines.append(f"  {section_id}: " + " ".join(paragraphs))
    lines.append("")
    lines.append("Rewrite every section. Sections that were accepted may be "
                 "returned unchanged.")
    return user + "\n".join(lines)


def narrate(profile: Any, results: Sequence[Any], findings: Sequence[Any],
            contents: Sequence[Any], period: str, *, spec: Any,
            meter: Optional[Meter] = None,
            client: Optional[Any] = None) -> Tuple[Dict[str, List[str]], Meter]:
    """Prose per section, with every number checked before it is returned.

    The failure policy is ROADMAP M7's, unchanged: validate, retry once with
    the error fed back, then fall back to the deterministic default. Here the
    deterministic default is simply the section without a narrative, which the
    renderers have always known how to draw.
    """
    meter = meter or Meter()
    wanted = [c.id for c in contents]
    if not wanted:
        return {}, meter

    request = build_request(profile, results, findings, contents, period,
                            max_paragraphs=spec.max_paragraphs)
    client = client or build_client(spec.model)

    allowed = allowed_numbers(
        results, findings, currency=profile.identity.currency, period=period,
        extra_counts=(profile.history_months, profile.intent.horizon_months))

    accepted: Dict[str, List[str]] = {}
    user = request["user"]
    problems: Dict[str, List[Any]] = {}

    for attempt in (1, 2):
        try:
            payload = client.json(system=request["system"], user=user,
                                  schema=SCHEMA, purpose=f"narrate:{attempt}")
        except AIUnavailable as exc:
            meter.absorb(client)
            meter.note(f"Narration was skipped: {exc}")
            return accepted, meter
        meter.absorb(client)

        candidate = _collect(payload, wanted, spec.max_paragraphs)
        problems = {}
        for section_id, paragraphs in candidate.items():
            violations = [v for p in paragraphs for v in check_prose(p, allowed)]
            if violations:
                problems[section_id] = violations
            else:
                accepted[section_id] = paragraphs

        if not problems:
            break
        if attempt == 1:
            user = _retry_message(request["user"], candidate, problems)

    if problems:
        # Named, not silent. A section that quietly lost its paragraph looks
        # identical to a section the narrator had nothing to say about, and the
        # reader deserves to know which happened.
        meter.note(
            "Narrative was dropped for "
            + ", ".join(sorted(problems))
            + " because it contained figures that do not appear in the facts "
              "table.")
    return accepted, meter
