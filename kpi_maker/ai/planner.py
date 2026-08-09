"""A patch to the RunSpec, proposed rather than applied.

ROADMAP M7 gave the planner a `plan.json` of its own — packs, sections,
exhibits — because at the time there was nothing else for it to write into.
There is now. `RunSpec` already holds every decision the pipeline makes, the
studio already edits it over `PATCH /api/runs/{id}/spec`, and pydantic already
refuses anything malformed. So the planner does not need a format; it needs to
write in the one that exists.

That collapses most of M7 into a diff. The planner returns a **list of
changes**, each a dotted path, a value and a reason. Not a nested merge patch:
the user accepts or rejects them one at a time, and a nested document cannot be
half-accepted. Each change is independently applicable, independently
reversible, and independently arguable — which is what "the user sees and can
edit every change the AI made" has to mean to be worth anything.

Three guards, checked here rather than trusted to the prompt:

  * **`profile` is unpatchable.** `PATCHABLE_SECTIONS` in `spec/schema.py` is
    the enforcement point. Changing who the company is would change the
    numbers, and the model does not produce numbers.
  * **Every id must exist.** KPI ids, section ids, exhibit ids, detector names
    and artifact names are checked against the same registries the studio
    serves, so a plausible-sounding invention is refused before it is shown.
  * **The merged spec must parse.** The final check is `RunSpec(**merged)` —
    the same validation a hand-written PATCH goes through. If the patch cannot
    produce a runnable spec it is not a patch.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..spec.schema import ALL_ARTIFACTS, PATCHABLE_SECTIONS, RunSpec
from .client import AIUnavailable, build_client
from .meter import Meter

PROMPTS = Path(__file__).parent / "prompts"

# More than this and the review stops being a review. The prompt asks for few
# changes; this is the backstop for when it does not listen.
MAX_CHANGES = 12


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# What the planner is allowed to name
# --------------------------------------------------------------------------

def catalog(profile: Any) -> Dict[str, Any]:
    """Every id the pipeline recognises, from the registries themselves.

    Built from the same sources as `GET /api/catalog/options` rather than from
    a list kept alongside it: a planner that can propose something the studio
    cannot show, or vice versa, is a bug waiting for a sector to expose it.
    """
    from ..insight.detectors import DETECTOR_NAMES
    from ..kpi.selection import candidates_for
    from ..render.sections import REGISTRY as SECTIONS, default_order
    from ..viz.charts import default_exhibits

    kpis = []
    for kpi in candidates_for(profile):
        kpis.append({
            "id": kpi.id, "name": kpi.name,
            "perspective": kpi.perspective.value, "tier": int(kpi.tier),
            "timing": kpi.timing.value, "unit": kpi.unit,
        })
    return {
        "kpis": kpis,
        "sections": [{"id": sid, "title": SECTIONS[sid].title}
                     for sid in default_order()],
        "exhibits": list(default_exhibits()),
        "detectors": list(DETECTOR_NAMES),
        "artifacts": list(ALL_ARTIFACTS),
    }


def _valid_ids(cat: Dict[str, Any]) -> Dict[str, set]:
    return {
        "kpi": {k["id"] for k in cat["kpis"]},
        "section": {s["id"] for s in cat["sections"]},
        "exhibit": set(cat["exhibits"]),
        "detector": set(cat["detectors"]),
        "artifact": set(cat["artifacts"]),
    }


# Which catalog each spec path draws its ids from. A path not listed here is
# checked only by pydantic, which is right for a target value or a boolean —
# there is no registry of legal numbers.
_ID_SOURCE = {
    "metrics.pinned": "kpi",
    "metrics.excluded": "kpi",
    "metrics.overrides": "kpi",
    "design.sections": "section",
    "design.exhibits": "exhibit",
    "design.exhibit_widths": "exhibit",
    "analysis.detectors": "detector",
    "analysis.disabled": "detector",
    "outputs.artifacts": "artifact",
}


# --------------------------------------------------------------------------
# Changes
# --------------------------------------------------------------------------

@dataclass
class Change:
    """One reviewable hunk: what moves, to what, and why."""
    path: str
    value: Any
    rationale: str = ""
    before: Any = None
    # Set when the change was refused. It is still returned, so the studio can
    # show what the planner wanted and why it was not offered — a silently
    # dropped suggestion looks identical to one that was never made.
    rejected: str = ""

    @property
    def ok(self) -> bool:
        return not self.rejected

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "value": self.value, "before": self.before,
                "rationale": self.rationale, "rejected": self.rejected,
                "ok": self.ok}


@dataclass
class Plan:
    changes: List[Change] = field(default_factory=list)
    summary: str = ""
    meter: Optional[Meter] = None

    @property
    def accepted(self) -> List[Change]:
        return [c for c in self.changes if c.ok]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "changes": [c.as_dict() for c in self.changes],
            "usage": self.meter.summary() if self.meter else None,
        }


def _dig(document: Dict[str, Any], parts: Sequence[str]) -> Any:
    node: Any = document
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _place(document: Dict[str, Any], parts: Sequence[str], value: Any) -> None:
    node = document
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _check_ids(path: str, value: Any, valid: Dict[str, set]) -> str:
    """Whether every id this change names actually exists.

    Matched on the longest prefix, so `metrics.overrides.cac_payback.target`
    is checked against `metrics.overrides` and its KPI id is verified even
    though the leaf is a number.
    """
    for prefix, kind in _ID_SOURCE.items():
        if path != prefix and not path.startswith(prefix + "."):
            continue
        known = valid[kind]
        if path == prefix:
            named = list(value) if isinstance(value, (list, dict)) else [value]
        else:
            named = [path[len(prefix) + 1:].split(".")[0]]
        unknown = [str(n) for n in named if str(n) not in known]
        if unknown:
            return (f"names {kind}(s) that do not exist: "
                    f"{', '.join(sorted(unknown))}")
        return ""
    return ""


def validate(changes: Sequence[Change], spec: RunSpec,
             cat: Dict[str, Any]) -> Tuple[List[Change], Optional[RunSpec]]:
    """Mark each change legal or not, then check that the survivors compose.

    Two passes on purpose. Per-change validation gives the user a reason next
    to the specific thing that was refused; the whole-spec parse at the end
    catches the case where each change is individually fine and the
    combination is not.
    """
    valid = _valid_ids(cat)
    document = spec.model_dump(mode="json")

    for change in changes:
        parts = [p for p in change.path.split(".") if p]
        if not parts:
            change.rejected = "empty path"
            continue
        if parts[0] not in PATCHABLE_SECTIONS:
            change.rejected = (
                f"`{parts[0]}` is not a section the planner may change"
                + (" — the profile describes who the company is, and changing "
                   "it would change the numbers" if parts[0] == "profile" else "")
            )
            continue
        problem = _check_ids(change.path, change.value, valid)
        if problem:
            change.rejected = problem
            continue
        change.before = _dig(document, parts)

    merged = copy.deepcopy(document)
    for change in changes:
        if change.ok:
            _place(merged, [p for p in change.path.split(".") if p], change.value)

    try:
        return list(changes), RunSpec(**merged)
    except Exception as exc:                     # noqa: BLE001 — pydantic
        # The combination does not compose. Rather than guess which change is
        # at fault, refuse the lot and say so: a partially applied patch that
        # nobody chose is worse than no patch.
        for change in changes:
            if change.ok:
                change.rejected = f"the patch does not produce a valid spec: {exc}"
        return list(changes), None


def apply_changes(spec: RunSpec, changes: Sequence[Change]) -> RunSpec:
    """The spec with these changes in it. Validated, so it is runnable or it raises."""
    document = spec.model_dump(mode="json")
    for change in changes:
        _place(document, [p for p in change.path.split(".") if p], change.value)
    return RunSpec(**document)


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "value_json": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["path", "value_json", "rationale"],
            },
        },
    },
    "required": ["summary", "changes"],
}


def build_request(spec: RunSpec, cat: Dict[str, Any]) -> Dict[str, str]:
    """The system and user text, separated so it can be priced before it is sent."""
    profile = spec.profile
    current = spec.model_dump(mode="json")
    current.pop("profile", None)              # shown separately, and unpatchable

    kpi_lines = "\n".join(
        f"- `{k['id']}` {k['name']} ({k['perspective']}, tier {k['tier']}, "
        f"{k['timing']})" for k in cat["kpis"])
    section_lines = "\n".join(f"- `{s['id']}` {s['title']}"
                              for s in cat["sections"])

    user = f"""# The company

