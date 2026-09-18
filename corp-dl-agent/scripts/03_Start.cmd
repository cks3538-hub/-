@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  03_Start: run the program with the active release venv (menu when no arguments)
rem  This wrapper is ASCII-only on purpose: cmd.exe mis-parses batch files that
rem  contain UTF-8 text (especially after chcp 65001). Korean messages are
rem  printed by the Python scripts themselves.
rem  Runs relative to its own location (%~dp0). Package root = parent of scripts.
rem  Env: PYTHON (approved python.exe), DIA_INSTALL_ROOT, DIA_DATA_ROOT.
rem  No automatic Python download/installation (no Store, no web installer).
rem ---------------------------------------------------------------------------
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PKG_DIR=%%~fI"
if not defined DIA_INSTALL_ROOT set "DIA_INSTALL_ROOT=%LOCALAPPDATA%\DIA"
set "DATA_ARG="
if defined DIA_DATA_ROOT set "DATA_ARG=--data-root "%DIA_DATA_ROOT%""
set "EC=0"
rem ---- Find Python: "%PYTHON%" first, then py -3.12, then python on PATH (Store alias excluded)
set "PYCMD="
if defined PYTHON (
  if exist "%PYTHON%" (
    set "PYCMD="%PYTHON%""
  ) else (
    echo [WARN] PYTHON env var points to a missing file: "%PYTHON%"
  )
)
if not defined PYCMD (
  where py >nul 2>&1 && (
    py -3.12 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.12"
  )
)
if not defined PYCMD (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYCMD (
      echo %%P | findstr /i /c:"\WindowsApps\" >nul || (
        "%%P" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>&1 && set "PYCMD="%%P""
      )
    )
  )
)
if not defined PYCMD (
  echo [BLOCKED_PREREQUISITE] Python 3.12 was not found.
  echo   Install the company-approved Python 3.12 x64 and set PYTHON to its path, e.g.
  echo   set "PYTHON=C:\Program Files\Python312\python.exe"
  echo   This script never starts a Microsoft Store or web download. Without Python run preflight.ps1.
  set "EC=8"
  goto :end
)
echo Python     : %PYCMD%
echo InstallRoot: %DIA_INSTALL_ROOT%
echo [03] start
%PYCMD% "%SCRIPT_DIR%launch.py" --install-root "%DIA_INSTALL_ROOT%" %DATA_ARG% -- %*
set "EC=%ERRORLEVEL%"

:end
echo.
echo exit code: %EC%
pause
exit /b %EC%
