@echo off
setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem ============================================================================
rem  corp-dl-agent 4.0.0 반입 ZIP 을 경순님 Windows PC 에서 재현하는 스크립트.
rem  필요: Python 3.12 x64 (python.org), 인터넷(PyPI). 이 스크립트 위치: <프로젝트>\corp-dl-agent\tools\
rem  결과: dist\DIA_4.0.0_win-x64-cp312-cpu.zip  +  .zip.sha256
rem  주의: 이 파일은 Linux 빌드 호스트에서 작성되어 Windows 에서 실행 검증되지 않았다(NOT_RUN).
rem        실패하면 docs\PERSONAL_BUILD_KO.md §2b 의 명령을 한 줄씩 직접 실행한다.
rem ============================================================================
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
cd /d "%ROOT%" || (echo [오류] 프로젝트 폴더로 이동 실패: %ROOT% & goto :fail)
echo 프로젝트 폴더: %ROOT%

rem ---- 1. Python 3.12 찾기 (PYTHON 환경변수 > py -3.12 > python)
set "PYCMD="
if defined PYTHON if exist "%PYTHON%" set "PYCMD="%PYTHON%""
if not defined PYCMD ( py -3.12 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.12" )
if not defined PYCMD ( python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 && set "PYCMD=python" )
if not defined PYCMD (
  echo [오류] Python 3.12 을 찾지 못했습니다. https://www.python.org/downloads/windows/ 에서 3.12 x64 를 설치한 뒤 다시 실행하세요.
  goto :fail
)
echo 사용 Python: %PYCMD%

rem ---- 2. 개발용 venv (재현·포장 전용; 반입 ZIP 에는 포함되지 않음)
if not exist ".venv\Scripts\python.exe" (
  echo [1/6] 가상환경 생성 .venv
  %PYCMD% -m venv .venv || goto :fail
)
set "VPY=%ROOT%\.venv\Scripts\python.exe"
echo [2/6] pip/build 준비
"%VPY%" -m pip install -U pip build wheel || goto :fail
echo [3/6] 앱 core 의존성 설치 (pydantic, PyYAML, httpx)
"%VPY%" -m pip install -e . || goto :fail

rem ---- 3. Windows wheelhouse 재현 + sha256 대조 (requirements\download_provenance.json)
echo [4/6] wheelhouse 다운로드 (PyPI, 약 220MB) 및 해시 대조
"%VPY%" tools\reproduce_wheelhouse.py --profile win-x64-cp312-cpu || goto :fail

rem ---- 4. 앱 wheel
echo [5/6] 앱 wheel 빌드
if exist build rmdir /s /q build
"%VPY%" -m build --wheel -o dist || goto :fail

rem ---- 5. 반입 ZIP
echo [6/6] 반입 ZIP 생성
"%VPY%" -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse\win-x64-cp312-cpu --app-wheel dist\corp_dl_agent-4.0.0-py3-none-any.whl --evidence-dir test-evidence --out dist --package-kind CPU_OFFLINE --verification "TARGET_OFFLINE_TESTED=NOT_RUN:이 PC 에서 아직 설치 시험 전. 새 폴더에 풀어 00~04 스크립트로 시험 후 acceptance.py 로 기록" || goto :fail

echo.
echo ===== 완료 =====
dir /b dist\DIA_4.0.0_win-x64-cp312-cpu.zip dist\DIA_4.0.0_win-x64-cp312-cpu.zip.sha256
type dist\DIA_4.0.0_win-x64-cp312-cpu.zip.sha256
echo 다음: dist\DIA_4.0.0_win-x64-cp312-cpu.zip 을 새 폴더에 풀고 scripts\00_Preflight.cmd -^> 01_Verify.cmd -^> 02_Install.cmd -^> 04_SelfTest.cmd 를 실행하세요.
pause
exit /b 0

:fail
echo.
echo [실패] 위 오류를 확인하세요. docs\PERSONAL_BUILD_KO.md §2b 의 명령을 한 줄씩 실행하면 어느 단계가 문제인지 알 수 있습니다.
pause
exit /b 1
