"""Optional OpenAI Responses adapter. No key or network needed with AI off."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Bounded synchronous requests; refusal and incomplete results never apply edits.
MAX_OUTPUT_TOKENS = 8_000

# Prose and a spec patch are both judgement calls over a compact input, which
# is what this setting is for. It is not a knob the user sees; a run that wants
# to spend less should use a smaller model, which `AISpec.model` allows.
EFFORT = "low"
DEFAULT_MODEL = "gpt-5.6-luna"
MODEL_TIERS = {"standard": DEFAULT_MODEL, "advanced": "gpt-6-astra"}


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

    def __add__(self, other: Usage) -> Usage:
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
    key = bool(os.environ.get("OPENAI_API_KEY"))
    try:
        import openai  # noqa: F401
        package = True
    except ImportError:
        package = False

    if not package:
        reason = ("the `openai` package is not installed — "
                  "pip install -r requirements-ai.txt")
    elif not key:
        reason = ("no OPENAI_API_KEY in the environment — "
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
        from openai import OpenAI
        if model not in MODEL_TIERS.values():
            raise AIUnavailable("Choose Standard or Advanced in Studio; this saved model is no longer supported.")
        self.model = model
        # No automatic paid retries; the user decides whether to try again.
        self._api = OpenAI(timeout=120.0, max_retries=0)
        self.calls: List[Call] = []

    def json(self, *, system: str, user: str, schema: Dict[str, Any],
             purpose: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> Dict[str, Any]:
        import time

        import openai

        from .meter import cost_usd

        # Conservative byte-based estimate, including the schema. This is a
        # local spending guard, not a promise about the provider's invoice.
        estimated = self.count(system=system, user=user + json.dumps(schema))
        limit = float(os.environ.get("MASTERBI_AI_MAX_CALL_USD", "0.50"))
        if cost_usd(Usage(input_tokens=estimated, output_tokens=max_tokens), self.model) > limit:
            raise AIUnavailable(f"This request exceeds the ${limit:.2f} per-call estimate limit. Use Standard or shorten the request.")
        started = time.perf_counter()
        try:
            response = self._api.responses.create(
                model=self.model, instructions=system, input=user,
                max_output_tokens=max_tokens, store=False,
                reasoning={"effort": EFFORT},
                text={"format": {"type": "json_schema", "name": "masterbi_response",
                                 "strict": True, "schema": schema}},
            )
        except openai.APIError as exc:
            # Do not echo provider request bodies or credentials to browsers.
            raise AIUnavailable(f"OpenAI request failed ({type(exc).__name__}). Check the server configuration and try again.") from exc
        raw = response.usage
        cached = getattr(getattr(raw, "input_tokens_details", None), "cached_tokens", 0) or 0
        usage = Usage(input_tokens=max(0, (getattr(raw, "input_tokens", 0) or 0) - cached),
                      output_tokens=getattr(raw, "output_tokens", 0) or 0,
                      cache_read_tokens=cached)
        self.calls.append(Call(purpose=purpose, model=self.model, usage=usage,
                               stop_reason=response.status,
                               seconds=round(time.perf_counter() - started, 2)))
        for item in response.output:
            for part in getattr(item, "content", []):
                if getattr(part, "type", "") == "refusal":
                    raise Refused("The model declined to answer. Try rephrasing your request.")
        if response.status != "completed":
            raise AIUnavailable("The OpenAI response was incomplete; no changes were applied.")
        try:
            result = json.loads(response.output_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIUnavailable("The response was not valid JSON; no changes were applied.") from exc
        if not isinstance(result, dict):
            raise AIUnavailable("The response was not an object; no changes were applied.")
        return result

    def count(self, *, system: str, user: str) -> int:
        """Conservative local estimate; makes no network request."""
        return len((system + user).encode("utf-8")) + 1024

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
