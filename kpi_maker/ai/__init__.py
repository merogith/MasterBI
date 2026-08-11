"""The one part of the pipeline that is not deterministic.

Everything else in `kpi_maker` computes: given the same inputs it produces the
same bytes, and the reconciliation gate refuses to render anything it cannot
verify. This package deliberately breaks that, in exactly two places and under
exactly two guards.

**The narrator** writes the connective prose that frames each report section.
It sees `facts_table()` — the compact, already-computed view built for this
purpose — and never a row of underlying data. Every number it writes is matched
against that table before the prose is allowed into a deliverable
(`verify.py`); prose that fails twice is dropped and the deterministic content
stands alone.

**The planner** proposes a patch to the `RunSpec`. It cannot apply one. The
patch is validated by the same pydantic schema the studio's PATCH endpoint
uses, it may not touch `profile`, and the user accepts or rejects it hunk by
hunk before anything runs.

Neither agent can move a number, and that is the property the whole design
protects. `spec.ai.enabled` is `False` by default, so a run that does not ask
for this never constructs a client and never spends a token.
"""
from .client import (
                     CLIENT_FACTORY,
                     AIUnavailable,
                     Call,
                     Usage,
                     availability,
                     build_client,
)
from .meter import Meter
from .verify import Violation, allowed_numbers, check_prose, extract_numbers

__all__ = ["AIUnavailable", "Call", "Usage", "availability", "build_client",
           "CLIENT_FACTORY", "Meter", "Violation", "allowed_numbers",
           "check_prose", "extract_numbers"]
