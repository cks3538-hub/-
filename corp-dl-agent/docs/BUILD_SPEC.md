개인 PC의 Claude Code Fable 전달용 — 설계·문서 업무 자동화 범용 코어 v4
개인 개발 → 합성 데이터 검증 → 사내 반입 → 오프라인 설치·현장 통합

이 문서 전체가 하나의 독립적인 구축 지시문이다. 이전 v1/v2/v3 또는 다른 대화가 없어도 구현할 수 있어야 한다. 아래 전체를 먼저 읽고 현재 개인 PC에서 실제 개발·시험·패키징을 수행하라. 설계 제안이나 코드 조각만 출력하고 끝내지 마라.

[0. 역할·최종 목표·중요한 전제]
너는 개인 PC에서 소프트웨어를 제작하는 Claude Code Fable이다. 목표는 사내 PC로 복사하여 설치할 수 있는 범용 설계 의사결정 지원 프로그램과 검증 가능한 배포물을 만드는 것이다. 프로젝트명 corp-dl-agent, Python package corp_dl_agent를 사용하라. 기존 프로젝트가 있으면 사용자 변경과 작동 기능을 보존하고 필요한 패치로 v4를 통합하라.

프로그램은 CATIA에서 추출된 설계 데이터, 중량·원가 계산, 시험/해석 기반 성능 예측, 기존 PPT/Excel 근거 검색, 신규 문서 작성, 선택적 사내 AI/Atlassian 연결을 수행한다. 소개 명칭은 설계·문서 업무 자동화 프로그램이며 실제 실행 소프트웨어로 구현하라. skill이나 프롬프트 모음만 만들어 완료하지 마라. 너의 Fable은 개발용 모델이다. 완성 프로그램이 Fable·개인 구독·개인 API 키·개인 로그인에 의존하게 만들지 마라.

사용자의 개인 PC에서는 Fable 사용이 가능하지만 사내에서는 사진에 있는 허용 모델만 사용할 수 있다. 사용자 설명상 표시 이름은 gpt-5.6-terra, gpt-5.6-luna, claude sonnet 5, claude haiku 4.5, gemini 3.7 flash, gemini 3.5 flash lite다. 이 표시를 API ID나 프로토콜로 추정하지 마라. 실제 사내 model ID·주소·권한·성능은 현장에서 확인한다. 사내에 Fable 설치를 요구하지 마라.

최종 목표는 단순히 개인 개발 환경에서 실행되는 소스가 아니다. 소스+앱 wheel+대상별 dependency wheelhouse+lock+manifest+오프라인 설치기+운영서+실제 시험 증거를 생성하라. 실제 대상 환경이 미확정이면 가정과 미검증 상태를 분명히 하면서 가능한 코어와 참조 패키지까지 완료하라.

[1. 개발 환경·데이터·행동 범위]
현재 폴더와 적용되는 CLAUDE.md/AGENTS.md, 기존 Git 상태를 확인하라. 관련 없는 개인 폴더·인증 파일·회사 자료를 탐색하지 마라. 프로젝트 지침은 CLAUDE.md와 필요한 docs/rules에 간결하게 남겨라. CLAUDE.md는 약200줄 이내의 핵심 규칙·실행 명령으로 유지하고 전체 명세는 docs/BUILD_SPEC.md에 보존하라. AGENTS.md가 있으면 내용을 명시적으로 읽고 필요한 공유 규칙을 연결하되 자동 로딩된다고 가정하지 마라. .claudecode를 공식 지침 파일로 가정하지 마라. 지침 파일만으로 권한·네트워크 차단이 강제된다고 주장하지 마라. 사용자의 Fable 모델/로그인/전역 설정을 변경하지 마라.

개인 PC에서는 공개 가능한 코드와 직접 생성한 합성 CAD snapshot·원가표·시험 CSV·PPTX·XLSX만 사용하라. 사내 도면·CAD 원본·견적·PPT/Excel·시험 결과·인증키·내부 URL을 개인 PC로 가져오라고 요구하지 마라. 이름만 바꾼 사내 자료도 합성 데이터라고 간주하지 마라.

개인 개발에는 지정한 공식 배포처의 패키지를 사용할 수 있다. 다운로드 출처·버전·라이선스·해시를 남기고 프로젝트 가상환경에서 작업하라. 사내 runtime/installer는 인터넷 없이 동작해야 한다. 사내 GitHub 금지와 개인 개발의 패키지 확보를 혼동하지 마라. 사내 패키지에 public API key·public package fallback·자동 GitHub clone·자동 모델 다운로드를 포함하지 마라.

관리자 권한·드라이버 설치·전역 패키지 교체·보안 설정 해제·광범위한 권한 변경을 자동 실행하지 마라. 외부 공개 repo 생성/push·계정/과금 등록·사내 원격 쓰기·타인 메시지 전송은 이번 구축 지시에 포함되지 않는다.

모르는 회사 정보는 자리표시자로 두고 해당 연결만 NOT_RUN/BLOCKED로 표시하라. 회사 정보가 없다는 이유로 개인 환경에서 가능한 구현·합성 시험·배포물 준비를 중단하지 마라. 단, 필수 패키지 자체가 없으면 완전한 실행 패키지라고 거짓 보고하지 마라.

