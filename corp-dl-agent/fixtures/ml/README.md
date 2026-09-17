# fixtures/ml — 합성 클립 데이터 (synthetic: true)

이 데이터는 합성(synthetic) 데이터이며 실제 차량 부품·재료 물리를 대표하지 않습니다. 회사 자료를 이름만 바꾼 것이 아닙니다.

생성기: `corp_dl_agent.ml.synthetic.write_fixtures` (clip-synthetic/1.0), 결정적(seed 고정). 재생성:

```
.venv/bin/python -c "from corp_dl_agent.ml.synthetic import write_fixtures; write_fixtures('fixtures/ml')"
```

| 파일 | 내용 |
|---|---|
| `clip_regression.csv` | 600행 / 40 설계 계열(group), target `insertion_force_n` (N), UTF-8 |
| `clip_classification.csv` | 600행 / 40 설계 계열, target `retention_pass` (0/1), UTF-8-SIG(BOM) |
| `task_regression.yaml` | 회귀 TaskSpec (group split, metric mae, acceptance 없음 → NEEDS_ACCEPTANCE_CRITERIA) |
| `task_classification.yaml` | 분류 TaskSpec (time split, metric average_precision, acceptance 없음) |

열: `specimen_id` (문자열 ID, 선행 0 보존), `group_id` (설계 계열; 같은 계열은 한 split 에만),
숫자 feature ['thickness_mm', 'hole_diameter_mm', 'angle_deg', 'elastic_modulus_mpa', 'friction_coef', 'temperature_c', 'insertion_speed_mm_s'], 범주 feature ['material_family', 'data_source'] (`data_source` 는 시험/CAE 출처 구분),
`design_revision` (메타, feature 아님), `measured_at` (ISO 8601, 시간 분할용),
`post_retention_force_n` (시험 후 측정한 사후 결과 → 추론 시점에 알 수 없으므로 `excluded_columns`).

target 은 두께·홀 직경·각도·탄성계수·마찰·온도·삽입속도의 **임의 비선형 함수 + 잡음 + 계열 효과** 로 만든 값이며
물리 법칙이나 실제 시험 결과와 무관합니다. 이 자료로 만든 모델은 `data_origin: synthetic` 표시를 가지며 운영 판단에 자동 사용되지 않습니다.
