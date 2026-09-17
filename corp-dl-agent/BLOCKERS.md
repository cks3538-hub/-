# BLOCKERS (개인 개발 단계에서 해결할 수 없는 항목)

| 항목 | 상태 | 사유 | 사내에서 필요한 것 |
|---|---|---|---|
| 사내 타깃 사양 | TARGET_UNCONFIRMED | Windows x64 / CPython 3.12 / CPU 는 임시 참조값 | 실제 OS/Python/ABI (preflight 의 target_profile.min.json) |
| Windows 실행 시험 | NOT_RUN | 빌드 호스트가 Linux 컨테이너 | Windows PC 에서 00~04 스크립트 실행 (사용자 개인 Windows PC 로 가능) |
| GPU 프로파일 | NOT_RUN | GPU/드라이버 없음, download.pytorch.org 차단 | GPU/드라이버/승인 torch 빌드 조합 확인 후 별도 wheelhouse |
| CATIA live adapter | INACTIVE | CATIA 미설치 | 설치 제품/버전/COM 허가/라이선스 |
| 사내 LLM gateway | NOT_RUN (MOCK_TESTED) | endpoint/model ID/인증 없음 | base URL, api_format, model_id, secret 참조, CA, approved origins |
| Gemini 사내 형식 | NOT_SUPPORTED | 제공 형식 미확정 | 사내 계약 명세 |
| Atlassian | NOT_RUN (MOCK_TESTED) | 연결 정보 없음 | 제품별 base URL/인증/버전 |
| Office 재계산/렌더링 | RECALC_NOT_RUN / RENDER_NOT_RUN | Excel/렌더러 없음 | 승인 렌더러 또는 Excel 대화형 확인 |
| 라이브러리 반입 승인 | UNREVIEWED | 회사 승인은 별도 절차 | dependency-inventory.json 검토 |
| 실데이터/업무 허용오차 | NOT_RUN | 합성 데이터만 사용 | 실제 CAD/시험 데이터, acceptance 기준 |
| 실제 네트워크 격리 설치 시험 | PARTIAL | 프로세스 내 OutboundGuard 검사만 가능(컨테이너 방화벽 변경 불가) | 승인된 격리 환경 |