[2. 전체 구현 순서]
M0 기존 파일·호스트·타깃 조건 진단, 작업 계획과 BUILD_STATUS 작성.
M1 설정·단위·snapshot 수입·중량/원가 계산·설계안 비교.
M2 CSV 기준 모델과 CPU PyTorch, 상태·예산·checkpoint·재개.
M3 합성 PPT/Excel 색인·근거 검색·템플릿 생성·검증.
M4 LLM/CATIA/Atlassian의 분리된 adapter와 mock 계약 시험.
M5 wheel 빌드·타깃 lock/wheelhouse·manifest·installer·launcher.
M6 새 환경/새 경로에 반입 패키지만 설치하여 합성 self-test.
M7 회사 설정 보존 upgrade/rollback 시험, 최종 ZIP·hash·외부 acceptance·설명서.

각 단계의 실제 결과를 기록하고 다음 단계로 진행하라. build_state.json, BUILD_STATUS.md, NEXT_ACTION.md에 단계·변경 파일·시험 명령/결과·미완료 조건·재개 명령을 기록하라. 세션 중단/컨텍스트 축약 후에는 이 기록과 실제 파일을 대조하고 완료 작업을 반복 생성하지 마라. 한 번의 지시가 세션 시간·권한 제한을 없앤다고 가정하지 마라. 가능한 작업은 계속하되 실제 제한에 도달하면 정확한 재개 지점을 남겨라. 핵심 기능이 없는 거대한 scaffold를 완료로 선언하지 마라. 모든 역할을 별도 LLM/에이전트로 만들 필요는 없으며 서브에이전트 사용은 필수가 아니다.

[3. 기술과 package 경계]
Python, NumPy, pandas, scikit-learn, PyTorch, Pydantic strict validation, PyYAML safe loader, HTTPX, SQLite, python-pptx, openpyxl을 기본 후보로 사용한다. pytest/Ruff/Mypy는 개발 검증에 사용한다. 호환 버전은 실제로 해결·검증하고 임의로 pin/hash를 만들어내지 마라.

core, documents, ml-cpu, cad-windows, ml-gpu 의존성 묶음을 분리하라. 기본 검증된 배포 프로파일에는 core+documents+ml-cpu가 포함되어야 한다. ml-gpu/cad-windows live 연결은 별도 선택이다. torch/COM이 없는 경우에도 표준 라이브러리 preflight/verifier는 실행되어야 하며 불필요한 최상위 import로 진단기를 깨뜨리지 마라.

TensorFlow/LangGraph/Celery/Redis/Kubernetes/외부 벡터DB/외부 SaaS/Docker/WSL을 필수로 추가하지 마라. 상태 머신·직렬 실험 관리·로컬 색인으로 우선 완성하라. 사전학습 가중치를 자동 다운로드할 필요 없는 모델을 선택하라.

다음 모듈 책임을 구현하라:
config/schemas/cli/doctor, state/budget/coordinator,
engineering(cad_snapshot,units,mass,cost,compare),
ml(data,split,preprocessing,baselines,mlp,trainer,checkpoint,evaluation,export),
documents(index,search,evidence,payload,templates,validation),
adapters(cad,llm,atlassian), security, reporting,
packaging, scripts, tests, fixtures, docs.

__main__.py와 pyproject의 올바른 package metadata/entry point를 포함하라. installer에서 source checkout이나 editable install에 의존하지 않도록 앱 wheel을 개인 PC에서 미리 빌드하라.

[4. 타깃 프로파일과 preflight]
먼저 개인 호스트의 OS/architecture/Python·패키지·GPU를 필요한 범위에서 진단하라. 개인 PC의 사양을 사내 PC 사양으로 간주하지 마라. 사내 타깃이 주어지지 않았으면 Windows x64 / CPython 3.12 / CPU를 임시 참조 프로파일로 작성하고 target_confirmed=false로 남겨라. 모든 의존성이 그 Python을 지원하는지 실제 확인하라. 임시값이 안 맞으면 가능한 호환 후보를 이유와 함께 기록하되 사내 확정값으로 표현하지 마라.

target_profile.json에는 OS/version, architecture, Python implementation/version/ABI,
features, cpu/gpu, target_confirmed, verified_environment를 기록하라.
GPU는 선택한 torch 빌드·GPU·드라이버 조합의 확인이 필요하다. CPU와 GPU torch를 동일 venv에 임의 덮어쓰지 마라.

표준 라이브러리만 사용하는 preflight.py와 Python 미설치 상태를 확인할 preflight.ps1을 제공하라. 회사에서 승인한 Python 경로를 명시할 수 있게 하라. 자동 Python/Store 다운로드·관리자 설치를 실행하지 마라. ensurepip/pip/venv가 없는 회사 Python도 구분해 BLOCKED_PREREQUISITE로 안내하라.

로컬 상세 진단과 target_profile.min.json을 분리하라. 최소 요약에는 OS/arch/Python ABI/기능 상태 같은 허용 필드만 담고 이름·호스트·사용자 경로·내부 URL·파일 목록·인증값을 제외하라. 자동 외부 업로드 기능을 넣지 마라.

다른 OS/Python용 wheel 확보 시 pip download의 platform/python-version/implementation/abi/only-binary 설정을 명시하고 환경 marker와 전이 의존성까지 검사하라. host pip freeze를 다른 target lock으로 쓰지 마라. cross-download 성공은 TARGET_BUNDLE_PREPARED이며 타깃 실행 시험을 뜻하지 않는다. 동일 target 환경이 없으면 TARGET_OFFLINE_TESTED=NOT_RUN으로 기록하라.

