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


def test_bringing_data_walks_the_funnel_to_a_finished_run(page, tmp_path):
    """The screen that had no button, all the way to a board pack.

    "Bring your data" used to profile a file, print its columns, and stop. The
    only actual route was to start a *synthetic* run, open the Studio, and
    upload the same file again from the Source panel — two screens and a wasted
    run to do one thing. This walks the four steps that replaced that: read it,
    check the mapping, see what it will produce, answer what the file could not
    say, run.

    Deliberately a file whose name says nothing about its contents, because
    keying uploads by their stem is the bug the funnel is built on top of.
    """
    csv = tmp_path / "q4_export.csv"
    csv.write_text(
        "Period,Total Revenue,Cost of Sales,Marketing\n"
        + "".join(f"2025-{m:02d},{100000 + m * 900},{40000 + m * 300},{7000 + m * 40}\n"
                  for m in range(1, 13)),
        encoding="utf-8")

    page.click('[data-nav="builder"]')
    _visible(page, "builder")
    page.set_input_files("#file-input", str(csv))

    # Step 2 — what we read. The table is the decision, not the filename.
    page.wait_for_selector("#to-quality")
    read = page.locator("#view-builder").inner_text()
    assert "monthly financials" in read.lower(), read[:400]
    assert page.locator(".mapping-table tbody tr").count() >= 3, \
        "the mapping editor listed no fields"
    assert page.locator(".mapping-table .map-select").count() >= 3, \
        "the mapping is not editable, so a wrong guess cannot be corrected"

    # Step 3 — what you will get, before committing to anything.
    page.click("#to-quality")
    page.wait_for_selector("#to-questions")
    gate = page.locator("#view-builder").inner_text()
    assert "KPI" in gate, gate[:400]
    assert "monthly_financials" in gate

    # Step 4 — only the questions the file could not answer.
    page.click("#to-questions")
    page.wait_for_selector("#run-upload")
    questions = page.locator(".question[data-qid]")
    assert questions.count() > 0, "the shortened survey asked nothing at all"
    assert questions.count() < 19, \
        "nothing was shortened — the file's own answers were asked for again"

    page.fill(".name-input", "Wayfarer Freight")
    page.click("#run-upload")
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    assert page.locator("#res-tiles .tile").count() > 0, \
        "the upload produced a results screen with no numbers on it"
    # Names the company *this funnel* was told about, so the results cannot be
    # some other run that happened to be on screen.
    assert "Wayfarer Freight" in page.locator("#res-company").inner_text()


def test_the_funnel_goes_backwards_without_losing_the_file(page, tmp_path):
    """Each step is reversible. A gate you cannot walk back out of is a trap."""
    csv = tmp_path / "ledger.csv"
    csv.write_text("Period,Total Revenue,Cost of Sales\n2025-01,1000,400\n"
                   "2025-02,1100,430\n", encoding="utf-8")

    page.click('[data-nav="builder"]')
    _visible(page, "builder")
    page.set_input_files("#file-input", str(csv))
    page.wait_for_selector("#to-quality")
    page.click("#to-quality")
    page.wait_for_selector("#to-questions")

    page.click(".survey-nav .ghost")
    page.wait_for_selector("#to-quality")
    assert page.locator(".mapping-table tbody tr").count() >= 2, \
        "going back lost the file that had already been read"


def _walk_survey_to_review(page, choose=None) -> set:
    """Skip through the survey, answering `choose`, and return every id asked.

    Returns the union rather than the last step's, because the whole point of
    branching is that a question is absent from *every* step it might have been
    on.
    """
    seen: set = set()
    for _ in range(25):
        for element in page.query_selector_all(".question[data-qid]"):
            seen.add(element.get_attribute("data-qid"))
        for qid, label in (choose or {}).items():
            target = page.locator(f'.question[data-qid="{qid}"] .option', has_text=label)
            if target.count():
                target.first.click()
                page.wait_for_selector(
                    f'.question[data-qid="{qid}"] .option.selected')
        if page.locator("#survey-next").inner_text().strip() == "Generate my pack":
            return seen
        page.click("#survey-skip")
    raise AssertionError("never reached the review step")


