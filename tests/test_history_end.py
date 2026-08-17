"""Generated history ended in December 2025 whatever the date was.

`datagen/base.py` pinned `pd.Period("2025-12")` and `datagen/subscription.py`
repeated it, so a board pack produced in August 2026 opened on figures fifteen
months old and every chart's x-axis stopped in the same place forever. It was
listed in the plan under 0.2, fixed in neither of that item's commits, and not
named as deferred either.

It also parked every sample at the one calendar position where a fourth-quarter
peak sits at the very end of the series — which is precisely what hid the
seasonal artefact 3.4b fixes, so the two are the same fact seen twice.

**Resolved in the spec, not in the generator**, and that is the load-bearing
decision. A generator reading the clock would make a run's data a function of
*when it ran*, while the cache key is a function of the spec — so two runs with
identical specs, one in December and one in January, would be served from the
same cache and be silently different. `GeneratorParams.history_end` is filled
in at spec construction and written to `spec.json`, so a saved run reproduces
itself and 0.7's `spec_versions` records what each set of artifacts was as of.

This module unsets the suite's pin deliberately: `tests/conftest.py` fixes the
calendar for every other test, and the default is the thing this item exists to
fix, so something has to exercise it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.datagen import GENERATORS  # noqa: E402
from kpi_maker.datagen.base import (  # noqa: E402
    HISTORY_END_ENV,
    default_history_end,
    month_range,
)
from kpi_maker.spec.schema import GeneratorParams, RunSpec  # noqa: E402


@pytest.fixture
def unpinned(monkeypatch):
    monkeypatch.delenv(HISTORY_END_ENV, raising=False)


def test_history_ends_at_the_last_completed_month(unpinned):
    """Not this month: it is not over, and a partial month rendered beside
    twelve complete ones is a cliff at the right-hand edge of every chart."""
    expected = pd.Period(pd.Timestamp.utcnow(), freq="M") - 1
    assert default_history_end() == expected
    assert default_history_end() != pd.Period("2025-12", freq="M") or \
        expected == pd.Period("2025-12", freq="M")


def test_the_pin_is_honoured(monkeypatch):
    monkeypatch.setenv(HISTORY_END_ENV, "2019-04")
    assert default_history_end() == pd.Period("2019-04", freq="M")
    _, reported = month_range(12)
    assert str(reported[-1]) == "2019-04" and len(reported) == 12


def test_a_spec_records_the_month_its_data_was_as_of(unpinned):
    """Resolved once, at spec construction. A generator reading the clock would
    make two identical specs produce different data from the same cache key."""
    spec = RunSpec.for_profile(load_profile(ROOT / "samples" / "kestrel_retail.json"))
    assert spec.source.generator.history_end == str(default_history_end())

    # And it survives the round trip, which is what makes a saved run
    # reproducible rather than merely repeatable.
    reloaded = RunSpec.model_validate_json(spec.model_dump_json())
    assert reloaded.source.generator.history_end == spec.source.generator.history_end


def test_a_saved_spec_reproduces_its_own_data_a_month_later(monkeypatch):
    """The failure this design exists to prevent: same spec, different clock."""
    profile = load_profile(ROOT / "samples" / "kestrel_retail.json")
    profile = profile.model_copy(update={"history_months": 36})
    spec = RunSpec.for_profile(profile)
    spec.source.generator.history_end = "2024-06"

    monkeypatch.setenv(HISTORY_END_ENV, "2030-01")
    tables = GENERATORS["ecommerce"](profile, spec.source.generator).tables
    assert str(tables["monthly_financials"]["month"].max()) == "2024-06"


@pytest.mark.parametrize("archetype,sample", [
    ("saas", "northwind_saas"), ("ecommerce", "kestrel_retail")])
def test_both_generators_stop_where_they_are_told(archetype, sample):
    """`subscription.py` had its own copy of the hardcoded period range, so
    fixing only `base.month_range` would have fixed one archetype."""
    profile = load_profile(ROOT / "samples" / f"{sample}.json")
    params = GeneratorParams(history_end="2027-09")
    tables = GENERATORS[archetype](profile, params).tables
    for name, frame in tables.items():
        if "month" not in getattr(frame, "columns", ()):
            continue
        assert frame["month"].max() <= pd.Period("2027-09", freq="M"), name
    assert str(tables["monthly_financials"]["month"].max()) == "2027-09"


def test_no_module_pins_the_end_of_history_again():
    """The drift check. Two files stated the same fact and one of them was
    edited; a third copy would be invisible until a user noticed the date."""
    offenders = []
    for path in (ROOT / "kpi_maker").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if 'Period("2025-12"' in text or "Period('2025-12'" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"the end of history is hardcoded again in {offenders}"


def test_the_suite_pins_the_calendar():
    """Without the pin a seasonal sample's findings depend on the month CI runs
    in, and a red February would look like a code change nobody made."""
    assert os.environ.get(HISTORY_END_ENV), \
        "tests/conftest.py no longer pins the calendar for the suite"