[5. 개인/사내 프로파일과 네트워크]
personal-dev: 합성 데이터, 지정 공식 배포처의 개발 패키지 다운로드 가능.
transfer-test: 완성 ZIP만으로 새 환경 설치·실행, 인터넷/cache/source checkout 비의존.
corp-offline: 로컬 파일 처리와 규칙 기반 계획기, API 호출 0회.
corp-gateway: 사내 endpoint/인증/model ID/CA/allowlist가 명시된 경우만 호출.

offline에서는 구조화된 입력·로컬 검색·템플릿으로 문서를 작성하라. 자유로운 자연어 요청 해석·요약·문안 생성은 승인된 LLM을 연결해 실제 구현·검증한 경우에 제공하라. 지원되지 않는 자연어 기능을 offline에서 완성됐다고 표시하지 마라.

위 프로파일은 새 앱의 자체 설정이며 Claude Code의 공식 설정 키가 아니다. personal-dev의 주소나 credentials를 corp 프로파일에 자동 복사하지 마라. 개인 Fable 실행 환경의 API key/프록시/SDK 기본값을 worker에 상속하지 마라. 비밀값은 지정된 참조로만 주입하라.

요청하지 않은 원격 telemetry·analytics·버전 확인·crash upload·모델/폰트/CDN 다운로드를 넣지 마라. 사내 runtime에 Git/pip/npm이 반드시 설치되어 있어야 하는 구조로 만들지 마라. Git이 없는 경우에도 검증된 앱 wheel로 실행되어야 한다.

[6. 설치 파일·업무 데이터·설정 분리]
install_root 아래 releases/<release_id>/<profile_id>에 불변 앱과 해당 venv를 설치한다. active.json으로 활성 release/profile을 지정한다. 별도 company/config, company/templates, company/extensions와 workspace/data,runs,models,outputs,state,indexes 및 backups 경로를 둬라. install_root/data_root는 사용자가 선택하고 한글·공백 경로를 지원하라.

설정 우선순위는 package defaults < company config < 명시적 CLI다. 회사 mapping·템플릿·업무 데이터·secret 참조는 release ZIP의 실제값이 아니라 사내에서 채운다. 회사 extension은 명시적 경로/버전/호환성을 확인해 로드하고 임의 폴더 plugin discovery로 외부 코드를 실행하지 마라.

원본 입력은 읽기 전용으로 취급하라. SQLite는 로컬 디스크에 두고 단일 writer와 transaction을 사용하라. 경로 resolve 후 승인 input/output root 이탈과 symlink/junction·archive traversal을 검사하라. 관리 폴더 외 임의 파일 삭제를 금지하라. 비밀값을 resolved config·로그·exception·테스트 snapshot에 출력하지 마라.

[7. CATIA snapshot 수입·중량·원가]
CAD snapshot 스키마:
document_id, revision, configuration_id, occurrence_path, reference_id, quantity,
material_id, density_kg_m3, volume_m3, mass_kg, parameter_map, units,
geometry_status, source_hash, extracted_at, extractor_version.

cad import는 CSV/JSON으로 실제 작동하게 하라. 개인 PC에서는 알려진 질량과 여러 occurrence를 가진 합성 부품/조립체 fixture를 생성하라. 같은 reference가 여러 번 등장하는 수량, suppression, 상위 총량+하위 중복 합산, unloaded geometry, 밀도 누락/기본값을 검사하라. surface의 두께/층별 밀도를 모르면서 정상 질량을 만들지 마라.

중량 직접 계산: kg = m³ × kg/m³. mm³와 g/cm³이면 kg 계수10^-6.
합성 fixture 1,000,000 mm³ × 1.0 g/cm³ = 1.0 kg으로 검증하라.
도장/표피/폼/접착제/미모델링 구매품의 포함 범위를 명시하라.

원가는 공정별 recipe로 구성하고 모든 CATIA 부품이 사출품이라고 가정하지 마라. 사출품 예시 항목: 재료비, 사출가공비, 후가공/조립, 구매품, 금형상각, 명시된 기타.
사출가공비 예시: 시간당 설비율 × cycle초 / (3600 × cavity × 양품률).
통화·기준일·수량·생산량·runner/regrind·수율·setup의 중복 반영을 검사하라. 견적가와 제조 추정원가를 구분하고 단가/생산량이 없으면 MISSING 또는 명시적 scenario 가정으로 표시하라. LLM이 숫자를 만들어 채우지 마라.

각 계산값은 값/단위/source/calculation_version/가정 여부를 보존하라. design compare는 동일 조건·기준일·revision 정합을 확인하여 A/B/C 후보의 mass,cost,performance,constraint,evidence,unknown을 출력하라. 실제 CAD 원본이나 PLM을 자동 수정하지 마라.

[8. 데이터 계약·기준 모델·PyTorch]
TaskSpec: task_id, task_type(regression|binary_classification), data_path,
target,numeric_features,categorical_features,units,id_column,group_column,
split_policy,seed,metric,direction,acceptance,resource_budget.
strict schema로 추가 필드·잘못된 dtype·범위·단위·명령을 거부하라.

CSV의 UTF-8/UTF-8-SIG/CP949를 검사하고 채택 결과를 기록하라. ID는 문자열로 보존하라. 타깃 누락·중복 ID·NaN/Inf·feature/target 중복·그룹 누락은 원인과 함께 실패하라. 오류 행을 조용히 삭제하지 마라. 시험과 CAE, 설계 revision과 경계조건을 구분하라.

합성 클립 데이터는 두께·홀 직경·각도·탄성계수·마찰·온도·삽입 속도와 삽입력 target, 독립 group을 가진 비선형 생성기로 만들되 실제 차량 물리를 대표한다고 주장하지 마라. ID와 추론 시점에 알 수 없는 사후 결과를 feature에서 제외하라.

