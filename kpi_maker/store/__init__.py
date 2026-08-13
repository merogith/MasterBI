"""Durable index for runs.

The filesystem stays the artifact store — `runs/<id>/` holds everything a run
produced, and stays portable. This is the index over it: what a run's mode was,
when it started, whether it finished, and if not, why not.
"""

from .runs import COLUMNS, Store, reset_cache, store

__all__ = ["COLUMNS", "Store", "reset_cache", "store"]
