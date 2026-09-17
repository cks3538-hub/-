# 업데이트·롤백·회사 설정 보존 (UPDATE_ROLLBACK_KO)

## 원칙
- release 는 불변입니다. 새 버전은 `releases\<새버전>\<profile>\` 에 **추가 설치**되고 이전 버전은 남습니다.
- `company\config`, `company\templates`, `company\extensions`, `workspace\`(runs/models/outputs/state/indexes), `backups\` 는 release 밖에 있어 업데이트가 덮어쓰지 않습니다. 예시 설정(config-examples)이 회사 설정을 덮어쓰지 않습니다.
- `active.json` 은 새 버전의 설치·self-test 가 모두 성공한 뒤에만 원자적으로 바뀝니다. 실패하면 이전 활성 버전이 그대로 유지됩니다.
- 실행 중인 학습/문서 작업(lease 가 살아있는 run)이 있으면 업데이트를 시작하지 않습니다(E_UPGRADE_LOCKED). 작업을 완료하거나 `pause` 한 뒤 다시 시도합니다.

## 업데이트 순서
1. 새 ZIP 의 SHA-256 을 확인하고 압축을 풉니다 (COMPANY_INSTALL_KO.md §1).
2. 새 ZIP 의 `scripts\00_Preflight.cmd` → `01_Verify.cmd` 를 실행합니다.
3. `05_Update.cmd` 를 실행합니다. 내부 동작:
   1. 활성 run/lease 검사 (있으면 중단)
   2. 새 release 폴더에 venv 생성·오프라인 설치·self-test
   3. 상태 DB(`workspace\state\agent_state.sqlite`) 를 **sqlite backup API** 로 `backups\<시각>\` 에 일관된 사본 생성 (WAL 사용 중 파일 복사 아님) + 회사 설정/템플릿 snapshot
   4. DB schema 호환 검사 → `migration_plan.json` 작성 → 사본에 migration 적용·검증 후 교체
   5. 모두 성공하면 `active.json` 전환. 하나라도 실패하면 이전 버전 유지, 새 폴더는 `install_failed` 표시
4. `04_SelfTest.cmd` 로 확인합니다.

같은 버전을 다시 설치하면 manifest 가 동일할 때만 재사용(idempotent)하고, 내용이 다르면 조용히 덮어쓰지 않고 중단합니다.

## 롤백 순서
- **코드 롤백**: `06_Rollback.cmd` (또는 `launch.cmd rollback --to 4.0.0`) → `active.json` 을 이전 release 로 되돌립니다. 이전 venv 는 그대로 있으므로 재설치가 없습니다.
- **DB 롤백**: 새 버전이 DB schema 를 올렸고 이전 버전이 새 schema 를 읽을 수 없으면 코드 롤백만으로는 동작하지 않습니다. 이때 `rollback --to <버전> --restore-db <backups\시각>` 로 snapshot 을 복원할 수 있으나, **업데이트 이후에 추가된 run/문서 이력은 사라집니다**. 프로그램은 영향 범위를 먼저 보고하고 명시적 지정 없이는 복원하지 않습니다.
- 인덱스(`workspace\indexes`)는 원본 권한이 유지되므로 `docs index` 로 재구축할 수 있습니다.

## 버전별 schema 호환
| 항목 | 4.0.x |
|---|---|
| 설정 schema_version | 4.0 |
| DB schema | 4 |
| export 모델 번들 | manifest.schema_version 4.0 |
자동 downgrade 는 지원하지 않습니다. 호환 범위 밖이면 E_ROLLBACK_INCOMPATIBLE 로 중단하고 안내합니다.

## 사내 변경 보존
- 회사 전용 매핑/연결 코드는 `company\extensions\` (설정 `extensions:` 에 이름/경로/버전/compatible_schema 명시) 로 두고 사내 Bitbucket 에서 관리합니다. 임의 폴더 plugin 자동 탐색은 하지 않습니다.
- 개인 release 와 사내 저장소를 자동 동기화하지 않습니다. 개인에게 문제를 전달할 때는 합성 입력으로 재현한 최소 예제를 만듭니다. `doctor --export-diagnostics-preview` 는 허용 필드(OS/Python/ABI/기능 상태)만의 로컬 미리보기이며 자동 반출하지 않습니다.
