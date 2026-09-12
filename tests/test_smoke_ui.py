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

import json
import os
import re
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


def _freeze_effects(page) -> None:
    """Stop `useEffect` from flushing, and find out what still works.

    CI caught two races a local run never did: a column choice that vanished on
    reload, and a record sheet that ignored Escape. Both were rendered,
    painted and clickable while the effect meant to complete them had not run —
    Preact schedules an effect flush with `requestAnimationFrame` raced against
    `setTimeout(flush, 35)`, and on a headless runner acting inside that window
    is ordinary rather than unlucky.

    So the reproduction is not "act quickly and hope". `requestAnimationFrame`
    is neutered and Preact's own 35ms fallback is pushed out of reach, which
    makes the failure deterministic and turns a flaky assertion into a real
    one: **whatever the user can see and touch must already work, with no
    effect having run.**

    Applied to the loaded page rather than at init, and that distinction is the
    point of the test. Effects are the right place for asynchronous work — the
    app fetches the run in one, and freezing that from the start just leaves a
    blank screen. They are the wrong place for *completing something the user
    can already see and act on*. So the app boots normally, the freeze goes on
    immediately before the interaction, and a page reload gets a fresh context
    that lifts it.

    The 35 is Preact's constant, matched exactly so nothing else in the app is
    slowed — the poll backoff starts at 400ms and never lands on it.
    """
    page.evaluate("""() => {
        window.requestAnimationFrame = () => 0;
        const realTimeout = window.setTimeout.bind(window);
        window.setTimeout = (fn, delay, ...rest) =>
          realTimeout(fn, delay === 35 ? 100000 : delay, ...rest);
    }""")


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

    # The promise, kept to compare against the delivery below. This is the one
    # screen where the product's central claim is a number, and until this
    # walk read it back nothing checked the two agreed: on a 36-month P&L the
    # gate said 6 and the run computed 2, because the count came from a
    # table-granular map and the engine is column-granular. `test_upload_run`
    # pins that server-side; this pins that the two screens a user actually
    # sees say the same thing.
    promised = int(re.search(r"(\d+)\s+KPIs?\s+from what you supplied",
                             gate).group(1))

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

    # The delivery, against the promise two screens earlier.
    count_line = page.locator("#res-kpi-count").inner_text()
    delivered = int(re.search(r"(\d+)\s+of\s+\d+\s+computed", count_line).group(1))
    assert delivered == promised, (
        f"the gate promised {promised} KPIs and the results screen reports "
        f"{delivered} computed — {count_line!r}")

    # And that number is the scorecard's own, not the selected total. It read
    # "1 headline KPIs of 25 computed" over a scorecard where 23 rows said
    # "not computed": `summary.kpis` is every selected row, so the sentence
    # named one number and printed another. True by accident on a sample, where
    # every KPI computes, and false on exactly this screen — a partial upload,
    # which is the case the whole funnel exists to be honest about.
    rows = page.locator(".scorecard tbody tr")
    blank = sum(1 for i in range(rows.count())
                if "not computed" in rows.nth(i).inner_text())
    assert delivered == rows.count() - blank, (
        f"the line says {delivered} computed and the scorecard shows "
        f"{rows.count() - blank} of {rows.count()} rows with a value")


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
    # Not frozen, and the reason is worth stating: the tour's *appearance* is
    # properly effect-driven — 2.1 moved its step filtering into an effect
    # precisely so it runs after its targets exist — so a freeze stops it
    # rendering at all. What must not depend on an effect is dismissing an
    # overlay that is already on screen, and that rule is asserted at the
    # source in `tests/test_packaging.py` for all three overlays at once.
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


# --------------------------------------------------------------------------
# 3.5 — the scorecard as a semantic-layer surface
# --------------------------------------------------------------------------

def test_twenty_sectors_can_be_searched_rather_than_scrolled(page):
    """4.1 took the sector question from ten options to twenty.

    Ten is a list you read; twenty is a list you scan and give up on. The
    filter matches what a user types — "gym", "haulage", "dtc" — rather than
    the label the taxonomy happens to use, because nobody types "Distribution
    or wholesale".
    """
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    for _ in range(12):
        if page.locator('[data-qid="business_model"]').count():
            break
        page.click("#survey-skip")
    page.wait_for_selector('[data-qid="business_model"]')

    options = page.locator('[data-qid="business_model"] .option')
    assert options.count() >= 20, f"only {options.count()} sectors offered"

    page.fill("#filter-business_model", "gym")
    assert options.count() == 1, \
        f"'gym' narrowed twenty sectors to {options.count()}"
    assert "Gyms" in options.first.inner_text()

    # And an alias nobody would guess from the label.
    page.fill("#filter-business_model", "haulage")
    assert "Logistics" in options.first.inner_text(), options.first.inner_text()


def test_a_chosen_sector_names_the_standard_it_was_matched_against(page):
    """The official codes are carried for credibility, and a code nobody can
    see is not credibility. Shown on the chosen option only — on nineteen rows
    the user did not pick it is noise."""
    page.click('[data-nav="survey"]')
    page.wait_for_selector("#survey-next")
    for _ in range(12):
        if page.locator('[data-qid="business_model"]').count():
            break
        page.click("#survey-skip")
    page.fill("#filter-business_model", "retail")

    option = page.locator('[data-qid="business_model"] .option').first
    assert option.locator(".option-code").count() == 0, \
        "the classification is shown before anything is chosen"
    option.click()
    code = option.locator(".option-code")
    code.wait_for()
    assert "NACE" in code.inner_text() and "NAICS" in code.inner_text(), \
        code.inner_text()


