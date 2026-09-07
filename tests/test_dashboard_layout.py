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


# --------------------------------------------------------------------------
# 7.4c — the artifact's own accessibility
# --------------------------------------------------------------------------

AXE_JS = ROOT / "web" / "node_modules" / "axe-core" / "axe.min.js"


def _settled(page) -> None:
    """Wait until Plotly's responsive resize has run, before auditing anything.

    Plotly lays each figure out at a fixed width and then resizes it to its
    container, so for the first frames one chart is **700px wide inside a 570px
    card** and the card is a scrollable region that nothing can focus. axe
    reports that as `scrollable-region-focusable`, and it is real for exactly
    as long as the resize takes: measured on this dashboard, present at 0ms and
    gone by 200ms.

    Waited on as a condition rather than slept through, and this is 7.4a's
    lesson arriving in a new place — that sweep kept catching `.question-help`
    mid-fade at 4.18:1, a colour that measures 7.53:1 once it lands. **An audit
    that races a layout grades the frame it happened to catch.** A fixed sleep
    would be the same race with a different constant.
    """
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('.theme-set.active .plot'))
             .every(e => e.scrollWidth <= e.clientWidth
                      && e.scrollHeight <= e.clientHeight)""",
        timeout=15_000)


def _axe(page, where: str) -> None:
    """Run axe over the current page and report what failed and why.

    The selector alone is not actionable — "`.chip-amber`" says nothing a
    reader can fix — so each violation carries its own failure summary, which
    is where the measured ratio and the two colours are.
    """
    _settled(page)
    page.add_script_tag(content=AXE_JS.read_text(encoding="utf-8"))
    result = page.evaluate(
        "async () => await window.axe.run(document, {runOnly: {type: 'tag', "
        "values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
    problems = []
    for violation in result["violations"]:
        for node in violation["nodes"]:
            problems.append(
                f"{violation['id']} at {node['target']}: "
                + node["failureSummary"].splitlines()[-1].strip())
    assert not problems, (
        f"{len(problems)} accessibility violation(s) on the dashboard "
        f"in {where}:\n  " + "\n  ".join(problems[:12]))


def test_the_dashboard_a_board_is_emailed_has_no_axe_violation(tmp_path):
    """The one artifact with no gate on it, and it is the one that gets sent.

    7.4a put the app under an axe sweep and fixed 183 nodes. It did not reach
    here, and the reason is instructive rather than an oversight: the `-text`
    twins were derived inside `tools/gen_tokens.py`, which writes
    `web/src/tokens.css` and nothing else, while `render/dashboard.py` builds
    its own `:root` from the same `viz.theme.TOKENS`. One palette, two
    consumers, one of them fixed. Measured before this item: **14 nodes in
    light, 7 in dark**, plus a *critical* `aria-required-children` — the
    `role="tablist"` whose children were plain buttons, so a screen reader
    announced a tab list containing no tabs.

    **Both themes, because they disagree about which roles fail.** Light fails
    `good` and `serious`; dark fails `critical` and passes both of those. A
    sweep of one mode would have reported the other clean.

    Lives in this module rather than `test_smoke_ui.py` for the reason that
    module's own docstring gives: the sync Playwright API cannot run after
    those tests establish an asyncio loop in the same thread. This one needs no
    server — it renders a dashboard and opens the file.
    """
    from kpi_maker.cli import load_profile, run_pipeline

    assert AXE_JS.exists(), (
        f"{AXE_JS.relative_to(ROOT)} is missing, so the artifact's "
        f"accessibility gate would not have run. Run `npm --prefix web ci`.")

    out = tmp_path / "run"
    run_pipeline(load_profile(ROOT / "samples" / "kestrel_retail.json"),
                 out, quiet=True)

    executable = _chromium_executable()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            **({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # Reduced motion for the same two reasons as the app's sweep: it is a
        # real user's rendering path, and it stops the audit racing a fade and
        # reporting the frame it happened to catch.
        page.emulate_media(reduced_motion="reduce")
        page.goto((out / "dashboard.html").as_uri())
        page.wait_for_selector(".tab-btn")
        _axe(page, "light mode")

        page.click("#themeToggle")
        page.wait_for_timeout(300)
        _axe(page, "dark mode")
        browser.close()


def test_every_colour_the_dashboard_paints_as_text_clears_aa(tmp_path):
    """The half a rendered sweep cannot see, and the gap is measurable.

    axe grades the elements one company's dashboard happened to draw, so a
    declaration no sample exercises is a declaration nothing checks. Counted on
    the retailer's own rendered file: `.pitfall` appears 20 times, `.chip-green`
    7, `.delta-bad` once — and **`.variance-good` and `.variance-bad` appear
    zero times**, because 5.1's rule is that a run with no plan renders no
    variance at all. Two colour declarations that a planned run puts on every
    scorecard row are invisible to a sweep of this sample. Add a sample with a
    plan and they appear; change the palette and they could appear failing,
    with the suite green the whole way.

    That is the "grading the samples rather than the rule" trap in its
    accessibility form, and the answer is the usual one: check the rule. Read
    the tokens `render/dashboard.py` actually sets `color:` with, out of the
    file, and measure each against the two grounds it can sit on, in both
    modes.

    Parsed rather than listed, because a hand-kept list of "the colours we use
    as text" goes stale the next time somebody adds a rule — 5.4a's exhibit
    plan and 7.4b's overlay list, again. The lookbehind matters: a naive
    `color:` also matches `border-color` and `background-color`, and the first
    version of this test failed on `--border`, which is a translucent rgba and
    not text at all. A border that carries meaning is WCAG 1.4.11 at 3:1 — a
    different rule for a different population, which is the mistake this repo
    keeps making in both directions.
    """
    import re

    from kpi_maker.design.contrast import AA_TEXT, ratio
    from kpi_maker.viz.theme import TOKENS

    source = (ROOT / "kpi_maker" / "render" / "dashboard.py").read_text(
        encoding="utf-8")
    used = sorted({m.replace("-", "_") for m in re.findall(
        r"(?<![-a-z])color:\s*var\(--([a-z0-9-]+)\)", source)})
    assert used, "no `color: var(--…)` declarations found — did the parse break?"

    failures = []
    for mode in ("light", "dark"):
        tokens = TOKENS[mode]
        for name in used:
            value = tokens.get(name)
            if value is None:
                # A `-text` twin or a chrome-only variable, neither of which is
                # a palette token. The twins are derived by `design/tokens.py`
                # from a token that IS here, and `test_packaging.py` holds them
                # to the same bar.
                continue
            for ground in ("page", "surface"):
                measured = ratio(value, tokens[ground])
                if measured < AA_TEXT:
                    failures.append(
                        f"{mode} --{name.replace('_', '-')} {value} is "
                        f"{measured:.2f}:1 against {ground} "
                        f"({tokens[ground]}), below {AA_TEXT}")

    assert not failures, (
        "the dashboard paints a colour as text that a reader cannot read:\n  "
        + "\n  ".join(failures))


def test_the_dashboard_prints_and_respects_the_reader_s_settings(tmp_path):
    """Three media queries, checked by emulating them rather than by grepping.

    `render/dashboard.py` had **zero** `@media print`, `prefers-reduced-motion`
    and `prefers-contrast` rules, in the one file this product emails to a
    board. Printing it gave you the screen: whichever theme the reader last
    toggled, a layout sized for 1280px, one tab's exhibits with the other four
    behind buttons that do nothing on paper, and cards split across breaks.

    Asserted through `emulate_media` because that is what a reader's browser
    does. A grep for the at-rules would pass on a query that exists and selects
    nothing — 7.4a's `prefers-reduced-motion` block named exactly one selector
    and read as support that was present, which is the failure this style of
    check is written against.

    The print half tests the two properties that matter to a reader holding
    paper: every exhibit is on it, and the controls that cannot be pressed are
    not. Whether a card straddles a page break is a `break-inside` declaration
    with no observable geometry in a headless browser, so it is left to the
    stylesheet rather than asserted through it.
    """
    from kpi_maker.cli import load_profile, run_pipeline

    out = tmp_path / "run"
    run_pipeline(load_profile(ROOT / "samples" / "kestrel_retail.json"),
                 out, quiet=True)

    executable = _chromium_executable()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            **({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto((out / "dashboard.html").as_uri())
        page.wait_for_selector(".tab-btn")

        on_screen = page.eval_on_selector_all(
            ".theme-set.active .tab-panel",
            "els => els.filter(e => getComputedStyle(e).display !== 'none').length")
        total = page.eval_on_selector_all(
            ".theme-set.active .tab-panel", "els => els.length")
        assert total > 1, "this sample has one tab, so the print rule is untested"
        assert on_screen == 1, \
            f"{on_screen} of {total} panels are open on screen before printing"

        page.emulate_media(media="print")
        printed = page.eval_on_selector_all(
            ".theme-set.active .tab-panel",
            "els => els.filter(e => getComputedStyle(e).display !== 'none').length")
        assert printed == total, (
            f"only {printed} of {total} exhibit groups reach the paper; the "
            f"rest are behind tab buttons the reader cannot press")
        for hidden in (".tabs", ".theme-toggle"):
            assert page.eval_on_selector(
                hidden, "e => getComputedStyle(e).display") == "none", \
                f"{hidden} is printed, and it is a control paper cannot offer"

        page.emulate_media(media="screen", reduced_motion="reduce")
        # Read off an element that actually carries a transition, so a query
        # that exists and matches nothing cannot pass this.
        moved = page.eval_on_selector(
            ".tab-btn", "e => getComputedStyle(e).transitionDuration")
        assert moved in ("0.01ms", "1e-05s"), \
            f"reduced motion leaves a {moved} transition on the tab buttons"

        page.emulate_media(reduced_motion="no-preference", forced_colors=None)
        browser.close()


def test_the_record_sheets_are_open_when_the_dashboard_prints(tmp_path):
    """The appendix must not print as twenty headings with nothing under them.

    Every KPI definition on the dashboard is a collapsed `<details>`, which is
    right on screen and wrong on paper: a reader holding the printout cannot
    click, so a closed disclosure prints as a name and an owner with the
    formula, grain, pitfalls and interpretation silently gone. That is the
    appendix disappearing from a board pack.

    CSS cannot fix it — the closed content is hidden by the user agent, not by
    a rule this stylesheet can override — so it is a `beforeprint` handler,
    with `afterprint` putting the reader's own state back.

    **Driven by dispatching the event rather than by generating a PDF, and the
    difference is a measurement rather than a convenience.** Chromium's
    headless `page.pdf()` does not dispatch `beforeprint`; printing from a real
    browser does. A PDF generated here therefore shows the disclosures closed
    whether or not the handler works, so asserting on one would have been a
    test that could not fail — and, read the other way, the 14-page PDF this
    item produced while checking its own work understates what a reader gets.
    """
    from kpi_maker.cli import load_profile, run_pipeline

    out = tmp_path / "run"
    run_pipeline(load_profile(ROOT / "samples" / "kestrel_retail.json"),
                 out, quiet=True)

    executable = _chromium_executable()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            **({"executable_path": executable} if executable else {}))
        page = browser.new_page()
        page.goto((out / "dashboard.html").as_uri())
        page.wait_for_selector(".kpi-def")

        total = page.evaluate("() => document.querySelectorAll('details').length")
        assert total > 5, f"only {total} disclosures — is this the right page?"
        assert page.evaluate(
            "() => document.querySelectorAll('details[open]').length") == 0, \
            "the disclosures start open, so this proves nothing"

        page.evaluate("() => window.dispatchEvent(new Event('beforeprint'))")
        opened = page.evaluate(
            "() => document.querySelectorAll('details[open]').length")
        assert opened == total, (
            f"{total - opened} of {total} record sheets would print as a "
            f"heading with their definition missing")

        # And the reader's own state comes back: a printout must not silently
        # leave twenty panels expanded on the screen behind it.
        page.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
        assert page.evaluate(
            "() => document.querySelectorAll('details[open]').length") == 0, \
            "printing left every disclosure open afterwards"
        browser.close()