def test_the_survey_hides_questions_that_cannot_apply(page):
    """Pick "Retail" and the two subscription questions stop being asked.

    `contract_terms` fills `annual_prepay_share` and `avg_contract_months`,
    whose only readers are in `datagen/subscription.py`; `sales_motion`'s seven
    `applies_when` clauses are all in the SaaS packs. Both were asked of every
    respondent, so a shop was answering "how do customers commit and pay" into
    a void.

    Asserted on the state *after* the sector is chosen, not on the union of
    every step: `sales_motion` shares a step with the sector question, so it is
    legitimately on screen until the answer that makes it irrelevant arrives —
    and then it has to go, in the same render.
    """
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")

    # Walk to the step holding the sector question.
    for _ in range(10):
        if page.locator('.question[data-qid="business_model"]').count():
            break
        page.click("#survey-skip")
    else:
        raise AssertionError("never reached the sector question")

    assert page.locator('.question[data-qid="sales_motion"]').count() == 1, \
        "the test proves nothing unless the question is there to begin with"

    _answer(page, "business_model", "Retail (physical)")

    assert page.locator('.question[data-qid="sales_motion"]').count() == 0, \
        "a retailer is still asked a question only the SaaS packs read"

    seen = _walk_survey_to_review(page)
    assert "contract_terms" not in seen, \
        "a retailer was asked how customers commit and pay"
    assert "revenue_band" in seen, "branching removed a question the profile needs"

    review = page.locator("#review-list").inner_text()
    assert "commit and pay" not in review and "win customers" not in review, \
        "the review lists answers the user was never asked for"


def test_the_survey_still_asks_a_saas_business_everything(page):
    """The other half of the same rule: nothing was removed for the businesses
    the two questions exist for."""
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    seen = _walk_survey_to_review(page, {"business_model": "Software / subscription"})

    assert {"contract_terms", "sales_motion"} <= seen, \
        "branching hid a question from the archetype that reads it"


def _answer(page, qid: str, label: str) -> None:
    """Click an option and wait for the app to register it.

    Clicking and immediately pressing Next raced on the CI runner: the survey
    saw the question as unanswered, refused to advance, and the draft was saved
    on step 0 — so the test that reloaded and reached for Back found it
    correctly hidden. Waiting for `.selected` waits for the state, not the
    paint.
    """
    page.click(f'.question[data-qid="{qid}"] .option:has-text("{label}")')
    page.wait_for_selector(f'.question[data-qid="{qid}"] .option.selected')


def test_an_unfinished_survey_survives_a_reload(page):
    """Nineteen questions is four minutes, and a reload lost every one."""
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    _answer(page, "objective", "Protect cash")
    _answer(page, "audience", "The board")
    page.click("#survey-next")
    page.wait_for_selector('.question[data-qid="country"]')

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#survey-resumed")
    assert "Picked up where you left off" in page.locator("#survey-resumed").inner_text()

    # Whichever step the draft restored, the answer itself has to have survived.
    if page.locator('.question[data-qid="objective"]').count() == 0:
        page.click("#survey-back")
    page.wait_for_selector('.question[data-qid="objective"] .option.selected')
    assert page.locator(
        '.question[data-qid="objective"] .option.selected').inner_text().strip() \
        .startswith("Protect cash")

    page.click("#survey-resumed .linkish")
    assert page.locator(
        '.question[data-qid="objective"] .option.selected').count() == 0, \
        "'Start again' left the old answers in place"


def test_the_review_jumps_back_to_the_question_it_names(page):
    """A review you cannot act on is a receipt, not a review."""
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    for _ in range(20):
        if page.locator("#survey-next").inner_text().strip() == 'Generate my pack':
            break
        page.click("#survey-skip")
    else:
        raise AssertionError("never reached the review step")

    page.click('#review-list [data-edit="objective"]')
    page.wait_for_selector('.question[data-qid="objective"]')
    _answer(page, "objective", "Cut cost")

    # Forward again to the review, which must show the change.
    for _ in range(20):
        if page.locator("#survey-next").inner_text().strip() == 'Generate my pack':
            break
        page.click("#survey-next")
    assert "Cut cost" in page.locator("#review-list").inner_text()


