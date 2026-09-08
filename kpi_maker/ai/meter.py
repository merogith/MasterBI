"""Token accounting, written beside the run like every other input.

ROADMAP M7 asks for "token metering per run, surfaced to the user before they
commit". Two halves, and the second is the one that matters: an estimate after
the fact is a receipt, and a receipt is not a decision.

So the meter does both. `estimate()` prices a request through
`count_tokens` before anything is sent, and the studio shows that number next
to the enable switch. `record()` accumulates what was actually spent and
`write()` puts it in `runs/<id>/ai.json`, next to `profile.json` and
`kpi_set.json` — the reproducibility inputs. What the AI cost is part of how
the report was made.

Prices are per million tokens and deliberately hardcoded rather than fetched.
A number that silently changes under a cost estimate is worse than one that is
visibly stale, and this is a local tool showing an order of magnitude, not an
invoice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .client import Call, Usage

# USD per million tokens, input / output. Cache reads are billed at roughly a
# tenth of input, which matters here because the narrator's facts block is
# identical across a retry.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_READ_DISCOUNT = 0.10

# What a model is assumed to cost when it is not in the table above. Priced at
# the most expensive thing we ship rather than at zero, so an unknown model
# over-states rather than under-states.
FALLBACK_PRICE = (5.00, 25.00)


def cost_usd(usage: Usage, model: str) -> float:
    inp, out = PRICES.get(model, FALLBACK_PRICE)
    return round(
        (usage.input_tokens * inp
         + usage.cache_read_tokens * inp * CACHE_READ_DISCOUNT
         + usage.output_tokens * out) / 1_000_000.0,
        4,
    )


@dataclass
class Meter:
    """Everything the AI layer did during one run, and what it may still do.

    **The ceiling was declared and enforced by nothing.** `AISpec` has carried
    `max_tokens_per_run` since the AI layer was written, defaulting to 150,000,
    with a docstring saying it is "checked against the pre-flight estimate
    before the first call rather than discovered afterwards". Measured rather
    than read: with the ceiling set to its floor of 1,000, a narrate spent
    1,200 and nothing raised, noted or stopped. A dead spec field of the kind
    0.3 was spent removing, in the one module whose subject is spending.

    6.2b wires it, because the goal box that item adds is what makes it matter:
    until then every request was assembled from the run's own data and was
    therefore bounded by it. A free-text field is not.
    """
    model: str = ""
    #: 0 means no ceiling — the CLI and the tests construct meters that are not
    #: gating anything, and a default of 150,000 here would silently apply a
    #: policy nobody asked for.
    ceiling: int = 0
    calls: List[Call] = field(default_factory=list)
    # Sentences about what fell back and why — a dropped narrative, a refused
    # patch, a missing key. These reach the methodology appendix, because a
    # report that quietly has less in it than the user asked for is the kind of
    # thing they should hear from us rather than notice.
    notes: List[str] = field(default_factory=list)

    def record(self, call: Call) -> None:
        self.calls.append(call)
        if not self.model:
            self.model = call.model

    def absorb(self, client: Any) -> None:
        """Take the calls a client accumulated, then clear it.

        Clearing matters on the retry path: the narrator reuses one client
        across its attempts, and counting the first attempt twice would make
        the reported spend depend on how many times we looked.
        """
        for call in getattr(client, "calls", []):
            self.record(call)
        if hasattr(client, "calls"):
            client.calls = []

    def note(self, sentence: str) -> None:
        if sentence not in self.notes:
            self.notes.append(sentence)

    def refuses(self, purpose: str) -> str:
        """Why the next call may not be made, or "" if it may.

        **Checked on what has already been spent, so a run may cross the
        ceiling by at most one call.** That is a real limit and it is stated
        rather than papered over: bounding the overshoot to zero would mean
        counting each request before sending it, which is a second network
        round trip per call to enforce a number whose whole job is to stop a
        runaway. One call of slack costs a few thousand tokens; a `count_tokens`
        before every request costs latency on every run that is nowhere near
        the ceiling.

        The pre-flight half the field's own docstring described lives in
        `estimate()`, which prices the exact prompts and now says whether they
        clear the ceiling. Between the two, the estimate is what a user reads
        before committing and this is what binds when they do.
        """
        if self.ceiling <= 0 or self.spent < self.ceiling:
            return ""
        return (f"{purpose} was skipped: this run has spent {self.spent:,} "
                f"tokens against its ceiling of {self.ceiling:,}.")

    @property
    def usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    @property
    def spent(self) -> int:
        return self.usage.total

    def cost(self) -> float:
        return round(sum(cost_usd(c.usage, c.model) for c in self.calls), 4)

    def summary(self) -> Dict[str, Any]:
        usage = self.usage
        return {
            "model": self.model,
            "calls": len(self.calls),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "total_tokens": usage.total,
            "estimated_cost_usd": self.cost(),
            "notes": list(self.notes),
            "detail": [
                {"purpose": c.purpose, "model": c.model,
                 "input_tokens": c.usage.input_tokens,
                 "output_tokens": c.usage.output_tokens,
                 "stop_reason": c.stop_reason, "seconds": c.seconds}
                for c in self.calls
            ],
        }

    def write(self, out_dir: Path) -> Optional[Path]:
        """`ai.json`, but only when there is something to report.

        A run with AI off must leave no trace, or the no-regression gate sees a
        new file in the tree and is right to complain.
        """
        if not self.calls and not self.notes:
            return None
        path = Path(out_dir) / "ai.json"
        path.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")
        return path


def estimate(client: Any, requests: Dict[str, Dict[str, str]],
             model: str, ceiling: int = 0) -> Dict[str, Any]:
    """Price a set of prepared requests without sending them.

    Output tokens cannot be counted in advance, so they are assumed at the
    maximum — an estimate that surprises on the low side is the only kind worth
    showing before someone spends money.

    `ceiling` is `AISpec.max_tokens_per_run`, and comparing against it here is
    the "checked against the pre-flight estimate" half that field's docstring
    has always claimed. It is reported rather than raised: this endpoint exists
    so a user can decide before committing, and refusing to answer the question
    they asked in order to enforce a limit they can change is the wrong shape.
    `Meter.refuses` is what binds once they commit.
    """
    from .client import MAX_OUTPUT_TOKENS

    per: Dict[str, int] = {}
    for name, payload in requests.items():
        per[name] = client.count(system=payload["system"], user=payload["user"])

    input_total = sum(per.values())
    assumed = Usage(input_tokens=input_total,
                    output_tokens=MAX_OUTPUT_TOKENS * max(len(requests), 1))
    return {
        "input_tokens": input_total,
        "assumed_output_tokens": assumed.output_tokens,
        "worst_case_tokens": assumed.total,
        "worst_case_cost_usd": cost_usd(assumed, model),
        "per_request": per,
        "model": model,
        "ceiling": ceiling,
        # Against the worst case, not the input alone: a ceiling that only
        # counts what goes up is not a ceiling on what a run spends.
        "within_ceiling": ceiling <= 0 or assumed.total <= ceiling,
    }
