"""Static image export for print deliverables.

Same ChartSpec objects the dashboard uses — rendered through kaleido instead of
the browser. One chart implementation, five output formats. Adding a chart to
`charts.py` makes it appear in the PDF, the deck and the doc automatically.

Three environment problems are handled here rather than in a README:

1. **Non-ASCII paths.** kaleido 0.2.1 passes the plotly.js path to a native
   subprocess that mangles non-ASCII characters on Windows. A user account like
   "Meriç" — or any accented/CJK username — breaks export with a misleading
   "not a valid URL or file path" error. We stage plotly.min.js at an ASCII-safe
   location on first use.
2. **A subprocess that never starts.** Same subprocess, worse failure: on
   Windows it sometimes launches and never writes its startup line, and
   `kaleido/scopes/base.py::_ensure_kaleido` reads that line with a plain
   `readline()` and no timeout. The call never returns. Found by a CI stack
   dump after two runs' worth of Windows jobs sat in the test step for twenty
   minutes and were recorded as merely "cancelled". `_guarded_to_image` below
   bounds it.
3. **Print backgrounds.** The dashboard uses transparent backgrounds so the card
   shows through. On paper that produces charts floating on nothing, so print
   exports get an explicit opaque surface.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

import plotly.graph_objects as go
import plotly.io as pio

# fonttools logs a "NOT subset; don't know how to subset" warning for every
# OpenType table it doesn't recognise in Segoe UI. Harmless, and it buries the
# pipeline's own output.
logging.getLogger("fontTools").setLevel(logging.ERROR)
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

from .charts import ChartSpec
from .theme import TOKENS

# Print renders always use the light palette: paper has no dark mode. The
# *values* still have to be passed in rather than bound here, for the same
# reason the three document renderers stopped binding them — a brand-derived
# token set cannot reach a module constant.
_bootstrapped = False


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _bootstrap_kaleido() -> None:
    """Stage plotly.min.js somewhere kaleido's subprocess can actually read."""
    global _bootstrapped
    if _bootstrapped:
        return

    current = getattr(pio.kaleido.scope, "plotlyjs", None)
    if current and not _is_ascii(str(current)):
        source = Path(str(current))
        if source.exists():
            staging = Path(tempfile.gettempdir()) / "kpi_maker_kaleido"
            if not _is_ascii(str(staging)):
                # Even the temp dir can sit under a non-ASCII profile.
                staging = Path("C:/Users/Public/kpi-maker") if Path("C:/Users/Public").exists() \
                    else Path("/tmp/kpi-maker")
            staging.mkdir(parents=True, exist_ok=True)
            target = staging / "plotly.min.js"
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copy2(source, target)
            pio.kaleido.scope.plotlyjs = str(target)
    _bootstrapped = True


def _prepare_for_print(spec: ChartSpec, width: int, height: int,
                       tokens: Optional[Dict[str, str]] = None):
    """Opaque surface, print-legible type, no interactive affordances.

    Works on a copy. The print treatment pins a pixel width and an opaque
    background, which are exactly wrong for the dashboard's responsive,
    transparent figures — and a ChartSpec is shared between the two now that
    each theme is built once per run rather than once per renderer.
    """
    surface = (tokens or TOKENS["light"])["surface"]
    fig = go.Figure(spec.figure)
    fig.update_layout(
        width=width,
        height=height,
        paper_bgcolor=surface,
        plot_bgcolor=surface,
        # Let Plotly size the margins around the tick labels. A fixed left
        # margin clips long category names on horizontal bar charts — the
        # benchmark exhibit lost the start of every KPI name to it.
        margin=dict(l=8, r=24, t=16, b=32, autoexpand=True),
        font=dict(size=13),
    )
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    return fig


# How long one chart may take before we assume the subprocess is wedged. A
# cold kaleido start is a second or two even on a loaded CI runner and a warm
# transform is a fraction of that, so this is roughly twenty times the real
# cost — high enough that a slow machine is never mistaken for a hung one.
EXPORT_TIMEOUT_SECONDS = 45.0

# Set once kaleido has proved unusable in this process, and never cleared. The
# subprocess is shared by every chart, so the first hang means the remaining
# twelve would each wait the full timeout for nothing: three-quarters of an
# hour to produce the same report with no images. Latching turns that into one
# wait and one message.
_export_failure: Optional[str] = None


