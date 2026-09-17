# fixtures (합성 자료, synthetic)

이 폴더의 모든 자료는 개인 개발 환경에서 직접 생성한 **합성(synthetic)** 데이터입니다. 실제 회사 도면·원가·시험·문서를 이름만 바꾼 것이 아니며, 실제 차량 물리나 회사 원가 기준을 대표하지 않습니다.

- `cad/`: 합성 CAD snapshot A/B (알려진 질량 1,000,000 mm³ × 1.0 g/cm³ = 1.0 kg 부품 포함), 합성 원가 recipe, 재료표
- `ml/`: 클립 삽입력(회귀)·유지력 합격(분류) 합성 CSV + TaskSpec 예시 (독립 group, 사후 결과 열 포함 → 제외 대상)
- `documents/`: 가상 과거 검토서 PPTX(구 차종/날짜/원가), 가상 원가표 XLSX(수식+cached), placeholder/named range 템플릿

demo 와 self-test 는 이 자료만 사용합니다. 이 자료로 만든 모델/문서에는 `synthetic: true` 표시가 붙으며 운영 판단에 자동 사용되지 않습니다.
