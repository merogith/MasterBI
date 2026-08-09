"""User-facing formula language.

Two scopes, one grammar:

  * **series** — a KPI. Evaluates to a monthly pd.Series on the financial
    calendar, exactly what a hand-written metric in `metrics/engine.py`
    returns, so a formula KPI is indistinguishable downstream.
  * **row** — a calculated column. Evaluates to a pd.Series on one table's row
    index and is appended to that table.

Nothing here ever reaches `eval()`. See `sandbox.py`.
"""
from .errors import FormulaError                      # noqa: F401
from .evaluate import Scope, evaluate, validate       # noqa: F401
from .functions import FUNCTIONS, describe_functions  # noqa: F401
from .introspect import references                    # noqa: F401

__all__ = [
    "FormulaError", "FUNCTIONS", "Scope", "describe_functions", "evaluate",
    "references", "validate",
]
