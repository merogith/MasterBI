"""The Anthropic client, wrapped thinly and imported lazily.

Three things this file exists to get right:

**The import is lazy.** `anthropic` lives in `requirements-ai.txt`, not in
`requirements.txt`, and is imported inside the function that needs it. A
machine without the package runs the entire pipeline unchanged; the only thing
it cannot do is turn `spec.ai.enabled` on. Importing at module scope would make
an optional dependency mandatory the moment anything touched `kpi_maker.ai`,
and `spec/schema.py` touches it by way of the studio.

**The failure is named.** A missing key and a missing package are the two most
likely ways this goes wrong on someone else's machine, and both produce
`AIUnavailable` with a sentence saying what to do — not an ImportError from
four frames down, and not a silent fall-through that leaves the user wondering
why their report has no prose.

**There is a seam for tests.** `CLIENT_FACTORY` is the single place a client is
constructed, so `tests/ai.py` swaps in a transcript player and the whole suite
runs offline. It is also what lets the no-regression test assert something
stronger than "no tokens were spent": with `ai.enabled` false the factory is
never called at all.

Model parameters follow the Opus 5 contract: adaptive thinking, structured
output via `output_config.format`, and none of `temperature`, `top_p`,
`top_k` or `thinking.budget_tokens`, all of which are 400s on this model.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Streaming is not about showing progress here — nothing watches the stream.
# It is about not tripping the request timeout: a narrator writing five
# sections of prose with adaptive thinking in front of it is exactly the
# long-output shape non-streaming requests fall over on.
MAX_OUTPUT_TOKENS = 8_000

# Prose and a spec patch are both judgement calls over a compact input, which
# is what this setting is for. It is not a knob the user sees; a run that wants
# to spend less should use a smaller model, which `AISpec.model` allows.
EFFORT = "high"


class AIUnavailable(RuntimeError):
    """The AI layer was asked to run and cannot.

    Always carries a sentence the user can act on. Every caller treats this as
    a fallback signal rather than a crash: the pipeline must still produce a
    deliverable.
    """


@dataclass
class Usage:
    """What one call cost, in the API's own units."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens,
                     self.cache_read_tokens + other.cache_read_tokens)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens


@dataclass
class Call:
    """One request and what came back, as the meter records it."""
    purpose: str
    model: str
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    seconds: float = 0.0


class Refused(AIUnavailable):
    """`stop_reason: "refusal"` — checked before `content` is ever read."""


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def availability() -> Dict[str, Any]:
    """Whether the AI layer could run, and if not, why.

    Answered without constructing anything, because the studio asks on every
    page load and a run that never enables AI should not pay for the question.
    """
    key = bool(os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    try:
        import anthropic  # noqa: F401
        package = True
    except ImportError:
        package = False

    if not package:
        reason = ("the `anthropic` package is not installed — "
                  "pip install -r requirements-ai.txt")
    elif not key:
        reason = ("no ANTHROPIC_API_KEY in the environment — "
                  "export one and restart the server")
    else:
        reason = ""
    return {"available": package and key, "package": package, "key": key,
            "reason": reason}


def require() -> None:
    state = availability()
    if not state["available"]:
        raise AIUnavailable(state["reason"])


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class Client:
    """Two operations, because two is all the pipeline needs.

    `json` asks for a schema-constrained object and `count` prices a request
    before it is sent. Anything richer belongs in the agent module that wants
    it, not in a wrapper that has to be understood by everyone.
    """

    def __init__(self, model: str):
        import anthropic
        self.model = model
        self._api = anthropic.Anthropic()
        self.calls: List[Call] = []

    # -- the one request shape this codebase makes -------------------------

    def json(self, *, system: str, user: str, schema: Dict[str, Any],
             purpose: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> Dict[str, Any]:
        """A schema-constrained object, streamed.

        `output_config.format` rather than an assistant-turn prefill: prefills
        are a 400 on this model, and a compiled schema is a stronger guarantee
        than "please reply with JSON" plus a regex to fish it back out.
        """
        import time
        import anthropic

        started = time.perf_counter()
        try:
            with self._api.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": EFFORT,
                    "format": {"type": "json_schema", "schema": schema},
                },
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as exc:
            # Everything the SDK can raise arrives here as one named failure,
            # because every caller does the same thing with it: fall back to
            # the deterministic path and say so.
            raise AIUnavailable(f"the API call failed: {exc}") from exc

        usage = Usage(
            input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        )
        self.calls.append(Call(purpose=purpose, model=self.model, usage=usage,
                               stop_reason=message.stop_reason or "",
                               seconds=round(time.perf_counter() - started, 2)))

        # Checked BEFORE `content` is read. On a refusal the content blocks do
        # not honour the schema, so parsing first would produce a confusing
        # JSON error in place of the real reason.
        if message.stop_reason == "refusal":
            detail = getattr(message, "stop_details", None)
            raise Refused("the model declined to answer"
                          + (f": {detail.explanation}" if detail else ""))
        if message.stop_reason == "max_tokens":
            raise AIUnavailable(
                f"the response hit the {max_tokens}-token ceiling and is "
                f"incomplete")

        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIUnavailable(f"the response was not valid JSON: {exc}") from exc

    def count(self, *, system: str, user: str) -> int:
        """Input tokens for a request, before spending anything on it.

        This is what lets the studio show a cost before the user commits, which
        is the difference between an opt-in and a surprise.
        """
        import anthropic
        try:
            counted = self._api.messages.count_tokens(
                model=self.model, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            raise AIUnavailable(f"could not count tokens: {exc}") from exc
        return int(counted.input_tokens)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total


def _default_factory(model: str) -> Client:
    require()
    return Client(model)


# The single construction point, swapped wholesale by `tests/ai.py`. A module
# global rather than a parameter threaded through five call sites: the agents
# should not each carry a hook that exists only for testing.
CLIENT_FACTORY: Callable[[str], Any] = _default_factory


def build_client(model: str) -> Any:
    return CLIENT_FACTORY(model)


def use_factory(factory: Optional[Callable[[str], Any]]) -> Callable[[str], Any]:
    """Swap the factory, returning the previous one so a test can restore it."""
    global CLIENT_FACTORY
    previous = CLIENT_FACTORY
    CLIENT_FACTORY = factory or _default_factory
    return previous
