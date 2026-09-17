# fixtures/cad — 합성(synthetic) CAD snapshot·재료표·원가 recipe

**synthetic: true** — 이 폴더의 모든 파일은 검증 시험용으로 직접 생성한 합성 데이터다.
실제 사내 도면·CAD 원본·재료표·단가·설비율·금형비·생산량이 아니며, 어떤 실제 차종/부품/공급사도 나타내지 않는다.
실제 차량 물리나 사내 원가 구조를 대표하지 않는다. 사내 반입 후에는 회사 export/재료표/recipe 로 교체한다.

## 파일

| 파일 | 내용 | 인코딩 |
|---|---|---|
| `asm_a_snapshot.csv` | 설계안 A (DOC-ASM-A, revision R1) | UTF-8 (BOM) |
| `asm_b_snapshot.csv` | 설계안 B (DOC-ASM-B, revision R1) — A 대비 PART_CUBE 두께 축소(부피 1,000,000 → 800,000 mm3), PART_BRACKET 재료 변경(AL-6061 → PA66-GF30) | UTF-8 |
| `asm_a_snapshot_cp949.csv` | A 와 동일 + 한글 열/값 (`configuration_id=기본형`, `param:비고`) — CP949 수입 시험용 | CP949 |
| `material_table.csv` | material_id → 밀도 (예시 값, `cad.default_density_policy=use_material_table` 일 때만 사용) | UTF-8 |
| `cost_recipes.json` | 사출/구매품/generic recipe 예시 (`synthetic: true`) | UTF-8 |

## snapshot 구성 (A 기준, B 도 동일 구조)

| occurrence_path | 의미 | 기대 결과 |
|---|---|---|
| `ROOT/ASM_A` | assembly 총량 노드 (`is_assembly=true`, CAD 총량 4.675 kg) | `assembly_node`, 합산 제외 (상위 총량 + 하위 이중합산 금지) |
| `PART_CUBE.1/.2/.3` | 같은 reference `PART_CUBE` 3회 등장, `.3` 은 quantity 2 → instance 4개. 1,000,000 mm3 × 1.0 g/cm3 | 단위 질량 **1.0 kg** (검증값), 합계 4.0 kg |
| `PART_BRACKET.1` | 250 cm3 × 2700 kg/m3 | 0.675 kg |
| `PART_SUPPRESSED.1` | suppressed | 합산 제외 (MISSING 아님) |
| `PART_UNLOADED.1` | unloaded geometry | MISSING → `complete=False` |
| `PART_NODENSITY.1` | 밀도 누락 (material PP-GF30) | 기본 정책 `reject` 에서 MISSING; 재료표 정책이면 0.113 kg (`assumption=True`) |
| `PART_SKIN.1` | surface, 면적 200,000 mm2, 두께 없음 | `surface_no_thickness` MISSING (두께 모르면 질량 생성 안 함) |

A 계산 가능 합계: 4.0 + 0.675 = **4.675 kg** (complete=False, 누락 3건).
B 계산 가능 합계: 4 × 0.8 + 250 cm3 × 1370 kg/m3 = 3.2 + 0.3425 = **3.5425 kg**.

## cost_recipes.json

- `injection_synthetic_pp`: 사출가공비 = 36,000 KRW/h × 30 s / (3600 × 2 cavity × 0.95) = 157.894736… KRW/unit.
  1.0 kg 부품 기준 재료비 2,500 × 1.0 × (1 + runner 0.1) = 2,750, 후가공 50, 조립 30, 구매품 120×2 = 240,
  금형상각 50,000,000 / 100,000 = 500, setup 200,000 / 5,000 = 40, 기타(포장) 15 → 단위 원가 3,782.894736… KRW.
- `injection_regrind`: regrind 허용 → runner 재료 미과금 (재료비 2,500).
- `injection_missing_price`: 재료 단가·생산량 없음 → 해당 line MISSING, unit_cost MISSING.
- `injection_usd_2025`: 통화/기준일 불일치 시험용 (USD, 2025-01-15).
- `purchased_clip_quote` / `purchased_missing_price`: 구매품 견적(quote) 과 단가 누락.
- `generic_stamping`: 사출이 아닌 공정 예시 (`KRW/kg`, `KRW/unit` line).

## 재생성

정적 파일이며 위 표의 값으로 손으로 검산할 수 있다. 값을 바꾸면 `tests/test_engineering_*.py` 의 기대값(4.675 kg, 3.5425 kg, 157.894736… KRW 등)도 함께 바꿔야 한다. 인코딩(UTF-8 BOM / UTF-8 / CP949)은 시험 대상이므로 편집기로 저장할 때 유지해야 한다.
