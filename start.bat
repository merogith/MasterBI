@echo off
REM Double-click launcher for Windows. Nothing to read, nothing to type.
REM
REM First run: builds a private environment and installs the dependencies,
REM which takes a few minutes. Every run after that starts in seconds.

setlocal enabledelayedexpansion
cd /d "%~dp0"
title KPI Dashboard Maker

echo.
echo   KPI Dashboard Maker
echo   ===================
echo.

REM ---- find a Python ------------------------------------------------------
REM The py launcher ships with python.org installs and picks the newest
REM version; plain `python` on PATH is the fallback. The Microsoft Store stub
REM is also called python.exe but fails on -V, so the check has to run it.
set "PYCMD="
py -3 -V >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  python -V >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
  echo   Python is not installed, or not on your PATH.
  echo.
  echo   Install it from https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add python.exe to PATH" in the installer.
  echo.
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

REM ---- private environment ------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo   First run. Building a private Python environment...
  %PYCMD% -m venv .venv
  if errorlevel 1 (
    echo.
    echo   Could not create the environment. The error is above.
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"

REM ---- dependencies -------------------------------------------------------
REM Importing what the pipeline actually needs is a truer check than a marker
REM file, which would lie after a half-finished install.
"%VPY%" -c "import fastapi, pandas, plotly, kaleido, fpdf, pptx, docx, xlsxwriter" >nul 2>nul
if errorlevel 1 (
  echo   Installing dependencies. This takes a few minutes, once.
  echo.
  "%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
  "%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
  if errorlevel 1 (
    echo.
    REM Plain ASCII throughout: the console codepage is not UTF-8, so a typed
    REM dash or curly quote arrives as mojibake in the one window a stuck user
    REM is trying to read.
    echo   That failed. Retrying - some company networks inspect TLS, which
    echo   breaks pip's certificate check.
    echo.
    "%VPY%" -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org ^
        -r requirements.txt --quiet --disable-pip-version-check
    if errorlevel 1 (
      echo.
      echo   Still failing. The error is above.
      pause
      exit /b 1
    )
  )
  echo   Done.
  echo.
)

REM ---- go -----------------------------------------------------------------
echo   Starting. Your browser opens by itself in a moment.
echo   Leave this window open while you use the app; close it to stop.
echo.
"%VPY%" -m kpi_maker serve --open

echo.
echo   Stopped.
pause
