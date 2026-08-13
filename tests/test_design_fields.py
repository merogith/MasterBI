"""Every settable field must change something.

`design.locale`, `design.page_size`, `brand.font_stack`, `brand.footer_text`
and `AnalysisSpec.params` were all declared in the RunSpec, editable from the
studio, round-tripping through JSON — and read by nothing. A field that accepts
a value and ignores it is worse than a missing one: the user believes they
configured something.

So these are outcome tests, not plumbing tests. Each sets a field and asserts
the artifact changed, because "the parameter reaches the function" is exactly
what was true before and still produced nothing.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile, run_pipeline  # noqa: E402
from kpi_maker.fmt import fmt_percent, fmt_value  # noqa: E402
from kpi_maker.spec.schema import RunSpec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "northwind_saas.json"

# A number carrying both separators, which is the only shape that catches a
# naive sequential replace: swapping "," for "." and then "." for "," turns
# 1,234.50 into 1.234.50.
BOTH_SEPARATORS = 1234.5


# --------------------------------------------------------------------------
# The formatter itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("locale", "expected"), [
    (None, "1,234.50"),
    ("en", "1,234.50"),
    ("de", "1.234,50"),
    ("de-AT", "1.234,50"),
    ("tr", "1.234,50"),
    # A narrow no-break space (U+202F), which is what French typography uses.
    ("fr", "1\u202f234,50"),
    ("zz", "1,234.50"),      # unknown tags read as English rather than guessing
])
def test_separator_families(locale, expected):
    assert fmt_value(BOTH_SEPARATORS, "ratio", locale=locale) == expected


def test_percent_precision_survives_localisation():
    """`fmt_percent` exists so prose can keep its own precision.

    Detector sentences used raw `f"{x:.0%}"`, which is anglo-only, so a finding
    could read "$767,0K of ARR ... a rate of 40.7%" — currency localised and
    percentage not, in one sentence.
    """
    assert fmt_percent(0.407, 1, "de") == "40,7%"
    assert fmt_percent(0.407, 0, "de") == "41%"
    assert fmt_percent(0.407, 1, None) == "40.7%"


def test_locale_falls_back_through_language_then_country():
    profile = load_profile(SAMPLE)
    spec = RunSpec.for_profile(profile)
    # The sample declares a language, so that wins over the country.
    assert spec.resolve_locale() == profile.identity.language

    explicit = RunSpec(**{**spec.model_dump(), "design": {"locale": "de"}})
    assert explicit.resolve_locale() == "de"


def test_a_bad_page_size_is_refused():
    """`page_size` was a free string, so any typo round-tripped and did nothing."""
    from pydantic import ValidationError

    spec = RunSpec.for_profile(load_profile(SAMPLE))
    with pytest.raises(ValidationError, match="A4"):
        RunSpec(**{**spec.model_dump(), "design": {"page_size": "A3"}})


def test_font_stack_parses_to_names_in_order():
    spec = RunSpec.for_profile(load_profile(SAMPLE))
    styled = RunSpec(**{**spec.model_dump(), "design": {
        "brand": {"font_stack": "Inter, 'Segoe UI', Arial"}}})
    assert styled.resolve_font_stack() == ["Inter", "Segoe UI", "Arial"]
    assert spec.resolve_font_stack() == []


# --------------------------------------------------------------------------
# Detector thresholds
# --------------------------------------------------------------------------

def test_analysis_params_override_thresholds():
    from kpi_maker.insight.detectors import _PARAMS, DEFAULT_PARAMS, _param

    assert _param("runway", "warn_months") == DEFAULT_PARAMS["runway"]["warn_months"]
    token = _PARAMS.set({"runway": {"warn_months": 24}})
    try:
        assert _param("runway", "warn_months") == 24
        # An override of one key must not disturb its siblings.
        assert _param("runway", "critical_months") == \
            DEFAULT_PARAMS["runway"]["critical_months"]
    finally:
        _PARAMS.reset(token)
    assert _param("runway", "warn_months") == DEFAULT_PARAMS["runway"]["warn_months"]


def test_detector_formatting_state_is_per_context():
    """The currency was a module global while two runs share a thread pool.

    `api/server.py` executes pipelines on `ThreadPoolExecutor(max_workers=2)`,
    so a second `detect_all` used to overwrite the symbol the first was still
    formatting with. ContextVars make each run's value its own.
    """
    import threading

    from kpi_maker.insight.detectors import _CURRENCY, _fmt

    seen = {}

    def run(currency: str) -> None:
        _CURRENCY.set(currency)
        seen[currency] = _fmt(1000.0, "currency")

    threads = [threading.Thread(target=run, args=(c,)) for c in ("USD", "EUR")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen["USD"].startswith("$")
    assert seen["EUR"].startswith("€")


# --------------------------------------------------------------------------
# End to end: the fields must reach the artifacts
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """One default run and one fully-styled run, compared against each other."""
    out = tmp_path_factory.mktemp("design")
    profile = load_profile(SAMPLE)
    base = RunSpec.for_profile(profile)
    styled = RunSpec(**{**base.model_dump(), "design": {
        "locale": "de",
        "page_size": "Letter",
        "brand": {"footer_text": "Nordwind AG — vertraulich"},
    }})
    run_pipeline(profile, out / "default", quiet=True, spec=base)
    run_pipeline(profile, out / "styled", quiet=True, spec=styled)
    return out


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return re.sub(r"\s+", " ",
                  "\n".join((p.extract_text() or "") for p in PdfReader(path).pages))


def test_page_size_changes_the_pdf(rendered):
    from pypdf import PdfReader

    a4 = PdfReader(rendered / "default" / "report.pdf").pages[0].mediabox
    letter = PdfReader(rendered / "styled" / "report.pdf").pages[0].mediabox
    assert round(float(a4.width)) == 595 and round(float(a4.height)) == 842
    assert round(float(letter.width)) == 612 and round(float(letter.height)) == 792


def test_footer_text_reaches_pdf_deck_and_docx(rendered):
    assert "Nordwind AG" in _pdf_text(rendered / "styled" / "report.pdf")
    assert "Nordwind AG" not in _pdf_text(rendered / "default" / "report.pdf")

    deck = zipfile.ZipFile(rendered / "styled" / "deck.pptx")
    slides = " ".join(deck.read(n).decode("utf8") for n in deck.namelist()
                      if n.startswith("ppt/slides/slide"))
    assert "Nordwind AG" in slides

    # The editable report had no footer at all before this.
    docx = zipfile.ZipFile(rendered / "styled" / "report.docx")
    footers = [n for n in docx.namelist() if n.startswith("word/footer")]
    assert footers, "the DOCX has no footer part"
    assert "Nordwind AG" in docx.read(footers[0]).decode("utf8")


def test_locale_reaches_every_number_in_the_report(rendered):
    """Not "some numbers": a half-localised report is worse than none.

    One sentence used to carry `$767,0K` and `40.7%` together, because the
    currency went through the shared formatter and the percentage did not.
    """
    styled = _pdf_text(rendered / "styled" / "report.pdf")
    default = _pdf_text(rendered / "default" / "report.pdf")

    assert not re.search(r"[0-9]\.[0-9]%", styled), \
        "a percentage in the German report is still anglo-formatted"
    assert re.search(r"[0-9],[0-9]%", styled), "no localised percentage found"

    # And the default is untouched, which is what keeps every existing run
    # byte-identical.
    assert not re.search(r"[0-9],[0-9]%", default)
    assert re.search(r"[0-9]\.[0-9]%", default)


def test_locale_reaches_the_dashboard_and_the_deck(rendered):
    html = (rendered / "styled" / "dashboard.html").read_text(encoding="utf8")
    # Scoped to the tiles: plotly's bundled CSS contains `hsla(0,0%,100%,.5)`,
    # which a naive search for "N,N%" matches and which is not our number.
    tiles = re.findall(r'class="tile-value">([^<]+)<', html)
    assert tiles, "no stat tiles found in the dashboard"
    assert any("," in t for t in tiles), f"no localised tile value: {tiles[:5]}"

    deck = zipfile.ZipFile(rendered / "styled" / "deck.pptx")
    slides = " ".join(deck.read(n).decode("utf8") for n in deck.namelist()
                      if n.startswith("ppt/slides/slide"))
    assert re.search(r"[0-9],[0-9]%", slides)