def test_an_overlay_can_be_escaped_from_the_moment_it_is_visible(page):
    """The history drawer, under the same freeze as the record sheet.

    All three overlays in the app registered their Escape listener in a
    `useEffect`, so each was on screen and un-dismissable for as long as Preact
    took to flush — invisible locally, reproducible on a CI runner. This is the
    third of the three, and the one that had no test at all.
    """
    # Frozen before the drawer opens, so the effect that would have registered
    # its Escape listener never runs. The run list is fetched in an effect too
    # and stays empty — which is the point: the panel is visible, so it has to
    # be dismissable, finished loading or not.
    _freeze_effects(page)
    page.click("#btn-history")
    page.wait_for_selector("#drawer")

    page.keyboard.press("Escape")
    # Absent rather than `[hidden]`: 7.4b moved the drawer to mounting and
    # unmounting, because a focus trap has to know when the overlay went away
    # and `hidden` looks the same as "still here" from a ref callback.
    assert page.locator("#drawer").count() == 0, \
        "Escape did not close the history drawer"


def test_the_scorecard_is_on_the_page_not_behind_a_triangle(page):
    """It rendered inside the collapsed "How this was built" panel, which is
    the wrong home for the surface carrying every metric's definition."""
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    assert page.locator("#res-scorecard").is_visible(), \
        "the scorecard needs a click before it exists"
    assert page.locator("#res-scorecard tbody tr").count() >= 20


def test_a_column_can_be_sorted_and_the_table_says_which(page):
    """Ordering was whatever the engine emitted, which is not a fact about the
    metrics. And a sorted table that does not mark the column it sorted by
    leaves a screen reader with no way to know."""
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    # `localeCompare`, not codepoint order: "Active Customers" belongs above
    # "ARR Growth" for a reader, and Python's default sort would disagree.
    def names() -> list:
        return page.locator(
            "#res-scorecard tbody tr td:first-child").all_inner_texts()

    def collated(rows: list) -> list:
        return sorted(rows, key=lambda n: n.lower())

    before = names()
    page.click("#sc-sort-name")
    ascending = names()
    assert ascending != before, "clicking a header changed nothing"
    assert ascending == collated(ascending), f"not sorted: {ascending[:4]}"

    header = page.locator("#res-scorecard thead th").first
    assert header.get_attribute("aria-sort") == "ascending", \
        "the sorted column does not announce itself"

    page.click("#sc-sort-name")
    assert names() == collated(ascending)[::-1], "the sort does not reverse"
    assert page.locator("#res-scorecard thead th").first.get_attribute(
        "aria-sort") == "descending"


def test_the_columns_can_be_chosen_and_the_choice_survives_a_reload(page):
    """A scorecard nobody can shape is a screenshot. And a preference that
    resets on every visit is worse than not offering it.

    Run with effects frozen: the choice must be saved by the click that makes
    it, not by an effect afterwards. Persisting in `useEffect` passed here
    every time and failed on CI every time — see `_freeze_effects`.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")
    _freeze_effects(page)

    def widths() -> int:
        return page.locator("#res-scorecard thead th").count()

    start = widths()
    page.click("#sc-columns")
    page.wait_for_selector("#sc-chooser")
    page.locator("#sc-chooser label", has_text="Perspective").locator(
        "input").check()
    assert widths() == start + 1, "adding a column did not add a column"

    page.reload()
    page.wait_for_selector("#res-scorecard")
    assert widths() == start + 1, "the column choice did not survive a reload"
    # Rendered uppercase by the stylesheet, and `inner_text` returns what is
    # on screen rather than what is in the DOM.
    assert "PERSPECTIVE" in page.locator("#res-scorecard thead").inner_text().upper()


def test_a_kpi_opens_the_record_sheet_it_has_always_had(page):
    """Formula, grain, owner, source systems, benchmark **and its citation**,
    alert bands, target rule, pitfalls, interpretation. Every field authored in
    `kpi/library/*.yaml` from the beginning, reaching only the PDF appendix.

    Effects frozen here too: a dialog that is on screen and cannot be dismissed
    is a trap, so Escape has to work from the first paint rather than from
    whenever a `keydown` listener gets registered.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")
    _freeze_effects(page)

    page.locator("#res-scorecard .kpi-open").first.click()
    page.wait_for_selector("#kpi-sheet")
    sheet = page.locator("#kpi-sheet").inner_text()
    for field in ("DEFINITION", "GRAIN", "OWNER", "SOURCE SYSTEMS", "BENCHMARK"):
        assert field in sheet.upper(), f"the record sheet has no {field}: {sheet[:200]}"
    assert "HOW TO READ IT" in sheet.upper(), "the interpretation prose is not shown"

    # Escape closes it, like every other overlay in the app.
    page.keyboard.press("Escape")
    assert page.locator("#kpi-sheet").count() == 0


def test_the_export_is_of_what_you_are_looking_at(page):
    """Not a second copy of facts.csv — that artifact already exists as a
    download, and an export that differs from the table above it is the kind of
    small dishonesty this project spends its time removing."""
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    page.click("#sc-sort-name")
    with page.expect_download() as caught:
        page.click("#sc-export")
    download = caught.value
    path = download.path()
    assert path is not None
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]

    header = lines[0].split(",")
    assert header[0] == "kpi_id"
    on_screen = page.locator("#res-scorecard thead th").all_inner_texts()
    assert len(header) == len(on_screen) + 1, \
        f"the export has {len(header)} columns and the table shows {len(on_screen)}"

    rows = page.locator("#res-scorecard tbody tr").count()
    assert len(lines) - 1 == rows, \
        f"the export has {len(lines) - 1} rows and the table shows {rows}"
    # And in the order it was sorted into, not the order the engine emitted.
    exported = [line.split(",")[1].strip('"') for line in lines[1:]]
    assert exported == sorted(exported, key=lambda n: n.lower()), \
        "the export ignored the sort"


