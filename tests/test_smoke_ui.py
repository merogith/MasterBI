"""The first test that opens the product in a browser.

Every other test in this repo checks the engine, the API, or a string in a file.
Nothing has ever driven the actual UI, and `ui/app.js` is 2,000 lines of
`innerHTML` with no build step, no types and no error surface — so a mistake in
it fails silently in front of a user rather than loudly in CI. Both bugs found
in 0.7 were found by booting the server and clicking, which is exactly the
evidence that this ought to be automated instead of remembered.

It walks the path that has to keep working: pick a sample, watch it run, land on
results, open the Studio, change something, re-run it, and find it in history.
That is the product's whole spine, and it is about to be rewritten — 1.1 replaces
this front end with Vite and Preact, and this file is what says the replacement
still does what the original did. Written against the DOM the user sees (visible
views, real buttons, text on screen) rather than against internals, so it
survives that rewrite instead of being thrown away with the code it tests.

Uncaught JS exceptions fail the test that provoked them. There is no other
place a front-end error is currently reported at all.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="browser smoke test needs `pip install playwright`")
sync_playwright = _playwright.sync_playwright

# A cold sample run is about twelve seconds on this hardware; a slow CI runner
# doing PDF, deck, workbook and kaleido's PNG export needs considerably more
# headroom than the default fifteen.
RUN_TIMEOUT_MS = 180_000


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chromium_executable() -> str | None:
    """Playwright's pinned build, or whatever Chromium this machine already has.

    Returns None when the pinned build is present, which is the CI case —
    `playwright install chromium` puts it exactly where Playwright looks. Dev
    containers often ship a different build under `PLAYWRIGHT_BROWSERS_PATH`,
    and re-downloading 170 MB to obtain a browser that is already installed is
    not a trade worth making. The two known layouts differ (`chrome-linux` vs
    `chrome-linux64`), so match both rather than guessing.
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


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """The real server, on a throwaway run directory.

    A subprocess rather than an in-process TestClient: the point is to exercise
    what a user's browser talks to, including the static file mounts and the
    thread pool. `MASTERBI_RUNS_DIR` keeps it out of the developer's own history.
    """
    runs = tmp_path_factory.mktemp("smoke-runs")
    log = tmp_path_factory.mktemp("smoke-log") / "uvicorn.log"
    port = _free_port()

    with log.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "kpi_maker.api.server:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, stdout=sink, stderr=subprocess.STDOUT,
            env={**os.environ, "MASTERBI_RUNS_DIR": str(runs)})

        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited early:\n{log.read_text()}")
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=1).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            proc.kill()
            pytest.fail(f"server never became healthy:\n{log.read_text()}")

        try:
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="module")
def browser():
    executable = _chromium_executable()
    with sync_playwright() as pw:
        instance = pw.chromium.launch(
            **({"executable_path": executable} if executable else {}))
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(20_000)

    # The app has no error surface: an uncaught exception in `app.js` leaves a
    # half-rendered screen and says nothing. Here it fails the test.
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(server, wait_until="domcontentloaded")
    try:
        yield page
    finally:
        context.close()
    assert not errors, "uncaught JS errors: " + "; ".join(errors)


def _visible(page, view: str):
    return page.wait_for_selector(f"#view-{view}:not([hidden])")


def _start_first_sample(page) -> None:
    page.click('[data-nav="samples"]')
    page.wait_for_selector("#sample-grid [data-sample]")
    page.click("#sample-grid [data-sample]")


# --------------------------------------------------------------------------

def test_a_sample_run_reaches_the_results_screen(page):
    """The activation path, end to end: three clicks to a finished board pack.

    Asserts the tiles and the download cards, not just that the view changed —
    an empty results screen is the failure this is most likely to catch.
    """
    _start_first_sample(page)
    _visible(page, "running")

    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    assert page.locator("#res-tiles .tile").count() > 0, "no KPI tiles rendered"
    assert page.locator("#res-downloads .dl-card").count() >= 5, \
        "the results screen is missing its artifacts"
    assert page.locator("#res-company").inner_text().strip()


def test_the_studio_edits_a_spec_and_re_runs_it(page):
    """The Studio's contract: an edit produces a plan, and the plan re-runs.

    `#studio-rerun` starts disabled and is enabled only when the server reports
    dirty stages, so this also covers the PUT round trip that computes them.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)

    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="design"]')

    theme = page.locator('[data-spec="design.theme"]')
    theme.select_option("dark" if theme.input_value() == "light" else "light")

    page.wait_for_selector("#studio-rerun:not([disabled])")
    assert "rebuild" in page.locator("#studio-plan").inner_text()

    page.click("#studio-rerun")
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)


def test_a_cancelled_run_is_listed_in_history_and_can_be_resumed(page):
    """0.7's fix, from the outside.

    A cancelled run writes no `summary.json`, so before the run store it
    vanished from this drawer entirely — along with the finished stages 0.6
    kept on disk to make resuming cheap. It must now be listed, say where it
    stopped, and offer the one action that fits it.
    """
    _start_first_sample(page)
    _visible(page, "running")
    page.click("#run-cancel")

    # The poll returns home once the server confirms the run stopped.
    page.wait_for_selector("#view-home:not([hidden])", timeout=60_000)

    page.click("#btn-history")
    row = page.locator(".run-row").filter(has_text="cancelled").first
    row.wait_for()
    assert "stopped before" in row.inner_text(), \
        "the drawer does not say where the run stopped"

    row.locator("[data-resume-run]").click()
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)


def test_a_finished_run_reopens_after_a_reload(page):
    """History has to reopen what it lists, from a client that has forgotten.

    The reload is the point: it drops every scrap of in-page state, so getting
    back to the results screen exercises the same recovery the restart tests
    cover on the server side — this time through the door a user uses.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    company = page.locator("#res-company").inner_text().strip()

    page.reload(wait_until="domcontentloaded")
    _visible(page, "home")

    page.click("#btn-history")
    row = page.locator(".run-row").filter(has_text="done").first
    row.locator("[data-open-run]").click()

    _visible(page, "results")
    assert page.locator("#res-company").inner_text().strip() == company
