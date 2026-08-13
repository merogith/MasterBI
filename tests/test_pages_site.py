"""The hosted demo, driven in a browser before it is deployed.

Three times running, building the Pages site and clicking through it found a
bug the build succeeding did not: a deployment root stamped in as a path where
an absolute URL was required, artifact links joined into `/MasterBIfiles/...`,
and a shim guard reading a global the bundle no longer exposes. None of those
are visible from `tools/build_pages.py` exiting zero.

So this is that walk, automated. It runs against a *built* site — the Pages
workflow builds one and then runs this — and is skipped anywhere there is not
one, because building it takes four full pipeline runs and does not belong in
the per-push suite.

What it asserts is what only the hosted build can get wrong: the sub-path, the
`404.html` fallback that makes a shared link resolve, the frozen JSON standing
in for the API, and the read-only Studio.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SITE = Path(os.environ.get("MASTERBI_SITE") or ROOT / "site")
PREFIX = "/MasterBI"

_playwright = pytest.importorskip(
    "playwright.sync_api", reason="needs `pip install playwright`")
sync_playwright = _playwright.sync_playwright

if not (SITE / "index.html").exists():
    pytest.skip(f"no built site at {SITE} — run `python -m tools.build_pages`",
                allow_module_level=True)


def _chromium_executable() -> str | None:
    """Playwright's pinned build, or whatever Chromium this machine has."""
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
def base():
    from tools.serve_pages import serve

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    httpd = serve(SITE, PREFIX, port)
    try:
        yield f"http://127.0.0.1:{port}{PREFIX}"
    finally:
        httpd.shutdown()
        httpd.server_close()


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
def page(browser, base):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(25_000)
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{base}/", wait_until="domcontentloaded")
    try:
        yield page
    finally:
        context.close()
    assert not errors, "uncaught JS errors: " + "; ".join(errors)


# --------------------------------------------------------------------------

def test_the_demo_says_it_is_a_demo(page):
    """Modes 2 and 3 cannot run on a static host, and the page has to say so
    rather than offering buttons that fail."""
    page.wait_for_selector("#view-home:not([hidden])")
    assert page.locator(".mode-card").count() >= 3
    assert "Demo mode" in page.locator(".static-notice").inner_text()


def test_a_shared_link_resolves(page, base):
    """The `404.html` fallback, which is the only reason a deep link works.

    Pages has no rewrite rule: without the shell served as the 404 page, every
    URL into this site except the root is a 404 — and the root is the one URL
    nobody needs to share.
    """
    page.goto(f"{base}/samples", wait_until="domcontentloaded")
    page.wait_for_selector("#sample-grid [data-sample]")
    assert page.locator("#sample-grid [data-sample]").count() >= 3
    assert page.url.endswith("/samples"), "the shared URL was not preserved"


def test_a_frozen_run_opens_with_its_artifacts(page):
    """The pre-rendered gallery, reached the way a visitor reaches it."""
    page.click("#btn-history")
    page.wait_for_selector(".run-row [data-open-run]")
    page.locator(".run-row [data-open-run]").first.click()

    page.wait_for_selector("#view-results:not([hidden])")
    assert page.locator("#res-tiles .tile").count() > 0
    assert page.locator("#res-downloads .dl-card").count() >= 5


def test_artifact_links_point_at_files_that_exist(page):
    """Joined, not concatenated. This shipped as `/MasterBIfiles/...` once —
    a 404 on every download card, invisible from the server where the base is
    empty."""
    page.click("#btn-history")
    page.wait_for_selector(".run-row [data-open-run]")
    page.locator(".run-row [data-open-run]").first.click()
    page.wait_for_selector("#res-downloads .dl-card")

    href = page.locator("#res-downloads .dl-card").first.get_attribute("href")
    assert href and f"{PREFIX}/files/" in href, f"malformed artifact URL: {href}"
    assert page.request.get(href).ok, f"artifact URL does not resolve: {href}"


def test_the_studio_opens_read_only(page, base):
    """The demo's Studio shows what a run was built from and changes nothing.

    It used to 404 outright — the most differentiated screen in the product,
    invisible to everyone who clicked the public link. Every control is
    disabled because changing a spec means re-running the pipeline, which a
    static host cannot do.
    """
    page.goto(f"{base}/runs/northwind_saas/studio", wait_until="domcontentloaded")
    page.wait_for_selector("#studio-rail [data-stage]")

    assert page.locator("#studio-company").inner_text().strip()
    assert "Read-only" in page.locator("#view-studio .notice").first.inner_text()
    assert page.locator("#studio-rerun").is_disabled()
    assert page.locator("#studio-revert").is_disabled()

    page.click('#studio-rail [data-stage="design"]')
    theme = page.locator('[data-spec="design.theme"]')
    assert theme.is_disabled(), "a read-only Studio must not accept edits"
    assert theme.input_value(), "the panel shows nothing about the actual run"