def test_the_record_sheet_walks_down_the_driver_tree(page):
    """`ARCHITECTURE.md` promised drill-down along the value-driver tree.
    `driver_parent` is authored on 56 record sheets and had no consumer at all
    until 3.3 built the graph — and 3.3 then shipped one, `DriverPath`, which
    walks *up*: "ARR -> NRR -> GRR", what this metric rolls up into.

    **This walks down, which is the other question.** A reader looking at a
    number that is off does not ask what it contributes to; they ask what
    moved it. Measured on real runs the tree can answer: nine to eleven KPIs
    per scorecard have children, and roots carry five to ten.

    Mutations: render the driver names as text rather than buttons; drop
    `onOpen` so the panel is a dead end; read `parent` instead of `children`
    and the section becomes the path that already existed.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")
    _freeze_effects(page)

    # The north star is the tree's root on every sample, so it always has
    # children — picking a row at random would test the sample's ordering.
    page.locator("#res-scorecard .kpi-open").first.click()
    page.wait_for_selector("#kpi-sheet")

    drivers = page.locator("#kpi-sheet .driver-open")
    assert drivers.count() >= 2, (
        "the record sheet shows no drivers for the top KPI: "
        + page.locator("#kpi-sheet").inner_text()[:300])
    opened = page.locator("#kpi-sheet h3").inner_text()
    first = drivers.first.inner_text()

    # Each driver carries its own number, because a list of names is a diagram
    # and a list of numbers is an answer.
    row = page.locator("#kpi-sheet .driver-list li").first.inner_text()
    assert any(ch.isdigit() for ch in row), row

    # And it is a walk, not a dead end: clicking a driver opens its own sheet.
    drivers.first.click()
    page.wait_for_timeout(200)
    now = page.locator("#kpi-sheet h3").inner_text()
    assert now != opened, "clicking a driver did not move the panel"
    assert now.lower().startswith(first.lower()[:8]), (now, first)


def test_a_percentage_move_is_reported_in_points(page):
    """A `pct` metric moves in **points**, and its values arrive as fractions.

    The rule lived inline in `render/dashboard._stat_tile` and dropped the
    hundred: a real dashboard showed gross margin going 28.5% to 32.9% as
    "0.0 pts" and EBITDA 5.4% to 10.8% as "0.1 pts" — every percentage tile,
    understated a hundredfold, reading as a plausible "barely moved". It is
    `fmt.fmt_move` now, mirrored in `format.ts` under a drift test, and this
    asserts the browser half against a real run.

    Mutation: drop the `* 100` in `fmtMove`.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")
    _freeze_effects(page)

    page.locator("#res-scorecard .kpi-open").first.click()
    page.wait_for_selector("#kpi-sheet")
    moves = page.locator("#kpi-sheet .driver-move").all_inner_texts()
    points = [m for m in moves if "pts" in m]
    if not points:
        pytest.skip("this sample's top KPI has no percentage drivers")

    # A hundredfold understatement shows up as every move rounding to nothing.
    values = [float(m.split()[1]) for m in points]
    assert any(v >= 0.5 for v in values), (
        f"every percentage move rounds to nothing, which is what the missing "
        f"hundred looked like: {points}")


