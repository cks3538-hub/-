# TEST_EVIDENCE

개인 빌드 환경에서 실제 실행한 시험 목록. 경로는 `<PROJECT_ROOT>`/`<TEST_ROOT>`/`<HOME>` 으로 정규화했고, 원시 로그(개인 절대 경로 포함)는 `test-evidence/raw/` 에 로컬 보관하며 반입 ZIP 에 포함하지 않습니다. 실패/미실행은 그대로 기록합니다.

| 이름 | 상태 | exit | 소요(s) | 환경 | 명령 |
|---|---|---|---|---|---|
| smoke-version | PASS | 0 | 0.04 | Linux x86_64 / CPython 3.12.3 (.venv) | `.venv/bin/python -m corp_dl_agent version` |

각 항목의 stdout/stderr 꼬리와 lock hash 는 `test-evidence/<이름>.json` 에 있습니다.