고정 group split과 source/data/split/code/lock hash를 저장하라. 약70/15/15는 초기 그룹 비율일 뿐 실제 행 비율과 다를 수 있다. 그룹 교집합0을 시험하고 유효한 split이 불가능하면 행 무작위 분할로 후퇴하지 마라. 여러 축을 동시에 격리하려면 연결요소/명시적 holdout을 사용하라. 시간 분할이면 미래 정보 누수를 막아라.

전처리는 train에서만 fit하고 fold마다 재학습하라. unknown category와 고유 ID 과다 인코딩을 처리하라. 모델별 sparse/dense 호환성과 메모리를 확인하라. validation으로 후보/분류 임계값을 정하고 test는 선택 고정 후 한번의 평가 계획으로 사용하라. 재실행 시 이미 저장한 최종 평가를 읽고 test 기반 자동 재튜닝하지 마라.

회귀 기준: DummyRegressor/Ridge/HistGradientBoostingRegressor.
분류 기준: DummyClassifier/LogisticRegression/HistGradientBoostingClassifier.
딥러닝: 작은 MLP, 회귀 출력/target shape 일치, 분류 logits+BCEWithLogitsLoss.
기본 후보 hidden_dims [64,32] 또는 [128,64], dropout0~0.2,
learning_rate1e-4~3e-3, weight_decay0~1e-3. 허용 범위와 모델은 registry로 고정한다.

회귀는 원단위 MAE/RMSE/R²/P95 절대오차, 분류는 정의가 명확한 average precision/ROC-AUC/recall/F1/confusion matrix를 남겨라. 기준 모델이 우수하면 그 모델을 선택할 수 있어야 한다. 업무 허용오차는 미입력 시 NEEDS_ACCEPTANCE_CRITERIA이며 임의로 만들어내지 마라. model artifact에는 data_origin=synthetic|corporate와 목적을 기록하여 사내 실행에서 synthetic 모델을 자동 채택하지 않게 하라.

CPU FP32 기본. CUDA/torch.amp/FP16 GradScaler는 실제 지원 확인 후 적용하라. train/eval/inference_mode, zero_grad(set_to_none=True), finite loss/gradient, shape/dtype를 검사하라. accumulation은 마지막 불완전 묶음까지 실제 표본 수로 정규화하고 AMP clipping은 unscale 뒤 수행하라.

scalar 로그는 제한 주기로 item(), 예측은 detach().cpu() 후 스트리밍/크기 제한을 사용하라. detach만으로 GPU 메모리가 해제된다고 가정하지 마라. CUDA allocated/reserved/peak를 구분하고 allocator 옵션은 버전/backend·단편화 근거가 있을 때만 프로세스 시작 전에 적용하라. 매 배치 empty_cache로 문제를 덮지 마라.

실제 커스텀 autograd만 float64 gradcheck, 2차 미분을 약속하면 gradgradcheck를 시험하라. 이를 정확도나 전역적 동치성 증명으로 표현하지 마라. 일반 MLP에 불필요한 커스텀 autograd를 만들지 마라.

[9. 상태·예산·중단과 재개]
상태 CREATED/VALIDATING/PLANNED/RUNNING/EVALUATING/COMPLETED와
PAUSED/CANCELLED/FAILED/BLOCKED_CONFIG/BLOCKED_DEPENDENCY/BUDGET_EXCEEDED를 기록하라.
trial_id와 attempt_id, lease·heartbeat·자신의 worker 식별정보를 분리하라. 동일 fingerprint(task/data/split/code/lock/model/seed)의 완료 실험은 산출물 hash까지 확인한 뒤 건너뛰어라.

demo는 회귀/이진분류 각각 별도 run으로 최대2개 MLP후보, 후보당10epochs, run전체300초다. pilot는6개/100epochs/patience10/3600초다. 기준 모델·평가·재시도도 시간에 포함하고 새로운 작업 시작과 현재 worker 종료를 제한하라. 이는 완료시간 보증이 아니다.

epoch 완료 시 model/optimizer/scheduler/scaler/RNG/DataLoader generator,
next_epoch/best_validation/config hash를 temp→flush→검증→원자적 교체로 저장하라.
latest와 best를 분리하라. pause는 epoch 경계, 강제 종료 후 resume은 마지막 완료 epoch 뒤다. 진행 중 epoch 일부는 다시 계산될 수 있다. 코드/data/lock이 달라지면 자동 재개하지 마라. scheduler/로그/worker 중복을 방지하라.

Python/NumPy/PyTorch seed와 환경을 기록하라. Windows는 기본 num_workers=0과 main guard를 적용하라. 다른 플랫폼/버전에서 비트 단위 동일성을 보장하지 마라. OOM은 자신의 실패 worker 정리 후 microbatch/accumulation을 바꿔 새 attempt로 최대2회, 변경을 기록하라. 동일 수학 실험이라고 주장하지 마라. NaN/Inf는 진단 후 허용된 정밀도/LR 교정 최대1회다.

export는 모델·전처리·입력 schema·단위·target 역변환·분류 threshold·학습 범위·checksum을 포함하라. 새로운 프로세스에서 재로드 예측을 비교하라. sklearn과 MLP 모두 predict가 되어야 한다. 신뢰된 자기 생성 artifact만 로드하고 외부 임의 pickle/joblib 입력을 허용하지 마라. tensor 가중치 로드는 지원 버전에서 weights_only=True를 쓰되 이것만으로 완전 안전이라고 주장하지 마라.

