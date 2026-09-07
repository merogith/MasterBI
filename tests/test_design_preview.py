"""The Design panel shows the artifact, not the palette.

The brand preview was the best-built thing in the app and it answered the
wrong question. Swatches and WCAG ratios tell you whether a colour is legible;
somebody opening a Design panel wants to know what the document they are about
to send a board looks like. Three things follow from that, and they are this
file's three groups:

  * the preview is rendered by `render_report` itself, so it cannot drift from
    the document it claims to show;
  * the fields that change how it looks — `brand.font_stack`,
    `brand.footer_text` and `design.locale` — were wired into all three
    renderers by 0.3 and offered by nothing, and are now edited beside it;
  * the logo is uploaded rather than typed as a server-side path.

Every test here was verified to fail with its fix reverted; the mutation is
named in each docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pypdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kpi_maker.cli import load_profile  # noqa: E402
from kpi_maker.pipeline.runner import execute  # noqa: E402
from kpi_maker.spec.schema import DesignSpec, RunSpec  # noqa: E402

SAMPLE = ROOT / "samples" / "orbis_works.json"


@pytest.fixture(scope="module")
def profile():
    return load_profile(SAMPLE)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The server module pointed at a throwaway tree.

    Called as functions rather than over HTTP, the way `test_progress.py` and
    `test_upload_run.py` drive the API, so the suite still needs no client.
    """
    from kpi_maker.api import server

    runs = tmp_path / "runs"
    uploads = runs / "_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "RUNS_DIR", runs)
    monkeypatch.setattr(server, "UPLOADS_DIR", uploads)
    return server


