# NEXT_ACTION (세션 1 완료 후)

빌드·검증은 완료되었다. 다음은 사용자/사내 단계다.

1. Windows 개인 PC 에서 TARGET_OFFLINE_TESTED 수행: `DIA_4.0.0_win-x64-cp312-cpu.zip` (SHA-256 397e7ea19e14064b04de5d3440313e88c9c23bdb7b382cb46e62dd6778f1afe7) 을 새 폴더에 풀고 `scripts\00_Preflight.cmd → 01_Verify.cmd → 02_Install.cmd → 04_SelfTest.cmd → launch.cmd demo --offline --device cpu` 실행. 결과 JSON 을 `python scripts\acceptance.py --zip <zip> --evidence-dir <폴더> --out <폴더> --status TARGET_OFFLINE_TESTED=PASS=...` 로 기록.
2. ZIP 을 직접 받지 못했으면 docs/PERSONAL_BUILD_KO.md §2b 로 재현 (tools/reproduce_wheelhouse.py).
3. 사내: docs/COMPANY_INSTALL_KO.md 순서. 사내 AI 인계문: docs/CORPORATE_AI_HANDOFF.txt.
4. 다음 버전 개발 재개: `.venv/bin/python -m pytest -q tests` (575 tests), CLAUDE.md 의 실행 명령.