def test_the_design_panel_shows_the_artifact_not_only_the_palette(page):
    """**The best-built thing in the app answered the wrong question.**

    The brand preview measured WCAG ratios and ΔE under two colour-vision
    models, and showed swatches. That answers "is this colour legible", which
    is worth answering and is not what somebody opening a Design panel wants
    to know: they want to see the document they are about to send a board.

    So the real first two pages render here, through `render_report` itself —
    a preview drawn by a second implementation is a mock, and a mock drifts
    from the document it claims to show, which is the failure this panel
    already had one level up.

    The three fields beside it are the ones 0.3 wired into all three renderers
    and nothing ever offered: the face and the footer print on the summary
    page, and the number format repunctuates every figure on both.

    Mutations: drop `<ArtifactPreview>`; render it without a run and let it
    invent a cover; remove any of the three fields; put the logo back to a
    text box asking for a server-side path.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="design"]')

    # The three fields that had no control at all.
    for field in ("#brand-font_stack", "#brand-footer_text", "#design-locale"):
        assert page.locator(field).count() == 1, f"{field} is not on the panel"

    # A file input, not a text box asking a business user for a server path.
    logo = page.locator("#brand-logo_file")
    assert logo.count() == 1, "the logo is still typed rather than uploaded"
    assert logo.get_attribute("type") == "file"

    # And the artifact itself. The frame is filled asynchronously — the
    # component debounces, then the server renders — so this waits for it
    # rather than asserting on the first paint.
    page.wait_for_selector("#artifact-preview .preview-frame", timeout=20_000)
    frame = page.locator("#artifact-preview .preview-frame")
    data = frame.get_attribute("data") or ""
    assert data.startswith("blob:"), data[:80]
    # The viewer's own toolbar and thumbnail rail are suppressed: they took
    # about 40% of the frame and captioned it with the blob's UUID.
    assert "toolbar=0" in data and "navpanes=0" in data, data[-60:]


#: axe-core, injected into the page under test. A file rather than a CDN tag:
#: the CI runner has no general egress, and a gate that depends on the network
#: is a gate that goes quiet on a bad day.
AXE_JS = ROOT / "web" / "node_modules" / "axe-core" / "axe.min.js"


def _axe(page, where: str) -> None:
    """Assert this screen has no axe violations, and name them if it does."""
    page.add_script_tag(content=AXE_JS.read_text(encoding="utf-8"))
    violations = page.evaluate("""async () => {
        const r = await axe.run(document, {resultTypes: ['violations']});
        return r.violations.map(v => ({
            id: v.id, impact: v.impact, n: v.nodes.length,
            // The reason, not just the selector: "insufficient contrast of
            // 4.18 (#2a78d6 on #f9f9f7)" is actionable where a CSS path is a
            // starting point for another half hour.
            why: v.nodes.slice(0, 3).map(
                n => n.target.join(' ') + ' -- ' +
                     ((n.any[0] || n.all[0] || {}).message || '')),
        }));
    }""")
    assert not violations, "\n".join(
        [f"{where}: {len(violations)} axe rule(s) failing"]
        + [f"  {v['impact']} {v['id']} x{v['n']}\n      "
           + "\n      ".join(v["why"]) for v in violations])


def test_no_screen_has_an_axe_violation(page):
    """**The gate, and the reason it exists is what it found.**

    7.4's brief in the plan was written before the Preact rewrite and is
    stale — it says `grep focus-visible` returns 0 (8), `prefers-reduced-motion`
    0 (2), and `role="tablist"` with no `role="tab"` (there is no tablist).
    2.1, 3.5b and the port built most of that in.

    What no one had run is a checker. One pass reported **183 colour-contrast
    nodes across six screens, 125 on results alone**, because the AA text rule
    had only ever been applied to the brand colour a *user* supplies and never
    to the palette the product ships. Plus a heading that skipped a level and
    an `<aside role="dialog">`, which is a role that element may not carry.

    Asserted per screen rather than once, because a violation is a property of
    a rendered page: the tokens are shared but which of them carries text is
    not, and the results screen had two thirds of the failures.

    Every screen a first run passes through, including the two that need a
    finished run behind them. Mutation: revert any `--*-text` use in
    `styles.css` to its graphical twin.
    """
    # Audited with reduced motion, which does two things at once: it is a
    # rendering path a real user selects and it therefore deserves the sweep,
    # and it makes the sweep deterministic. Without it axe caught
    # `.question-help` mid-fade at 4.18:1 -- a colour that measures 7.53:1
    # once the animation lands. An audit racing an animation reports the
    # frame it happened to catch.
    page.emulate_media(reduced_motion="reduce")

    # **Not a `skipif`.** The obvious spelling was to skip when axe-core is
    # missing, and that makes the gate able to disappear without saying so:
    # `npm ci` stops installing devDependencies, the sweep quietly stops
    # running, and the next contrast regression ships green. A gate that can
    # silently skip is not a gate. The suite already skips as a whole when the
    # bundle is absent -- the honest condition, since the Python tests must not
    # require Node -- and anyone who has a bundle built it with `npm ci`, which
    # installs devDependencies. So reaching here without axe is a broken
    # install, not a valid configuration.
    assert AXE_JS.exists(), (
        f"{AXE_JS.relative_to(ROOT)} is missing while a built bundle is "
        f"present, so the accessibility gate would not have run. "
        f"Run `npm --prefix web ci`.")

    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.wait_for_selector("#res-scorecard")

    # Swept with the tour still up, because that is the screen a first-time
    # user actually gets. Dismissing first left the one overlay every new user
    # sees out of the audit entirely -- and it was carrying `role="dialog"` on
    # an `<aside>`, which that element may not have. Found by mutation: the
    # fix was in place and this test passed without it.
    page.wait_for_selector("#tour")
    _axe(page, "results (first run, tour up)")

    page.click("#tour-dismiss")
    _axe(page, "results")

    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="design"]')
    page.wait_for_timeout(300)
    _axe(page, "studio")

    # Path -> view id, taken from `web/src/lib/router.ts`. `/data` renders
    # `view-builder`, not `view-data`, which is the sort of thing a guessed
    # selector gets wrong and a timeout then reports as an accessibility
    # failure.
    base = page.url.split("/runs/")[0]
    for path, view in (("/", "home"), ("/samples", "samples"),
                       ("/survey", "survey"), ("/data", "builder")):
        page.goto(base + path)
        _visible(page, view)
        _axe(page, path)


# --------------------------------------------------------------------------
# 7.4b — what a keyboard can reach, and what it must not escape
# --------------------------------------------------------------------------

def _focus_id(page) -> str:
    """The id of whatever has focus, or the tag when it has no id."""
    return page.evaluate(
        "() => document.activeElement ? "
        "(document.activeElement.id || document.activeElement.tagName) : 'none'")


def test_a_modal_keeps_the_tab_key_inside_it(page):
    """`aria-modal="true"` was a promise the record sheet did not keep.

    It told a screen reader the rest of the page was inert while Tab walked
    straight out of the sheet and into the scorecard behind it — a keyboard
    user tabbing through a dialog ends up somewhere the scrim says does not
    exist, with no way back and nothing to press Escape on.

    Tabbing to the far end and once more must wrap, not leave. Measured by
    where focus lands rather than by reading the handler, because the trap is
    only as good as its list of focusable children — a filter that misses
    everything inside a `position: fixed` panel would pass a source check and
    fail here.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")

    page.locator("#res-scorecard .kpi-open").first.click()
    page.wait_for_selector("#kpi-sheet")

    inside = page.evaluate(
        "() => document.querySelectorAll('#kpi-sheet a[href], "
        "#kpi-sheet button:not([disabled])').length")
    assert inside > 0, "the record sheet has nothing focusable to trap"

    # One press past the last stop. Without the trap this leaves the dialog.
    for _ in range(inside + 1):
        page.keyboard.press("Tab")
    assert page.evaluate("() => document.getElementById('kpi-sheet')"
                         ".contains(document.activeElement)"), \
        f"Tab left the record sheet and landed on {_focus_id(page)}"

    # And backwards off the top, which is the half a naive trap forgets.
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => document.getElementById('kpi-sheet')"
                         ".contains(document.activeElement)"), \
        f"Shift+Tab left the record sheet and landed on {_focus_id(page)}"


def test_closing_an_overlay_gives_focus_back_to_what_opened_it(page):
    """Where a keyboard user is left when the panel goes away.

    All five overlays took focus and none gave it back, so dismissing one
    dropped focus on `<body>` and the next Tab started again from the top of
    the document. On the results screen that is roughly thirty stops back to
    the row you were reading.

    The drawer is the case with a definite answer: it is opened by a button
    that is still on screen afterwards, so "back where it came from" is
    checkable rather than a matter of taste.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    page.focus("#btn-history")
    page.keyboard.press("Enter")
    page.wait_for_selector("#drawer")
    assert page.evaluate("() => document.getElementById('drawer')"
                         ".contains(document.activeElement)"), \
        f"the drawer never took focus; it is on {_focus_id(page)}"

    page.keyboard.press("Escape")
    page.wait_for_selector("#drawer", state="detached")
    assert _focus_id(page) == "btn-history", \
        f"focus was dropped rather than restored; it is on {_focus_id(page)}"


def test_the_studio_rail_is_a_tab_strip_a_keyboard_can_drive(page):
    """Eight buttons that looked like tabs and claimed to be nothing.

    `.active` painted the selection and no attribute carried it, so a screen
    reader announced eight unrelated buttons with no indication that seven were
    closed or that pressing one replaced the region below. And eight tab stops
    for one choice is why the ARIA practices put a strip on a single stop with
    arrow keys inside it.

    Asserted through the keyboard rather than through the attributes alone: a
    roving `tabindex` that is never moved reads correctly in the DOM and traps
    a user on the first tab.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")

    rail = page.locator("#studio-rail")
    assert rail.get_attribute("role") == "tablist", \
        "the rail does not say it is a tab strip"
    assert page.locator('#studio-rail [role="tab"]').count() == 8, \
        "the rail's buttons are not tabs"
    assert page.locator('#studio-rail [aria-selected="true"]').count() == 1, \
        "no tab, or more than one, says it is the selected one"

    # One stop for the whole strip.
    assert page.locator('#studio-rail [tabindex="0"]').count() == 1, \
        "every tab is its own tab stop, so the rail costs eight presses to pass"

    page.focus('#studio-rail [data-stage="source"]')
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert _focus_id(page) == "rail-tab-model", \
        f"arrow keys do not move along the rail; focus is on {_focus_id(page)}"
    assert page.locator('[data-panel="model"]').count() == 1, \
        "arrowing to a tab did not open its panel"

    # Wraps rather than stopping dead, at both ends.
    page.keyboard.press("End")
    assert _focus_id(page) == "rail-tab-ai", _focus_id(page)
    page.keyboard.press("ArrowDown")
    assert _focus_id(page) == "rail-tab-source", \
        f"the strip does not wrap; focus is on {_focus_id(page)}"


