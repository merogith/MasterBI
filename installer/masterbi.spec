# PyInstaller spec — one file, three platforms.
#
#     python -m installer.build          (writes the build id, then runs this)
#
# Everything the engine reads at runtime has to be listed here, because a
# `Path(__file__).parent / "library"` resolves inside the bundle and finds
# nothing unless the data came along. The KPI packs and the AI prompts were
# already an installed-package problem — `pyproject.toml` force-includes them
# for exactly this reason — and a frozen build is the same problem again.
import sys
from pathlib import Path

# SPECPATH is the spec's *directory*, so one step up is the repository.
ROOT = Path(SPECPATH).resolve().parent

datas = [
    # Read at runtime by the selection engine and the narrator.
    (str(ROOT / "kpi_maker" / "kpi" / "library"), "kpi_maker/kpi/library"),
    (str(ROOT / "kpi_maker" / "ai" / "prompts"), "kpi_maker/ai/prompts"),
    # The front end. Without this the app serves its own "no front end" 500.
    (str(ROOT / "kpi_maker" / "ui_dist"), "kpi_maker/ui_dist"),
    # Mode 1 is the first thing anyone clicks.
    (str(ROOT / "samples"), "samples"),
    # What CODE_VERSION falls back to when there is no source to hash.
    (str(ROOT / "kpi_maker" / "_build_id.txt"), "kpi_maker"),
]

# kaleido ships a native binary and its own JS; PyInstaller's analysis cannot
# see either through the Python import graph.
try:
    import kaleido
    datas.append((str(Path(kaleido.__file__).parent / "executable"),
                  "kaleido/executable"))
except Exception:
    pass

# plotly's bundled JS is data, not code, and the dashboard is inert without it.
try:
    import plotly
    datas.append((str(Path(plotly.__file__).parent / "package_data"),
                  "plotly/package_data"))
except Exception:
    pass

hiddenimports = [
    # uvicorn resolves these by string at runtime, so nothing imports them.
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    # Engines pandas selects by name when a file extension asks for them.
    "openpyxl", "xlsxwriter",
]

a = Analysis(
    # A shim, not the module itself: PyInstaller runs the entry script as
    # `__main__`, where `kpi_maker/desktop.py`'s relative imports have no
    # package to resolve against.
    [str(ROOT / "installer" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # Nothing here is used by the app, and each drags in tens of megabytes.
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="MasterBI",
    console=True,          # the URL and the runs directory are printed there
    onefile=True,
    upx=False,             # UPX trips antivirus heuristics on Windows
    target_arch=None,
)

if sys.platform == "darwin":
    app = BUNDLE(exe, name="MasterBI.app", bundle_identifier="io.github.merogith.masterbi")