[10. PPT·Excel 검색과 신규 작성]
회사 파일 없이 가상 과거 PPTX/XLSX, 가상 새 설계 payload, 명명된 placeholder/named range 템플릿을 직접 만들고 synthetic 표시를 하라. 오래된 차종명/날짜/원가/결론과 다른 새 값이 들어가는 회귀 시험을 구성하라.

허용된 sources root만 색인하라. PPTX는 slide/shape/table/notes/chart 원데이터,
XLSX는 sheet/table/named range/cell range를 추출하고 source_id/hash/revision/locator를 보존하라. 수식과 cached value, 단위·기준일·approval_state를 구분하라. 매크로·외부 링크·OLE 실행 콘텐츠를 실행하지 마라. 구형 .ppt/.xls는 승인 변환 필요 상태다. OCR/embedding은 승인 엔진이 있을 때만 선택 기능으로 둬라.

과거 양식 참고와 현재 사실 근거를 분리하라. 숨겨진 내용은 표시하고 명시적으로 허용되지 않으면 LLM 입력/산출물에서 제외하라. 첫 검색은 로컬 키워드/metadata로 구현하라. 사용자 권한 범위를 넘는 색인·검색·캐시를 만들지 말고 삭제/권한 철회를 반영하라.

report_payload.json 하나를 PPT와 XLSX의 수치 원본으로 사용하라.
각 항목은 value/unit/value_type(source|calculated|predicted|missing),
source_locator 또는 calculation_id/model_run_id를 가진다.
새 자료에 없는 값은 MISSING, 상충은 CONFLICT로 표시하고 LLM이 숫자/근거를 꾸며내지 마라. 원가·중량·합계·차이는 계산 엔진으로 검증하라.

템플릿 복사본의 승인된 shape/placeholder/range/table만 수정하라. slide/sheet 순서, master/theme, 구조·표·차트·수식을 보존하고 unsupported 요소를 조용히 삭제하지 마라. 라이브러리 미지원 복잡 요소는 호환성 보고서와 대체 경로를 남겨라.

openpyxl은 수식을 계산하지 않으며 data_only 값은 기존 cache일 수 있다. 핵심값을 검증한 Python 계산 결과로 작성하는 경로와 실제 Excel/호환 엔진 재계산을 구분하라. 수식 재계산/렌더링을 실제 수행하지 않았으면 RECALC_NOT_RUN/RENDER_NOT_RUN이다. Office COM을 비대화형 서비스의 기본 안정적 서버 엔진으로 가정하지 마라.

가능한 렌더러로 slide/인쇄영역을 확인해 잘림·겹침·단위·차트 오류를 검사하라. 사내 실행 때 폰트/CDN/렌더러를 인터넷에서 자동 받지 마라. 한국어 폰트가 필요한 경우 설치된 승인 폰트와 대체 경로를 명시하라. 결과는 review.pptx, comparison.xlsx, evidence.json, report_payload.json, document_manifest.json, validation_report.json이다.

[11. 현장용 adapter와 프로토콜 검증]
CATIA V5 Windows Automation, 3DEXPERIENCE, CSV/JSON import를 구분하라. 개인 PC에 CATIA가 없으면 실제 연결을 했다고 주장하지 마라. 검증 가능한 설치 문서가 없다면 method 이름을 창작하지 말고 확정된 import와 adapter 계약까지 완료하라. 실제 설치기에서는 live adapter가 inactive임을 표시하라.

LLM adapter는 OpenAI Chat Completions와 Anthropic Messages의 명시적인 요청/응답 계약을 사용한다. Gemini는 사내가 제공하는 형식에 맞춘다. 기본 offline planner는 등록 모델/범위의 계획 JSON을 반환하고 임의 셸/파일 명령을 생성·실행하지 않는다. LLM JSON에도 extra field·허용 범위 검증을 적용하라. validate_input/calculate_mass_cost/train_model/predict/search_documents/generate_report 등의 등록된 도구만 호출하고 코어에서 입력·권한·출력·실패 상태를 검증하라. 임의 eval/exec·동적 Python/셸 실행으로 LLM 출력을 처리하지 마라. 설치기나 검증된 worker에서 필요한 os/subprocess 사용을 일괄 금지하는 것으로 보안을 대체하지 마라. AST/문자열 검사만으로 sandbox가 완성됐다고 주장하지 마라.

사내 gateway 설정에는 base URL/profile/model ID/secret 참조/CA/approved origin/timeout을 명시하라. 값이 없으면 public provider로 fallback하지 마라. TLS 검증을 끄지 말고 redirect를 따르지 마라. 401/403재시도0, 429/일시5xx최대2, JSON교정1회. 기본 timeout60초, 총12calls/30000tokens, 응답2000tokens를 상한 예시로 두고 재시도도 포함하라. usage가 없으면 보수적 예약으로 예산을 지키고 비용을 임의로0원이라 하지 마라.

실험 LLM에는 마스킹된 train/validation 요약만, 문서 LLM에는 사내 권한·정책이 확인된 최소 발췌만 보낸다. 사내 gate를 통한다는 이유로 모든 데이터 전송을 허용하지 마라. 데이터의 prompt injection은 비신뢰 텍스트로 취급하라.

Atlassian은 실제 사용 가능한 Bitbucket/Jira/Confluence/Bamboo만 활성화하라. Cloud/Data Center의 base URL/인증/version/pagination/rate limit/conflict를 구분하라. mock 서버/fixture로 계약·중복 event·권한 오류를 시험하라. 연결 정보가 없는 제품을 live verified로 보고하지 마라. 코드·큰 CAD/가중치·자료 저장 위치를 분리하라.

