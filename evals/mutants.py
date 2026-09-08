"""Adversarial cases derived from a real run, rather than typed by hand.

Hand-authored cases have two problems this repo has hit before. They quote
numbers somebody typed, so they can only test what that person thought of; and
they go stale silently when a formatter changes, because nothing connects the
literal `$12.0M` in a JSON file to the run that used to produce it.

So the bulk of the corpus is **derived from the figures the sample really
produces**. Each mutation is one named way a model gets a number wrong, applied
to a figure taken straight out of the facts table:

  * **honest** — the figure verbatim. The control, and not a formality: a gate
    that refuses everything would score 100% on the adversarial half alone.
  * **digit** — one digit changed. The commonest hallucination and the hardest
    to see by eye.
  * **magnitude** — `$12.0M` as `$12.0K`. The value is right and the claim is
    off by a thousand.
  * **unit** — `55.3%` as `55.3`. `verify.py`'s docstring calls this out
    explicitly: "`34.5` and `34.5%` and `34.5M` are three different claims
    about the business."
  * **precision** — `55.3%` as `55.34%`. A number the table nearly contains,
    which is what a model does when it re-derives instead of copying.

The `honest` half is what makes the score meaningful, and it is also the half
most likely to expose a *false positive* in the gate — a legitimate figure
refused because the extractor mis-parses it. That has happened here: 4.4's
honesty guard matched `sec` inside "technology-sector" on its first run.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

MAX_FIGURES = 14

#: Deliberately not `1 -> 2` for every digit: a mutation that lands on another
#: real figure in the same table is a case that fails for the wrong reason.
#: Rotating the last significant digit by five keeps the shape and moves it
#: well away from neighbouring values.
def _bend(token: str) -> Optional[str]:
    digits = [i for i, ch in enumerate(token) if ch.isdigit()]
    if not digits:
        return None
    at = digits[-1]
    return token[:at] + str((int(token[at]) + 5) % 10) + token[at + 1:]


def _magnitude(token: str) -> Optional[str]:
    for a, b in (("M", "K"), ("K", "M"), ("B", "M"), ("bn", "M")):
        if token.endswith(a):
            return token[: -len(a)] + b
    return None


def _unit(token: str) -> Optional[str]:
    return token[:-1] if token.endswith("%") else None


def _precision(token: str) -> Optional[str]:
    match = re.search(r"(\d)\.(\d+)", token)
    if not match:
        return None
    return token.replace(match.group(0), f"{match.group(1)}.{match.group(2)}4", 1)


MUTATIONS = {
    "digit": _bend,
    "magnitude": _magnitude,
    "unit": _unit,
    "precision": _precision,
}


def figures(sample: str) -> List[str]:
    """The formatted figures this run actually put in front of the narrator.

    Read from `facts_block`, which is what `build_request` sends, so the corpus
    quotes exactly what the model is told it may quote. Taking them from the
    `MetricResult`s instead would test a view nobody is given.
    """
    from evals.harness import fixture
    from kpi_maker.ai.narrator import facts_block

    fix = fixture(sample)
    block = facts_block(fix["results"], fix["profile"].identity.currency)

    # **Skip the header row.** Its first version did not, and the `honest` half
    # immediately caught it: the column is called "12mo ago", so `12mo` was
    # scraped as though it were a figure and the gate rightly refused it. The
    # control cases exist to expose a false positive in the *gate*, and the
    # first one they exposed was in the corpus that generates them — which is
    # the better outcome, since a generator that mines headers would have
    # produced adversarial cases about nothing.
    body = block.splitlines()[1:]

    seen, out = set(), []
    # The formatted column is the one the prompt tells the model to copy.
    for token in re.findall(r"[^\s|]*\d[^\s|]*", "\n".join(body)):
        token = token.strip(",;()")
        if token in seen or not any(ch.isdigit() for ch in token):
            continue
        # A bare year or a row index is not a measurement, and mutating one
        # produces a case about nothing.
        if re.fullmatch(r"\d{1,2}", token) or re.fullmatch(r"20\d\d", token):
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= MAX_FIGURES:
            break
    return out


def derive(suite: Optional[str] = None,
           sample: str = "northwind_saas") -> List[Dict[str, Any]]:
    """One honest case and up to four adversarial ones per figure.

    **A mutant is only emitted once it is checked to be absent from the allowed
    set.** Without that, a case asserting "this must be refused" can be wrong
    on its own terms: stripping the `%` from `75.0%` produces `75.0`, which the
    gate legitimately admits because a *finding* quotes it, and the corpus
    would have been demanding a false positive.

    That happened twice while this was being written — first through the bare
    percentage `_render` used to add, then through findings — which is the
    argument for checking rather than for another heuristic. The digit
    mutation's rotate-by-five below is now a nicety that keeps mutants far from
    their neighbours; this check is what actually makes them valid.
    """
    if suite not in (None, "narrator"):
        return []

    from evals.harness import fixture
    from kpi_maker.ai import verify

    allowed = fixture(sample)["allowed"]

    def is_absent(token: str) -> bool:
        parsed = verify._parse(token)
        return parsed is not None and parsed not in allowed

    cases: List[Dict[str, Any]] = []
    for token in figures(sample):
        cases.append({
            "id": f"honest/{token}",
            "suite": "narrator", "sample": sample,
            "why": "the figure exactly as the facts table formats it",
            "output": {"exec_summary": [
                f"Performance this period is summarised by {token}."]},
            "expect": {"verdict": "clean"},
        })
        for name, mutate in MUTATIONS.items():
            bad = mutate(token)
            if bad is None or bad == token or not is_absent(bad):
                continue
            cases.append({
                "id": f"{name}/{token}->{bad}",
                "suite": "narrator", "sample": sample,
                "why": f"{name}: a figure the table does not contain",
                "output": {"exec_summary": [
                    f"Performance this period is summarised by {bad}."]},
                "expect": {"verdict": "refused", "tokens": [bad]},
            })
    return cases