def test_the_studio_rail_is_a_column_beside_the_panels(page):
    """Measured geometry, because a dead CSS selector is silent.

    `styles.css` laid the Studio out as `208px | 1fr` under `.studio`, the 1.1
    port renamed the element to `.studio-body`, and nothing connected the two —
    so the rule matched nothing and the rail rendered as eight full-width bars
    stacked above the panel, 1132px wide, sticky-positioned to no effect. The
    page rendered, every control worked, and every assertion about the Studio
    passed for four phases.

    Nothing except a measurement can see this: the failure is a layout that
    still lays out. Same class as 2.1's severity borders, where the selector
    never matched, and 4.3b's tile baselines — assert where things land, not
    that a rule exists.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.wait_for_selector("#studio-panels")

    rail = page.locator("#studio-rail").bounding_box()
    panels = page.locator("#studio-panels").bounding_box()
    assert rail and panels

    # Beside, not above. The viewport is 1280 wide, well clear of the 820px
    # breakpoint where stacking is the intended layout.
    assert rail["width"] < 300, \
        f"the rail is {rail['width']:.0f}px wide — it is not a rail"
    assert rail["x"] + rail["width"] <= panels["x"] + 1, \
        "the rail overlaps the panel it is meant to sit beside"
    assert abs(rail["y"] - panels["y"]) < 40, \
        "the rail sits above the panels rather than alongside them"


def test_a_toggle_s_hint_is_a_second_line_not_a_run_on(page):
    """"Plant deliberate eventschurn spike, CAC inflation, margin compression".

    `.toggle-sub` was an inline span immediately after the label text with
    nothing between them, so the two ran together into one string. Found in the
    same screenshot as the rail, which is the argument for taking one at all:
    both are layout, neither raises, and no assertion about either element's
    presence or text could see it.

    Measured as "the hint starts at the left edge of its own block", not as a
    source check — a margin, a space or a `<br>` would each be a different way
    of spelling the same fix, and pinning the spelling pins the wrong thing.

    **The first version of this assertion was green under its own mutation**,
    the way roughly one in three has been in this program. It compared the
    hint's top against its parent's, on the reasoning that a second line starts
    lower — but an inline span at 11.5px inside 13px text also sits a few
    pixels lower, because it shares a baseline and has a shorter cap height. So
    the measurement could not tell a line break from a smaller font. The left
    edge can: text that follows other text on a line does not start at the
    margin.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")

    sub = page.locator(".toggle .toggle-sub").first
    sub.wait_for()
    box = sub.bounding_box()
    label = sub.locator("xpath=..").bounding_box()
    assert box and label
    assert abs(box["x"] - label["x"]) < 2, \
        (f"the hint starts {box['x'] - label['x']:.0f}px in from its block, so "
         f"it is running on from the label rather than beginning a line")


# --------------------------------------------------------------------------
# 7.4d — printing the app, and the reader's own settings
# --------------------------------------------------------------------------