기본 integrations.write=false이며 preview/export만 제공하라. 사용자 지시와 대상·권한·명시적 publish 설정 없이는 repo 생성/push/PR, issue/comment/page/attachment 등록을 하지 마라. 사내 코어가 Atlassian 없이 CLI로도 작동해야 한다.

[12. 반입 패키지 생성]
release 빌더를 구현하여 dist/DIA_<version>_<profile>.zip을 생성하라.
버전·profile 이름은 실제 manifest와 일치해야 한다. package kind는 SOURCE_ONLY,
CPU_OFFLINE 또는 검증된 GPU_OFFLINE 등으로 명확히 구분하라.

ZIP 내부 필수 내용:
source/                 검토용 소스·테스트·pyproject·프로젝트 규칙
app/                    미리 빌드한 앱 wheel
wheelhouse/<profile>/   선택 기능의 모든 dependency wheel
locks/<profile>.txt     앱 포함 version/hash 고정 설치 명세
scripts/                preflight,verify,install,launch,upgrade,rollback
config-examples/        실제 secret 없는 회사 설정·mapping 예시
fixtures/               합성 snapshot/CSV/PPTX/XLSX/payload
schemas/                config/task/snapshot/payload/release JSON schemas
docs/                   개인 build·사내 install·연동·운영·복구 설명서
release-manifest.json   릴리스·대상·기능·schema 버전·검증 상태·파일 목록
checksums.sha256        payload 파일 hash 목록
dependency-inventory.json   package/version/wheel/source/license/hash
test-evidence/          실제 환경·명령·exit code·검증 결과

ZIP 바깥에 ZIP 자체의 SHA-256 파일과 <zip_basename>.acceptance.json을 생성하라. 포장 전 코어 시험 증거는 내부 test-evidence에 담고, 최종 ZIP을 확정한 뒤 그 ZIP만 사용해 새 환경에서 설치·실행을 검증하라. 외부 acceptance에는 최종 ZIP hash·검증 환경·명령/exit code·PASS/FAIL/NOT_RUN과 이유를 기록하라. 시험 결과를 넣기 위해 검증 완료된 ZIP을 다시 포장하지 마라. 소스/의존성을 수정했다면 새 ZIP을 만들고 영향받는 시험을 다시 수행하라.

반입용 test evidence는 <PROJECT_ROOT>/<TEST_ROOT>와 상대 경로로 정규화하라. 개인 계정명/절대 경로가 든 원시 로그는 개인 로컬에 보관하고, 전달본에도 실제 실패·미실행 상태와 재현 가능한 명령·환경·hash를 보존하라. 사실을 바꾸거나 실패를 지우지 마라.

ZIP 자기 hash를 내부에 넣는 순환 정의를 만들지 마라. checksums 파일과 manifest 사이도 순환 hash가 없도록 정의하고 검사 범위를 문서화하라. hash는 무결성이지 배포자 진위 확인이 아니다. 승인된 전달 경로 또는 별도로 신뢰된 검증 키가 있는 경우에만 출처 검증을 주장하라. 임의 self-signed key를 같이 담고 신뢰가 확보됐다고 쓰지 마라.

포장은 allowlist 방식으로 하라. 개인 .venv, .env, Claude 로그인/대화/전역 설정,
API 키, 개인 경로, .git 이력, caches, 실제 회사 파일, 실행 결과·학습 모델을 통째로 포함하지 마라. 합성 demo 모델을 넣는 경우 origin=synthetic을 표시하고 운영 자동선택에서 제외하라.

목록에는 재배포한 패키지의 실제 라이선스/고지와 소스 출처를 남겨라. 회사 승인 여부는 별도 상태다. 회사가 라이브러리 반입을 허용했다고 가정하지 마라. source-only ZIP만 있는데 offline ready라고 보고하지 마라. ZIP에는 포함하지 않은 Python 런타임·CATIA·Office·GPU드라이버·렌더러의 선행 조건을 명시하라.

[13. 오프라인 설치기·실행기]
회사 승인 Python을 사용하는 경로가 기본이다. Python이 없다면 승인된 설치/런타임이 선행되어야 한다. 개인 PC의 venv를 복사하지 마라. Python 런타임 동봉/EXE는 재배포·사내 실행 허용 및 실제 target 시험이 가능한 경우만 optional이다. target별 빌드 없이 만능 EXE를 주장하지 마라.

표준 라이브러리 verifier/preflight를 먼저 실행한다. 검증에 third-party가 필요해 인터넷 pip 설치부터 요구하는 구조로 만들지 마라. manifest/schema/file count/size/hash, 경로 탈출, 링크, target OS/Python/ABI, 디스크 공간, 쓰기 권한을 점검하라. 검사 가능한 릴리스 ID로 설치 폴더를 제한하라.

새 releases/<id>/<profile> 최종 위치에서 venv를 생성하라. 설치 후 venv를 staging에서 다른 폴더로 옮기지 마라. 활성화 포인터만 바꿔라. 사용자 업무 폴더를 삭제하거나 기존 .venv를 --clear로 덮어쓰지 마라.

