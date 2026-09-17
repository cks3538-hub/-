# BLOCKERS (개인 개발 단계에서 해결할 수 없는 항목)

| 항목 | 상태 | 사유 | 사내에서 필요한 것 |
|---|---|---|---|
| 사내 타깃 사양 | TARGET_UNCONFIRMED | Windows x64 / CPython 3.12 / CPU 는 임시 참조값 (win-x64-cp312-cpu) | 실제 OS/Python/ABI (00_Preflight.cmd 가 만드는 target_profile.min.json) |
| Windows 실행 시험 | NOT_RUN | 빌드 호스트가 Linux 컨테이너 | 사용자 Windows PC 또는 사내 PC 에서 00~04 스크립트 실행 (TARGET_OFFLINE_TESTED 근거) |
| GPU 프로파일 | NOT_RUN | GPU/드라이버 없음, download.pytorch.org 차단 | GPU/드라이버/승인 torch 빌드 조합 확인 후 별도 wheelhouse(ml-gpu) |
| CATIA live adapter | INACTIVE | CATIA 미설치 | 설치 제품/버전/COM 허가/라이선스; 확인 전 method 이름 창작 금지 |
| 사내 LLM gateway | MOCK_TESTED / NOT_RUN | endpoint/model ID/인증 없음 | base URL, api_format, model_id, secret 참조, CA, approved origins |
| Gemini 사내 형식 | NOT_SUPPORTED | 제공 형식 미확정 | 사내 계약 명세 |
| Atlassian | MOCK_TESTED / NOT_RUN | 연결 정보 없음 | 제품별 base URL/인증/버전; write 는 명시 승인 |
| Office 재계산/렌더링 | RECALC_NOT_RUN / RENDER_NOT_RUN | Excel/렌더러 없음 | 승인 렌더러(LibreOffice 등) 또는 Excel 대화형 확인 |
| 라이브러리 반입 승인 | UNREVIEWED | 회사 승인은 별도 절차 | dependency-inventory.json / docs/DEPENDENCY_NOTICES.md 검토 (cuda-toolkit 라이선스 UNKNOWN 은 Linux 프로파일에만 해당) |
| 실데이터/업무 허용오차 | NOT_RUN | 합성 데이터만 사용 | 실제 CAD/시험 데이터, TaskSpec.acceptance |
| 실제 네트워크 격리 설치 시험 | PARTIAL | Linux rehearsal 은 unshare -n(네임스페이스) 격리, 프로세스 내 OutboundGuard 검사. 방화벽/사내 격리 환경은 미사용 | 승인된 격리 환경에서 install→self-test→demo 재실행 |
| DB migration end-to-end | NOT_RUN | schema 4 단일 (migrate 경로 없음) | 다음 schema 변경 시 migration 시험 |
| OOM 정책 실제 GPU | MOCK_TESTED | 모의 주입만 | GPU 환경 |
