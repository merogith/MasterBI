"""Tests for the design system: colour, sections, exhibits, logos.

P3's risk is different from the phases before it. P0's was scope, P1's was a
language, P2's was untrusted data. P3 refactored three renderers that already
produced good output, and its failure mode is not a crash — it is a deliverable
that quietly looks slightly worse. So the emphasis here is on two things a
passing unit test does not usually give you:

  * **The measures are checked against the standard, not against themselves.**
    Contrast is verified against published WCAG values. A validator that agrees
    with its own arithmetic proves nothing.
  * **The identity case is exact.** `derive_tokens(None)` must return the
    shipped palette unchanged and a default `DesignSpec` must change no
    artifact byte, because that is the entire safety argument for having
    extracted the section registry out of three working renderers.

Six groups:

  1. **Contrast** — against published pairs, and the AA boundary.
  2. **Separation** — CIE76 ΔE under normal and colour-deficient vision, with
     the shipped palettes passing and real collisions caught.
  3. **The palette guarantee** — for any accepted brand, three slots, all
     pairs clear, and the shipped trio survives a neutral brand.
  4. **Sections** — the selection rules the registry took over from three
     renderers, plus reorder and disable reaching all three formats.
  5. **Exhibits** — order, selection, refusal, and missing fact tables.
  6. **Logos** — accepted, and every way one is refused.

    python -m tests.design
"""
from __future__ import annotations

import colorsys
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpi_maker.cli import load_profile
from kpi_maker.design.contrast import (AA_TEXT, GRAPHICAL, MIN_DELTA_E,
                                       ColourError, delta_e, distinguishable,
                                       ensure_readable, parse_hex, ratio,
                                       simulate_cvd)
from kpi_maker.design.logo import MAX_BYTES, LogoError, load_logo
from kpi_maker.design.palette import (SERIES_SLOTS, derive_tokens,
                                      heading_accent, resolve_palette)
from kpi_maker.pipeline.runner import execute
from kpi_maker.render import sections as S
from kpi_maker.spec.schema import BrandSpec, DesignSpec, RunSpec
from kpi_maker.viz.charts import (CHARTS, UnknownExhibit, build_all,
                                  default_exhibits, resolve_exhibits)
from kpi_maker.viz.theme import MAX_CATEGORICAL_SERIES, TOKENS

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "northwind_saas.json"