@pytest.fixture
def run(api, profile, tmp_path):
    """A finished run on disk, which is what the preview needs and why it
    refuses without one."""
    import json

    run_id = "previewrun"
    run_dir = api.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    spec = RunSpec(profile=profile)
    execute(spec, run_dir, uploads_dir=api.UPLOADS_DIR)
    (run_dir / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
    assert json.loads((run_dir / "spec.json").read_text())["design"] is not None
    return run_id


def _pages(api, run_id, design=None):
    from kpi_maker.api.server import DesignPreviewRequest

    response = api.preview_pages(DesignPreviewRequest(run_id=run_id, design=design))
    return pypdf.PdfReader(__import__("io").BytesIO(response.body))


# --------------------------------------------------------------------------
# The contents page, which 5.4b added and did not gate
# --------------------------------------------------------------------------

def test_a_short_report_spends_no_page_on_a_one_line_contents(profile, tmp_path):
    """`design.sections` is a Studio control, so a two-section report is
    something a user can ask for today — and it spent a whole page on
    "Contents / 1. Executive summary ...... 3": one entry, pointing at the
    page immediately after it.

    5.4b added the contents page and checked only that it appears. This is the
    5.3d failure in another costume — a device that helps only in the case it
    is not in — found while measuring what the 5.5 preview would inherit,
    because the preview renders exactly two sections.

    Measured: the two-section report went 3 pages to 2, and the full report is
    unchanged at 16.

    Mutation: drop the `numbered >= MIN_SECTIONS_FOR_CONTENTS` guard.
    """
    def render(sections):
        out = tmp_path / f"n{len(sections or [])}"
        spec = RunSpec(profile=profile)
        spec.design.sections = sections
        execute(spec, out, artifacts=["report_pdf"])
        reader = pypdf.PdfReader(str(out / "report.pdf"))
        has = any("Contents" in (p.extract_text() or "") for p in reader.pages)
        return len(reader.pages), has

    short_pages, short_toc = render(["cover", "exec_summary"])
    assert not short_toc, "a one-entry contents page is worse than none"
    assert short_pages == 2, short_pages

    # Three numbered sections is where thumbing starts to beat reading the
    # headings, and the shipped report has eight — so the guard must not have
    # quietly disabled the feature it is protecting.
    _, three_toc = render(["cover", "exec_summary", "scorecard", "risks"])
    assert three_toc, "the contents page vanished from a report that wants one"

    full_pages, full_toc = render(None)
    assert full_toc and full_pages > 10, (full_pages, full_toc)


# --------------------------------------------------------------------------
# The preview is the artifact
# --------------------------------------------------------------------------

def test_the_preview_is_the_real_first_two_pages(api, run):
    """Not a mock of them.

    The one thing this must never be is a second implementation: a preview
    drawn by its own code drifts from the document, which is the failure the
    panel already had one level up. So it asserts the preview's cover carries
    what the run's own cover carries.

    Mutation: render the preview from anything but `render_report`, or drop
    `section_order=PREVIEW_SECTIONS` so it renders the whole report.
    """
    reader = _pages(api, run)
    assert len(reader.pages) == 2, len(reader.pages)

    cover = reader.pages[0].extract_text() or ""
    assert "Orbis Works" in cover
    assert "PERFORMANCE REVIEW" in cover
    # The window, which was blank until `RunResult.period` existed — the cover
    # read "MANUFACTURING · B2B · DE ·" with a trailing separator and nothing
    # after it.
    assert " to " in cover, cover[:200]
    assert "Executive summary" in (reader.pages[1].extract_text() or "")
    # And no contents page: two sections is below the floor.
    assert "Contents" not in cover


def test_the_period_is_not_blank(api, run):
    """`_run_inputs` hardcoded `period: ""`, which reached the AI narrator's
    section briefs as well as, once 5.5 existed, a rendered cover.

    Asserted on the *shape* rather than the months, because the window moves
    with `MASTERBI_HISTORY_END` and a pinned figure would be measuring the
    calendar rather than the fix — 5.3f's lesson.

    Mutation: put `period=""` back in `_run_inputs`.
    """
    import re

    spec = api._load_spec(run)
    inputs = api._run_inputs(run, spec)
    assert re.fullmatch(r"\d{4}-\d{2} to \d{4}-\d{2}", inputs["period"]), \
        inputs["period"]


def test_every_design_field_the_panel_offers_changes_the_page(api, run):
    """An outcome test, for `test_design_fields.py`'s reason: "the parameter
    reaches the function" is exactly what was true of these fields before 0.3
    wired them, and they still produced nothing.

    Mutation: stop passing any one of `locale`, `page_size` or `footer_text`
    from `preview_pages` into `render_report`.
    """
    plain = _pages(api, run)
    assert round(float(plain.pages[0].mediabox.width)) == 595  # A4

    branded = _pages(api, run, {
        "brand": {"primary": "#7a3fd6", "footer_text": "Confidential — board"},
        "locale": "de-DE", "page_size": "Letter"})

    assert round(float(branded.pages[0].mediabox.width)) == 612  # Letter
    assert "Confidential" in (branded.pages[1].extract_text() or "")

    # The locale repunctuates the cover's own figure: 38.0M -> 38,0M.
    assert "38,0" in (branded.pages[0].extract_text() or "")
    assert "38.0" in (plain.pages[0].extract_text() or "")


def test_a_design_field_that_does_not_exist_is_refused(api, run):
    """**Found by getting it wrong.** The first preview request written
    against this endpoint put `footer_text` at the design level, where it
    belongs to `BrandSpec` — and the preview came back cheerfully rendering
    the default footer, because `SpecModel` ignores unknown keys.

    A user editing a field and watching the preview not change would conclude
    the feature is broken. The nested case is the one that matters most: a
    typo inside `brand` is exactly what a hand-written payload gets wrong.

    Mutation: drop the `unknown_spec_fields` check, or make it non-recursive.
    """
    from fastapi import HTTPException

    from kpi_maker.api.server import DesignPreviewRequest

    for payload, expected in (({"footer_text": "x"}, "footer_text"),
                              ({"brand": {"footer_txt": "x"}}, "brand.footer_txt")):
        with pytest.raises(HTTPException) as caught:
            api.preview_pages(DesignPreviewRequest(run_id=run, design=payload))
        assert caught.value.status_code == 422
        assert expected in str(caught.value.detail)

    # And the correct spelling is still accepted, or the guard has just broken
    # the feature it protects.
    good = _pages(api, run, {"brand": {"footer_text": "Confidential — board"}})
    assert "Confidential" in (good.pages[1].extract_text() or "")


def test_unknown_spec_fields_leaves_a_valid_document_alone():
    """The walker itself, on the model it guards.

    Kept separate from the endpoint so a change to `DesignSpec` fails here
    with a readable name rather than inside a PDF assertion.

    Mutation: return every key rather than only the undefined ones.
    """
    from kpi_maker.api.server import unknown_spec_fields

    assert unknown_spec_fields({"theme": "dark", "page_size": "Letter",
                                "brand": {"primary": "#123456"}},
                               DesignSpec) == []
    assert unknown_spec_fields({"brand": {"primary": "#123456", "nope": 1}},
                               DesignSpec) == ["brand.nope"]
    # A non-dict value under a model field is the model's problem, not this
    # walker's: it must not crash trying to recurse into a string.
    assert unknown_spec_fields({"sections": ["cover"]}, DesignSpec) == []


def test_the_preview_refuses_without_a_run(api):
    """No run, no preview, rather than a plausible cover.

    The cover's figure is the company's north star and the summary's bullets
    are the run's findings; inventing either would make the panel show a
    different document from the one it edits — 5.1's "no plan means no
    variance", applied to a page.

    Mutation: fall back to a synthetic profile when the run is missing.
    """
    from fastapi import HTTPException

    from kpi_maker.api.server import DesignPreviewRequest

    with pytest.raises(HTTPException) as caught:
        api.preview_pages(DesignPreviewRequest(run_id="nosuchrun"))
    assert caught.value.status_code == 404


# --------------------------------------------------------------------------
# The logo, which was a text box asking for a server-side path
# --------------------------------------------------------------------------

def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (120, 40), (122, 63, 214)).save(path)
    return path


