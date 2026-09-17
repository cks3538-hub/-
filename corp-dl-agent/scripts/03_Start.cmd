@echo off
setlocal
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
rem 이 파일은 어느 작업 폴더에서 실행해도 자기 위치(%~dp0) 기준으로 동작한다. 패키지 root = scripts 의 상위 폴더.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PKG_DIR=%%~fI"
if not defined DIA_INSTALL_ROOT set "DIA_INSTALL_ROOT=%LOCALAPPDATA%\DIA"
set "DATA_ARG="
if defined DIA_DATA_ROOT set "DATA_ARG=--data-root "%DIA_DATA_ROOT%""
set "EC=0"
rem ---- Python 탐색: "%PYTHON%" 우선 -> py -3.12 -> PATH 의 python (Store alias 제외). 자동 설치/다운로드 없음.
set "PYCMD="
if defined PYTHON (
  if exist "%PYTHON%" (
    set "PYCMD="%PYTHON%""
  ) else (
    echo [경고] PYTHON 환경변수의 경로가 존재하지 않습니다: "%PYTHON%"
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
  echo [BLOCKED_PREREQUISITE] Python 3.12 을 찾지 못했습니다. 회사 승인 Python 3.12 x64 를 설치한 뒤 PYTHON 환경변수로 경로를 지정하세요.
  echo   예: set "PYTHON=C:\Program Files\Python312\python.exe"
  echo   Microsoft Store/웹 자동 설치는 실행하지 않습니다. Python 없이 진단하려면 preflight.ps1 을 실행하세요.
  set "EC=8"
  goto :end
)
echo 사용 Python: %PYCMD%
echo 설치 root : %DIA_INSTALL_ROOT%
echo [03] start: 활성 release 의 venv 로 프로그램 시작 (인자 없으면 메뉴)
%PYCMD% "%SCRIPT_DIR%launch.py" --install-root "%DIA_INSTALL_ROOT%" -- %*
set "EC=%ERRORLEVEL%"

:end
echo.
echo exit code: %EC%
pause
exit /b %EC%
