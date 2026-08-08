"""The fact-table contract: what a valid dataset looks like, whatever produced it.

Everything downstream of the `model` stage — metrics, detectors, charts, every
renderer — was written against data the generator produced. This package states
what that data actually guarantees, so real uploaded data can be held to the
same bar, and so the guarantees stop being private to `datagen/saas.py`.
"""
from .gate import GateResult, ReconciliationError, run_gate  # noqa: F401
from .identities import CHECKS, Tier                          # noqa: F401
from .schemas import FACT_SCHEMAS, validate_schemas           # noqa: F401

__all__ = [
    "CHECKS", "FACT_SCHEMAS", "GateResult", "ReconciliationError", "Tier",
    "run_gate", "validate_schemas",
]
