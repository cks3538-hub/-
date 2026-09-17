# TEST_EVIDENCE

개인 빌드 환경에서 실제 실행한 시험 목록. 경로는 `<PROJECT_ROOT>`/`<TEST_ROOT>`/`<HOME>` 으로 정규화했고, 원시 로그(개인 절대 경로 포함)는 `test-evidence/raw/` 에 로컬 보관하며 반입 ZIP 에 포함하지 않습니다. 실패/미실행은 그대로 기록합니다.

| 이름 | 상태 | exit | 소요(s) | 환경 | 명령 |
|---|---|---|---|---|---|
| host-01-pytest-all | PASS | 0 | 101.26 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m pytest -q tests -p no:cacheprovider` |
| host-02-ruff-check | PASS | 0 | 0.03 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m ruff check .` |
| host-03-ruff-format | PASS | 0 | 0.03 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m ruff format --check .` |
| host-04-mypy | PASS | 0 | 1.42 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m mypy corp_dl_agent` |
| host-05-build-wheel | PASS | 0 | 0.72 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m build --wheel --no-isolation -o dist` |
| host-06-demo-offline-cpu | PASS | 0 | 7.46 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m corp_dl_agent demo --offline --device cpu --output-dir workspace/demo-evidence --json` |
| host-07-selftest | PASS | 0 | 3.17 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m corp_dl_agent self-test --set paths.data_root=workspace/selftest-ws` |
| host-08-package-build-win | PASS | 0 | 4.64 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse/win-x64-cp312-cpu --app-wheel dist/co...` |
| host-09-package-build-linux | PASS | 0 | 56.4 | Linux x86_64 / CPython-3.12.3-(.venv) | `.venv/bin/python -m corp_dl_agent package build --profile linux-x64-cp312-cpu --wheelhouse wheelhouse/linux-x64-cp312-cpu --app-wheel dis...` |
| host-10-verify-win-zip-static | PASS | 0 | 0.76 | Linux x86_64 / CPython 3.12.3 (/usr/bin/python3.12, 표준 라이브러리 verifier) | `/usr/bin/python3.12 scripts/verify.py --package dist/DIA_4.0.0_win-x64-cp312-cpu.zip --target-profile /tmp/claude-0/-home-user/bb3392bd-2...` |
| host-11-verify-linux-zip | PASS | 0 | 9.45 | Linux x86_64 / CPython 3.12.3 (/usr/bin/python3.12, 표준 라이브러리 verifier) | `/usr/bin/python3.12 scripts/verify.py --package dist/DIA_4.0.0_linux-x64-cp312-cpu.zip --json` |

각 항목의 stdout/stderr 꼬리와 lock hash 는 `test-evidence/<이름>.json` 에 있습니다.
