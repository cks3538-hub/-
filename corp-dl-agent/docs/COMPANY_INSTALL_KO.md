# 사내 설치 안내 (COMPANY_INSTALL_KO)

이 문서는 반입 ZIP(`DIA_4.0.0_win-x64-cp312-cpu.zip`)을 사내 Windows PC 에 설치하는 순서입니다. 인터넷·Git·pip index 가 없어도 됩니다.
일반 실행에 Claude/Fable/개인 API 키는 필요하지 않습니다. 사내 LLM 연결은 선택 기능입니다.

## 0. 준비물 (사내에서 확인)

| 항목 | 필요 조건 | 확인 방법 |
|---|---|---|
| Windows | x64 (참조 타깃, 미확정) | 설정 → 시스템 → 정보 |
| Python | **회사 승인 CPython 3.12.x (64-bit)**, `venv`·`ensurepip` 포함 | `00_Preflight.cmd` 가 확인 |
| 디스크 | 약 2 GB 여유 (wheelhouse 0.3 GB + venv 1 GB + 작업 공간) | preflight 보고서 |
| 권한 | 설치 폴더·데이터 폴더 쓰기 권한 (관리자 권한 불필요) | preflight 보고서 |
| 미포함 | Python 런타임, CATIA, Office, GPU 드라이버, 문서 렌더러 | 회사 승인 절차로 별도 설치 |

Python 이 없으면 **회사가 승인한 방법**으로 먼저 설치해야 합니다. 이 설치기는 Microsoft Store/웹에서 Python 을 자동 설치하지 않습니다.

## 1. 반입 파일 확인

1. 승인된 경로로 받은 파일 3개를 한 폴더(예: `D:\반입\DIA`)에 둡니다.
   - `DIA_4.0.0_win-x64-cp312-cpu.zip`
   - `DIA_4.0.0_win-x64-cp312-cpu.zip.sha256`
   - `DIA_4.0.0_win-x64-cp312-cpu.acceptance.json`
2. PowerShell 을 열고(시작 → "PowerShell" 입력 → Enter) 아래를 붙여넣어 SHA-256 을 비교합니다.
   ```powershell
   cd "D:\반입\DIA"
   (Get-FileHash .\DIA_4.0.0_win-x64-cp312-cpu.zip -Algorithm SHA256).Hash.ToLower()
   Get-Content .\DIA_4.0.0_win-x64-cp312-cpu.zip.sha256
   ```
   두 값이 같아야 합니다. 다르면 설치하지 말고 다시 받으세요. (hash 는 무결성 확인이며, 보낸 사람의 진위는 승인된 전달 경로로 확인합니다.)
