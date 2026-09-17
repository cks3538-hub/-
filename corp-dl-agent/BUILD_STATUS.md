# BUILD_STATUS

갱신: 2026-09-17 (세션 1 완료)

| 단계 | 상태 | 비고 |
|---|---|---|
| M0 진단·계획·기반 코드 | DONE | Linux x86_64 컨테이너, CPython 3.12.3 venv, PyPI 접근 가능(download.pytorch.org 차단), GPU 없음 |
| M1 engineering | DONE | 101 tests: 단위(1,000,000 mm³×1.0 g/cm³=1.0 kg), 수량/이중합산 금지/suppressed/unloaded·밀도누락·surface MISSING, 사출원가식, 통화·기준일·revision 정합, A/B 비교 |
| M2 ml-core / ml-torch | DONE | 98 + 40 tests: 인코딩 판정, 그룹 누수 0, train-only fit, test 잠금, 기준 모델 3종+MLP, pause/resume/강제 종료, 완료 trial skip, OOM(MOCK)/NaN 정책, export 새 프로세스 재로드, 합성 모델 운영 차단 |
| M3 documents | DONE | 60 tests: PPTX/XLSX 색인·locator·수식/cached·hidden, 키워드 검색, payload 합계/차이 재검증(CONFLICT), 템플릿 placeholder/named range 만 수정·구조 보존, 과거 값 잔존 검사, RECALC/RENDER NOT_RUN |
| M4 adapters | DONE | 110 tests: mock OpenAI/Anthropic 계약·401/403 재시도 0·429/5xx 2회·redirect 거부·JSON 교정 1회·예산, Atlassian pagination/409/rate limit/write off, OutboundGuard, CATIA INACTIVE |
| M4b reporting/doctor/self-test/menu | DONE | 15 tests |
| M5 packaging·scripts | DONE | 59 tests(가짜 release 로 verify/install/upgrade/rollback/acceptance) + 실제 앱 wheel·wheelhouse(win 38/linux 56 wheel, PyPI sha256 대조)·lock·inventory·manifest·ZIP |
| 통합·demo(J) | DONE | 전체 572 tests, ruff/mypy 통과, demo 12/12 단계·16/16 검사 PASS, outbound 0, 산출물 33개 |
| M6 transfer-test | DONE(Linux) / NOT_RUN(Windows) | Linux rehearsal 12 단계 PASS: ZIP 만으로 sha256→extract→preflight→verify→변조 거부→install(--no-index --require-hashes, 107s)→active.json→self-test→doctor→demo(12/12)→변조 설치 시 active 유지→pip index 미사용. 비관리자 tester, 한글/공백 경로, CWD=/tmp, unshare -n, PIP_INDEX_URL 오염. Windows: 사용자 PC 에서 00~04 실행 필요 |
| M7 upgrade/rollback·최종 ZIP·acceptance·문서 | DONE | upgrade/rollback 은 scripts 시험(가짜 release, 회사 설정/템플릿/DB 보존, 실패 시 old active 유지, --restore-db) 로 검증; 최종 ZIP 2종 + 외부 .sha256 + .acceptance.json 생성 |

## 검증 상태
| 상태 | 값 | 근거 |
|---|---|---|
| HOST_CORE_TESTED | PASS | test-evidence/host-01~07 (pytest 572, ruff, mypy, wheel, demo, self-test) |
| TARGET_BUNDLE_PREPARED | PASS | wheelhouse/win-x64-cp312-cpu 38 wheel + locks/win-x64-cp312-cpu.txt(hash) + release-manifest; requirements/download_provenance.json |
| TARGET_OFFLINE_TESTED | NOT_RUN(Windows 참조 타깃) / PASS(Linux rehearsal 프로파일) | Windows ZIP SHA-256 397e7ea19e14064b… 는 정적 검증만(host-10); Linux ZIP d037dfe1c9753178… 는 test-evidence/transfer-* 12 단계 PASS |
| CORP_INSTALLED | NOT_RUN | 사내 PC 에서만 |
| CORP_INTEGRATED | NOT_RUN | 사내 PC 에서만 |
| BUSINESS_VALIDATED | NOT_RUN | 실데이터·업무 허용오차 필요 |
| TARGET_CONFIRMED | TARGET_UNCONFIRMED | Windows x64 / CPython 3.12 / CPU 는 임시 참조값 |

## 알려진 PARTIAL
- CUDA/AMP 경로 NOT_RUN(GPU 없음), OOM 정책 MOCK_TESTED, 실제 GPU OOM NOT_RUN
- 문서 렌더링/수식 재계산 엔진 미실행(RENDER_NOT_RUN/RECALC_NOT_RUN 표시)
- Gemini 사내 형식 계약 미확정(E_NOT_SUPPORTED), CATIA live adapter INACTIVE
- DB migration(migrate 경로) end-to-end 미시험(schema 4 단일)
- 네트워크 격리: 프로세스 내 OutboundGuard + Linux rehearsal 의 unshare -n. 승인된 사내 격리 환경 시험은 NOT_RUN
