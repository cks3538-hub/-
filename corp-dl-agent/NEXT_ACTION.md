# NEXT_ACTION

1. (완료) 통합 수정: `timeout 1500 .venv/bin/python -m pytest -q tests` 전체 통과, `ruff check .`/`ruff format --check .`/`mypy corp_dl_agent` 통과, CLI 실사용 점검 완료. packaging 코드 완료(M5 코드 DONE).
1b. (완료) demo 통합 시나리오 구현 (pytest 572 passed, demo 12/12 단계·16/16 검사 PASS, workspace/demo-full/demo_manifest.json): `corp_dl_agent/demo.py` + `commands/demo_cmd.py` (tests/test_cli_basic.py 의 ALLOWED_MISSING_BEFORE_INTEGRATION 에서 demo_cmd 제거) → `python -m corp_dl_agent demo --offline --device cpu --output-dir workspace/demo-full`.
2. 앱 wheel: `.venv/bin/python -m build --wheel` → dist/corp_dl_agent-4.0.0-py3-none-any.whl
3. ZIP: `python -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse/win-x64-cp312-cpu --app-wheel dist/... --evidence-dir test-evidence --out dist` (linux 프로파일도 동일)
4. Linux rehearsal: `sudo bash tools/transfer_test_linux.sh dist/DIA_4.0.0_linux-x64-cp312-cpu.zip linux-x64-cp312-cpu` → acceptance.py
5. 문서 갱신(TEST_EVIDENCE.md, BUILD_STATUS, BLOCKERS, TRANSFER_CONTENTS), git commit/push, 최종 보고.