def test_printing_the_results_screen_drops_what_paper_cannot_offer(page):
    """Measured on the real screen before any rule was written.

    `web/src/styles.css` had **no `@media print` at all**, so Ctrl+P on the
    results screen gave you the app: the sticky topbar with Home / Recent runs
    / Dark, then "Adjust in Studio", "Open dashboard", "Columns (6)" and
    "Export CSV" — six controls a reader holding paper cannot press — over the
    off-white `--page` rather than white.

    The scorecard is the part people print, since 3.5 made it the surface
    carrying every metric's definition, so it stays and the chrome goes.

    Emulated rather than grepped for the at-rule: a `@media print` block whose
    selectors match nothing would pass a source check and change nothing on
    paper. `.sc-tools` was exactly that on the first attempt — the wrapper is
    `.scorecard-tools`, and only printing the page showed the two buttons
    still there.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")

    on_screen = page.eval_on_selector(
        ".topbar", "e => getComputedStyle(e).display")
    assert on_screen != "none", "the topbar is hidden on screen, so this proves nothing"

    page.emulate_media(media="print")
    for selector, what in ((".topbar", "the app's top bar"),
                           (".res-actions", "the run's action buttons"),
                           (".scorecard-tools", "the column and export controls")):
        assert page.eval_on_selector(
            selector, "e => getComputedStyle(e).display") == "none", \
            f"{what} ({selector}) is printed, and paper cannot offer it"

    assert page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor") \
        == "rgb(255, 255, 255)", "the page prints on the screen's off-white ground"

    # The table itself must survive: hiding the chrome is worthless if the
    # thing worth printing goes with it.
    assert page.eval_on_selector(
        "#res-scorecard", "e => getComputedStyle(e).display") != "none", \
        "the scorecard is hidden when printing, which is the only reason to print"


def test_no_control_is_smaller_than_a_pointer_can_hit(page):
    """WCAG 2.5.8: 24x24 CSS px, measured rather than assumed.

    **Measuring the wrong thing first is what makes this worth a docstring.**
    A sweep of `input, button, a[href]` at phone width reported 14 failures on
    the survey — every option's radio at 13x13. They are not failures: each is
    wrapped in a `label.option` measuring **312x46**, and the label is what a
    finger hits. Reporting them would have padded fourteen controls that were
    already the size of a card.

    The real ones were on the desktop scorecard and there were three: the
    column sort buttons, the worst at **19x18** for "KPI" — the smallest thing
    in the app and the only genuine breach. The 24 KPI-name links at 22px were
    a genuine judgement call rather than a false positive: 2.5.8's inline
    exception covers a target "constrained by the line-height of non-target
    text", and each of these is the entire content of its cell, so nothing
    constrained it and the exemption did not apply.

    Asserted over the effective target — the nearest label wrapping an input,
    the element itself otherwise — because that is what the criterion is about.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")
    page.wait_for_selector("#res-scorecard")

    small = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll(
          'button, a[href], input, select, [role="tab"], summary')) {
        // An input inside a label is hit by tapping the label.
        const target = el.closest('label') || el;
        const r = target.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (Math.min(r.width, r.height) >= 24) continue;
        out.push((el.id || el.className || el.tagName)
                 + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
      }
      return out;
    }""")
    assert not small, (
        f"{len(small)} control(s) below the 24x24 floor: " + ", ".join(small[:10]))


def test_asking_the_os_for_more_contrast_changes_something(page):
    """`prefers-contrast: more` had no rule in the app at all.

    The palette clears AA everywhere after 7.4a, so this is not about a
    failure — it is about a reader who has told the operating system that AA
    is not enough for them. The greys are what they lose first, and in this app
    the greys carry every record-sheet field label, every basis chip and every
    hint under a control.

    Measured on a computed colour rather than by grepping for the at-rule.
    7.4a's `prefers-reduced-motion` block existed and named exactly one
    selector, which read as support that was present; a query that matches
    nothing passes any source check.
    """
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#tour-dismiss")

    read = ("() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--muted').trim()")
    primary = ("() => getComputedStyle(document.documentElement)"
               ".getPropertyValue('--text-primary').trim()")

    page.emulate_media(contrast="no-preference")
    ordinary = page.evaluate(read)
    page.emulate_media(contrast="more")
    raised = page.evaluate(read)

    assert ordinary != raised, (
        f"--muted is {ordinary} whether or not the reader asked for more "
        f"contrast, so the query matches nothing")
    assert raised == page.evaluate(primary), (
        f"--muted became {raised} under more-contrast rather than collapsing "
        f"onto the primary text colour")


def _configured_ai(page):
    """Answer `GET /api/ai/status` as a machine with the AI layer installed.

    **The panel cannot render otherwise, and that is a property of the
    environment rather than of the code.** `availability()` is false unless the
    `anthropic` package is importable *and* a key is in the environment;
    neither is true here, nor on any CI runner — `requirements-ai.txt` is not
    installed by the matrix, deliberately, because the pipeline must never need
    it. So the AI panel renders its setup instructions instead of its controls,
    and every walk through it fails on a selector that is correctly absent.

    Routing the status endpoint is the honest way through: it makes the real
    component render with the real code, and it fakes nothing about the agent.
    No request that would reach a model is made below — those are covered in
    `tests/ai.py` against the transcript player and in `tests/test_store.py`
    against the endpoints. This is 3.1's deferral answered rather than
    repeated: that item left the AI payload untested because "there is no way
    to assert it in this suite", and for the *panel* there is one.
    """
    page.route("**/api/ai/status", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"available": True, "reason": "",
                         "default_model": "claude-opus-5",
                         "narratable_sections": ["exec_summary"]})))


def test_the_planner_can_be_told_what_the_report_is_for(page):
    """"Suggest changes" took no user input at all.

    `POST /api/ai/plan/{id}` had no body and `propose()` no goal, so the
    planner inferred what this reader wanted from the profile and the catalog,
    and the only way to steer it was to reject a patch and pay for another
    request. Verified against the code before it was built rather than taken
    from the plan.

    Mutations: drop the textarea; drop its `maxLength`; drop the label; drop
    the character count.
    """
    _configured_ai(page)
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="ai"]')

    box = page.locator("#ai-goal")
    assert box.count() == 1, "there is still no way to say what the report is for"
    assert box.get_attribute("maxlength") == "2000", (
        "the box does not bound what it will send, so the first a user hears "
        "of the limit is a 422 after they have written a page")
    # Named for a screen reader. Labelled by the visible heading rather than by
    # a hidden `<label>`: a `.visually-hidden` class would have been the very
    # defect this item's screenshot turned up, a class emitted with no rule
    # behind it. Asserted through the accessibility tree, so it holds however
    # the name is supplied.
    assert page.get_by_label("What is this report for?").count() == 1, \
        "the one free-text field in the panel has no accessible name"

    # The count is what makes the bound visible while typing rather than after.
    empty = page.text_content("#ai-goal-count")
    box.fill("This goes to the board and the question is the cash runway.")
    filled = page.text_content("#ai-goal-count")
    assert filled != empty and "2,000" in (filled or ""), (
        f"the character count did not respond to typing: {filled!r}")
    assert "59 of" in filled, f"the count is not counting: {filled!r}"


def test_an_estimate_priced_before_the_goal_says_it_is_stale(page):
    """The estimate prices the exact prompts, so it prices the goal too.

    Its docstring promises "the number in the studio is the number that will be
    spent rather than a guess at it". Once a goal exists, an estimate taken
    before it was typed answers a different question — the same drift this item
    found in the endpoint, arriving one layer up as a stale number rather than
    a wrong one.

    The estimate itself is routed, for the reason `_configured_ai` gives: the
    real endpoint needs a model to count tokens with. What is being asserted is
    the panel's own state machine — priced, then edited, then noticed.

    Mutations: drop the notice; compare against the wrong thing so it never
    fires; forget to record what the estimate was priced for so it fires
    always.
    """
    _configured_ai(page)
    page.route("**/api/ai/estimate/**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"worst_case_tokens": 24000,
                         "worst_case_cost_usd": 0.62,
                         "ceiling": 150000, "within_ceiling": True})))

    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="ai"]')

    assert page.locator("#ai-estimate-stale").count() == 0, \
        "the panel called the estimate stale before anything had been priced"

    page.fill("#ai-goal", "for the board")
    assert page.locator("#ai-estimate-stale").count() == 0, \
        "typing a goal with nothing priced yet should say nothing"

    page.click("#ai-estimate-btn")
    page.wait_for_selector("#ai-estimate strong")
    assert page.locator("#ai-estimate-stale").count() == 0, (
        "an estimate priced with the goal that is on screen is not stale")

    # The control for the ceiling notice, and the mutation sweep is what asked
    # for it: this run has a ceiling and is comfortably inside it. Without this
    # line, a panel that warned whenever a ceiling *existed* passed every
    # check — the over-ceiling walk only ever sees the case where both are
    # true. 5.3e's detector that could not say no, in a paragraph of copy.
    assert page.locator("#ai-over-ceiling").count() == 0, (
        "the panel warns about a ceiling this run is nowhere near, which is "
        "how a warning becomes invisible through repetition")

    page.fill("#ai-goal", "for the operating team, weekly")
    page.wait_for_selector("#ai-estimate-stale")
    assert "Estimate again" in (page.text_content("#ai-estimate-stale") or ""), (
        "the panel shows a price for a request nobody is going to send")


def test_a_run_over_its_ceiling_is_told_before_it_spends(page):
    """`max_tokens_per_run` bound nothing at all before this item.

    It shipped with the AI layer, defaulting to 150,000, with a docstring
    saying it was "checked against the pre-flight estimate before the first
    call". Measured: set to its floor of 1,000, a narrate spent 1,200 and
    nothing raised, noted or stopped.

    Said out loud only when the worst case would cross it. A limit reported on
    every run that is nowhere near it is the "invisible through repetition"
    failure 5.3d found in the basis badge and moved into a subtitle.

    Mutations: drop the warning; show it unconditionally; read `ceiling`
    instead of `within_ceiling` so it fires whenever a ceiling exists.
    """
    _configured_ai(page)
    page.route("**/api/ai/estimate/**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"worst_case_tokens": 240000,
                         "worst_case_cost_usd": 6.20,
                         "ceiling": 150000, "within_ceiling": False})))

    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="ai"]')

    assert page.locator("#ai-over-ceiling").count() == 0, \
        "the panel warned about a ceiling before pricing anything"
    page.click("#ai-estimate-btn")
    page.wait_for_selector("#ai-over-ceiling")
    warning = page.text_content("#ai-over-ceiling") or ""
    assert "150,000" in warning, f"the warning does not name the ceiling: {warning!r}"

    # **And it has to look like a caution.** `.warn` was emitted in six places
    # across four files and the stylesheet defined nothing for it, so every
    # error message in the Studio rendered as ordinary body text — 2.1's
    # `.finding.high` in a new file, found the same way and invisible to every
    # assertion above. Measured as a computed colour, per 2.1: a rule that
    # matches nothing passes any source check.
    styles = page.evaluate("""() => {
      const note = getComputedStyle(document.querySelector('#ai-over-ceiling'));
      const body = getComputedStyle(document.querySelector('#ai-estimate'));
      return {noteColor: note.color, bodyColor: body.color,
              rule: parseFloat(note.borderLeftWidth)};
    }""")
    assert styles["noteColor"] != styles["bodyColor"], (
        f"the caution is the same colour as the paragraph above it "
        f"({styles['noteColor']}), so nothing marks it as one")
    assert styles["rule"] >= 2, (
        f"the caution has no rule beside it ({styles['rule']}px), so it reads "
        f"as another sentence rather than as an aside")


def _a_plan(page, changes):
    """Answer `POST /api/ai/plan` with a patch, so the review modal has one.

    Routed for the reason `_configured_ai` gives — the agent is unreachable
    here and on every runner. What is under test is the review surface, and the
    **validation behind it is not routed**: `/api/ai/validate` runs no model,
    so the real endpoint answers, and an edit made below is graded by the same
    `planner.validate` that `apply` enforces and 6.3's corpus scores.
    """
    page.route("**/api/ai/plan/**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"summary": "Tightened for a board audience.",
                         "changes": changes})))


def _open_the_plan(page, changes):
    _configured_ai(page)
    _a_plan(page, changes)
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="ai"]')
    page.click("#ai-plan-btn")
    page.wait_for_selector("#plan-modal")


def test_a_proposed_value_can_be_edited_rather_than_only_refused(page):
    """The reviewer could accept or reject a change and nothing else.

    So somebody who agreed with the reasoning and not with the value had one
    move: reject it and pay for another request. "The user sees and can edit
    every change the AI made" was half true.

    Typed on the **proposed** value, never on the current one: measured across
    a real spec, 17 of 51 patchable paths currently hold `null`, which is why
    the diff has a `plan-unset` class at all. Typing off `before` would leave a
    third of the surface uneditable, and the third a planner most often fills
    in.

    Mutations: render the value read-only again; type the editor off `before`;
    drop the chip's remove button; drop the edited badge.
    """
    _open_the_plan(page, [
        {"path": "design.theme", "value": "dark", "before": "light",
         "rationale": "A board reads this printed.", "ok": True, "rejected": ""},
        {"path": "design.sections",
         "value": ["cover", "exec_summary", "appendix"], "before": None,
         "rationale": "Three sections, not nine.", "ok": True, "rejected": ""},
    ])

    # A string is a text box, not a JSON blob: 1.1 names "a Studio that asks
    # business users to type raw JSON" as a defect of the old app, and the one
    # screen where somebody decides whether to trust a model is the worst place
    # to bring it back.
    theme = page.locator('[data-plan-value="0"]')
    assert theme.count() == 1, "the proposed value is still read-only"
    assert theme.get_attribute("type") == "text", \
        f"a string value is edited as {theme.get_attribute('type')!r}"

    # A list is chips, and the edit a reviewer actually makes is dropping one.
    chips = page.locator('[data-plan-value="1"] .plan-chip')
    assert chips.count() == 3, f"expected three ids, found {chips.count()}"
    page.click('[data-plan-drop="1:2"]')
    page.wait_for_selector('[data-plan-edited="1"]')
    assert page.locator('[data-plan-value="1"] .plan-chip').count() == 2

    # And the edit is marked, with the planner's own value still on screen —
    # applying a value the model did not propose under the model's rationale,
    # with nothing saying so, is the provenance mistake 6.2a found in
    # `plan_basis`.
    said = page.text_content('[data-plan-edited="1"]') or ""
    assert "appendix" in said, f"the planner's own value is lost: {said!r}"

    # **And the modal gets 7.4d's target-size sweep, which never reached it.**
    # That item measured home, samples, the survey, the upload funnel and the
    # results screen and found three breaches on the scorecard. It could not
    # open this screen: the review modal needs a plan, and a plan needs a key.
    # Measured here rather than assumed — the row checkboxes were 688x20.
    small = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll(
          '#plan-modal button, #plan-modal input, #plan-modal a[href]')) {
        const target = el.closest('label') || el;
        const box = target.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) continue;
        if (Math.min(box.width, box.height) >= 24) continue;
        out.push((el.id || el.className || el.tagName)
                 + ' ' + Math.round(box.width) + 'x' + Math.round(box.height));
      }
      return out;
    }""")
    assert not small, (
        f"{len(small)} control(s) in the review modal below the 24x24 floor: "
        + ", ".join(small[:10]))