def test_an_uploaded_logo_comes_back_as_a_name_the_spec_accepts(api, tmp_path):
    """The field wanted `brand.logo_path`, the loader resolves a bare name
    inside the uploads directory, and there was no route that would ever put a
    file there — `/api/ingest/profile` takes spreadsheets. So the control was
    a text box captioned "path or uploaded filename" on the one screen whose
    subject is what the artifact looks like.

    The returned name has to be what the spec wants, not a path: the run
    resolves it the same way whoever opens the run next will.

    Mutation: return the absolute path instead of the stored name.
    """
    import asyncio

    from fastapi import UploadFile

    from kpi_maker.design.logo import load_logo

    source = _png(tmp_path / "mark.png")
    with source.open("rb") as handle:
        result = asyncio.run(api.upload_logo(
            UploadFile(filename="mark.png", file=handle)))

    assert result.mime == "image/png"
    assert result.bytes > 0
    assert "/" not in result.logo_path and "\\" not in result.logo_path
    # The proof that it is a name the spec accepts: resolve it the way a run
    # would, through the loader rather than by re-deriving the rule here.
    assert load_logo(result.logo_path, api.UPLOADS_DIR) is not None


def test_a_file_that_is_not_an_image_is_refused_by_the_name_the_user_gave(
        api, tmp_path):
    """Two claims, and the second was found by reading the response.

    Validating on upload rather than at render time is the point: a PDF
    renamed `.png` is refused now, with a sentence, instead of failing
    silently in a board pack.

    And the message must name *the user's* file. `load_logo` quotes the path
    it was given, which is right for the CLI where that path is what the user
    typed; here it is a name the server invented inside its own uploads
    directory, so quoting it leaks the install layout and tells the user
    nothing — they picked "notalogo.png".

    Mutation: drop the `str(exc).replace(...)`, or the `load_logo` call.
    """
    import asyncio

    from fastapi import HTTPException, UploadFile

    fake = tmp_path / "notalogo.png"
    fake.write_bytes(b"%PDF-1.4 not an image at all")
    with fake.open("rb") as handle, pytest.raises(HTTPException) as caught:
        asyncio.run(api.upload_logo(
            UploadFile(filename="notalogo.png", file=handle)))

    assert caught.value.status_code == 422
    detail = str(caught.value.detail)
    assert "notalogo.png" in detail
    assert str(api.UPLOADS_DIR) not in detail, detail
    # Nothing is left behind for a later run to trip over.
    assert not list(api.UPLOADS_DIR.glob("logo-*"))
