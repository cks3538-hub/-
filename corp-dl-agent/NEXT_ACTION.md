# NEXT_ACTION

1. wave 1 구현 완료 확인: `.venv/bin/python -m pytest -q` 전체 통과, `ruff check .`, `mypy corp_dl_agent`.
2. wave 2: ml-torch(mlp/checkpoint/trainer/export/runner), demo.py 통합.
3. app wheel 빌드 → win-x64-cp312 wheelhouse cross-download + linux-x64-cp312 wheelhouse → lock/inventory → ZIP.
4. Linux 새 경로 transfer-test (한글/공백 경로, 다른 CWD, 비관리자, 네트워크 차단) → acceptance.
5. 문서 12종 작성, BUILD_STATUS/BLOCKERS 갱신, git commit/push.