def test_an_edit_is_graded_by_the_gate_that_will_decide_it(page):
    """An illegal edit is refused beside itself, not as a 422 on Apply.

    `/api/ai/validate` is deliberately not routed here: it runs no model, so
    the real endpoint answers and the verdict below is `planner.validate`'s —
    the same function `apply` enforces. A review screen graded by a second
    implementation would be one that disagrees with what it is reviewing for.

    Mutations: never re-validate; keep an illegal change ticked; ignore the
    server's verdict and keep the local one.

    **The first version of this test could not fail.** It proposed
    `metrics.excluded: ["arr"]` and removed the chip — but an empty list is
    perfectly legal, so the edit it called illegal was not one. Measured
    against the real guard rather than assumed, which is the same premise
    failure 5.3c hit twice on a minimum it could not reach from the samples.
    """
    _open_the_plan(page, [
        {"path": "design.theme", "value": "dark", "before": "light",
         "rationale": "A board reads this printed.", "ok": True, "rejected": ""},
    ])

    assert page.locator("#plan-count").inner_text().startswith("1 of 1"), \
        "a legal change should arrive ticked"

    # A theme the schema does not define. Individually well-formed, and the
    # whole-spec parse is what catches it — the second of `validate`'s two
    # passes, run over the reviewer's typing rather than only the planner's.
    page.fill('[data-plan-value="0"]', "chartreuse")
    page.wait_for_selector(".plan-refused", timeout=10_000)
    refusal = page.text_content(".plan-reject") or ""
    assert refusal, "the edit was refused with no reason beside it"

    # And it is no longer counted, because it can no longer be applied.
    assert page.locator("#plan-count").inner_text().startswith("0 of 1"), \
        "an edit that made the change illegal left it ticked"
    assert page.locator("#plan-apply").is_disabled(), \
        "Apply is offering to send a patch the server has already refused"


