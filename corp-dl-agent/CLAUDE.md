# corp-dl-agent (설계·문서 업무 자동화 범용 코어 v4)

전체 명세: `docs/BUILD_SPEC.md` (지시문 원문, 우선), `docs/ARCHITECTURE_V4.md`, 모듈 계약: `docs/CONTRACT.md`.
진행 상태: `build_state.json`, `BUILD_STATUS.md`, `NEXT_ACTION.md` — 세션 재개 시 이 셋과 실제 파일을 대조하고 완료 작업을 반복 생성하지 않는다.

## 핵심 규칙
- 완성 프로그램은 Fable/개인 구독/개인 API 키/로그인에 의존하지 않는다. 사내 runtime 은 인터넷·Git·pip index 없이 동작한다.
- 개인 PC 에서는 합성 데이터만 사용. 사내 도면/원가/문서/키/URL 을 요구하지 않는다. 모르는 회사 정보는 자리표시자 + NOT_RUN/BLOCKED.
- 프로파일: personal-dev / transfer-test / corp-offline(기본, API 0회) / corp-gateway(명시된 origin/model/secret 참조가 있을 때만).
- 숫자는 계산 엔진/metrics 만. LLM 이 수치·근거를 생성하지 않는다. 없는 값은 MISSING, 상충은 CONFLICT.
- 최상위 third-party import 금지 (torch/pandas/pptx/openpyxl/httpx 는 함수 내부 lazy import). doctor/preflight 는 표준 라이브러리로 동작.
- 비밀값은 env:/file: 참조만. 로그/예외/설정 출력에서 마스킹.
- 파일 쓰기는 원자적(temp→fsync→replace). 원본 입력은 읽기 전용. 관리 폴더 밖 삭제 금지.
- 합성 모델/문서에는 synthetic 표시. 운영에서 synthetic 모델 자동 채택 금지.
- 상태 6종(HOST_CORE_TESTED, TARGET_BUNDLE_PREPARED, TARGET_OFFLINE_TESTED, CORP_INSTALLED, CORP_INTEGRATED, BUSINESS_VALIDATED) 은 근거와 함께 표시. 개인 개발 단계에서 뒤 셋은 PASS 금지.

## 실행 명령 (개발)
```
.venv/bin/python -m pytest -q                      # 전체 시험
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy corp_dl_agent
.venv/bin/python -m corp_dl_agent doctor
.venv/bin/python -m corp_dl_agent demo --offline --device cpu --output-dir workspace/demo
.venv/bin/python -m build --wheel                  # app wheel
.venv/bin/python -m corp_dl_agent package build --profile win-x64-cp312-cpu
python3 scripts/preflight.py --report /tmp/preflight.json   # 표준 라이브러리만
```

## 폴더
- `corp_dl_agent/` 패키지 (config, state, engineering, ml, documents, adapters, security, reporting, packaging, commands)
- `scripts/` 표준 라이브러리 설치기/실행기 (+ .cmd/.ps1/.sh)
- `fixtures/` 합성 CAD/CSV/PPTX/XLSX, `config-examples/`, `schemas/`, `docs/`, `tests/`
- 생성물(커밋 금지): `.venv/`, `dist/`, `wheelhouse/`, `workspace/`, `build/`, `test-evidence/raw/`

## 개발 환경 (이 세션)
- Linux x86_64 클라우드 컨테이너, CPython 3.12.3 (.venv), GPU 없음. 사용자의 개인 PC 는 Windows.
- 참조 타깃: Windows x64 / CPython 3.12 / CPU (target_confirmed=false). PyPI 만 접근 가능(download.pytorch.org 차단).