3. ZIP 을 마우스 오른쪽 → "압축 풀기" 로 `D:\반입\DIA\DIA_4.0.0_win-x64-cp312-cpu\` 에 풉니다. `scripts\` 폴더가 보이면 정상입니다.

## 2. 설치 순서 (더블클릭 순서)

`scripts\` 폴더에서 아래 파일을 **순서대로** 더블클릭합니다. 각 창은 결과를 보여주고 아무 키나 누르면 닫힙니다. 창 제목/마지막 줄의 `exit code` 가 0 이면 성공입니다.

| 순서 | 파일 | 하는 일 | 실패 시 |
|---|---|---|---|
| ① | `00_Preflight.cmd` | Python/venv/ensurepip/디스크/쓰기 권한 진단. `target_profile.min.json` 생성 | exit 8 = 선행 조건 미충족(BLOCKED_PREREQUISITE). 승인 Python 경로를 `PYTHON` 환경변수로 지정하거나 관리자에게 요청 |
| ② | `01_Verify.cmd` | 패키지 manifest/파일 수/크기/hash/경로 탈출/대상 OS·Python·ABI 검사 | exit 5 = 패키지 손상/불일치. 다시 받기 |
| ③ | `02_Install.cmd` | `releases\4.0.0\win-x64-cp312-cpu\` 에 새 venv 생성 → 로컬 wheelhouse 로만 설치(`--no-index --require-hashes`) → import/self-test → `active.json` 전환 | exit 6 = 설치 실패. 기존 활성 버전 유지. `install_log.json` 확인 |
| ④ | `04_SelfTest.cmd` | 설치된 venv 로 `self-test` 실행(단위 1.0 kg 검증, sqlite, torch CPU, 문서 라이브러리) | 실패 항목이 있으면 TROUBLESHOOTING_KO.md |
| ⑤ | `03_Start.cmd` | 프로그램 시작 (메뉴) | |

### 설치 폴더 지정

기본 설치 폴더는 `%LOCALAPPDATA%\DIA` 입니다. 다른 폴더(한글/공백 가능)를 쓰려면 `02_Install.cmd` 를 실행하기 **전에** PowerShell 에서:
```powershell
$env:DIA_INSTALL_ROOT = "D:\설계 자동화\DIA"
$env:DIA_DATA_ROOT    = "D:\설계 자동화\workspace"
$env:PYTHON           = "C:\Program Files\Python312\python.exe"   # 승인 Python 경로 (선택)
& ".\DIA_4.0.0_win-x64-cp312-cpu\scripts\02_Install.cmd"
```
`.cmd` 파일은 어느 작업 폴더에서 실행해도 자기 위치(`%~dp0`) 기준으로 동작합니다.

### 실행 정책/보안 프로그램

PowerShell 실행 정책·인증서·GPO·백신을 해제하지 않습니다. `.cmd` 가 차단되면 아래 "직접 실행" 명령을 승인된 방법으로 실행하세요. venv 의 `Activate.ps1` 은 필요하지 않습니다.

```powershell
# 직접 실행 (Activate 불필요)
& "C:\Program Files\Python312\python.exe" ".\scripts\preflight.py" --report "%LOCALAPPDATA%\DIA\preflight_report.json"
& "C:\Program Files\Python312\python.exe" ".\scripts\verify.py" --package ".."
& "C:\Program Files\Python312\python.exe" ".\scripts\install.py" --package ".." --install-root "D:\설계 자동화\DIA" --data-root "D:\설계 자동화\workspace"
& "D:\설계 자동화\DIA\releases\4.0.0\win-x64-cp312-cpu\.venv\Scripts\python.exe" -m corp_dl_agent self-test
```

## 3. 설치 결과 폴더

```
<install_root>\
  active.json                                  활성 release/profile (설치 성공 후에만 원자적 교체)
  releases\4.0.0\win-x64-cp312-cpu\            불변 앱 + .venv (이동 금지)
  company\config\  company\templates\  company\extensions\   회사 자료 (업데이트 시 보존)
  backups\                                     업데이트 전 snapshot
<data_root>\ (기본 <install_root>\workspace)
  data\ runs\ models\ outputs\ state\ indexes\ logs\
```

## 4. 회사 설정 입력

1. `config-examples\company_config.example.yaml` 을 `<install_root>\company\config\company_config.yaml` 로 복사합니다.
2. 메모장으로 열어 `paths.sources_roots`(PPT/Excel 자료 폴더), `cad.field_mapping`, `documents.approved_fonts` 를 채웁니다. **secret 값은 넣지 않습니다**(`env:` / `file:` 참조만).
3. 확인: `03_Start.cmd` → 메뉴 "설정 검사" 또는
   ```
   scripts\launch.cmd config validate --config "<install_root>\company\config\company_config.yaml"
   ```
4. 회사 PPTX/XLSX 템플릿은 `company\templates\` 에 둡니다(placeholder `{{key}}` / named range 규칙은 USER_GUIDE_KO.md).

## 5. 합성 데이터 demo (사내 PC 에서 첫 확인)

```
scripts\launch.cmd demo --offline --device cpu --output-dir "<data_root>\outputs\demo"
```
CAD A/B 중량·원가 비교 → 합성 CSV 학습/예측 → 근거 검색 → review.pptx/comparison.xlsx 생성까지 한 번에 실행되며 `demo_manifest.json` 에 항목별 PASS/FAIL 이 남습니다. 이 결과가 CORP_INSTALLED 의 근거입니다. (합성 데이터이며 업무 검증이 아닙니다.)

## 6. 다음 단계

- 실제 CATIA export → `cad import` (COMPANY_CONNECTORS_KO.md)
- 실데이터 TaskSpec 작성 → `validate` → `run` (USER_GUIDE_KO.md)
- 사내 LLM/Atlassian 연결(선택) → `integrations doctor`
- 업데이트/롤백 → UPDATE_ROLLBACK_KO.md
