"""Synthetic data generators, one per business archetype.

Importing this package registers every shipped generator, so `GENERATORS` is
populated by the time anything asks. A new sector is a new module plus one
line here — the pipeline does not change.
"""
from . import (
                   ecommerce,  # noqa: F401  (registers "ecommerce")
                   project,  # noqa: F401  (registers "project")
                   subscription,  # noqa: F401  (registers "saas")
)
from .base import GENERATORS, Anomaly, GeneratedData, available, generator  # noqa: F401

__all__ = ["GENERATORS", "Anomaly", "GeneratedData", "available", "generator"]
