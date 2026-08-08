"""Synthetic data generators, one per business archetype.

Importing this package registers every shipped generator, so `GENERATORS` is
populated by the time anything asks. A new sector is a new module plus one
line here — the pipeline does not change.
"""
from .base import (GENERATORS, Anomaly, GeneratedData,  # noqa: F401
                   available, generator)
from . import subscription                              # noqa: F401  (registers "saas")

__all__ = ["GENERATORS", "Anomaly", "GeneratedData", "available", "generator"]
