# NEXT_ACTION

1. 통합/demo 워크플로 완료 확인: `timeout 1500 .venv/bin/python -m pytest -q tests`, `ruff check .`, `mypy corp_dl_agent`, `python -m corp_dl_agent demo --offline --device cpu --output-dir workspace/demo-full`.
2. 앱 wheel: `.venv/bin/python -m build --wheel` → dist/corp_dl_agent-4.0.0-py3-none-any.whl
3. ZIP: `python -m corp_dl_agent package build --profile win-x64-cp312-cpu --wheelhouse wheelhouse/win-x64-cp312-cpu --app-wheel dist/... --evidence-dir test-evidence --out dist` (linux 프로파일도 동일)
4. Linux rehearsal: `sudo bash tools/transfer_test_linux.sh dist/DIA_4.0.0_linux-x64-cp312-cpu.zip linux-x64-cp312-cpu` → acceptance.py
5. 문서 갱신(TEST_EVIDENCE.md, BUILD_STATUS, BLOCKERS, TRANSFER_CONTENTS), git commit/push, 최종 보고.