{profile.identity.name} · {profile.business_model.type.value} · \
{profile.business_model.customer_type.value} · {profile.identity.country}
Stage: {profile.size.stage.value} · headcount {profile.size.headcount_total} · \
revenue band {profile.size.revenue_band} · {profile.size.ownership.value}
Audience: {profile.intent.audience.value}
Primary objective: {profile.intent.primary_objective.value.replace('_', ' ')} \
over {profile.intent.horizon_months} months
Secondary objectives: {', '.join(
    o.value.replace('_', ' ') for o in profile.intent.secondary) or 'none'}
Data the company says it does not have: {
    ', '.join(profile.data_availability.missing) or 'nothing declared missing'}

# Current configuration

```json
{json.dumps(current, indent=2, sort_keys=True)}
```

# Catalog

## KPIs available to this company
{kpi_lines}

## Report sections, in their current order
{section_lines}

## Exhibits
{', '.join(f'`{e}`' for e in cat['exhibits'])}

## Detectors
{', '.join(f'`{d}`' for d in cat['detectors'])}

## Artifacts
{', '.join(f'`{a}`' for a in cat['artifacts'])}

# Your task

Propose at most {MAX_CHANGES} changes. Give each one a dotted `path` into the \
configuration above, the new value as a JSON string in `value_json` (so \
`["a","b"]` for a list, `0.85` for a number, `"dark"` for a string), and a \
`rationale` the reviewer can disagree with.

