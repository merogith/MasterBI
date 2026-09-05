"""The dashboard renders a row of headline figures that reads as a row.

Split out of `test_smoke_ui.py` rather than added to it: that module drives a
live server, and running the Playwright **sync** API after those tests have
established an asyncio loop in the same thread fails with "Sync API inside the
asyncio loop". The test passed alone and failed in the suite, which is the
isolation trap worth naming — a green run of one file is not a green suite.

This one needs no server at all: it renders a dashboard to a temp directory and
opens the file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="the layout check needs `pip install playwright`")
sync_playwright = _playwright.sync_playwright


def _chromium_executable() -> str | None:
    """Playwright's pinned build, or whatever Chromium this machine has.

    Same two layouts `test_smoke_ui.py` handles; see the note there.
    """
    with sync_playwright() as pw:
        pinned = pw.chromium.executable_path
    if Path(pinned).exists():
        return None
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "")
    if not root.is_dir():
        return None
    for candidate in sorted(root.glob("chromium-*/chrome-linux*/chrome"),
                            reverse=True):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def test_a_wrapped_tile_label_does_not_drag_its_number_out_of_line(tmp_path):
    """The headline row must read as a row.

    Found by looking at the consultancy board pack once 4.3b put the `project`
    pack's sheets on it. "Utilisation" and "Realisation" are longer than the
    cross-sector labels either side of them, so at six tiles "Gross Margin"
    broke over two lines and its number and target then sat about fifteen
    pixels below every other tile's. Nothing failed; six figures meant to be
    compared at a glance simply stopped sharing a baseline.

    Measured rather than grepped for the rule: a `min-height` in the
    stylesheet is the current fix, and asserting *that* would pass a
    stylesheet that had the property and still misaligned. What matters is
    where the numbers land.

    Mutation: drop `min-height` from `.tile-head` in `render/dashboard.py`.
    """
    from kpi_maker.cli import load_profile, run_pipeline

    out = tmp_path / "run"
    profile = load_profile(ROOT / "samples" / "halberd_consulting.json")
    run_pipeline(profile, out, quiet=True)

    executable = _chromium_executable()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            **({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.goto((out / "dashboard.html").as_uri())
        page.wait_for_selector(".tile-value")
        tops = page.eval_on_selector_all(
            ".tiles .tile-value",
            "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
        labels = page.eval_on_selector_all(
            ".tiles .tile-head h3", "els => els.map(e => e.textContent.trim())")
        browser.close()

    assert len(tops) >= 5, f"only {len(tops)} tiles rendered"
    assert max(tops) - min(tops) <= 1, (
        f"the headline numbers span {max(tops) - min(tops)}px vertically; "
        f"labels were {labels}")
