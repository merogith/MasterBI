"""Where the app puts things, and why a packaged build cannot use the defaults.

Three constants were computed from `Path(__file__).parents[...]`, which is
correct in a checkout and wrong in a one-file executable — PyInstaller unpacks
the bundle into a temporary directory and *deletes it on exit*. Left alone,
each would have been a distinct bug in the shipped app:

* every run written next to the code, so history came back empty each launch;
* every KPI "kept for future runs" gone with the session that saved it;
* `CODE_VERSION` a constant, because there is no source to hash — which is the
  stale-result bug the constant exists to prevent, wearing working code.

The frozen state cannot be entered from a test, so these drive the seam
(`paths.frozen`) rather than a real bundle. `installer/smoke.py` covers the
real one: it runs the built executable and checks a board pack comes out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker import paths  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def test_a_checkout_keeps_runs_beside_the_code(monkeypatch):
    """Unchanged behaviour where it was already right — `runs/` is gitignored
    at that path and every existing tool assumes it."""
    monkeypatch.delenv("MASTERBI_RUNS_DIR", raising=False)
    monkeypatch.setattr(paths, "frozen", lambda: False)
    assert paths.runs_dir() == REPO / "runs"


def test_a_frozen_build_writes_runs_where_they_survive(monkeypatch, tmp_path):
    """The bundle's own directory is deleted on exit, so runs cannot live there.

    This is the difference between an app that remembers what you made and one
    that greets you with an empty history every time you open it.
    """
    monkeypatch.delenv("MASTERBI_RUNS_DIR", raising=False)
    monkeypatch.setenv("MASTERBI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "frozen", lambda: True)

    runs = paths.runs_dir()
    assert runs == tmp_path / "runs"
    assert not str(runs).startswith(str(REPO)), \
        "a frozen build must not write into its own installation"


def test_a_frozen_build_keeps_saved_kpis(monkeypatch, tmp_path):
    """A KPI saved "for future runs" has to outlive this run.

    In a bundle the package directory is read-only and temporary, so the
    checkout's `kpi/library/user/` is the one place it must not go.
    """
    monkeypatch.setenv("MASTERBI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "frozen", lambda: True)

    library = paths.user_library_dir()
    assert library == tmp_path / "kpi-library"
    assert "kpi_maker" not in library.parts


def test_the_runs_override_wins_everywhere(monkeypatch, tmp_path):
    """`MASTERBI_RUNS_DIR` is what the browser smoke test drives the real
    server with, and what a portable install would use."""
    monkeypatch.setenv("MASTERBI_RUNS_DIR", str(tmp_path / "elsewhere"))
    for frozen in (True, False):
        monkeypatch.setattr(paths, "frozen", lambda frozen=frozen: frozen)
        assert paths.runs_dir() == tmp_path / "elsewhere"


def test_the_code_version_refuses_to_be_a_constant(monkeypatch, tmp_path):
    """No source and no build stamp must raise, not quietly return a fixed hash.

    A constant `CODE_VERSION` lets a warm cache serve a number computed by code
    that has since changed — a wrong figure in a board pack, which is the one
    failure this project treats as unacceptable. Returning something plausible
    would hide it; raising cannot.
    """
    from kpi_maker.pipeline import cache

    class NoSourceHere:
        """Stands in for `Path`, resolving the package root to an empty tree —
        which is what a frozen bundle looks like to that scan."""

        def __init__(self, *_args):
            pass

        def resolve(self):
            return self

        @property
        def parents(self):
            return {1: tmp_path}

    monkeypatch.setattr(paths, "build_id", lambda: None)
    monkeypatch.setattr(cache, "Path", NoSourceHere)

    with pytest.raises(RuntimeError, match="CODE_VERSION"):
        cache._code_version()


def test_a_build_stamp_changes_the_code_version(monkeypatch):
    """Two builds of different source must not share a cache key."""
    from kpi_maker.pipeline import cache

    monkeypatch.setattr(paths, "build_id", lambda: "aaaa1111 2026-01-01T00:00:00Z")
    first = cache._code_version()
    monkeypatch.setattr(paths, "build_id", lambda: "bbbb2222 2026-01-02T00:00:00Z")
    second = cache._code_version()

    assert first != second, "different builds produced the same cache key"
    assert len(first) == 16