An empty list is a valid and sometimes correct answer.
"""
    return {"system": _prompt("planner"), "user": user}


def _parse_changes(payload: Dict[str, Any]) -> List[Change]:
    out: List[Change] = []
    for entry in (payload.get("changes") or [])[:MAX_CHANGES]:
        raw = entry.get("value_json", "")
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            # A value the model could not express as JSON is not a value we can
            # put in a spec. Kept and marked so the reviewer sees the intent.
            out.append(Change(path=str(entry.get("path", "")), value=raw,
                              rationale=str(entry.get("rationale", "")),
                              rejected=f"value is not valid JSON: {raw!r}"))
            continue
        out.append(Change(path=str(entry.get("path", "")), value=value,
                          rationale=str(entry.get("rationale", ""))))
    return out


def propose(spec: RunSpec, *, meter: Optional[Meter] = None,
            client: Optional[Any] = None) -> Plan:
    """Ask for a patch. Never apply one.

    The returned `Plan` carries rejected changes as well as accepted ones,
    because "the planner wanted to exclude a KPI that does not exist" is
    information about the planner, and hiding it would make the feature look
    better than it is.
    """
    ai = spec.ai
    meter = meter or Meter()
    cat = catalog(spec.profile)
    request = build_request(spec, cat)
    client = client or build_client(ai.model)

    try:
        payload = client.json(system=request["system"], user=request["user"],
                              schema=SCHEMA, purpose="plan")
    except AIUnavailable as exc:
        meter.absorb(client)
        meter.note(f"No plan was produced: {exc}")
        return Plan(changes=[], summary="", meter=meter)
    meter.absorb(client)

    changes, _ = validate(_parse_changes(payload), spec, cat)
    return Plan(changes=changes, summary=str(payload.get("summary", "")),
                meter=meter)
