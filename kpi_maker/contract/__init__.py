"""The fact-table contract: what a valid dataset looks like, whatever produced it.

Everything downstream of the `model` stage — metrics, detectors, charts, every
renderer — was written against data the generator produced. This package states
what that data actually guarantees, so real uploaded data can be held to the
same bar, and so the guarantees stop being private to `datagen/saas.py`.
"""
from .gate import (GateResult, ReconciliationError,  # noqa: F401
                   UncheckedArchetype, run_gate)
from .identities import CHECKS, Tier                          # noqa: F401
from .schemas import (FACT_SCHEMAS, SCHEMAS_BY_ARCHETYPE,     # noqa: F401
                      UNIVERSAL_SCHEMAS, schemas_for, validate_schemas)

__all__ = [
    "CHECKS", "FACT_SCHEMAS", "GateResult", "ReconciliationError",
    "SCHEMAS_BY_ARCHETYPE", "Tier", "UNIVERSAL_SCHEMAS", "UncheckedArchetype",
    "run_gate", "schemas_for", "validate_schemas",
]
