"""Compatibility shim. The subscription generator lives in `subscription.py`.

Renamed in P4, when the sector-agnostic half moved to `base.py` and the file
stopped being "the generator" and became "one of them". The name survives here
because two callers import from it — `contract/identities.py` wants
`calibration_tolerance` for its customer-count check, and `tests/stress.py`
wants both — and breaking those to rename a file would be a poor trade.
"""
from __future__ import annotations

from .base import Anomaly, GeneratedData, calibration_tolerance  # noqa: F401
from .subscription import generate, reconcile                    # noqa: F401
from ..contract.gate import ReconciliationError                  # noqa: F401

__all__ = ["Anomaly", "GeneratedData", "ReconciliationError",
           "calibration_tolerance", "generate", "reconcile"]