실제 설치는 명시적 로컬 wheelhouse와 완전한 hash lock을 사용하라:
--no-index --find-links <local> --require-hashes --only-binary=:all:
--no-cache-dir --disable-pip-version-check.
사용 버전 pip의 사용자/환경 설정을 조사하여 의도치 않은 PIP_INDEX_URL,
PIP_EXTRA_INDEX_URL, PIP_FIND_LINKS, proxy/config 영향을 차단하고 시험하라.
no-index만으로 direct URL까지 차단됐다고 가정하지 마라. requirements의
http(s) URL/VCS/editable/source directory/index option/include escape를 거부하고
모든 앱·전이 의존성의 로컬 wheel 및 hash를 사전 검증하라.

설치 중 source build/build isolation/공개 index fallback을 하지 마라. 앱 wheel도 hash 대상이어야 한다. pip/venv bootstrap 조건이 안 맞으면 정확한 BLOCKED_PREREQUISITE를 반환하라. pip check, import, self-test, 합성 smoke가 통과한 뒤만 active.json을 원자적으로 변경하라. 실패하면 기존 활성 버전을 유지하라.

00_Preflight.cmd, 01_Verify.cmd, 02_Install.cmd, 03_Start.cmd,
04_SelfTest.cmd, 05_Update.cmd, 06_Rollback.cmd와 대응 Python 명령을 제공하라.
파일을 다른 작업 디렉터리에서 실행해도 script 위치 기준으로 동작하고 모든 경로를 안전하게 인자로 전달하라. 실제 대상 Windows에서 검증하지 못한 wrapper는 NOT_RUN으로 표시하라. 선택한 Python 경로를 명시할 수 있게 하고 시스템 Python 검색 실패 시 Store/웹 설치를 자동 시작하지 마라.

PowerShell execution policy/인증서/GPO/백신을 해제하지 마라. Activate.ps1 없이 venv Python을 직접 호출하는 경로를 제공하라. 실행 차단이 있으면 회사의 승인된 실행 방법이 필요하다고 안내하라.

[14. 업데이트·롤백·사내 변경 보존]
release는 불변, 회사 설정/템플릿/extension/데이터/모델/이력은 별도다. 새 버전은 새 경로에 설치하라. 동일 버전은 manifest가 같을 때만 재사용하며 다른 내용으로 조용히 덮어쓰지 마라. active.json 변경은 원자적으로 하라.

실행 중 학습/문서 job이 있으면 신규 시작을 잠그고 안전한 완료 또는 pause 지점에서 전환하라. 구버전 worker를 실행한 상태로 사용하는 파일을 교체하지 마라. 버전별 데이터 schema 호환 범위를 명시하고 자동 downgrade로 무조건 해결한다고 가정하지 마라.

회사 설정/템플릿/경로/secret을 기본 예제로 덮어쓰지 마라. migration plan과 backup을 만든다. SQLite WAL 환경에서는 일관된 backup API 또는 닫힌 DB snapshot을 사용하라. DB 파일 하나만 복사하여 완전 백업이라고 주장하지 마라. migration은 복사본에 적용하고 검증 성공 후 전환하라.

코드 rollback과 DB rollback을 분리하라. 구버전이 새 DB를 읽을 수 없으면 active 포인터만 되돌리지 마라. 업데이트 후 추가된 업무 이력을 잃을 수 있는 복구는 영향과 대안을 보고하고 임의 삭제/덮어쓰기를 하지 마라. 구버전과 snapshot은 보존하라. 인덱스는 원본 권한을 유지해 재구축 가능하게 하라.

개인 release와 사내 repository를 자동 양방향 동기화하지 마라. 회사 변경은 내부 Bitbucket/extension으로 관리하고, 개인에게 전달할 필요가 있으면 사내에서 승인된 합성 최소 재현 예제를 만든다. export-diagnostics는 허용 필드 로컬 preview만 제공하고 자동 반출하지 마라.

[15. CLI·산출물·사용자 메시지]
python -m corp_dl_agent 와 고정 launcher로 다음 기능을 구현하라:
doctor, self-test, demo --offline --device cpu,
validate --config, run --config, status/pause/resume/cancel --run-id,
predict --model --input --output, report --run-id,
cad import, cad extract(조건 충족시), design compare,
docs index/search/generate, integrations doctor/preview,
version, config validate, package verify, upgrade, rollback.
preflight/install은 앱 미설치 상태에서도 scripts의 표준 라이브러리 명령으로 작동해야 한다.

각 명령의 --help·한국어 오류 코드/해결 방법을 제공하라. 처음 시작 메뉴에는 demo/설정검사/설계비교/학습/문서작성/상태확인의 실제 구현된 경로만 노출하라. 복잡한 웹 서비스나 외부 로그인 없이 시작할 수 있게 하라. 일반 사용 중 Fable을 실행할 필요가 없어야 한다.

run 산출물: environment/manifest/data_report/split_manifest/plan JSON,
상태 DB, events와epoch metrics, checkpoint, metrics.csv, final_evaluation,
report_ko.html/md, model_card, model export.
업무 산출물: cad_snapshot, cost_breakdown, performance_predictions,
design_comparison, evidence, report_payload, review.pptx, comparison.xlsx,
document_manifest, validation_report.
HTML은 외부 CDN 없이 열려야 하고 escape를 적용하라. 숫자는 실제 계산/metrics만 사용하라.

[16. 필수 검증과 실패를 숨기지 않는 상태]
실제 시험을 실행하여 결과·환경·exit code를 기록하라. Ruff/Mypy/pytest를 광범위 ignore나 합격 기준 완화로 통과시키지 마라. 검증 대상은 다음이다.

A 코어: 알려진 단위·중량·원가/assembly fixture, 잘못된 revision/누락값 거부.
B 학습: 그룹 누수0, train-only fit, test 잠금, CPU 회귀/분류, finite metrics,
  export/새 프로세스 재로드, 기준 모델/MLP predict, 합성 모델의 운영 자동채택 차단.
