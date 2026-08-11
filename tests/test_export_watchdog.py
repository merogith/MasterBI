"""Static export must always return or raise — never neither.

kaleido 0.2.1 drives a native subprocess and reads its startup line with a
plain `readline()` and no timeout. On Windows that subprocess sometimes
launches and never speaks, so the read blocks forever. It cost two CI runs of
twenty-plus minutes each, and both were recorded as "cancelled" rather than
"hung" because a stuck job and a cancelled job produce the same word.

`render_all` already had a `try/except` around each chart, which is why this
was invisible for so long: it catches exceptions, and a hang is not one.

These tests never touch the real subprocess. They exercise the control flow
around it, because the control flow is the fix — the hang itself belongs to a
pinned third-party dependency we do not get to change.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.viz import export  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_latch():
    """The failure latch is module state that never clears by design."""
    export._export_failure = None
    yield
    export._export_failure = None


class Wedged:
    """A figure whose `to_image` blocks until the subprocess is killed.

    This is exactly the shape of the real hang: a call that returns only when
    something else closes the pipe underneath it. Faking the *timer* instead
    would test that `threading.Timer` works, which is not in question.
    """

    def __init__(self) -> None:
        self.released = threading.Event()
        self.calls = 0

    def to_image(self, **kwargs):
        self.calls += 1
        # Bounded so a broken watchdog fails the test in seconds instead of
        # hanging the suite — which would be an ironic way to test this.
        if not self.released.wait(timeout=20):
            raise AssertionError("the watchdog never fired")
        raise ValueError("Failed to start Kaleido subprocess")


@pytest.fixture
def wedged(monkeypatch):
    fig = Wedged()
    monkeypatch.setattr(export, "EXPORT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(export, "_kill_kaleido", fig.released.set)
    return fig


def test_a_wedged_export_raises_instead_of_blocking(wedged):
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="did not respond"):
        export._guarded_to_image(wedged, 900, 320, 2.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"took {elapsed:.1f}s — the watchdog did not bound the call"
    assert wedged.released.is_set(), "the subprocess was never killed"


def test_the_failure_latches_so_later_charts_do_not_each_wait(wedged):
    """Thirteen charts share one subprocess. Without the latch, one hang costs
    thirteen timeouts — the same report with no images, three-quarters of an
    hour later."""
    with pytest.raises(RuntimeError):
        export._guarded_to_image(wedged, 900, 320, 2.0)
    assert export._export_failure is not None

    spec = _fake_spec("second_chart")
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="did not respond"):
        export.render_png(spec)
    assert time.perf_counter() - started < 0.2, "the second chart waited again"
    assert wedged.calls == 1, "kaleido was called again after it had failed"


def test_an_ordinary_export_error_does_not_latch(monkeypatch):
    """A single bad chart must not disable the other twelve. Only a timeout
    means the subprocess itself is gone."""
    class Broken:
        def to_image(self, **kwargs):
            raise ValueError("this one chart has an invalid layout")

    monkeypatch.setattr(export, "EXPORT_TIMEOUT_SECONDS", 30.0)
    with pytest.raises(ValueError, match="invalid layout"):
        export._guarded_to_image(Broken(), 900, 320, 2.0)
    assert export._export_failure is None


def test_a_successful_export_cancels_the_watchdog(monkeypatch):
    """The timer must not fire behind a healthy run and kill a live
    subprocess mid-report."""
    killed = threading.Event()
    monkeypatch.setattr(export, "EXPORT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(export, "_kill_kaleido", killed.set)

    class Fine:
        def to_image(self, **kwargs):
            return b"\x89PNG-ish"

    assert export._guarded_to_image(Fine(), 900, 320, 2.0) == b"\x89PNG-ish"
    time.sleep(0.6)
    assert not killed.is_set(), "the watchdog fired after the export succeeded"


# --------------------------------------------------------------------------

class _FakeSpec:
    width = "full"

    def __init__(self, ident: str) -> None:
        self.id = ident
        import plotly.graph_objects as go
        self.figure = go.Figure()


def _fake_spec(ident: str) -> _FakeSpec:
    return _FakeSpec(ident)


def test_render_all_reports_once_and_keeps_what_worked(monkeypatch, tmp_path, capsys):
    """The run must survive: a report without charts beats no report."""
    specs = [_fake_spec(f"chart_{i}") for i in range(4)]

    calls = {"n": 0}

    def fake_render(spec, width=900, tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return b"PNG"
        export._export_failure = "kaleido did not respond within 45s and was stopped"
        raise RuntimeError(export._export_failure)

    monkeypatch.setattr(export, "render_png", fake_render)
    images = export.render_all(specs, out_dir=tmp_path)

    assert set(images) == {"chart_0"}, "the chart that worked was thrown away"
    assert (tmp_path / "chart_0.png").exists()
    # Stopped at the second, rather than trying all four for the same reason.
    assert calls["n"] == 2
    out = capsys.readouterr().out
    assert out.count("kaleido did not respond") == 1, out
