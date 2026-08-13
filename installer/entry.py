"""The frozen executable's entry point.

Deliberately three lines. PyInstaller runs its entry script as `__main__`, with
no package around it, so a script that used relative imports would fail on the
first one — `kpi_maker/desktop.py` opens with `from . import paths`, and the
build did exactly that until this shim existed:

    ImportError: attempted relative import with no known parent package

Keeping the real logic in `kpi_maker.desktop` means it stays importable, stays
testable, and stays runnable as `python -m kpi_maker.desktop` from a checkout.
"""
from kpi_maker.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
