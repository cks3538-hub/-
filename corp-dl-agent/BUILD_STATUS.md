# BUILD_STATUS

갱신: 2026-09-17 (세션 1, 통합 단계)

| 단계 | 상태 | 비고 |
|---|---|---|
| M0 진단·계획·기반 코드 | DONE | Linux 컨테이너, CPython 3.12.3 venv, PyPI 접근 가능, download.pytorch.org 차단, GPU 없음 |
| M1 engineering | DONE | 101 tests (단위 1.0 kg, 수량/이중합산/suppressed/MISSING, 사출원가, A/B 비교) |
| M2 ml-core / ml-torch | DONE | 98 + 40 tests (그룹 누수 0, train-only fit, test 잠금, pause/resume, OOM/NaN 정책(MOCK), export 재로드) |
| M3 documents | DONE | 60 tests (색인/검색/payload/템플릿/검증; RENDER/RECALC NOT_RUN) |
| M4 adapters | DONE | 110 tests (mock gateway/Atlassian, OutboundGuard, CATIA INACTIVE) |
| M4b reporting/doctor/self-test/menu | DONE | 15 tests |
| M5 packaging 코드·scripts | DONE(코드) | 59 tests (가짜 wheelhouse 로 lock/manifest/ZIP/verify/install/upgrade/rollback). Windows .cmd/.ps1 실행은 NOT_RUN(정적 검사만) |
| 통합 수정 | DONE | 전체 pytest 통과, ruff/mypy 통과, CLI 실사용 점검, tools dispatch(train_model/predict) 실제 연결, config-examples YAML 수정, docs search --evidence-output 경로 수정 |
| demo 통합 시나리오 | DONE | corp_dl_agent/demo.py + commands/demo_cmd.py, tests/test_demo.py 10 tests. `demo --offline --device cpu --output-dir workspace/demo-full` 실행: 12/12 단계·16/16 검사 PASS, 7.5초, 산출물 33개 hash, 원본 fixture 불변, 문서 수치==payload, outbound 0회(프로세스 내 guard). 메뉴 1번 노출. documents/validation.py 수정: PPTX 텍스트 수치 대조를 렌더링 정밀도(소수 4자리) 기준으로 비교 |
| M5 실제 wheel/lock/ZIP | PENDING | wheelhouse win/linux 준비됨(PyPI hash 대조 완료) |
| M6 transfer-test | PENDING | Linux rehearsal(tools/transfer_test_linux.sh); Windows 는 사용자 PC 에서 NOT_RUN 상태로 인계 |
| M7 upgrade/rollback·최종 ZIP·acceptance·문서 | PENDING | |

검증 상태: HOST_CORE_TESTED=PASS(근거: pytest 572 passed, ruff/mypy 통과, demo all_pass — Linux x86_64/CPython 3.12/CPU·offline 합성 데이터), TARGET_BUNDLE_PREPARED=NOT_RUN(wheel 확보됨, lock/manifest 미생성), TARGET_OFFLINE_TESTED=NOT_RUN, CORP_INSTALLED=NOT_RUN, CORP_INTEGRATED=NOT_RUN, BUSINESS_VALIDATED=NOT_RUN
