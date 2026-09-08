"""Deterministic scorers. Every one of them is arithmetic or set membership.

The plan's rule is "deterministic scorers wherever possible; LLM-as-judge only
for prose quality, never as a correctness gate". Here it is *everywhere*
possible, because everything worth gating in this layer is decidable:

  * a number is in the facts table or it is not;
  * a spec path is patchable or it is not;
  * a phrase the prompt forbids is present or it is not.

`verify.py` makes the same argument one level down — "ROADMAP M7 gives this job
to a Sonnet critic. It should not be a model... a critic that can hallucinate
is not a critic." A scorer that can hallucinate is not a scorer either.

**Nothing here restates a rule that lives somewhere else.** The number gate is
`verify.check_sections`, the patch guard is `planner.validate`, and the banned
phrases are parsed out of `prompts/narrator.md`. A scorer holding its own copy
of any of them would drift from the thing it grades, and would then either pass
a real regression or fail a legitimate change — which is worse than no scorer,
the same way 4.3a argued a linter with a false positive teaches the author to
ignore it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

from kpi_maker.ai import planner, verify

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "kpi_maker" / "ai" / "prompts"


# --------------------------------------------------------------------------
# Banned phrases, read from the instruction that bans them
# --------------------------------------------------------------------------

def banned_phrases() -> List[str]:
    """The phrases `narrator.md` tells the model never to write.

    Parsed rather than copied. The prompt's "What never to write" section
    already lists them — "In conclusion", "it is worth noting", "delve",
    "landscape", "leverage" as a verb — and a second list here would be a
    second place to update, which is this repo's characteristic bug. Adding a
    word to the prompt starts checking it; removing one stops.

    Only the quoted items are taken. The surrounding bullets are prohibitions
    on *kinds* of statement ("Praise, reassurance", "any claim about
    causation") which no string match can decide, and pretending otherwise
    would be a scorer that fires on the word "improve".
    """
    text = (PROMPTS / "narrator.md").read_text(encoding="utf-8")
    section = text.split("## What never to write", 1)
    if len(section) < 2:
        raise ValueError(
            "narrator.md has no 'What never to write' section — the eval reads "
            "its banned phrases from there, so the prompt and the scorer have "
            "drifted apart")
    body = section[1].split("\n## ", 1)[0]
    return [m.group(1).lower() for m in re.finditer(r'"([^"]+)"', body)]


def banned_hits(paragraphs: Sequence[str]) -> List[str]:
    """Which banned phrases appear, in order of the prompt's own list.

    Word-boundary matched. 4.4's honesty guard grew a false positive on its
    first run by matching `sec` inside "technology-**sec**tor"; "delve" inside
    "delved" is a real hit and "landscape" inside "landscaped" is not the same
    word, so the boundary is the honest rule.
    """
    joined = "\n".join(paragraphs).lower()
    return [phrase for phrase in banned_phrases()
            if re.search(rf"\b{re.escape(phrase)}", joined)]


# --------------------------------------------------------------------------
# The narrator: does the number gate catch what it should?
# --------------------------------------------------------------------------

def score_narrator(case: Dict[str, Any], allowed: Set[Any]) -> Dict[str, Any]:
    """Grade one narrator case against the real gate.

    `expect.verdict` is "clean" or "refused". Refused cases may also name the
    exact tokens they expect to be caught, which is what stops a case from
    passing because the gate objected to *something else* — a real risk when
    prose carries several figures and only one of them was mutated.
    """
    sections: Dict[str, List[str]] = case["output"]
    found = verify.check_sections(sections, allowed)
    caught = [v.token for section in found.values() for v in section]

    expected = case["expect"]
    verdict = "refused" if caught else "clean"
    ok = verdict == expected["verdict"]
    detail = ""

    if ok and expected.get("tokens"):
        # Compared as sets: the gate reports every offending token and the
        # case names the ones it planted, so a superset is fine and a miss is
        # not.
        missing = [t for t in expected["tokens"] if t not in caught]
        if missing:
            ok = False
            detail = (f"planted {missing} and the gate reported {caught}, so "
                      f"it refused the paragraph for the wrong reason")

    if not ok and not detail:
        detail = f"expected {expected['verdict']}, got {verdict} ({caught})"

    banned = banned_hits([p for ps in sections.values() for p in ps])
    if expected.get("banned") is not None:
        if sorted(banned) != sorted(expected["banned"]):
            ok = False
            detail = (detail + "; " if detail else "") + \
                f"banned phrases {banned} != expected {expected['banned']}"

    return {"ok": ok, "detail": detail, "caught": caught, "banned": banned}


# --------------------------------------------------------------------------
# The planner: does the patch guard hold?
# --------------------------------------------------------------------------

def score_planner(case: Dict[str, Any], spec: Any,
                  cat: Dict[str, Any]) -> Dict[str, Any]:
    """Grade one planner case against `planner.validate`.

    Three properties the brief names, all decidable:

      * **path legality** — `profile` and anything outside `PATCHABLE_SECTIONS`
        must be refused, and the refusal must say so;
      * **patch validity** — a set that does not compose into a `RunSpec` is
        refused whole rather than half-applied;
      * **no-op rate** — a change whose value is already the value. Not
        illegal, and not a bug in the guard, but a planner that proposes them
        wastes the reviewer's attention, so it is *measured* rather than
        failed. A rate the corpus reports is the honest treatment of something
        that is bad in quantity rather than in kind.
    """
    changes = [planner.Change(path=c["path"], value=c["value"])
               for c in case["changes"]]
    graded, merged = planner.validate(changes, spec, cat)

    accepted = [c.path for c in graded if c.ok]
    refused = {c.path: (c.rejected or "") for c in graded if not c.ok}
    no_ops = [c.path for c in graded if c.ok and c.before == c.value]

    expected = case["expect"]
    ok = True
    detail = ""

    if "accepted" in expected and sorted(accepted) != sorted(expected["accepted"]):
        ok, detail = False, f"accepted {accepted} != expected {expected['accepted']}"
    if "refused" in expected and sorted(refused) != sorted(expected["refused"]):
        ok = False
        detail = (detail + "; " if detail else "") + \
            f"refused {sorted(refused)} != expected {sorted(expected['refused'])}"

    # The reason is part of the contract: 6.2 renders it beside the change, and
    # "not a section the planner may change" is what tells a user why the
    # profile is off limits rather than leaving it looking like a bug.
    for path, fragment in (expected.get("because") or {}).items():
        if fragment.lower() not in refused.get(path, "").lower():
            ok = False
            detail = (detail + "; " if detail else "") + \
                f"{path} was refused with {refused.get(path)!r}, expected to " \
                f"mention {fragment!r}"

    if expected.get("composes") is False and merged is not None:
        ok = False
        detail = (detail + "; " if detail else "") + \
            "the patch composed into a valid spec and should not have"
    if expected.get("composes") is True and merged is None:
        ok = False
        detail = (detail + "; " if detail else "") + \
            "the patch did not compose and should have"

    return {"ok": ok, "detail": detail, "accepted": accepted,
            "refused": sorted(refused), "no_ops": no_ops}
