# 개인 빌드 안내 (PERSONAL_BUILD_KO)

이 문서는 개인 환경에서 소스·시험·wheelhouse·반입 ZIP 을 만드는 절차입니다. 실제 4.0.0 빌드는 Linux 클라우드 컨테이너(Claude Code 세션)에서 수행했고, Windows 개인 PC 에서도 같은 절차로 재현할 수 있습니다.

## 1. 빌드 환경 (실제)
| 항목 | 값 |
|---|---|
| 빌드 호스트 | Linux x86_64 (클라우드 컨테이너), GPU 없음 |
| 개발 Python | CPython 3.12.3 (`.venv`, uv 로 생성) |
| 패키지 출처 | PyPI 만 (download.pytorch.org 는 정책 차단) — `requirements/download_provenance.json` 에 파일별 sha256 과 PyPI 대조 결과 |
| 참조 타깃 | `win-x64-cp312-cpu` (Windows x64 / CPython 3.12 / CPU), `target_confirmed=false` |
| rehearsal 타깃 | `linux-x64-cp312-cpu` (호스트와 동일 → 새 경로 설치 시험 가능) |

개인 PC 의 사양을 사내 사양으로 간주하지 않습니다. Windows 개인 PC 에서 아래 절차를 수행하면 Windows 타깃의 실행 시험(TARGET_OFFLINE_TESTED)까지 가능합니다.

## 2. Windows 개인 PC 에서 재현하는 절차
1. Python 3.12 x64 설치(공식 python.org) 후 PowerShell:
   ```powershell
   cd <프로젝트 폴더>\corp-dl-agent
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -U pip build wheel
   .\.venv\Scripts\python.exe -m pip install -e ".[documents,ml-cpu,dev]"
   ```
2. 시험: `.\.venv\Scripts\python.exe -m pytest -q` , `ruff check .`, `mypy corp_dl_agent`
3. 앱 wheel: `.\.venv\Scripts\python.exe -m build --wheel` → `dist\corp_dl_agent-4.0.0-py3-none-any.whl`
4. Windows wheelhouse (호스트 = 타깃이면 native):
   ```powershell
   .\.venv\Scripts\python.exe -m pip download --no-cache-dir --only-binary=:all: -d wheelhouse\win-x64-cp312-cpu -r requirements\profile-cpu-offline.in
   ```
   (Linux 에서 Windows 용을 받을 때는 marker 평가 문제로 `uv pip compile --python-platform x86_64-pc-windows-msvc` 로 해석한 뒤 `--no-deps` 로 개별 다운로드합니다. `requirements/download_provenance.json` 참조.)
5. lock/inventory/manifest/ZIP:
   ```powershell
   .\.venv\Scripts\python.exe -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse\win-x64-cp312-cpu --app-wheel dist\corp_dl_agent-4.0.0-py3-none-any.whl --evidence test-evidence --out dist
   ```
   → `dist\DIA_4.0.0_win-x64-cp312-cpu.zip`, `.zip.sha256`
6. 새 환경 설치 시험(transfer-test): 다른 드라이브/한글·공백 폴더에 ZIP 만 복사 → `00_Preflight.cmd` → `01_Verify.cmd` → `02_Install.cmd` → `04_SelfTest.cmd` → `launch.cmd demo --offline --device cpu`. 네트워크를 끊거나(승인된 방법) 프로그램의 OutboundGuard 결과로 outbound 0 을 확인.
7. 외부 acceptance: `python scripts\acceptance.py --zip dist\DIA_4.0.0_win-x64-cp312-cpu.zip --evidence-dir <transfer-test 결과 폴더> --out dist\` → `DIA_4.0.0_win-x64-cp312-cpu.acceptance.json`. **시험 결과를 넣기 위해 ZIP 을 다시 포장하지 않습니다.** 소스를 고쳤으면 새 ZIP 을 만들고 시험을 다시 합니다.

## 2b. ZIP 을 직접 받지 못한 경우 (git 저장소만 있을 때)
소스 저장소에는 wheelhouse/ZIP 이 없습니다(용량). Windows PC 에서 아래 순서로 같은 ZIP 을 재현합니다. 내려받은 wheel 의 sha256 은 `requirements/download_provenance.json` 과 대조되어 하나라도 다르면 실패합니다.
```powershell
cd <프로젝트>\corp-dl-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip build wheel
.\.venv\Scripts\python.exe tools\reproduce_wheelhouse.py --profile win-x64-cp312-cpu
.\.venv\Scripts\python.exe -m build --wheel
.\.venv\Scripts\python.exe -m pip install -e ".[documents,ml-cpu,dev]"   # 시험/ZIP 빌드용
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse\win-x64-cp312-cpu --app-wheel dist\corp_dl_agent-4.0.0-py3-none-any.whl --evidence-dir test-evidence --out dist
```
이렇게 만든 ZIP 은 wheel 내용이 동일하지만 ZIP 자체의 sha256 은 빌드 시각 등으로 달라질 수 있으므로, 사내 반입 hash 는 **실제로 옮기는 ZIP** 의 `.zip.sha256` 을 기준으로 합니다.

## 3. 포장 allowlist / 제외
포함: `source/ app/ wheelhouse/<profile>/ locks/ scripts/ config-examples/ fixtures/ schemas/ docs/ release-manifest.json checksums.sha256 dependency-inventory.json test-evidence/`
제외: `.venv .env .git .claude __pycache__ workspace dist 개인 절대경로 API 키 실제 회사 파일 학습 결과`. 합성 demo 모델을 넣는 경우 origin=synthetic 표시.
빌더는 금지 패턴을 검사하고 발견 시 실패합니다.

## 4. hash 정의(순환 없음)
- `checksums.sha256`: payload 파일(release-manifest.json 포함) 의 sha256. 자기 자신은 제외.
- `release-manifest.json.files`: payload 파일 목록/크기/hash. `checksums.sha256` 과 manifest 자신은 제외.
- ZIP 자체 hash 는 ZIP 밖 `.sha256` 파일과 acceptance.json 에만 기록.
- hash 는 무결성 확인이며 배포자 진위 확인이 아닙니다. self-signed key 를 동봉하지 않습니다.

## 5. 다음 버전 만들기
1. 코어 개선은 개인에서 새 버전(`version.py`, `pyproject.toml`)으로. 회사 설정/템플릿/데이터는 가져오지 않습니다.
2. 같은 절차로 ZIP 생성 → 사내에서는 `05_Update.cmd` (UPDATE_ROLLBACK_KO.md).
3. DB schema 를 바꾸면 `DB_SCHEMA_VERSION` 을 올리고 migration 을 `state/db.py` 에 추가하며 호환 범위를 문서에 적습니다.
