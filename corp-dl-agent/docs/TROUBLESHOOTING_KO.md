# 문제 해결 (TROUBLESHOOTING_KO)

| 증상 / 코드 | 원인 | 조치 |
|---|---|---|
| `00_Preflight.cmd` exit 8, `BLOCKED_PREREQUISITE` | Python 3.12 없음 / venv·ensurepip 없음 / 32-bit Python | 회사 승인 Python 3.12 x64 설치 요청. 설치 후 `PYTHON` 환경변수로 경로 지정 |
| `01_Verify.cmd` `E_PACKAGE_INVALID` hash 불일치 | 전송 중 손상 또는 다른 ZIP | ZIP SHA-256 재확인 후 다시 받기 |
| `E_PACKAGE_INVALID` target 불일치(OS/Python/ABI) | 반입 패키지가 다른 타깃용 | `target_profile.min.json` 을 개인 빌드 담당에게 보내 해당 타깃 패키지 요청 |
| `02_Install.cmd` "No matching distribution" / hash mismatch | wheelhouse 누락/변조, lock 불일치 | verify 결과 확인. 인터넷 fallback 은 없으므로 패키지 재반입 |
| 설치 중 `PIP_INDEX_URL`/프록시 관련 메시지 | 사용자 pip 설정이 상속됨 | 설치기는 PIP_* 환경/설정을 차단합니다(`--isolated`). 그래도 나오면 `install_log.json` 첨부하여 보고 |
| `E_BLOCKED_DEPENDENCY` torch/pandas 없음 | ml-cpu 프로파일 아닌 패키지 설치 | `release-manifest.json` 의 features 확인. CPU_OFFLINE 패키지로 재설치 |
| `E_CONFIG_UNKNOWN_KEY` | 설정 오타/지원하지 않는 키 | `config validate` 의 위치 확인 후 수정 |
| `E_CONFIG_SECRET_MISSING` | env:/file: 참조 대상 없음 | 환경 변수 또는 파일 생성. 값을 YAML 에 직접 쓰지 말 것 |
| `E_NETWORK_BLOCKED` / `E_ORIGIN_NOT_ALLOWED` | offline 프로파일에서 호출 시도 / approved_origins 밖 | corp-gateway 프로파일 + approved_origins 설정 |
| `E_GATEWAY_AUTH` (401/403) | 토큰/권한 문제 | 재시도하지 않음. 담당자에게 권한 확인 |
| `E_SPLIT_IMPOSSIBLE` | group_column 종류 부족 | 그룹 수 ≥3 필요. 행 무작위 분할로 대체하지 않음 |
| `E_LEAKAGE` | ID/사후 결과 열이 feature 에 포함 | excluded_columns 에 추가 |
| `E_INPUT_INVALID` (NaN/Inf/중복 ID/타깃 누락) | 데이터 품질 | 원인 행 번호를 보고 수정. 자동 삭제하지 않음 |
| `E_NEEDS_ACCEPTANCE_CRITERIA` | TaskSpec.acceptance 없음 | 업무 허용오차 입력 |
| `E_ARTIFACT_SYNTHETIC` | 합성 demo 모델을 운영 predict 에 사용 | 실데이터 모델 지정 또는 `--allow-synthetic`(검증 목적만) |
| `E_LEASE_HELD` | 다른 worker 가 같은 run 실행 중 | heartbeat 만료 대기 또는 해당 프로세스 종료 |
| `E_FINGERPRINT_CHANGED` | 코드/데이터/lock 변경 후 resume | 새 run 시작 |
| `E_BUDGET_EXCEEDED` | 시간/호출/토큰 한도 | resource_budget 조정(완료 보증 아님) |
| `E_UPGRADE_LOCKED` | 실행 중 run | 완료/pause 후 업데이트 |
| `E_ROLLBACK_INCOMPATIBLE` | 구버전이 새 DB schema 를 못 읽음 | UPDATE_ROLLBACK_KO.md 의 DB 롤백 절차 |
| `E_DOC_UNSUPPORTED` .ppt/.xls | 구형 형식 | 승인된 변환 후 재색인 |
| `RECALC_NOT_RUN` / `RENDER_NOT_RUN` | 계산 엔진/렌더러 없음 | 정상 상태 표시. Excel 에서 열어 재계산, 승인 렌더러 설정 시 검사 |
| 한글/공백 경로 문제 | 인용 부호 누락 | 경로를 큰따옴표로 감싸기. `.cmd` 는 `%~dp0` 기준으로 동작 |
| `.cmd` 실행 차단 | 회사 정책 | 직접 실행 명령(COMPANY_INSTALL_KO §2) 을 승인된 방법으로 실행. 정책 해제 금지 |
| 학습이 느림 | CPU 전용 | demo 예산(후보 2/10 epochs/300초)·pilot 예산 조정. GPU 는 별도 프로파일과 드라이버 검증 필요 |

로그 위치: `<data_root>\logs\`, run 별 `runs\<run_id>\events.jsonl`, 설치 `releases\<id>\<profile>\install_log.json`.
문제 보고 시 `doctor --json`(비밀 마스킹됨)과 합성 입력으로 재현한 최소 예제를 첨부합니다. 실제 회사 파일/URL/키는 포함하지 않습니다.