def test_the_run_shows_what_has_already_been_applied(page):
    """0.7 recorded every spec a run built from; nothing read them.

    A table with no consumer is the pattern that phase existed to stop, and it
    shipped with one of its own — the same "computed and rendered nowhere" gap
    5.3a-c closed three times in the exhibits.

    Mutations: drop the list; drop the author, so a planner change and a hand
    edit look alike.
    """
    _configured_ai(page)
    _start_first_sample(page)
    page.wait_for_selector("#view-results:not([hidden])", timeout=RUN_TIMEOUT_MS)
    page.click("#res-adjust")
    _visible(page, "studio")
    page.click('#studio-rail [data-stage="ai"]')

    page.wait_for_selector("#plan-history li")
    rows = page.locator("#plan-history li")
    assert rows.count() >= 1, "the run has built from at least one spec"
    first = rows.first.inner_text()
    assert "user" in first.lower(), \
        f"the row does not say who made this version: {first!r}"
    assert "sample" in first.lower(), \
        f"the row does not say what it was: {first!r}"


def test_general_workspace_edits_undo_and_persist(page):
    """The non-business case follows the same calculation and persistence contract."""
    page.click('[data-nav="explore"]')
    page.wait_for_selector('#view-explore')
    page.locator('.mode-card').filter(has_text='Pokemon VGC').click()
    page.wait_for_selector('.explore-kpis')
    assert page.locator('.explore-kpis strong').all_text_contents() == ['0.5', '160']
    page.get_by_role('button', name='Studio', exact=True).click()
    page.get_by_label('Group by', exact=True).select_option('event')
    with page.expect_response(lambda r: '/api/explore/projects/' in r.url and r.request.method == 'PUT'):
        page.get_by_role('button', name='Apply and recalculate').click()
    page.get_by_role('button', name='Dashboard', exact=True).click()
    _playwright.expect(page.locator('.explore-scroll tbody tr')).to_have_count(5)
    page.reload()
    page.wait_for_selector('.explore-kpis')
    _playwright.expect(page.locator('.explore-scroll tbody tr')).to_have_count(5)
    page.get_by_role('button', name='Studio', exact=True).click()
    with page.expect_response(lambda r: r.url.endswith('/undo')):
        page.get_by_role('button', name='Undo last edit').click()
    page.get_by_role('button', name='Dashboard', exact=True).click()
    _playwright.expect(page.locator('.explore-scroll tbody tr')).to_have_count(4)
    with page.expect_download() as download:
        page.get_by_role('link', name='PDF ↓', exact=True).click()
    assert download.value.suggested_filename.endswith('.pdf')
