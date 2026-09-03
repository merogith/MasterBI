"""Checks that make ~20 KPI packs affordable instead of ~20 hand-audits.

Phase 4 plans roughly six hundred record sheets. Reviewing those by reading
them is not a plan, and the failures that matter are not ones a reader catches:
a `driver_parent` pointing at an id that no longer exists, an `applies_when`
that stopped parsing, a `kind: builtin` with nothing registered to compute it —
each of those is invisible in a diff and shows up as a quietly missing row.

Two levels, because they answer different questions:

* **`validate`** — is this one record sheet coherent? Its expression parses,
  its parent resolves, something computes it, its benchmark cites a source.
* **`lint`** — is this *pack* a scorecard's worth of candidates? Enough per
  perspective to choose from, enough leading indicators to be a tracker rather
  than a post-mortem, no duplicate ids in a group that loads together.

**Three scoping decisions, each taken because measuring the shipped packs
showed the obvious rule was wrong.**

*The unit is a load group, not a file.* `saas_standard` points nine
`driver_parent`s at ids in `saas`, which is correct for a pack that supplements
another. Per-file it reads as nine dangling parents; per group, as zero. The
groups come from `resolve_packs`, so they are the combinations a real profile
loads — and the first version of this got that wrong in the other direction, by
adding `general` to every group on the assumption that it is always loaded
alongside a sector pack. It is not. A group that never occurs is worse than no
lint: it would pass a cross-pack parent that breaks in every real run.

*The Balanced Scorecard maximum is a scorecard rule, not a pack rule.* The plan
asked for "min 2 / max 7 per perspective" here; applied to packs it fails three
of the four shipped ones — `saas` has fourteen financial candidates. A pack is
a menu and a scorecard is the meal: `kpi/selection.py` picks about 25 of 44 and
is where the maximum belongs. What a pack owes is a *minimum* — enough
candidates that selection has a choice.

*Placeholder benchmarks are counted, not gated.* Sixty of sixty-seven cite
"Illustrative composite", which 4.4 replaces with Damodaran, Eurostat and SEC
frames. Gating on it today would fail every pack over work that is scheduled,
which teaches an author to ignore the linter.
"""
from .lint import (
                   LEVELS,
                   Finding,
                   Report,
                   lint_all,
                   lint_group,
                   warnings_a_user_cannot_act_on,
)
from .validate import validate_sheet

__all__ = ["Finding", "Report", "LEVELS", "lint_all", "lint_group",
           "validate_sheet", "warnings_a_user_cannot_act_on"]