C 복구: pause/resume/강제 종료, 완료 trial skip, lease 중복 방지, 시간/호출 한도,
  자신의 worker만 정리, OOM정책의 모의 주입과 실제 GPU시험 구분.
D 문서: 지정 slide/cell 근거 검색, 신규 값 반영, 원본 hash 유지,
  PPT/XLSX 수치 일치·필수 구조 보존, 캐시/재계산/렌더링 상태 구분.
E 연결: 모의 gateway/Atlassian 스키마·인증오류·rate limit·pagination·conflict,
  redirect/public URL fallback 거부·비밀값 누출 방지·기본 원격쓰기 off.
F 설치: source checkout과 개발 venv를 쓰지 않는 새 경로/새 환경,
  반입 ZIP만으로 cache 없이 설치, missing/hash/ABI 오류의 구체적 실패,
  한글/공백 경로·다른 CWD·관리자 권한 없는 실행.
G 무통신: corp-offline의 의도치 않은 outgoing request 차단/검사.
  설치도 승인된 network isolation 환경에서 시험 가능한 경우 수행하고,
  단순 socket mock과 실제 네트워크 격리 실행을 구분하라.
H 갱신: 이전 릴리스와 가짜 회사 설정/템플릿/업무 이력을 준비해
  upgrade 성공/실패, old active 유지, DB migration/rollback·설정 보존을 시험.
I 패키지: 개인 secret/절대 개인 경로/회사 원본/.venv/.git 혼입 검사,
  manifest 파일목록/hash·target·license inventory의 일치,
  외부 acceptance의 ZIP hash와 실제 반입할 최종 ZIP의 일치.
J 통합 시나리오: 최종 ZIP의 새 설치에서 CPU/offline 합성 demo를 실행한다.
  CAD A/B 수입→중량/원가 비교→합성 성능 데이터 학습/예측→근거 검색→
  같은 report_payload로 PPTX/XLSX 생성까지 하나의 demo manifest로 묶어라.
  원본 보존·문서 수치 일치·합성/예측 출처 표시를 검사하라.
  LLM/CATIA/Office가 없어도 지원되는 offline 경로는 실제로 완료되어야 한다.

상태를 각각 표시하라:
HOST_CORE_TESTED, TARGET_BUNDLE_PREPARED, TARGET_OFFLINE_TESTED,
CORP_INSTALLED, CORP_INTEGRATED, BUSINESS_VALIDATED.
사내에 접속하지 않은 개인 개발 단계에서 마지막 세 상태를 PASS로 표시하지 마라.
각 상태는 PASS/FAIL/NOT_RUN/BLOCKED 등 근거를 가진다.
미확정 타깃은 TARGET_UNCONFIRMED, source만 있으면 SOURCE_ONLY,
미확인 기능은 MOCK_TESTED/NOT_RUN으로 구분한다.
GPU/CATIA/Office live test와 실제 사내 gateway가 없으면 이유를 명시하라.

[17. 문서와 사내 인계]
다음 한국어 문서를 만들어라:
PERSONAL_BUILD_KO.md, TRANSFER_CONTENTS_KO.md, COMPANY_INSTALL_KO.md,
COMPANY_CONNECTORS_KO.md, USER_GUIDE_KO.md, UPDATE_ROLLBACK_KO.md,
TROUBLESHOOTING_KO.md, BUILD_STATUS.md, BLOCKERS.md,
CORPORATE_AI_HANDOFF.txt, TEST_EVIDENCE.md, DEPENDENCY_NOTICES.

회사 인계문은 처음부터 전체 재개발을 지시하지 마라. 받은 release/hash/target 확인,
사내 preflight, 새 venv 설치·합성 self-test, 회사 설정/템플릿 입력,
실제 adapter 검증·실데이터 검증 순서를 지시하라. 사내에서 source 변경이 꼭 필요하면
회사 extension/adapter 또는 최소 패치로 하고 변경 이력을 내부에 남겨라.

사용자가 옮길 정확한 ZIP과 SHA-256 파일, 포함/미포함 항목, 필요한 승인 Python,
사내에서 클릭/실행할 순서, 중간 실패 복구와 업데이트 방법을 구체적으로 설명하라.
일반 실행에 Fable이 필요하지 않으며 회사 LLM 연결도 선택 기능임을 명시하라.

[18. 완료 보고와 시작 지시]
최종 답변은 한국어로 다음만 근거 있게 보고하라:
1. 구현된 기능과 PARTIAL/BLOCKED 항목.
2. 실제 개인/참조타깃 사양·검증 상태와 사내 미검증 상태.
3. 실행한 시험·설치 rehearsal·실패 원인·수정 결과.
4. 개인 PC에 생성된 실제 ZIP/외부 SHA-256/acceptance/소스/문서의 정확한 경로와 크기.
5. 사내로 옮길 파일과 사내 설치·self-test·설정 순서.
6. 사내에서만 확인할 Python/라이선스/API/실데이터/승인 목록.
7. 다음 업데이트 때 company 설정·템플릿·학습 이력을 보존하는 방법.

요약만 쓰고 ‘진행할까요?’라고 끝내지 마라. 실제 권한·승인 요구는 지키면서
개인 PC에서 가능한 코어 구현과 합성 검증, 대상 패키지 제작까지 지금 수행하라.
새로운 요청이 없는 한 사내 연결 부재를 이유로 범용 제작을 멈추지 마라.
