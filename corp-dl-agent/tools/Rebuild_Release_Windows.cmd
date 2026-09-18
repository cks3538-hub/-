@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  Rebuild the corp-dl-agent 4.0.0 transfer ZIP on a personal Windows PC.
rem  Needs: Python 3.12 x64 (python.org) and internet access to PyPI.
rem  Location: <project>\corp-dl-agent\tools\  (works from any current directory)
rem  Result:   dist\DIA_4.0.0_win-x64-cp312-cpu.zip  +  .zip.sha256
rem  ASCII-only on purpose (cmd.exe mis-parses UTF-8 batch files when the code page is switched). Korean output
rem  comes from the Python tools. Written on a Linux build host: Windows run = NOT_RUN
rem  until you confirm it; if it fails, run docs\PERSONAL_BUILD_KO.md section 2b by hand.
rem ---------------------------------------------------------------------------
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
cd /d "%ROOT%" || (echo [ERROR] cannot enter project folder: %ROOT% & goto :fail)
echo Project: %ROOT%

rem ---- 1. Find Python 3.12 (PYTHON env var, then py -3.12, then python)
set "PYCMD="
if defined PYTHON if exist "%PYTHON%" set "PYCMD="%PYTHON%""
if not defined PYCMD ( py -3.12 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.12" )
if not defined PYCMD ( python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 && set "PYCMD=python" )
if not defined PYCMD (
  echo [ERROR] Python 3.12 not found. Install Python 3.12 x64 from https://www.python.org/downloads/windows/
  echo         - tick "Add python.exe to PATH" during setup, then run this script again.
  goto :fail
)
echo Python : %PYCMD%

rem ---- 2. Build-only virtualenv (never shipped in the ZIP)
if not exist ".venv\Scripts\python.exe" (
  echo [1/6] creating .venv
  %PYCMD% -m venv .venv || goto :fail
)
set "VPY=%ROOT%\.venv\Scripts\python.exe"
echo [2/6] pip / build / wheel
"%VPY%" -m pip install -U pip build wheel || goto :fail
echo [3/6] app core dependencies (pydantic, PyYAML, httpx)
"%VPY%" -m pip install -e . || goto :fail

rem ---- 3. Reproduce the Windows wheelhouse and compare sha256 with requirements\download_provenance.json
echo [4/6] downloading wheelhouse from PyPI (about 220 MB) and checking sha256
"%VPY%" tools\reproduce_wheelhouse.py --profile win-x64-cp312-cpu || goto :fail

rem ---- 4. App wheel
echo [5/6] building app wheel
if exist build rmdir /s /q build
"%VPY%" -m build --wheel -o dist || goto :fail

rem ---- 5. Transfer ZIP
echo [6/6] building transfer ZIP
"%VPY%" -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse\win-x64-cp312-cpu --app-wheel dist\corp_dl_agent-4.0.0-py3-none-any.whl --evidence-dir test-evidence --out dist --package-kind CPU_OFFLINE --verification "TARGET_OFFLINE_TESTED=NOT_RUN:not yet installed on this PC. Extract the ZIP to a new folder, run scripts 00-04, then record with acceptance.py" || goto :fail

echo.
echo ===== DONE =====
dir /b dist\DIA_4.0.0_win-x64-cp312-cpu.zip dist\DIA_4.0.0_win-x64-cp312-cpu.zip.sha256
type dist\DIA_4.0.0_win-x64-cp312-cpu.zip.sha256
echo Next: extract dist\DIA_4.0.0_win-x64-cp312-cpu.zip into a NEW folder and run scripts\00_Preflight.cmd, 01_Verify.cmd, 02_Install.cmd, 04_SelfTest.cmd
pause
exit /b 0

:fail
echo.
echo [FAILED] See the error above. docs\PERSONAL_BUILD_KO.md section 2b lists the same steps one by one.
pause
exit /b 1
