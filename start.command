#!/usr/bin/env bash
# Double-click launcher for macOS (Finder runs .command files directly) and
# Linux. Same contract as start.bat: nothing to read, nothing to type.
#
# First run builds a private environment and installs dependencies; later runs
# start in seconds.

set -u
cd "$(dirname "$0")"

echo
echo "  KPI Dashboard Maker"
echo "  ==================="
echo

die() { echo; echo "  $1"; echo; read -r -p "  Press Enter to close." _; exit 1; }

# ---- find a Python --------------------------------------------------------
PYCMD=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    # macOS ships a python3 stub that only prompts to install the tools.
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
      PYCMD="$candidate"; break
    fi
  fi
done
# 3.11 is the floor in pyproject.toml, and this gate has to agree with it: on
# 3.9 the launcher used to build a venv, run pip, and fail somewhere inside a
# dependency resolution the user cannot read. Refusing up front says why.
[ -n "$PYCMD" ] || die "Python 3.11 or newer is not installed. Get it from https://www.python.org/downloads/ then double-click this file again."

# ---- private environment --------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo "  First run. Building a private Python environment..."
  "$PYCMD" -m venv .venv || die "Could not create the environment. The error is above."
fi
VPY=".venv/bin/python"

# ---- dependencies ---------------------------------------------------------
# Importing what the pipeline needs beats a marker file, which would lie after
# a half-finished install.
if ! "$VPY" -c "import fastapi, pandas, plotly, kaleido, fpdf, pptx, docx, xlsxwriter" >/dev/null 2>&1; then
  echo "  Installing dependencies. This takes a few minutes, once."
  echo
  "$VPY" -m pip install --upgrade pip --quiet --disable-pip-version-check
  if ! "$VPY" -m pip install -r requirements.txt --quiet --disable-pip-version-check; then
    echo
    echo "  That failed. Retrying — some corporate networks inspect TLS, which"
    echo "  breaks pip's certificate check."
    echo
    "$VPY" -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org \
        -r requirements.txt --quiet --disable-pip-version-check \
      || die "Still failing. The error is above."
  fi
  echo "  Done."
  echo
fi

# ---- go -------------------------------------------------------------------
echo "  Starting. Your browser opens by itself in a moment."
echo "  Leave this window open while you use the app; close it to stop."
echo
"$VPY" -m kpi_maker serve --open

echo
echo "  Stopped."
