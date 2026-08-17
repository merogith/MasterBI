"""Pin the calendar for the whole suite.

Generated history used to end at a hardcoded December 2025. It now ends at the
last completed month, which is right for a user and wrong for a test suite: a
seasonal business's findings depend on where in the year its history stops, so
without a pin a green suite in January could be a red one in February, and the
failure would look like a code change nobody made.

Set here at import rather than in a fixture, because test modules build specs
and generate data at collection time, before any fixture has run.

`tests/test_history_end.py` unsets it deliberately — the default is the thing
that item exists to fix, so something has to exercise it.
"""
import os

from kpi_maker.datagen.base import HISTORY_END_ENV

#: The month the samples were authored against, so every number quoted in the
#: plan and in these tests' docstrings still refers to the same data.
PINNED_HISTORY_END = "2025-12"

os.environ.setdefault(HISTORY_END_ENV, PINNED_HISTORY_END)
