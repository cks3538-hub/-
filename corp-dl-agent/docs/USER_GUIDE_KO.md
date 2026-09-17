# 사용자 안내 (USER_GUIDE_KO)

프로그램 이름: **설계·문서 업무 자동화 프로그램** (corp-dl-agent 4.0.0). 실행에 인터넷·Claude·개인 API 키가 필요하지 않습니다.

## 1. 시작하기
- `scripts\03_Start.cmd` 더블클릭 → 메뉴가 뜹니다. (또는 `scripts\launch.cmd <명령>`)
- 메뉴 항목(실제 구현된 것만 표시): ① demo(합성 데이터 전체 흐름) ② 설정 검사(doctor) ③ 설계 비교 ④ 학습 ⑤ 문서 작성 ⑥ 상태 확인
- 모든 명령은 `--help` 로 한국어 사용법을 보여주고, 오류는 `오류 E_코드: 설명 / 해결 방법:` 형식입니다. `--json` 을 붙이면 결과가 JSON 입니다.

## 2. 명령 요약 (`launch.cmd` 뒤에 붙여 실행)
| 명령 | 용도 |
|---|---|
| `doctor` | 환경/패키지/경로/프로파일/연결 상태 진단 (secret 마스킹) |
| `self-test` | 설치 자체 검증 (단위 1.0 kg, sqlite, torch CPU, 문서 라이브러리) |
| `demo --offline --device cpu --output-dir <폴더> [--fast] [--fixtures-dir <폴더>]` | 합성 CAD A/B → 중량/원가 → 학습/예측 → 근거 검색 → PPTX/XLSX 까지 한 번에. 기본 산출 폴더 `<data_root>\outputs\demo-<UTC시각>` (run/상태 DB 는 그 아래 `workspace\`). fixtures 는 설치된 release 의 `fixtures\` 를 자동 사용 |
| `config validate --config <yaml>` / `config show` | 회사 설정 검증 / 마스킹된 최종 설정 표시 |
| `cad import --input <csv|json> --output <snapshot.json> [--mapping k=v]` | CATIA export 수입·검증 |
| `cad extract --adapter catia_v5_com` | (사내 CATIA 확인 후) live 추출. 개인 빌드에서는 INACTIVE |
| `design compare --candidate A=<폴더> --candidate B=<폴더> --recipes <json> --output <json>` | 동일 revision/기준일/단위 확인 후 mass/cost/performance/constraint/evidence/unknown 비교 |
| `validate --config <taskspec.yaml>` | 데이터 계약·인코딩·누수·그룹 분할 검사 (학습 없음) |
| `run --config <taskspec.yaml>` | 기준 모델 3종 + MLP 후보 학습 → validation 선택 → test 1회 평가 → 산출물 |
| `status/pause/resume/cancel --run-id <id>` | 실행 제어 (pause 는 epoch 경계) |
| `report --run-id <id>` | report_ko.html/md 재생성 |
| `predict --model <export 폴더> --input <csv> --output <csv>` | 저장 모델로 예측 (합성 모델은 기본 거부) |
| `docs index --roots <폴더...>` / `docs search --query "..."` / `docs generate --payload <json> --pptx-template ... --xlsx-template ... --output-dir ...` | 자료 색인/근거 검색/문서 생성 |
| `integrations doctor` / `integrations preview --product jira --payload <json>` | 연동 설정 상태 / 등록 전 미리보기(쓰기 없음) |
| `package verify --package <zip|폴더>` | 반입 패키지 검증 |
| `upgrade --package <zip>` / `rollback --to <release_id>` | 업데이트/롤백 (UPDATE_ROLLBACK_KO.md) |
| `version` | 버전 |

## 3. CATIA snapshot 수입 → 중량/원가 → 설계 비교
1. CATIA/PDM 에서 CSV(또는 JSON) 로 내보냅니다. 필요한 열: `document_id, revision, occurrence_path, reference_id, quantity, material_id, density, volume, geometry_status` (+ 단위 열). 열 이름이 다르면 `--mapping reference_id=PartNumber` 처럼 지정합니다.
2. `cad import --input "D:\export\A.csv" --output "D:\work\A\cad_snapshot.json"` 실행. 검증 오류(중복 경로, 단위 없음, 음수 등)는 행 번호와 함께 표시됩니다.
3. 원가 recipe(JSON)를 준비합니다(회사 기준값). 사출품 예시: 재료비 + 사출가공비(설비율×cycle/(3600×cavity×양품률)) + 후가공/조립 + 구매품 + 금형상각 + 명시된 기타. 단가/생산량이 없으면 MISSING 으로 남습니다.
4. B 안도 같은 방식으로 만든 뒤 `design compare --candidate A="D:\work\A" --candidate B="D:\work\B" --recipes recipes.json --output design_comparison.json`.
   - revision/기준일/통화/단위가 다르면 비교를 거부합니다(E_REVISION_MISMATCH).
   - 결과의 각 값은 `value/unit/value_type(source|calculated|predicted|missing|assumed)/source/calculation_version` 을 가집니다.
   - 포함 범위: 도장/표피/폼/접착제/미모델링 구매품은 `MassScope` 로 명시할 때만 더합니다.

## 4. 성능 예측 학습 (CSV)
1. TaskSpec YAML 작성 (예: `fixtures/ml/task_regression.yaml` 참고):
   ```yaml
   task_id: clip_insert_force
   task_type: regression            # regression | binary_classification
   data_path: D:/data/clip_tests.csv
   target: insertion_force_n
   numeric_features: [thickness_mm, hole_diameter_mm, angle_deg, modulus_mpa, friction, temperature_c, insert_speed_mm_s]
   categorical_features: [material]
   units: {insertion_force_n: N, thickness_mm: mm}
   id_column: specimen_id
   group_column: design_family      # 같은 설계 계열이 train/test 에 섞이지 않도록
   split_policy: group
   seed: 42
   metric: mae
   direction: min
   acceptance: {metric: mae, threshold: 2.0, direction: min}   # 없으면 NEEDS_ACCEPTANCE_CRITERIA
   resource_budget: {mode: demo}    # demo: 후보 2/10 epochs/300초, pilot: 6/100/3600초
   data_origin: corporate
   purpose: "클립 삽입력 예측"
   excluded_columns: [post_test_note]   # 추론 시점에 알 수 없는 사후 결과
   ```
2. `validate --config task.yaml` → `data_report.json`(인코딩 utf-8/utf-8-sig/cp949 판정, 결측/중복/Inf) 과 `split_manifest.json`(그룹 교집합 0, 실제 행 비율) 확인.
3. `run --config task.yaml` → `workspace\runs\<run_id>\` 에 environment/manifest/plan/events/epoch_metrics/checkpoints/metrics.csv/final_evaluation/report_ko.html/model_card/export.
   - 기준 모델(Dummy/Ridge 또는 Logistic/HistGradientBoosting)과 MLP 후보를 validation 으로 비교해 선택합니다. 기준 모델이 더 좋으면 기준 모델을 선택합니다.
   - test 는 선택 확정 후 **1회** 평가하며, 다시 실행해도 저장된 final_evaluation 을 읽기만 합니다.
   - 회귀: 원단위 MAE/RMSE/R²/P95 절대오차. 분류: AP/ROC-AUC/recall/precision/F1/혼동행렬(임계값은 validation 에서 고정).
4. 중단/재개: `pause --run-id <id>`(epoch 경계) → `resume --run-id <id>`. 강제 종료 후 재개는 마지막 완료 epoch 뒤부터입니다. 코드/데이터/lock 이 바뀌면 자동 재개하지 않습니다.
5. 예측: `predict --model "workspace\runs\<id>\export" --input new.csv --output pred.csv`. 학습 범위 밖 입력은 경고합니다.

## 5. 문서 검색·작성
1. 색인: `docs index --roots "\\fileserver\설계\검토서" "D:\원가표"` (설정 `paths.sources_roots` 에 있는 폴더만). PPTX 는 slide/shape/table/notes/chart, XLSX 는 sheet/table/named range/cell 을 위치(locator)와 hash 로 보존합니다. 숨김 요소는 표시만 하고 기본 제외, .ppt/.xls 는 승인 변환 필요.
2. 검색: `docs search --query "도어트림 원가 2024"` → source_id/파일/locator/발췌/양식참고(form_reference) 또는 사실근거(fact) 구분.
3. `report_payload.json` 작성: PPT 와 XLSX 의 **단일 수치 원본**입니다. 각 항목은 `value/unit/value_type/source_locator|calculation_id|model_run_id` 를 가지며, 없는 값은 MISSING, 상충은 CONFLICT 입니다. 합계/차이는 계산 엔진이 재검증합니다. (demo 가 예시 payload 를 만들어 줍니다.)
4. 템플릿 규칙: PPTX 의 텍스트/표 셀에 `{{key}}` placeholder, XLSX 는 named range 이름 = key. 프로그램은 복사본의 승인된 placeholder/named range/table 만 바꾸고 슬라이드 순서·master·theme·수식을 보존합니다. 지원되지 않는 요소는 삭제하지 않고 `document_manifest.json` 의 compatibility 에 기록합니다.
5. 생성: `docs generate --payload report_payload.json --pptx-template company\templates\review.pptx --xlsx-template company\templates\comparison.xlsx --output-dir out\` → `review.pptx, comparison.xlsx, evidence.json, document_manifest.json, validation_report.json`.
   - `validation_report.json`: 문서에서 다시 읽은 수치 == payload, 구조 보존, 과거 값 잔존 검사, `RECALC_NOT_RUN`/`RENDER_NOT_RUN` 상태.

## 5b. demo 산출물
`demo_manifest.json` 에 단계(12)·검사(16)별 PASS/FAIL, 모든 산출물 sha256, 원본 fixture 불변 여부, outbound 시도 횟수(프로세스 내 socket 검사)가 기록됩니다. 주요 파일: `design_comparison.json`, `performance_predictions.csv`, `retention_predictions.csv`, `evidence.json`, `report_payload.json`, `review.pptx`, `comparison.xlsx`, `document_manifest.json`, `validation_report.json`. 모두 `synthetic: true` 이며 업무 판단용이 아닙니다.

## 6. 산출물 이해
- `value_type=predicted` 값은 모델 예측이며 `model_run_id` 로 추적됩니다. `synthetic: true` 표시가 있는 산출물은 합성 데이터 결과이므로 업무 판단에 쓰지 않습니다.
- HTML 보고서는 외부 리소스 없이 열립니다.

## 7. 사내 LLM 이 있을 때 (선택)
corp-gateway 프로파일이 설정되면 구조화 요청을 등록 도구 계획(JSON)으로 만들 때 LLM 을 사용할 수 있습니다. 숫자·근거는 여전히 계산 엔진/검색 결과만 사용합니다. offline 에서는 자연어 요약/문안 생성 기능이 없습니다.