def _kill_kaleido() -> None:
    """Kill kaleido's subprocess so the blocked `readline()` returns empty.

    This is the only way out. The hang is inside a blocking read on a pipe,
    which no signal or flag reaches from another thread — but closing the
    other end does. kaleido then sees an empty startup line and raises
    `ValueError("Failed to start Kaleido subprocess")`, which is a path
    `render_all` already handles. An infinite hang becomes an ordinary failure.

    Reaching for `scope._proc` is reaching past a private name. It is the
    documented-by-source structure of a pinned, effectively frozen dependency,
    and the alternative is the product hanging — so it is guarded rather than
    avoided.
    """
    scope = getattr(pio.kaleido, "scope", None)
    proc = getattr(scope, "_proc", None)
    if proc is not None and proc.poll() is None:
        # Anything this raises — the process died between the poll and the
        # kill, a platform refusing the signal — leaves us exactly where we
        # already were, with a caller that will time out and report.
        with contextlib.suppress(Exception):
            proc.kill()


def _guarded_to_image(fig, width: int, height: int, scale: float) -> bytes:
    """`fig.to_image`, but it always returns or raises — never neither."""
    global _export_failure

    fired = threading.Event()

    def trip() -> None:
        fired.set()
        _kill_kaleido()

    watchdog = threading.Timer(EXPORT_TIMEOUT_SECONDS, trip)
    watchdog.daemon = True
    watchdog.start()
    try:
        return fig.to_image(format="png", width=width, height=height, scale=scale)
    except Exception as exc:                             # noqa: BLE001
        if fired.is_set():
            _export_failure = (
                f"kaleido did not respond within {EXPORT_TIMEOUT_SECONDS:.0f}s and "
                f"was stopped; charts will be missing from the print deliverables"
            )
            raise RuntimeError(_export_failure) from exc
        raise
    finally:
        watchdog.cancel()


def render_png(spec: ChartSpec, width: int = 900, height: Optional[int] = None,
               scale: float = 2.0,
               tokens: Optional[Dict[str, str]] = None) -> bytes:
    """A single chart as PNG bytes at print resolution."""
    if _export_failure is not None:
        raise RuntimeError(_export_failure)
    _bootstrap_kaleido()
    height = height or (360 if spec.width == "full" else 320)
    fig = _prepare_for_print(spec, width, height, tokens)
    return _guarded_to_image(fig, width, height, scale)


def render_all(specs: List[ChartSpec], out_dir: Optional[Path] = None,
               width: int = 900,
               tokens: Optional[Dict[str, str]] = None) -> Dict[str, bytes]:
    """Every chart as PNG bytes, keyed by spec id. Optionally also written to disk.

    A single chart failing to export must not lose the whole report — the caller
    gets whatever succeeded and the report omits the rest.
    """
    images: Dict[str, bytes] = {}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _drop_stale(out_dir, {spec.id for spec in specs})

    for spec in specs:
        try:
            png = render_png(spec, width=width, tokens=tokens)
        except Exception as exc:                       # noqa: BLE001 — reported below
            if _export_failure is None:
                print(f"  WARNING   chart {spec.id!r} did not export: {exc}")
                continue
            # One message, not one per chart: the subprocess is shared, so once
            # it is gone every remaining chart fails for the same reason and
            # thirteen identical lines would bury it.
            print(f"  WARNING   {_export_failure}")
            break
        images[spec.id] = png
        if out_dir is not None:
            (out_dir / f"{spec.id}.png").write_bytes(png)

    return images


def _drop_stale(out_dir: Path, wanted: set) -> None:
    """Remove PNGs for exhibits this run is not producing.

    A re-run reuses the run directory, so deselecting an exhibit used to leave
    its old image behind — still served at /files/<run>/charts/<id>.png, still
    in the previous run's brand colours, contradicting the spec beside it.

    Keyed on the *requested* set rather than on what exported successfully:
    a chart that was asked for and failed keeps its last good image and gets a
    warning, while a chart the user removed gets deleted. Those are different
    situations and only one of them is a deletion.
    """
    for existing in out_dir.glob("*.png"):
        if existing.stem not in wanted:
            existing.unlink()
