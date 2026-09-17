# fixtures/documents (synthetic: true)

이 폴더의 모든 문서는 `corp_dl_agent.documents.synthetic.make_synthetic_documents` 가 생성한 **합성(synthetic)** 자료입니다.
실제 회사 검토서·원가표·양식을 이름만 바꾼 것이 아니며, 차종명(X-OLD/X-NEW)·날짜·원가·중량은 모두 가상 값입니다.

| 파일 | 내용 | 용도 |
|---|---|---|
| past_review_2023.pptx | 구 차종 X-OLD, 2023-05-01, 원가 1,234 원, 결론 문구, 표, 발표자 노트, 차트, 숨김 slide 1개 | 색인·근거 검색·오래된 값 회귀 시험 |
| past_cost_table.xlsx | 수식(SUM) + cached value(B9 는 의도적으로 오래된 캐시 120, 실제 123.4), named range(UnitCost/BaseDate/Vehicle), 표 T1, 숨김 시트·숨김 행 | 수식/cached 구분, hidden 제외 시험 |
| template_review.pptx | {{key}} placeholder (제목/본문/표 셀, run 이 나뉜 placeholder) | fill_pptx |
| template_comparison.xlsx | named range 입력 셀, 차이/합계 수식 유지, tbl_cost_lines 표 범위 | fill_xlsx |
| report_payload.example.json | 템플릿과 짝이 되는 예시 payload (X-NEW, 2026-09-01, 원가 1,980 원) | docs generate |
| legacy_note.ppt | 구형 확장자 자리표시(실제 PPT 바이너리 아님) | NEEDS_APPROVED_CONVERSION 상태 시험 |

core properties 의 category/keywords 에 `synthetic` 을 표시했습니다. 이 자료로 만든 산출물에는 `synthetic: true` 가 붙습니다.
