# 반입 패키지 내용 (TRANSFER_CONTENTS_KO)

## 옮길 파일 (3개) — Windows 참조 타깃용

(Linux 참조 프로파일 ZIP `DIA_4.0.0_linux-x64-cp312-cpu.zip` 은 개인 환경 rehearsal 용이며 사내 Windows PC 에는 옮기지 않습니다.)
| 파일 | 설명 |
|---|---|
| `DIA_4.0.0_win-x64-cp312-cpu.zip` | 반입 패키지 (package_kind=CPU_OFFLINE, 약 220 MB, 254 항목) |
| `DIA_4.0.0_win-x64-cp312-cpu.zip.sha256` | ZIP 의 SHA-256 (ZIP 밖) |
| `DIA_4.0.0_win-x64-cp312-cpu.acceptance.json` | 최종 ZIP hash·검증 환경·명령/exit code·PASS/FAIL/NOT_RUN |

## ZIP 내부
| 경로 | 내용 |
|---|---|
| `source/` | 검토용 소스(corp_dl_agent/), tests/, pyproject.toml, CLAUDE.md, docs/CONTRACT.md |
| `app/` | 미리 빌드한 앱 wheel `corp_dl_agent-4.0.0-py3-none-any.whl` |
| `wheelhouse/win-x64-cp312-cpu/` | core+documents+ml-cpu 의 모든 dependency wheel (38개, 약 218 MB; torch 2.14.0 은 PyPI Windows CPU 빌드) |
| `locks/win-x64-cp312-cpu.txt` | 앱 포함 version + sha256 고정 설치 명세 (`--require-hashes`) |
| `scripts/` | preflight/verify/install/launch/selftest/upgrade/rollback/acceptance (.py 표준 라이브러리) + `00_Preflight.cmd`…`06_Rollback.cmd`, `preflight.ps1`, `*.sh` |
| `config-examples/` | secret 없는 회사 설정·매핑 예시 |
| `fixtures/` | 합성 CAD snapshot/CSV/PPTX/XLSX/payload (synthetic 표시) |
| `schemas/` | config/taskspec/snapshot/payload/release JSON schema |
| `docs/` | 이 문서들 (설치/연동/운영/복구/문제해결/라이선스 고지) |
| `release-manifest.json` | 릴리스·대상·기능·schema 버전·검증 상태·파일 목록 |
| `checksums.sha256` | payload 파일 hash (자기 자신 제외) |
| `dependency-inventory.json` | package/version/wheel/source/license/hash/approval_state=UNREVIEWED |
| `test-evidence/` | 개인 환경 시험 명령·exit code·환경·결과 (경로는 `<PROJECT_ROOT>`/`<TEST_ROOT>` 로 정규화) |

## 포함하지 않은 것 (사내 선행 조건)
- Python 3.12 런타임(회사 승인), CATIA, Office, GPU 드라이버, 문서 렌더러, 한국어 폰트
- 회사 설정 실제값, secret, 회사 템플릿/데이터, 학습 결과, 개인 .venv/.env/.git/Claude 설정

## 상태 (개인 빌드 시점)
| 상태 | 값 | 근거 |
|---|---|---|
| HOST_CORE_TESTED | PASS | test-evidence/host-01~07 (pytest 572, ruff, mypy, wheel, demo, self-test) |
| TARGET_BUNDLE_PREPARED | PASS | wheelhouse/lock/manifest/inventory (PyPI sha256 대조) |
| TARGET_OFFLINE_TESTED | NOT_RUN (Windows 환경 없음). Linux 참조 프로파일(linux-x64-cp312-cpu, 약 3.1 GB) 은 별도 ZIP 으로 새 환경 설치 rehearsal 수행 — 그 결과는 해당 ZIP 의 acceptance.json | acceptance.json |
| CORP_INSTALLED / CORP_INTEGRATED / BUSINESS_VALIDATED | NOT_RUN | 사내에서만 확인 |

라이브러리 반입/사용 승인은 회사 절차이며 `dependency-inventory.json` 의 `approval_state` 는 UNREVIEWED 입니다.