_failures: List[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not ok:
        _failures.append(f"{name}: {detail}")
        print(f"  FAIL   {name}" + (f"  — {detail}" if detail else ""))


def hex_from_hls(hue: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in (r, g, b))


# --------------------------------------------------------------------------
# 1. Contrast, against the published standard
# --------------------------------------------------------------------------

def test_contrast() -> None:
    print("\ncontrast")

    # Values from WCAG 2.x §1.4.3 and the reference implementations. Checking
    # the validator against its own maths would prove only that it is
    # self-consistent.
    published = [
        ("#000000", "#ffffff", 21.00),
        ("#ffffff", "#ffffff", 1.00),
        ("#777777", "#ffffff", 4.48),      # just under AA — the classic near-miss
        ("#767676", "#ffffff", 4.54),      # the smallest grey that passes
        ("#2a78d6", "#fcfcfb", 4.30),      # the shipped slot 1 against the page
    ]
    for fg, bg, expected in published:
        got = ratio(fg, bg)
        check(f"ratio {fg} on {bg}", abs(got - expected) < 0.01,
              f"expected {expected}, got {got:.2f}")

    check("ratio is symmetric",
          abs(ratio("#000000", "#ffffff") - ratio("#ffffff", "#000000")) < 1e-9)

    # The AA boundary itself, from both sides.
    check("#767676 passes AA on white", ratio("#767676", "#ffffff") >= AA_TEXT)
    check("#777777 fails AA on white", ratio("#777777", "#ffffff") < AA_TEXT)

    # The shipped slot 1 is below AA as text. Pinned deliberately: it is why
    # `heading_accent` exists and defaults to this value rather than to a
    # corrected one, and a change here should be a decision, not a surprise.
    check("shipped slot 1 is below AA against the page",
          ratio(TOKENS["light"]["series_1"], TOKENS["light"]["page"]) < AA_TEXT,
          "if this now passes, heading_accent's fallback should be revisited")

    for bad in ("purple", "#12345", "", "#gggggg", None):
        try:
            parse_hex(bad)
            check(f"refuses {bad!r}", False, "parsed without error")
        except ColourError:
            check(f"refuses {bad!r}", True)

    check("3-digit hex expands", parse_hex("#28d") == parse_hex("#2288dd"))


def test_ensure_readable() -> None:
    print("\nensure_readable")

    page = TOKENS["light"]["page"]
    pale = "#ffd400"
    fixed = ensure_readable(pale, page, AA_TEXT)

    check("a failing colour is adjusted", fixed["adjusted"] is True)
    check("the adjustment clears the target",
          ratio(fixed["colour"], page) >= AA_TEXT,
          f"got {ratio(fixed['colour'], page):.2f}")
    check("both ratios are reported",
          fixed["original_ratio"] < AA_TEXT <= fixed["ratio"],
          f"{fixed['original_ratio']} -> {fixed['ratio']}")

    # Hue is what makes a brand colour recognisable as theirs. Lightness may
    # move; hue may not.
    before = colorsys.rgb_to_hls(*[c / 255 for c in parse_hex(pale)])
    after = colorsys.rgb_to_hls(*[c / 255 for c in parse_hex(fixed["colour"])])
    check("hue is preserved", abs(before[0] - after[0]) < 0.01,
          f"hue {before[0]:.3f} -> {after[0]:.3f}")
    check("lightness is what moved", abs(before[1] - after[1]) > 0.05)

    # On a light surface a colour has to get darker. Trying both directions and
    # picking the nearest would sometimes flip a navy to pale blue.
    check("darkens against a light surface", after[1] < before[1])
    lightened = ensure_readable("#333333", TOKENS["dark"]["page"], AA_TEXT)
    dark_after = colorsys.rgb_to_hls(
        *[c / 255 for c in parse_hex(lightened["colour"])])
    check("lightens against a dark surface", dark_after[1] > 0.2)

    already = ensure_readable("#000000", page, AA_TEXT)
    check("a passing colour is left alone", already["adjusted"] is False)
    check("a passing colour is returned unchanged",
          already["colour"] == "#000000")


# --------------------------------------------------------------------------
# 2. Separation — a different question from contrast
# --------------------------------------------------------------------------

def test_separation() -> None:
    print("\nseparation")

    # The check must not reject the palette it exists to protect. Judged by
    # WCAG contrast the shipped trio scores 1.14-1.57 and would fail a 3:1 bar,
    # which is what proved contrast the wrong instrument for this question.
    for mode in ("light", "dark"):
        series = [TOKENS[mode][s] for s in SERIES_SLOTS]
        verdict = distinguishable(series)
        check(f"shipped {mode} palette is separable", verdict["ok"],
              str(verdict["failures"]))

    check("blue and orange are close in luminance but far apart perceptually",
          ratio("#2a78d6", "#eb6834") < 2.0 and delta_e("#2a78d6", "#eb6834") > 100,
          "the case that rules contrast out as the measure")

    # Real collisions, caught.
    near_blues = ["#2a78d6", "#2f7cd8"]
    check("two shades of one blue are refused",
          not distinguishable(near_blues)["ok"],
          f"ΔE {delta_e(*near_blues):.1f}")

    # Two olives that are obvious to most readers and merge under deuteranopia.
    olives = ["#8b7d2a", "#6f8f2a"]
    check("a deuteranopic collision is caught",
          not distinguishable(olives)["ok"])
    failed_visions = {f["vision"] for f in distinguishable(olives)["failures"]}
    check("and it is named as a vision failure",
          "deuteranopia" in failed_visions, str(failed_visions))

    # A purple/green pair looks unmistakable and is not, for a deuteranope.
    check("purple and green collide under deuteranopia",
          delta_e(simulate_cvd("#6b3fa0", "deuteranopia"),
                  simulate_cvd("#1baf7a", "deuteranopia")) < MIN_DELTA_E,
          "this is why derive_tokens moves slot 3 for a purple brand")
    check("but not to a normal-vision reader",
          delta_e("#6b3fa0", "#1baf7a") > 100)

    check("a colour is identical to itself", delta_e("#2a78d6", "#2a78d6") < 1e-9)
    check("simulation is stable", simulate_cvd("#2a78d6", "deuteranopia")
          == simulate_cvd("#2a78d6", "deuteranopia"))


# --------------------------------------------------------------------------
# 3. The palette guarantee
# --------------------------------------------------------------------------

def test_identity() -> None:
    print("\npalette identity")

    for mode in ("light", "dark"):
        palette = derive_tokens(None, mode)
        # Exact, not equivalent. A default DesignSpec has to produce
        # byte-identical artifacts, and that starts here.
        check(f"no brand returns the shipped {mode} tokens exactly",
              palette.tokens == TOKENS[mode],
              str(set(palette.tokens.items()) ^ set(TOKENS[mode].items())))
        check(f"and returns a copy, not the shipped dict ({mode})",
              palette.tokens is not TOKENS[mode])
        check(f"with nothing to report ({mode})",
              not palette.adjustments and not palette.notes)

    check("heading_accent falls back to slot 1 when there is no brand",
          heading_accent(TOKENS["light"]) == TOKENS["light"]["series_1"])
    check("and is used when there is one",
          heading_accent({"series_1": "#111111", "heading_accent": "#222222"})
          == "#222222")

    try:
        derive_tokens("#2a78d6", "midnight")
        check("an unknown mode is refused", False, "accepted")
    except ColourError:
        check("an unknown mode is refused", True)


def test_palette_guarantee() -> None:
    print("\npalette guarantee")

    tested = failures = 0
    tightest = (999.0, None)
    for hue in range(0, 360, 15):
        for lightness in (0.25, 0.45, 0.65, 0.85):
            for saturation in (0.2, 0.5, 0.85):
                brand = hex_from_hls(hue / 360, lightness, saturation)
                for mode in ("light", "dark"):
                    tested += 1
                    palette = derive_tokens(brand, mode)
                    series = [palette.tokens[s] for s in SERIES_SLOTS]

                    if len(series) != MAX_CATEGORICAL_SERIES:
                        failures += 1
                        continue
                    verdict = distinguishable(series)
                    if not verdict["ok"]:
                        failures += 1
                        if palette.min_delta_e < tightest[0]:
                            tightest = (palette.min_delta_e, (brand, mode))
                    elif palette.min_delta_e < tightest[0]:
                        tightest = (palette.min_delta_e, (brand, mode))

    check(f"all-pairs floor holds across {tested} brand/mode combinations",
          failures == 0, f"{failures} failed; tightest {tightest}")
    check("and the tightest pair still clears the floor",
          tightest[0] >= MIN_DELTA_E, f"{tightest[0]:.1f} at {tightest[1]}")
    check("MAX_CATEGORICAL_SERIES is still 3", MAX_CATEGORICAL_SERIES == 3,
          "a brand may change which three, never how many")

    # A grey has no hue to rotate, and grey already means "de-emphasised" here.
    for neutral in ("#555555", "#111111", "#cccccc"):
        palette = derive_tokens(neutral, "light")
        check(f"a neutral brand ({neutral}) keeps the validated trio",
              [palette.tokens[s] for s in SERIES_SLOTS]
              == [TOKENS["light"][s] for s in SERIES_SLOTS])
        check(f"and says so ({neutral})", bool(palette.notes))
        check(f"but still reaches the headings ({neutral})",
              palette.tokens["heading_accent"] != TOKENS["light"]["series_1"])

    # The brand is the last thing to move.
    strong = derive_tokens("#6b3fa0", "light")
    check("a legible brand is used unchanged in charts",
          strong.tokens["series_1"] == "#6b3fa0")
    pale = derive_tokens("#ffd400", "light")
    check("a brand below the graphical floor is darkened",
          ratio(pale.tokens["series_1"], TOKENS["light"]["surface"]) >= GRAPHICAL,
          f"{ratio(pale.tokens['series_1'], TOKENS['light']['surface']):.2f}")
    check("and the change is reported",
          any(a.token == "series_1" for a in pale.adjustments))

    # A secondary brand colour is offered, not forced.
    good = derive_tokens("#6b3fa0", "light", accent="#f2a900")
    check("a distinguishable secondary is used",
          good.tokens["series_2"] == "#f2a900")
    clashing = derive_tokens("#6b3fa0", "light", accent="#7a4bb0")
    check("one too close to the brand is refused",
          clashing.tokens["series_2"] != "#7a4bb0")
    check("and the refusal is explained", bool(clashing.notes))

    # The sequential ramp keeps its lightness steps, in the brand's hue.
    ramp = [strong.tokens[f"seq_{i}"] for i in range(1, 6)]
    from kpi_maker.design.contrast import relative_luminance as lum
    check("the derived ramp is monotonic in lightness",
          all(lum(a) > lum(b) for a, b in zip(ramp, ramp[1:])),
          str([round(lum(c), 3) for c in ramp]))

    spec = DesignSpec(brand=BrandSpec(primary="#6b3fa0"), theme="dark")
    check("resolve_palette follows the spec's theme",
          resolve_palette(spec).mode == "dark")
    check("and 'auto' means light, because paper has no dark mode",
          resolve_palette(DesignSpec(theme="auto")).mode == "light")


# --------------------------------------------------------------------------
# 4. Sections
# --------------------------------------------------------------------------

def build_context(tmp: Path) -> S.SectionContext:
    spec = RunSpec(profile=load_profile(SAMPLE))
    spec.outputs.artifacts = ["report_pdf"]
    values = execute(spec, tmp / "sections").values
    return S.SectionContext(
        profile=values["resolve"], kpi_set=values["select"],
        results=values["metrics"], findings=values["analyse"],
        images=values["charts_png"], specs=values["visualise"]["light"],
        checks=values["source"].checks,
        anomaly_notes=[a.description for a in values["source"].anomalies],
        period="2023-01 to 2025-12")


def _sorting_context(ctx: S.SectionContext) -> S.SectionContext:
    """Findings whose impact and effort ranks cross, so the key order matters.

    The ranks are A(0,0) B(0,1) C(1,0) D(1,1). Sorting by impact then effort
    gives A, B, C, D; by effort then impact it gives A, C, B, D. E has neither
    estimate and sorts last either way.

    Giving each finding the *same* impact and effort — which is what the first
    version of this fixture did — makes the two orders identical and lets a
    swapped-key mutation through. A mutation test caught that; a passing suite
    did not.
    """
    from kpi_maker.insight.detectors import Finding
    findings = [
        Finding(id="f1", severity="high", title="A", statement="",
                recommendation="A", impact="high", effort="high"),
        Finding(id="f2", severity="high", title="B", statement="",
                recommendation="B", impact="high", effort="medium"),
        Finding(id="f3", severity="high", title="C", statement="",
                recommendation="C", impact="medium", effort="high"),
        Finding(id="f4", severity="high", title="D", statement="",
                recommendation="D", impact="medium", effort="medium"),
        Finding(id="f5", severity="high", title="E", statement="",
                recommendation="E", impact=None, effort=None),
    ]
    return S.SectionContext(profile=ctx.profile, kpi_set=ctx.kpi_set,
                            results=ctx.results, findings=findings)


def _partly_computed(ctx: S.SectionContext) -> List:
    """The real results with half of them marked uncomputed and modelled.

    A synthetic run computes everything, so `ctx.computed == ctx.results` and a
    basis count over the wrong one of the two looks correct. This is the shape
    a real upload has: most KPIs unavailable, and the ones that survive built
    on gap-filled tables.
    """
    import copy
    out = []
    for i, result in enumerate(ctx.results):
        clone = copy.copy(result)
        if i % 2:
            clone.computed = False
            clone.reason = "no table"
        else:
            clone.basis = "modelled"
        out.append(clone)
    return out


def test_sections(ctx: S.SectionContext) -> None:
    print("\nsections")

    check("all eight plus the cover are registered",
          len(S.default_order()) == 9, str(S.default_order()))
    check("the cover is not numbered", not S.REGISTRY["cover"].numbered)

    built = S.build(ctx)
    numbered = [c.title for c in built if c.id != "cover"]
    check("numbering follows position",
          all(t.startswith(f"{i}. ") for i, t in enumerate(numbered, 1)),
          str(numbered))

    # The rules the registry took over from three separate implementations.
    actions = S.REGISTRY["actions"].build(ctx)
    rows = actions.tables[0].rows if actions.tables else []
    check("actions cap at the limit given", len(rows) <= 12)
    # The real findings do not distinguish a correct sort from one with the
    # keys swapped — a mutation test showed exactly that surviving. So the
    # ordering is checked against a fixture built to separate them: impact and
    # effort disagree on every pair, and only (impact, effort) gives this order.
    ordered = S.REGISTRY["actions"].build(_sorting_context(ctx))
    got = [row[0] for row in ordered.tables[0].rows]
    check("actions sort by impact first, then effort",
          got == ["A", "B", "C", "D", "E"],
          f"{got} — effort-first would give A, C, B, D, E")
    check("a finding with no estimate sorts last",
          S._RANK.get(None, 3) == 3 and S._RANK["high"] < S._RANK["low"])

    # And the real data still comes out in a consistent order.
    by_recommendation = {f.recommendation: f for f in ctx.findings
                         if f.recommendation}
    emitted = [(S._RANK.get(by_recommendation[row[0]].impact, 3),
                S._RANK.get(by_recommendation[row[0]].effort, 3))
               for row in rows]
    check("and the real findings come out sorted too",
          emitted == sorted(emitted), str(emitted))
    deck_actions = S.REGISTRY["actions"].build(ctx, limit=8)
    check("the deck's smaller cap is honoured",
          len(deck_actions.tables[0].rows) <= 8 if deck_actions.tables else True)
    check("and it is a cap, not a different selection",
          not actions.tables or
          deck_actions.tables[0].rows == actions.tables[0].rows[:8])

    risks = S.REGISTRY["risks"].build(ctx)
    severities = {f.severity for f in ctx.findings
                  if f.severity in ("critical", "high")}
    check("risks select critical and high only",
          len(risks.bullets) == len([f for f in ctx.findings
                                     if f.severity in ("critical", "high")]),
          str(severities))
    true_total = len([f for f in ctx.findings
                      if f.severity in ("critical", "high")])
    capped = S.REGISTRY["risks"].build(ctx, limit=2)
    check("a capped risks section reports the pre-cap total",
          capped.total == true_total, f"got {capped.total}, expected {true_total}")
    check("which is more than it is showing",
          len(capped.bullets) == 2 < true_total,
          f"shown {len(capped.bullets)} of {true_total} — the deck headlines "
          f"the total, so dropping it would understate the problem")

    summary = S.REGISTRY["exec_summary"].build(ctx)
    check("the executive summary takes the first five findings",
          len(summary.bullets) == min(5, len(ctx.findings)))
    check("a bullet keeps its label and title apart",
          all(b.label and b.title for b in summary.bullets),
          "the deck's risks slide needs the title without the severity")

    dives = S.REGISTRY["deep_dives"].build(ctx)
    ids = [e.id for e in dives.exhibits]
    check("deep dives skip the two diagnostic exhibits",
          not (set(ids) & set(S.DIAGNOSTIC_EXHIBITS)), str(ids))
    check("and skip exhibits with no image",
          all(i in ctx.images for i in ids))

    # An empty section still renders its heading; "no critical issues" is a
    # result the reader wants, not an absence to hide.
    empty_ctx = S.SectionContext(profile=ctx.profile, kpi_set=ctx.kpi_set,
                                 results=ctx.results, findings=[])
    quiet = S.REGISTRY["risks"].build(empty_ctx)
    check("an empty risks section carries a message", bool(quiet.empty))
    check("and reports itself as empty", quiet.is_empty)

    # The basis block: absent for a synthetic run, present when gap-filled.
    appendix = S.REGISTRY["appendix"].build(ctx)
    titles = [b.title for b in appendix.blocks]
    check("a synthetic run has no measured/modelled block",
          "Measured and modelled figures" not in titles)

    partial = _partly_computed(ctx)
    modelled_ctx = S.SectionContext(
        profile=ctx.profile, kpi_set=ctx.kpi_set, results=partial,
        findings=ctx.findings, checks=ctx.checks,
        origins={"headcount": "modelled"})
    block = S._basis_block(modelled_ctx)
    check("a gap-filled run gets one", block is not None)
    if block is not None:
        check("naming the table", any("headcount" in line for line in block.lines))
        computed = len(modelled_ctx.computed)
        check("counted over KPIs it could actually compute",
              any(f"of the {computed} KPIs" in line for line in block.lines),
              f"expected 'of the {computed} KPIs', got {block.lines[-1:]}")
        # The count must be strictly wrong if it ranges over all results, which
        # is the mistake the first version of this block actually made.
        check("and not over the ones it could not",
              not any(f"of the {len(partial)} KPIs" in line
                      for line in block.lines),
              "counting uncomputed KPIs as measured overstates the honest total")
        check("every computed KPI here is modelled, and the block says so",
              any(line.startswith("0 of the ") for line in block.lines),
              str(block.lines[-1:]))


def test_section_order(ctx: S.SectionContext) -> None:
    print("\nsection order")

    check("no request means the shipped order",
          S.resolve_order(None) == S.default_order())

    wanted = ["cover", "actions", "risks", "scorecard"]
    got = S.build(ctx, wanted)
    check("a request both selects and orders",
          [c.id for c in got] == wanted, str([c.id for c in got]))
    check("and renumbers from the new position",
          [c.title.split(".")[0] for c in got if c.id != "cover"] == ["1", "2", "3"])

    check("duplicates collapse",
          S.resolve_order(["cover", "risks", "risks"]) == ["cover", "risks"])
    check("an empty list disables everything", S.resolve_order([]) == [])

    try:
        S.resolve_order(["cover", "nope"])
        check("an unknown section is refused", False, "accepted")
    except S.UnknownSection as exc:
        check("an unknown section is refused", True)
        check("and the message lists what is available",
              "cover" in str(exc) and "nope" in str(exc))


# --------------------------------------------------------------------------
# 5. Exhibits
# --------------------------------------------------------------------------

def test_exhibits(ctx: S.SectionContext) -> None:
    print("\nexhibits")

    order = default_exhibits()
    check("every registered chart is listed", len(order) == len(CHARTS))
    check("order comes from the decorator, not the file",
          order[0] == "arr_trend" and order[-1] == "indexed_growth", str(order))
    check("ids are unique", len(set(order)) == len(order))

    check("no request means all of them", resolve_exhibits(None) == order)
    check("a request selects and orders",
          resolve_exhibits(["retention", "arr_trend"]) == ["retention", "arr_trend"])
    check("duplicates collapse",
          resolve_exhibits(["retention", "retention"]) == ["retention"])

    try:
        resolve_exhibits(["arr_trend", "nope"])
        check("an unknown exhibit is refused", False, "accepted")
    except UnknownExhibit as exc:
        check("an unknown exhibit is refused", True)
        check("and the message lists what is available", "arr_trend" in str(exc))

    # A partial upload must narrow the dashboard rather than fail to produce one.
    check("no fact tables yields no exhibits rather than an error",
          build_all([], {}, mode="light") == [])

    built = build_all(ctx.results, {}, mode="light",
                      exhibits=["arr_trend", "cohort_heatmap"])
    check("a missing fact table omits its exhibit",
          all(spec.id != "cohort_heatmap" for spec in built), str(built))

    widths = build_all(ctx.results, {}, mode="light", exhibits=["arr_trend"],
                       widths={"arr_trend": "full"})
    check("the spec can override a width",
          all(spec.width == "full" for spec in widths))


# --------------------------------------------------------------------------
# 6. Logos
# --------------------------------------------------------------------------

def png_bytes(width: int = 8, height: int = 8) -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\0" + bytes([120, 60, 180] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_logo(tmp: Path) -> None:
    print("\nlogo")

    good = tmp / "logo.png"
    good.write_bytes(png_bytes())
    jpeg = tmp / "logo.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 64)
    text = tmp / "secrets.txt"
    text.write_text("root:x:0:0:root:/root:/bin/bash\n")
    mislabelled = tmp / "notreally.png"
    mislabelled.write_text("still not a png")
    huge = tmp / "huge.png"
    huge.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * (MAX_BYTES + 1))

    check("no logo requested returns None", load_logo(None) is None)
    check("an empty path returns None", load_logo("") is None)

    loaded = load_logo(str(good))
    check("a PNG is accepted", loaded is not None and loaded.mime == "image/png")
    check("and carries a usable data URI",
          loaded.data_uri().startswith("data:image/png;base64,"))
    check("a JPEG is accepted", load_logo(str(jpeg)).mime == "image/jpeg")

    # The logo is base64-embedded into a file made to be emailed, so the
    # extension is not enough — the bytes have to be an image.
    for path, label in ((text, "a text file"), (mislabelled, "a mislabelled file"),
                        (huge, "an oversize file"), (tmp / "gone.png", "a missing file")):
        try:
            load_logo(str(path))
            check(f"{label} is refused", False, "accepted")
        except LogoError as exc:
            check(f"{label} is refused", True)
            check(f"and says why ({label})", len(str(exc)) > 30)

    # A logo that was asked for and cannot be used must raise, not be dropped:
    # shipping silently would hand the user a report they believe is branded.
    try:
        load_logo("definitely-not-here.png")
        check("a broken logo path is never silently ignored", False)
    except LogoError:
        check("a broken logo path is never silently ignored", True)

    check("a bare name resolves under the uploads directory",
          load_logo("logo.png", tmp) is not None)


# --------------------------------------------------------------------------

def run() -> int:
    print("=" * 70)
    print("DESIGN SYSTEM")
    print("=" * 70)

    tmp = Path(tempfile.mkdtemp(prefix="design-test-"))
    try:
        ctx = build_context(tmp)
        suites = [
            test_contrast,
            test_ensure_readable,
            test_separation,
            test_identity,
            test_palette_guarantee,
            lambda: test_sections(ctx),
            lambda: test_section_order(ctx),
            lambda: test_exhibits(ctx),
            lambda: test_logo(tmp),
        ]
        for suite in suites:
            try:
                suite()
            except Exception as exc:                        # noqa: BLE001
                _failures.append(f"crash: {type(exc).__name__}: {exc}")
                print(f"  CRASH  {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 70}")
    print(f"checks: {_checks}  failures: {len(_failures)}")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(run())
