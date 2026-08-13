"""The only test that opens the product in a browser.

Every other test in this repo checks the engine, the API, or a string in a file.
Before this existed nothing drove the UI at all, so a mistake there failed
silently in front of a user rather than loudly in CI. Both bugs found in 0.7
were found by booting the server and clicking — this is that, automated.

It walks the path that has to keep working: pick a sample, watch it run, land on
results, open the Studio, change something, re-run it, and find it in history.
That is the product's whole spine, and it is what graded the 1.1 rewrite: these
assertions are written against the DOM the user sees — visible views, real
buttons, text on screen — so they survived the front end being replaced
underneath them, which is the whole reason they were written that way.

Uncaught JS exceptions fail the test that provoked them. There is no other
place a front-end error is reported at all.
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


def _serve(tmp_path_factory, **extra_env):
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
            env={**os.environ, "MASTERBI_RUNS_DIR": str(runs), **extra_env})

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
def server(tmp_path_factory):
    """The app, served the way a user gets it.

    Skipped rather than failed when the bundle has not been built: the Python
    suite must not require Node, which is the same property that lets the
    packaged executable ship without it.
    """
    dist = ROOT / "kpi_maker" / "ui_dist" / "index.html"
    if not dist.exists():
        pytest.skip("no ui_dist bundle — run `npm --prefix web ci && "
                    "npm --prefix web run build`")
    yield from _serve(tmp_path_factory)


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


def _open(browser, base: str):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(20_000)

    # The app has no error surface: an uncaught exception leaves a half-rendered
    # screen and says nothing. Here it fails the test.
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(base, wait_until="domcontentloaded")
    try:
        yield page
    finally:
        context.close()
    assert not errors, "uncaught JS errors: " + "; ".join(errors)


@pytest.fixture
def page(browser, server):
    yield from _open(browser, server)



def _visible(page, view: str):
    return page.wait_for_selector(f"#view-{view}:not([hidden])")


def _go_home(page) -> None:
    """Back to the origin, dropping all in-page state.

    Not `reload()`: the rewrite keeps a run's URL across a refresh, so a reload
    on `/runs/<id>` correctly stays on that run rather than landing home.
    """
    origin = "/".join(page.url.split("/")[:3])
    page.goto(origin, wait_until="domcontentloaded")


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
    """0.7's fix, from the outside — and in both front ends.

    A cancelled run writes no `summary.json`, so before the run store it
    vanished from this drawer entirely — along with the finished stages 0.6
    kept on disk to make resuming cheap. It must now be listed, say where it
    stopped, and offer the one action that fits it.
    """
    _start_first_sample(page)
    _visible(page, "running")
    page.click("#run-cancel")

    # Where a stopped run leaves you differs by front end — the legacy poll
    # returns home, the rewrite keeps the run's own URL and explains itself
    # there. Both agree that it stops running, which is what this waits on.
    page.wait_for_selector("#view-running:not([hidden])", state="detached",
                           timeout=60_000)
    _go_home(page)

    page.click("#btn-history")
    row = page.locator(".run-row").filter(has_text="cancelled").first
    row.wait_for()
    assert "stopped before" in row.inner_text(), \
        "the drawer does not say where the run stopped"

    row.locator("[data-resume-run]").click()
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)


def test_a_finished_run_reopens_from_history(page):
    """History has to reopen what it lists, from a client that has forgotten.

    Going back to the origin is the point: it drops every scrap of in-page
    state, so getting to the results screen from the drawer exercises the same
    recovery the restart tests cover on the server side — through the door a
    user actually uses.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    company = page.locator("#res-company").inner_text().strip()

    _go_home(page)
    _visible(page, "home")

    page.click("#btn-history")
    row = page.locator(".run-row").filter(has_text="done").first
    row.locator("[data-open-run]").click()

    _visible(page, "results")
    assert page.locator("#res-company").inner_text().strip() == company


def test_back_and_forward_move_between_screens(page):
    """The point of the router. The front end this replaced had zero
    `pushState` calls, so every screen was the same URL, Back left the app,
    and no run could be sent to anyone."""
    assert page.url.endswith("/")

    page.click('[data-nav="samples"]')
    _visible(page, "samples")
    assert page.url.endswith("/samples")

    page.go_back()
    _visible(page, "home")

    page.go_forward()
    _visible(page, "samples")


def test_a_run_url_survives_a_reload(page):
    """`/runs/<id>` is a real address, not a screen you can only arrive at.

    This is the failure that makes hand-rolled SPA routing look fine until the
    first refresh: the server has to answer an unknown path with the shell.
    """
    _start_first_sample(page)
    _visible(page, "running")
    url = page.url
    assert "/runs/" in url

    page.reload(wait_until="domcontentloaded")
    assert page.url == url
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)



def test_the_survey_runs_end_to_end_on_averages(page):
    """Every question skipped is still a complete, honest run.

    "Skip — use averages" records `__unknown__` rather than leaving the field
    blank, so the provenance says the value was defaulted instead of quietly
    inventing one. Walking the whole survey that way is the fastest path that
    still exercises every step, the review screen and the run it starts.
    """
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")

    # Skip forward until the review step, which is the one that offers to run.
    for _ in range(20):
        if page.locator("#survey-next").inner_text().strip() == 'Generate my pack':
            break
        page.click("#survey-skip")
    else:
        raise AssertionError("never reached the review step")

    assert page.locator("#review-list .review-row").count() > 0, \
        "the review step lists none of the answers it is about to run"
    assert "assumed" in page.locator("#review-list").inner_text(), \
        "skipped answers must be marked as assumptions, not presented as facts"

    page.click("#survey-next")
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)


def test_bringing_data_profiles_the_file_without_running_it(page, tmp_path):
    """Profiling is not adoption, and the screen has to say which it did.

    The upload inspects a file and changes nothing: a run needs a company
    profile before it can read anyone's numbers. A screen that took a file and
    looked like it had started something would be promising a flow that does
    not exist yet.
    """
    csv = tmp_path / "monthly.csv"
    csv.write_text("month,revenue,cogs\n2025-01,1000,400\n2025-02,1100,430\n",
                   encoding="utf-8")

    page.click('[data-nav="builder"]')
    _visible(page, "builder")
    page.set_input_files("#file-input", str(csv))

    page.wait_for_selector("#upload-result table tbody tr")
    report = page.locator("#upload-result")
    assert "monthly.csv" in report.inner_text()
    assert report.locator("table tbody tr").count() == 3, \
        "one row per column of the uploaded file"
    # Still on the builder screen: nothing was run.
    assert page.locator("#view-builder:not([hidden])").count() == 1
