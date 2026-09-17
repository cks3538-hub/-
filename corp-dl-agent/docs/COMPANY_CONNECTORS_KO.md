# 사내 연동 안내 (COMPANY_CONNECTORS_KO)

개인 PC 에서는 회사 시스템에 접속할 수 없으므로 아래 연결은 모두 **계약(adapter)·mock 시험까지만** 완료되어 있습니다(MOCK_TESTED / NOT_RUN). 사내에서 실제 값을 채우고 검증해야 CORP_INTEGRATED 가 됩니다.

## 1. CATIA 데이터

| 방식 | 상태 | 사내 확인 항목 |
|---|---|---|
| CSV/JSON snapshot 수입 (`cad import`) | 구현·검증됨 | export 열 이름 ↔ snapshot 필드 매핑(`cad.field_mapping`), 단위(mm3/g/cm3 등) |
| CATIA V5 Windows Automation (`cad extract --adapter catia_v5_com`) | INACTIVE (개인 PC 에 CATIA 없음) | CATIA 설치/버전/라이선스, COM 사용 허가, pywin32 반입 승인, 실제 object model |
| 3DEXPERIENCE | INACTIVE | 제품/버전/API 제공 여부 |

수입 규칙: 동일 reference 의 다중 occurrence 는 quantity 로 합산, suppressed 제외, assembly 총량 노드와 하위 부품 이중합산 금지, unloaded/밀도 누락/두께 없는 surface 는 MISSING. 원본 CAD/PLM 은 수정하지 않습니다.

snapshot 필드: `document_id, revision, configuration_id, occurrence_path, reference_id, quantity, material_id, density_kg_m3, volume_m3, mass_kg, parameter_map, units, geometry_status, source_hash, extracted_at, extractor_version`.

## 2. 원가 기준
`fixtures/cad/cost_recipes.json` 은 합성 예시입니다. 회사 원가 기준(재료 단가·설비율·cavity·양품률·금형상각·생산량·통화·기준일)을 `company\config\cost_recipes.json` 로 만들고 `design compare --recipes` 로 지정합니다. 값이 없으면 MISSING 으로 남으며 프로그램이 임의로 채우지 않습니다.

## 3. 사내 LLM gateway (선택)
1. 담당자에게 확인: base URL, API 형식(OpenAI Chat Completions 호환 / Anthropic Messages 호환 / Gemini 사내 형식), **실제 model ID**(표시 이름 gpt-5.6-terra 등은 ID 가 아닙니다), 인증 방식, CA 번들, timeout, 호출/토큰 한도.
2. `company_config.yaml` 에 `profile: corp-gateway`, `gateway.enabled: true`, `base_url`, `model_id`, `secret_ref: env:CORP_LLM_TOKEN`, `ca_bundle`, `approved_origins` 를 채웁니다. 토큰 값은 환경 변수 또는 보호된 파일에만 둡니다.
3. `launch.cmd integrations doctor` 로 설정 상태를 확인하고, `launch.cmd doctor --json` 에서 secret 이 `***` 로 마스킹되는지 확인합니다.
4. 정책: approved_origins 밖 호출 금지, TLS 검증 유지, redirect 금지, 401/403 재시도 0, 429/5xx 최대 2회, JSON 교정 1회, 기본 12 calls/30,000 tokens/응답 2,000 tokens. usage 미제공 시 보수적 예약 차감.
5. LLM 은 등록된 도구(validate_input, calculate_mass_cost, train_model, predict, search_documents, generate_report)만 계획할 수 있고 코어가 입력/권한/출력을 검증합니다. 숫자/근거는 LLM 이 만들지 않습니다. Gemini 사내 형식은 계약 미확정으로 E_NOT_SUPPORTED 입니다.
6. offline(기본) 에서는 자연어 요청 해석·요약·문안 생성이 제공되지 않습니다. 구조화 입력·로컬 검색·템플릿으로 문서를 만듭니다.

## 4. Atlassian (선택, 기본 읽기/preview 만)
| 제품 | 역할 | 사내 확인 |
|---|---|---|
| Bitbucket | 코드/PR | Cloud/Data Center, base URL, 인증, API 버전 |
| Jira | 요청/실험 이력 | project key, issue type |
| Confluence | 자료/model card | space key |
| Bamboo | 시험/job 제출 | 승인 실행기 여부 |

`integrations.write=false` 가 기본이며 `integrations preview --product jira --payload x.json` 으로 등록 전 미리보기만 제공합니다. 쓰기는 사용자 지시 + 대상/권한 + `integrations.write: true` 설정이 모두 있어야 합니다. 개인 PC 와의 자동 동기화는 없습니다. 코어는 Atlassian 없이 CLI 로 동작합니다.

## 5. Office/렌더러
- PPTX/XLSX 생성은 python-pptx/openpyxl 로 수행합니다. openpyxl 은 수식을 계산하지 않으므로 핵심 수치는 Python 계산값을 기록하고 `RECALC_NOT_RUN` 으로 표시합니다. Excel 에서 열어 저장하면 재계산됩니다.
- 렌더링 검사(잘림/겹침)는 승인된 렌더러(`documents.renderer: libreoffice` + 경로)가 있을 때만 수행하며 없으면 `RENDER_NOT_RUN` 입니다. 한국어 폰트는 `documents.approved_fonts` 에 설치된 승인 폰트를 적습니다.
- Office COM 을 무인 서버 엔진으로 가정하지 않습니다.

## 6. 문서 색인 범위
`paths.sources_roots` 에 지정한 폴더의 PPTX/XLSX 만 색인합니다. 숨김 슬라이드/시트는 표시만 하고 기본적으로 LLM 입력에서 제외합니다. 구형 .ppt/.xls 는 승인 변환이 필요합니다. 매크로/OLE/외부 링크는 실행하지 않습니다. 사용자 권한 밖의 폴더를 색인하지 않으며 파일 삭제 시 `docs index` 재실행으로 반영됩니다.