def test_progress_does_not_read_full_while_there_is_work_left(page):
    """It used to: `index / (steps.length - 1)` is 100% on the review step —
    the one step that still has the name field and the run button on it."""
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    _answer(page, "objective", "Grow revenue")

    for _ in range(20):
        if page.locator("#survey-next").inner_text().strip() == 'Generate my pack':
            break
        page.click("#survey-skip")

    width = page.locator("#survey-progress").evaluate(
        "el => el.style.width")
    percent = float(width.replace("%", ""))
    assert 0 < percent < 100, \
        f"progress reads {width} on the review step, having skipped everything"


def test_the_home_screen_leads_with_one_finished_pack(page):
    """One milestone above the fold, and it has to actually reach a pack.

    The screen used to open with three equally-weighted doors and a paragraph
    each — a decision handed to someone who has not yet seen what the product
    makes, where two of the three cost minutes before anything appears.
    """
    _visible(page, "home")
    assert page.locator("#btn-activate").is_visible()

    # Real figures, read off the sample profiles by the server rather than
    # written into the page. Copy cannot drift from the packs it promises if it
    # is not copy.
    page.wait_for_selector("#home-proof .proof-card")
    stats = page.locator("#home-proof .proof-stats").first.inner_text()
    assert any(ch.isdigit() for ch in stats), \
        f"the proof strip shows no real figures: {stats!r}"

    page.click("#btn-activate")
    _visible(page, "running")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    assert page.locator("#res-downloads .dl-card").count() >= 5


def test_the_results_screen_admits_what_it_is_not_showing(page):
    """Nine of nineteen findings were dropped with nothing on screen saying so.

    Measured on a real `northwind_saas` run: 19 findings, 10 rendered. Silent
    truncation is the failure mode this is about — the eleventh finding is not
    less true than the tenth.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    more = page.locator("#res-more-findings")
    assert more.count() == 1, "no way to see the findings that were cut"
    assert "Show all" in more.inner_text()

    before = page.locator("#res-findings .finding").count()
    more.click()
    after = page.locator("#res-findings .finding").count()
    assert after > before, f"'Show all' revealed nothing ({before} -> {after})"

    # And the KPI tiles say they are a selection rather than the whole set.
    assert "of" in page.locator("#res-kpi-count").inner_text()


def test_the_tour_appears_once_and_can_be_escaped(page):
    """Four steps, on the screen with something to point at, never twice.

    Tours over five steps drop off sharply and over eight are abandoned by 84%,
    and one fired on arrival — before the user has anything of their own — is
    dismissed unread. So it waits for a finished board pack.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)

    tour = page.locator("#tour")
    assert tour.is_visible(), "no tour on the first finished pack"
    assert "1 of 4" in tour.inner_text(), tour.inner_text()

    page.keyboard.press("Escape")
    assert tour.count() == 0, "Escape did not close a dialog that appeared unbidden"

    # A second run must not show it again — dismissal is remembered.
    _go_home(page)
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    assert page.locator("#tour").count() == 0, \
        "the tour came back after being dismissed"


def test_loading_and_failure_do_not_look_the_same(page):
    """`<p class="empty">` was thirteen places, three meanings, one appearance.

    A user could not tell a slow network from a broken server from an empty
    list, and a screen reader was told nothing at all — none of them carried a
    live region.
    """
    # A failing request, on the screen whose loader used to look identical to
    # its error.
    page.route("**/api/survey", lambda route: route.abort())
    page.click('[data-nav="survey"]')
    failure = page.wait_for_selector(".state-failed[role=alert]")
    assert "Could not load the survey" in failure.inner_text()
    assert page.locator(".state-failed .state-icon").count() == 1, \
        "the failure state is indistinguishable from the loading one"


def test_a_findings_severity_is_actually_visible(page):
    """Severity is computed and ranked, and rendered identically for months.

    The component emits `sev-high`; the stylesheet targeted `.finding.high`, so
    **no severity rule ever matched** — every finding drew the same neutral left
    border on the one screen where ranking is the point. `.sev` had no rule at
    all, so the label ran into the title: "HighChurn is concentrated in the SMB
    segment". Both were plain in a screenshot and invisible to every assertion
    written about this screen, which is why this one measures pixels.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    findings = page.locator("#res-findings .finding")
    assert findings.count() > 1

    borders = page.locator("#res-findings .finding").evaluate_all(
        "els => els.map(el => getComputedStyle(el).borderLeftColor)")
    assert len(set(borders)) > 1, \
        f"every finding drew the same left border, whatever its severity: {borders[0]}"

    # The word is always there too — colour is never the only channel, the same
    # rule the engine applies to every chart it draws.
    label = page.locator("#res-findings .finding .sev").first
    assert label.inner_text().strip(), "the severity label renders empty"
    gap = page.locator("#res-findings .finding").first.evaluate(
        "el => getComputedStyle(el).columnGap")
    assert gap not in ("normal", "0px"), \
        f"the severity label is jammed against the finding text (gap: {gap})"


def test_every_figure_says_where_it_came_from(page):
    """`basis` is derived automatically and was rendered nowhere.

    `TrackedTables` records every fact table a metric reads, so each result is
    `measured`, `modelled` or `mixed` without anyone declaring it. It has been
    correct for as long as the metrics engine has existed, it arrives in the
    browser in every row of `facts.csv`, and no screen showed it.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    chips = page.locator("#res-tiles .basis")
    assert chips.count() == page.locator("#res-tiles .tile").count(), \
        "some headline figures do not say where they came from"
    assert chips.first.inner_text().strip().lower() in ("measured", "modelled",
                                                        "part modelled")


def test_the_run_explains_why_each_metric_is_on_the_scorecard(page):
    """25 rationales and 19 dropped KPIs, computed and shown to nobody.

    The selection engine records why each KPI was chosen and why each candidate
    was rejected. "Why isn't X on my dashboard" had an answer the whole time.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    toggle = page.locator("#res-provenance-toggle")
    assert toggle.count() == 1, "nothing on this screen explains how it was built"
    summary = toggle.inner_text()
    assert "north star" in summary.lower(), summary
    assert "KPIs computed" in summary, summary

    toggle.click()
    page.wait_for_selector("#res-scorecard")

    rows = page.locator("#res-scorecard tbody tr")
    assert rows.count() >= 20, f"the scorecard lists only {rows.count()} KPIs"
    body = page.locator("#res-scorecard").inner_text()
    assert "North Star for this business model" in body, \
        "the engine's own rationale is not shown against the metric it explains"

    dropped = page.locator("#res-dropped li")
    assert dropped.count() >= 10, \
        f"only {dropped.count()} rejected candidates listed; a real run drops 19"
    assert "requires unavailable data" in page.locator("#res-dropped").inner_text()


def test_a_survey_run_shows_what_was_assumed_rather_than_answered(page):
    """The survey's central promise, kept somewhere a user of the app can see.

    "Anything you don't know is filled from sector benchmarks and clearly
    footnoted" was honoured in the PDF appendix and nowhere else. Skipping the
    whole survey is the sharpest case: almost every field is a prior.
    """
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    for _ in range(25):
        if page.locator("#survey-next").inner_text().strip() == "Generate my pack":
            break
        page.click("#survey-skip")
    page.click("#survey-next")
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    page.click("#res-provenance-toggle")
    page.wait_for_selector("#res-assumed")
    assumed = page.locator("#res-assumed li")
    assert assumed.count() > 0, \
        "every answer was skipped and nothing says which figures are assumptions"
    assert "profile fields from you" in page.locator("#res-provenance-toggle").inner_text()
