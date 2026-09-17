# corp-dl-agent 모듈 계약 (v4) — 병렬 구현자 필독

이 문서는 모듈 간 인터페이스·소유권·공통 규칙을 정한다. 전체 요구사항은 `docs/BUILD_SPEC.md`(지시문 원문)와 `docs/ARCHITECTURE_V4.md` 에 있으며, 충돌 시 BUILD_SPEC 이 우선한다.

## 0. 공통 규칙 (모든 모듈)

- Python 3.11+ 호환 (`from __future__ import annotations`). 개발 venv: `.venv` (CPython 3.12.3, Linux). 실행: `.venv/bin/python -m pytest tests/<파일>`.
- **최상위 import 금지 대상**: `torch`, `numpy`, `pandas`, `sklearn`, `pptx`, `openpyxl`, `httpx` 는 함수 내부에서 lazy import 한다. import 실패 시 `corp_dl_agent.errors.blocked_dependency(package, feature)` 를 raise. (`corp_dl_agent/__init__.py`, `cli.py`, `doctor`, `version`, `config`, `security`, `common`, `packaging` 은 표준 라이브러리 + pydantic + yaml 만 사용.)
- 모든 schema 모델은 `corp_dl_agent.common.StrictModel` (extra=forbid, strict) 을 상속. python dict 에서 검증할 때는 `common.validate_strict(Model, data)` 를 사용 (Enum 문자열 허용, "1"->int 거부).
- 수치는 `common.Quantity` (value/unit/value_type/source_locator/calculation_id/model_run_id/calculation_version/assumption/notes) 로 보존. 없는 값은 `Quantity.missing()`, 상충은 `Quantity.conflict()`. **LLM/코드가 없는 숫자를 만들어 채우지 않는다.**
- 오류는 `corp_dl_agent.errors.AgentError(code, message_ko, hint=, details=)`. 새 코드가 필요하면 `ERROR_CATALOG` 에 추가(한국어 요약/힌트/종료코드). 비밀값을 message/details 에 넣지 않는다.
- 파일 쓰기는 `common.atomic_write_json/text/bytes` (temp→fsync→replace). JSON 출력은 `common.dump_json`, 해시는 `common.sha256_file/sha256_text`, 시각은 `common.now_iso()`.
- 경로 입력은 `security.paths.resolve_within(path, roots)` 로 승인 root 이탈·symlink 검사. 삭제는 `security.paths.assert_managed_delete` 통과 경로만.
- 폴더 구조는 `corp_dl_agent.workspace.Workspace.from_config(cfg)` 의 속성만 사용 (하드코딩 금지). 한글/공백 경로 지원 (tests 에 한글 경로 케이스 포함).
- 상태 표시는 `common.Status` (PASS/FAIL/NOT_RUN/BLOCKED/PARTIAL/MOCK_TESTED/SOURCE_ONLY/TARGET_UNCONFIRMED) + `StatusRecord`.
- 설정은 `corp_dl_agent.config.load_config(config_path, overrides, profile=)` → `AppConfig`. 네트워크 허용 여부는 `cfg.network_allowed()` 만 신뢰.
- 로깅: `corp_dl_agent.logs.setup_logging()`, run 이벤트는 `logs.EventLog(path).emit(event, **fields)`.
- CLI 명령은 `corp_dl_agent/commands/<name>_cmd.py` 에 `register(sub)` 와 handler(`args -> int`) 로 구현. 공통 인자는 `cli.add_common_arguments(parser)`, override 파싱은 `cli.parse_overrides(args.overrides)`. 한국어 help 필수. `--json` 출력 지원.
- 테스트: `tests/test_<모듈>.py`, pytest, 결정적(seed 고정), 네트워크 없음. torch/pptx 필요 시험은 `@pytest.mark.torch` / `@pytest.mark.documents` 표시 (설치되어 있으므로 skip 하지 말 것).
- 코드 품질: `ruff check`, `ruff format --check`, `mypy corp_dl_agent` 통과. 광범위 ignore 금지.
- Windows 호환: `pathlib` 사용, `os.replace`, 텍스트 파일은 `encoding="utf-8"` 명시, DataLoader `num_workers=0` 기본, `if __name__ == "__main__"` guard.
- 합성 데이터/모델/문서에는 `synthetic: true` / `data_origin: "synthetic"` 표시를 남긴다.

## 1. 소유권 (파일 단위, 다른 소유자의 파일은 수정 금지 — 필요하면 결과 보고서에 요청을 남길 것)

| 소유자 | 파일 |
|---|---|
| engineering | `corp_dl_agent/engineering/{__init__,cad_snapshot,units,mass,cost,compare}.py`, `corp_dl_agent/commands/{cad_cmd,design_cmd}.py`, `fixtures/cad/**`, `tests/test_engineering_*.py` |
| state | `corp_dl_agent/state/{__init__,db,machine,budget,coordinator}.py`, `tests/test_state_*.py` |
| ml-core | `corp_dl_agent/ml/{__init__,taskspec,data,split,preprocessing,baselines,evaluation,registry,synthetic}.py`, `corp_dl_agent/commands/validate_cmd.py`, `fixtures/ml/**`, `tests/test_ml_core_*.py` |
| ml-torch | `corp_dl_agent/ml/{mlp,checkpoint,trainer,export,runner,predict}.py`, `corp_dl_agent/commands/{run_cmd,predict_cmd}.py`, `tests/test_ml_torch_*.py` |
| documents | `corp_dl_agent/documents/**`, `corp_dl_agent/commands/docs_cmd.py`, `fixtures/documents/**`, `tests/test_documents_*.py` |
| adapters | `corp_dl_agent/adapters/**`, `corp_dl_agent/security/network.py`, `corp_dl_agent/commands/integrations_cmd.py`, `tests/test_adapters_*.py`, `tests/test_security_network.py` |
| packaging | `corp_dl_agent/packaging/**`, `scripts/**`, `corp_dl_agent/commands/{package_cmd,upgrade_cmd}.py`, `tests/test_packaging_*.py`, `tests/test_scripts_*.py` |
| reporting/ops | `corp_dl_agent/reporting/**`, `corp_dl_agent/doctor.py`, `corp_dl_agent/selftest.py`, `corp_dl_agent/demo.py`, `corp_dl_agent/commands/{doctor_cmd,config_cmd,selftest_cmd,demo_cmd,menu_cmd}.py`, `tests/test_reporting_*.py`, `tests/test_cli_*.py` |
| 공통(이미 작성됨, 수정 시 보고) | `common.py, errors.py, cli.py, workspace.py, logs.py, config/**, security/{paths,secrets}.py, version.py, pyproject.toml` |

## 2. 인터페이스

### 2.1 engineering

```python
# cad_snapshot.py
class CadSnapshotRow(LaxModel):   # CSV 유래이므로 LaxModel, 그러나 extra 금지
    document_id: str; revision: str; configuration_id: str | None = None
    occurrence_path: str            # 예 "ROOT/ASM_A/PART_1.2" (instance 경로, 고유)
    reference_id: str               # 부품 참조(동일 reference 가 여러 occurrence 로 등장 가능)
    quantity: int = 1               # 이 occurrence 의 수량 (instance 수)
    material_id: str | None = None
    density_kg_m3: float | None = None
    volume_m3: float | None = None
    mass_kg: float | None = None    # CAD 가 직접 준 질량(있으면 계산값과 비교, 출처 구분)
    parameter_map: dict[str, float | str] = {}
    units: dict[str, str] = {}      # {"volume": "mm3", "density": "g/cm3"} 등 원본 단위
    geometry_status: Literal["solid","surface","unloaded","suppressed","missing"] = "solid"
    surface_thickness_m: float | None = None   # surface 인 경우 명시 두께
    is_assembly: bool = False       # True 면 자식들의 총량 노드 (질량 이중합산 금지)
    parent_path: str | None = None
    source_hash: str = ""; extracted_at: str = ""; extractor_version: str = ""
class CadSnapshot(StrictModel): rows: list[CadSnapshotRow]; source_path: str; source_hash: str; extracted_at: str; extractor_version: str; issues: list[SnapshotIssue]
def import_snapshot(path: str|Path, *, field_mapping: dict[str,str] | None = None) -> CadSnapshot   # CSV(UTF-8/UTF-8-SIG/CP949)/JSON
def write_snapshot(snapshot, path)  # JSON
```
- 검증: 중복 occurrence_path, 알 수 없는 geometry_status, 단위 없는 volume, 음수 값 → issues(level=error) 및 `AgentError("E_INPUT_INVALID")` (strict=True 일 때).
- `units.py`: `convert_volume(value, unit)->m3`, `convert_density(value, unit)->kg/m3`, `mass_kg(volume_m3, density_kg_m3)`, 지원 단위: volume mm3/cm3/m3, density g/cm3/kg/m3, mass g/kg. 알 수 없는 단위 → `E_UNIT_MISMATCH`. 검증값: 1,000,000 mm3 × 1.0 g/cm3 = 1.0 kg.
- `mass.py`: `compute_mass(snapshot: CadSnapshot, *, scope: MassScope) -> MassResult`. MassResult.items: occurrence 별 `MassItem(occurrence_path, reference_id, quantity, unit_mass: Quantity, total_mass: Quantity, status: "ok"|"missing_density"|"unloaded"|"suppressed"|"surface_no_thickness"|"assembly_node")`; `MassResult.total: Quantity` (계산 가능한 것만 합산, 누락이 있으면 `total.notes` 에 누락 목록·`complete=False`). `MassScope(StrictModel)`: include_paint/include_foam/include_adhesive/include_purchased (기본 False, 각각 명시 kg 항목 리스트). suppressed/assembly 노드 제외, quantity 곱, 상위 총량과 하위 이중합산 금지.
- `cost.py`: recipe 모델 `InjectionMoldingRecipe(material_price_per_kg, currency, base_date, cycle_time_s, cavities, good_rate, machine_rate_per_hour, runner_ratio, regrind_allowed, post_process_cost, assembly_cost, purchased_items:[...], tooling_cost, tooling_amortization_qty, other:[{name,value}], production_volume, setup_cost_per_batch, batch_size)`, `PurchasedPartRecipe(unit_price, currency, base_date)`, `GenericProcessRecipe(lines:[{name,value,unit}])`. `compute_cost(mass_item_or_result, recipe) -> CostBreakdown(lines: list[CostLine(name, amount: Quantity)], unit_cost: Quantity, currency, base_date, quantity_basis, estimate_type: "manufacturing_estimate"|"quote", assumptions: list[str], missing: list[str])`. 사출가공비 = machine_rate × cycle / (3600 × cavity × good_rate). 단가/생산량 없으면 해당 line 은 MISSING, unit_cost 도 MISSING (`complete=False`). 통화/기준일 불일치 → `E_REVISION_MISMATCH`.
- `compare.py`: `DesignCandidate(StrictModel)`: id, label, snapshot_path, revision, base_date, currency, mass: MassResult, cost: CostBreakdown | None, performance: dict[str, Quantity], constraints: dict[str, bool|None], evidence: list[str], unknowns: list[str]. `compare_designs(candidates, *, require_same_revision_policy=True) -> DesignComparison(rows, deltas, warnings, consistent: bool)` — revision/base_date/currency/단위 불일치 시 `E_REVISION_MISMATCH`.
- CLI: `cad import --input <csv|json> --output <snapshot.json> [--mapping k=v]`, `cad extract --adapter catia_v5_com|3dexperience` (개인 PC 에서는 `E_NOT_SUPPORTED`, 상태 inactive 표시), `design compare --candidate A=<dir> --candidate B=<dir> --output design_comparison.json`.
- fixtures: `fixtures/cad/asm_a_snapshot.csv`, `asm_b_snapshot.csv` (알려진 질량: 1,000,000 mm3×1.0 g/cm3 부품, 동일 reference 3회, suppressed 1, unloaded 1, density 누락 1, surface 1, assembly 총량 노드), `fixtures/cad/cost_recipes.json`, `fixtures/cad/material_table.csv`.

### 2.2 state

```python
# machine.py
class RunStatus(str, Enum): CREATED, VALIDATING, PLANNED, RUNNING, EVALUATING, COMPLETED, PAUSED, CANCELLED, FAILED, BLOCKED_CONFIG, BLOCKED_DEPENDENCY, BUDGET_EXCEEDED
ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]]; def assert_transition(a,b) -> None  # E_STATE_TRANSITION
# budget.py
class ResourceBudget(StrictModel): wall_time_seconds:int; max_calls:int|None; max_tokens:int|None; max_candidates:int; max_epochs:int; patience:int
class BudgetTracker: start(); elapsed(); remaining(); check(raise_on_exceed=True); record_call(tokens_in, tokens_out, reserved=...); can_start_new_work(estimated_seconds) -> bool; snapshot() -> dict
# db.py
class StateDB: __init__(path: Path)  # sqlite3, WAL, busy_timeout, 단일 writer (BEGIN IMMEDIATE), schema_version 테이블, migrate()
   transaction() contextmanager; execute/query helpers; backup_to(path) (sqlite3 backup API); close()
# coordinator.py
class Coordinator: __init__(db: StateDB, worker_id: str)
   create_run(run_id, task_fingerprint, config_hash, kind) ; get_run(run_id) -> RunRecord ; set_status(run_id, new_status, reason="")
   acquire_lease(run_id, ttl_seconds) -> Lease | raises E_LEASE_HELD ; heartbeat(lease) ; release(lease)
   create_trial(run_id, trial_id, candidate_config, fingerprint) ; create_attempt(trial_id) -> attempt_id ; finish_trial(trial_id, status, metrics, artifact_hashes)
   find_completed_trial(fingerprint) -> TrialRecord|None  # 산출물 hash 검증까지 하는 helper: is_trial_reusable(trial, artifact_dir)
   request_pause(run_id) / request_cancel(run_id) / is_pause_requested(run_id) / is_cancel_requested(run_id)
   list_active_runs() ; expire_stale_leases(now) ; record_event(run_id, event, payload)
   worker_id 형식: "<hostname-hash>-<pid>-<random8>"; 자신의 worker 만 정리(cleanup_own(run_id))
```
- fingerprint = sha256(canonical_json({task, data_hash, split_hash, code_hash, lock_hash, model_config, seed})).
- 시험: pause/resume, 강제 종료(lease 만료) 후 재획득, 완료 trial skip(산출물 hash 검증), lease 중복 방지, 시간/호출 한도, 자신의 worker 만 정리, 상태 전이 위반.

### 2.3 ml-core

```python
# taskspec.py
class Acceptance(StrictModel): metric:str; threshold:float; direction: Literal["min","max"]
class ResourceBudgetSpec(StrictModel): mode: Literal["demo","pilot","custom"]="demo"; wall_time_seconds:int|None; max_candidates:int|None; max_epochs:int|None; patience:int|None
class TaskSpec(StrictModel):
    task_id:str; task_type: Literal["regression","binary_classification"]; data_path:str; target:str
    numeric_features:list[str]; categorical_features:list[str]=[]; units:dict[str,str]={}
    id_column:str; group_column:str; split_policy: Literal["group","time"]="group"; time_column:str|None=None
    seed:int=42; metric:str ("mae"|"rmse"|"r2"|"average_precision"|"roc_auc"|"f1"); direction: Literal["min","max"]
    acceptance: Acceptance|None=None   # None -> NEEDS_ACCEPTANCE_CRITERIA
    resource_budget: ResourceBudgetSpec; data_origin: Literal["synthetic","corporate"]; purpose:str=""
    excluded_columns:list[str]=[]  # 사후 결과 등 명시 제외
def load_taskspec(path) -> TaskSpec (YAML/JSON, strict); def taskspec_hash(spec) -> str
# data.py
class DataReport(StrictModel): path, sha256, encoding_detected, n_rows, n_cols, columns, dtypes, target_stats, issues(list), passed(bool)
def load_dataset(spec: TaskSpec, *, roots) -> tuple[pd.DataFrame, DataReport]  # 인코딩 utf-8-sig/utf-8/cp949 순 시도·기록; id 는 str dtype 유지; 타깃 누락·중복 ID·NaN/Inf·feature==target·그룹 누락 → E_INPUT_INVALID(details: rows). 오류 행 삭제 금지.
# split.py
class SplitManifest(StrictModel): policy, seed, ratios_requested(0.7,0.15,0.15), group_column, n_groups{train,val,test}, n_rows{...}, ids{train:[...],val:[...],test:[...]} (id 문자열), hashes{data,split,taskspec,code,lock}, overlap_checked: bool
def group_split(df, spec, *, code_hash, lock_hash) -> SplitManifest   # 그룹 교집합 0 검증; 그룹 수 < 3 → E_SPLIT_IMPOSSIBLE; time policy 면 미래 누수 금지
def save_split(manifest, path); load_split(path); apply_split(df, manifest) -> (train, val, test)
# preprocessing.py
def build_preprocessor(spec) -> sklearn ColumnTransformer (numeric: SimpleImputer+StandardScaler, categorical: OneHotEncoder(handle_unknown="infrequent_if_exist" or "ignore", max_categories=...)); fit 은 train 에만. 고유 ID 과다 열 감지 → E_LEAKAGE. 출력 dense float32. 
class FittedPreprocessor: transform(df)->np.ndarray; feature_names; to_state()/from_state() (json+npz 로 저장, pickle 금지 가능하면; 불가 시 joblib + checksum)
# baselines.py
def baseline_models(task_type) -> dict[name, estimator]: regression: DummyRegressor, Ridge, HistGradientBoostingRegressor; classification: DummyClassifier, LogisticRegression, HistGradientBoostingClassifier
def fit_baseline(name, est, X_train, y_train) ; predict_baseline(est, X, task_type) -> np.ndarray (회귀 값 / 분류 확률)
# evaluation.py
def evaluate_regression(y_true, y_pred, unit) -> dict: mae, rmse, r2, p95_abs_error (원단위)
def evaluate_classification(y_true, y_prob, threshold) -> dict: average_precision, roc_auc, recall, precision, f1, confusion_matrix, threshold
def choose_threshold(y_val_true, y_val_prob, metric="f1") -> float  # validation 에서만
def is_finite_metrics(d) -> bool
# registry.py
MLP_SEARCH_SPACE = {hidden_dims: [[64,32],[128,64]], dropout: (0.0,0.2), learning_rate: (1e-4,3e-3), weight_decay: (0.0,1e-3)}
def validate_candidate(cfg: dict) -> MlpCandidate(StrictModel)   # 범위 밖 → E_SCHEMA_INVALID
def demo_candidates(task_type, seed, n) -> list[MlpCandidate]
# synthetic.py
def generate_clip_dataset(n_rows, n_groups, seed, task_type) -> pd.DataFrame  # 두께·홀 직경·각도·탄성계수·마찰·온도·삽입속도 + 삽입력 target(회귀) / 유지력 합격여부(분류), group_id(설계 계열), id 문자열, post_result(사후 결과, excluded 대상) 열 포함. 비선형 + noise. 실제 차량 물리를 대표하지 않는다는 문구를 fixture README 에 남긴다.
def write_fixtures(dir)  # fixtures/ml/clip_regression.csv, clip_classification.csv, task_regression.yaml, task_classification.yaml
```
- CLI `validate --config <taskspec.yaml>`: data_report.json + split_manifest.json 생성, 누수·분할 검사만 (학습 없음).

### 2.4 ml-torch (ml-core 완료 후)

```python
# mlp.py: class MLP(nn.Module)(in_dim, hidden_dims, dropout, out_dim=1); 회귀 출력 (N,1)->target (N,1) shape 일치; 분류 logits + BCEWithLogitsLoss
# checkpoint.py: save_checkpoint(path, *, model, optimizer, scheduler, scaler, rng_state(python/numpy/torch/dataloader generator), next_epoch, best_validation, config_hash, metrics) -> temp→flush→검증(재로드)→os.replace; load_checkpoint(path, expected_config_hash) (weights_only=True 로 tensor 부분 로드; 메타는 json); latest.pt / best.pt 분리
# trainer.py (실제 시그니처): train_candidate(candidate, data: TrainingData, budget: BudgetTracker, coordinator, run_dir, *, device="cpu", resume=True, run_id, fingerprint, config_hash=None, trial_id=None, max_epochs=10, patience=3, amp=False, seed=42, hooks=None, event_log=None, epoch_metrics_path=None, artifact_root=None, oom_retries=2, nan_retries=1, log_every=10, lease=None) -> TrialResult
#   - epoch 경계 pause 확인, 강제 종료 후 resume 은 마지막 완료 epoch 뒤, config/data/lock hash 다르면 E_FINGERPRINT_CHANGED
#   - finite loss/grad 검사(NaN/Inf → 진단 후 LR/정밀도 교정 1회 새 attempt), OOM → microbatch/accumulation 변경 새 attempt 최대 2회(변경 기록), accumulation 마지막 불완전 묶음 실제 표본 수 정규화
#   - model.train()/eval(), torch.inference_mode 예측, zero_grad(set_to_none=True), scalar 로그 item() 주기 제한, 예측 detach().cpu()
#   - CPU FP32 기본; cuda/amp 는 torch.cuda.is_available() 및 설정 확인 후에만
# export.py: export_bundle(dir, *, model_kind: "sklearn"|"mlp", model, preprocessor_state, input_schema, units, target_inverse, threshold, train_ranges, data_origin, purpose, run_id) -> manifest(json, checksums); load_bundle(dir, *, allow_synthetic=False) → 신뢰(checksum 일치·이 프로그램 생성 marker) 만; predict(bundle, df) -> DataFrame(id, prediction, [probability])
# runner.py (실제 시그니처): run_task(spec, cfg, ws, *, run_id=None, resume=False, budget_override: ResourceBudget|BudgetTracker|dict|None=None, lock_hash=None, hooks=None, worker_id=None, lease_ttl_seconds=120.0) -> RunSummary
#   budget_override=None 이면 TaskSpec.resource_budget.mode(demo→cfg.ml.demo: 후보 2/10 epochs/300초, pilot→cfg.ml.pilot) 적용. RunSummary 필드: run_id, task_id, task_type, status(COMPLETED|PAUSED|CANCELLED|BUDGET_EXCEEDED), data_origin, synthetic, run_dir, candidates[CandidateSummary(name, kind, trial_id, status, metrics, epochs, attempts, reused_from)], selected, selected_kind, selected_trial_id, validation_metrics, final_evaluation, acceptance{state,...}, threshold, train_ranges, budget(snapshot), hashes, environment, artifacts{파일명: sha256}, warnings, trials_skipped, resumed, test_locked, message. export 폴더: <run_dir>/export
#   산출물: environment.json, manifest.json, data_report, split_manifest, plan.json, events.jsonl, epoch_metrics.jsonl, checkpoints/, metrics.csv, final_evaluation.json, model_card.json, export/  ; 기준 모델 + MLP 후보 비교 → validation 으로 선택 → test 1회 평가(final_evaluation.json 있으면 재평가 금지) ; pause/resume/cancel 은 coordinator 통해
# predict.py (실제 시그니처): predict_cli(model_dir, input_csv, output_csv, cfg, *, allow_synthetic: bool|None=None, extra_roots=()) -> dict  # allow_synthetic=None 이면 cfg.ml.allow_synthetic_models_in_production(기본 False) → synthetic 번들은 E_ARTIFACT_SYNTHETIC. 출력 CSV 열: id, prediction, [probability, label], in_train_range ; <output>.manifest.json sidecar
# adapters.tools.dispatch("train_model") 은 run_task(..., budget_override=<mode/상한 인자>) 로, dispatch("predict") 는 predict_cli(..., allow_synthetic=True|None, extra_roots=ctx roots) 로 호출한다.
# CLI: run --config <taskspec>, status/pause/resume/cancel --run-id, report --run-id, predict --model --input --output
```

### 2.5 documents

```python
# index.py: class DocumentIndex(db_path): index_roots(roots: list[Path], *, include_hidden_flag=True) -> IndexReport ; PPTX: slide/shape/table/notes/chart 원데이터(정확한 locator "slide:3/shape:Title 1", "slide:3/table:r2c1", "slide:3/notes", "slide:3/chart:series"), XLSX: sheet/table/named range/cell (locator "sheet:Cost!B7", "range:UnitCost", "table:T1!r2c3"), 수식(formula) 과 cached value 구분, hidden 표시, source_id/sha256/revision(파일명 또는 core properties)/mtime 보존, 삭제된 파일은 index 에서 제거(prune). 구형 .ppt/.xls → status "NEEDS_APPROVED_CONVERSION". 매크로/OLE/외부 링크는 실행하지 않고 flag 만.
# search.py: search(index, query, *, limit, include_hidden=False) -> list[Hit(source_id, path, locator, text, score, hidden, kind: "form_reference"|"fact", revision, approval_state)]  키워드/metadata 검색 (embedding 불필요)
# evidence.py: build_evidence(hits, *, purpose) -> evidence.json 스키마 (각 항목 source_id/hash/locator/quoted_text/kind)
# payload.py: class PayloadItem(StrictModel): key, label_ko, quantity: Quantity ; class ReportPayload(StrictModel): report_id, created_at, subject, items: dict[key, PayloadItem], tables: dict[name, PayloadTable], synthetic: bool ; validate_payload(payload) → 합계/차이(예: mass_delta = mass_b - mass_a, cost_total = sum(lines)) 를 계산 엔진으로 재검증하여 불일치면 CONFLICT
# templates.py: fill_pptx(template, payload, out) — 승인된 placeholder `{{key}}` 가 있는 shape/table cell 만 치환, slide 순서/master/theme 보존, 미지원 요소 삭제 금지, 채워지지 않은 placeholder 는 'MISSING' 표시 ; fill_xlsx(template, payload, out) — named range / 지정 cell 만 기록, 수식 보존, data_only 사용 금지 ; 반환 DocumentManifest(input_hash, output_hash, changed_locators, unsupported_elements, recalc_status="RECALC_NOT_RUN", render_status="RENDER_NOT_RUN")
# validation.py: validate_documents(pptx, xlsx, payload) -> validation_report.json: 수치 일치(문서에서 읽은 값 == payload), 필수 구조 보존(slide 수, sheet 이름, named range), 오래된 값(과거 차종/날짜/원가) 잔존 검사, 상태 RECALC_NOT_RUN/RENDER_NOT_RUN 명시
# synthetic.py: make_synthetic_documents(dir) → fixtures/documents/past_review_2023.pptx (구 차종 "X-OLD", 2023-05-01, 원가 1,234), past_cost_table.xlsx(수식+cached), template_review.pptx({{...}}), template_comparison.xlsx(named ranges), README.md(synthetic 표시)
# CLI: docs index --roots ... ; docs search --query ... ; docs generate --payload report_payload.json --pptx-template ... --xlsx-template ... --output-dir ...  → review.pptx, comparison.xlsx, evidence.json, document_manifest.json, validation_report.json
```

### 2.6 adapters + security.network

```python
# security/network.py: class OutboundGuard: 컨텍스트로 진입하면 socket.create_connection/ socket.socket.connect 를 가로채 approved_origins 외 연결 시 E_NETWORK_BLOCKED (loopback 은 mock 서버용으로 허용 목록에 명시할 때만). offline 프로파일에서 활성. is_origin_allowed(url, allowed) ; 리다이렉트 금지 httpx.Client 생성 helper make_client(cfg, ca_bundle) (follow_redirects=False, verify=ca_bundle or True, timeout)
# adapters/tools.py: TOOL_REGISTRY = {validate_input, calculate_mass_cost, train_model, predict, search_documents, generate_report}; 각 ToolSpec(name, input_model: StrictModel, description_ko, side_effects) ; dispatch(name, payload, ctx) → 코어 함수 호출(실제 구현 모듈을 lazy import; 없으면 E_BLOCKED_DEPENDENCY) ; 미등록 → E_TOOL_NOT_REGISTERED. eval/exec/subprocess 로 LLM 출력을 실행하지 않는다.
# adapters/llm.py: class PlanStep(StrictModel): tool, arguments(dict), reason ; class Plan(StrictModel): steps, model_id, source: "offline_planner"|"gateway"
#   OfflinePlanner.plan(request: StructuredRequest) -> Plan (규칙 기반; 자연어 해석 미지원 → E_NOT_SUPPORTED with 안내)
#   class GatewayLLM(cfg.gateway, budget): OpenAIChatAdapter / AnthropicMessagesAdapter / GeminiCustomAdapter(계약만, 호출 시 E_NOT_SUPPORTED 사유 "사내 제공 형식 미확정") ; 요청/응답 계약 pydantic 모델; 401/403 재시도 0, 429/5xx 최대 2, JSON 교정 1회, usage 없으면 reserve_tokens_per_call 로 예산 차감, 총 calls/tokens 상한, redirect 금지, TLS 검증 유지, approved_origins 검사; prompt injection: 데이터 텍스트는 "untrusted" 블록으로 감싸 계획 검증은 코어에서
# adapters/atlassian.py: BitbucketClient/JiraClient/ConfluenceClient/BambooClient(product_cfg, write_enabled=False): Cloud/DataCenter base path·auth·pagination(start/limit vs startAt/maxResults vs cursor)·rate limit(429 Retry-After)·conflict(409) 처리; write 메서드는 integrations.write=false 면 E_INTEGRATION_WRITE_DISABLED 전에 preview dict 반환(preview_issue/preview_page/preview_pr) ; doctor() -> 제품별 StatusRecord(NOT_RUN/BLOCKED 사유)
# adapters/cad.py: class CadAdapter(Protocol) extract(...)->CadSnapshot ; CsvJsonCadAdapter (engineering.import_snapshot 위임) ; CatiaV5ComAdapter/ThreeDExperienceAdapter: available() False + status INACTIVE 사유(설치/COM/라이선스 미확인), extract → E_NOT_SUPPORTED. 존재하지 않는 method 이름 창작 금지.
# tests: 로컬 mock HTTP 서버(http.server, 127.0.0.1)로 OpenAI/Anthropic 계약, 401/429/5xx, redirect 거부, public URL fallback 없음, 비밀 누출 없음(로그/예외 문자열 검사), Atlassian pagination/409/rate limit, OutboundGuard 가 offline 에서 차단.
# CLI: integrations doctor ; integrations preview --product jira --payload file.json
```

### 2.7 packaging + scripts

```python
# packaging/target.py: TargetProfile(StrictModel): profile_id ("win-x64-cp312-cpu"), os, os_version, architecture, python_implementation, python_version, python_abi ("cp312"), platform_tags(["win_amd64"]), features(["core","documents","ml-cpu"]), device("cpu"), gpu(None), target_confirmed(False), verified_environment(dict|None), notes ; write_target_profile(path) ; minimal summary(허용 필드만) write_min_profile(path) — 이름/호스트/사용자 경로/URL/파일 목록/인증값 제외 ; detect_host() -> TargetProfile(현 호스트)
# packaging/lockfile.py: build_lock(profile, wheelhouse_dir, app_wheel) -> locks/<profile>.txt (pip --require-hashes 형식: name==ver --hash=sha256:...), 모든 wheel 이 lock 에 있고 lock 의 모든 항목이 wheel 존재; requirements 파싱 시 URL/VCS/-e/디렉터리/--index-url/-r 거부(validate_lock_text)
# packaging/inventory.py: dependency-inventory.json (wheel METADATA 의 Name/Version/License/License-Expression/Home-page/Project-URL, sha256, filename, source "pypi") — 실제 metadata 없으면 "UNKNOWN"
# packaging/manifest.py: ReleaseManifest(StrictModel): release_id, version, profile_id, package_kind ("SOURCE_ONLY"|"CPU_OFFLINE"|"GPU_OFFLINE"), target: TargetProfile, features, schema_version, db_schema_version, verification: dict[str, StatusRecord] (HOST_CORE_TESTED 등 6 상태 + TARGET_UNCONFIRMED), files: list[{path,size,sha256}] (checksums.sha256 과 release-manifest.json 자신은 files 에서 제외 → 순환 없음), prerequisites(list[str]), created_at ; checksums.sha256 은 manifest 를 포함한 payload 파일 hash(자기 자신 제외) ; 검사 범위 문서화
# packaging/release.py: build_release(project_root, *, profile: TargetProfile, wheelhouse, app_wheel, out_dir, evidence_dir) -> zip path ; allowlist 포장 (source/ app/ wheelhouse/<profile>/ locks/ scripts/ config-examples/ fixtures/ schemas/ docs/ release-manifest.json checksums.sha256 dependency-inventory.json test-evidence/) ; 금지 패턴 검사(.venv, .env, .git, __pycache__, *.pyc, workspace/, dist/, .claude/, 개인 절대경로, 'sk-', 'BEGIN PRIVATE KEY') ; ZIP 밖 <zip>.sha256 생성 ; acceptance json 은 scripts/acceptance.py 가 생성
# scripts/ (표준 라이브러리만, 앱 미설치 상태 실행):
#   preflight.py [--python <path>] [--install-root] [--data-root] --report <json> : OS/arch/Python/ABI/venv/ensurepip/pip 유무, 디스크, 쓰기 권한, 한글/공백 경로 → target_profile.min.json + 상세 report(로컬). Python 없으면 preflight.ps1 이 진단.
#   verify.py --package <zip|dir> : manifest/schema/file count/size/hash/경로 탈출/link/target OS·Python·ABI 검사 → exit code
#   install.py --package <zip> --install-root <dir> [--data-root] [--python <path>] : verify → releases/<id>/<profile>/ 에 추출 → 그 위치에서 venv 생성(이동 금지) → pip 환경/설정 차단(PIP_* 제거, PIP_CONFIG_FILE=os.devnull, --isolated) → `pip install --no-index --find-links <wheelhouse> --require-hashes --only-binary=:all: --no-cache-dir --disable-pip-version-check -r locks/<profile>.txt` → pip check → import → self-test → smoke → active.json 원자적 교체(실패 시 기존 유지) ; ensurepip/pip/venv 없으면 BLOCKED_PREREQUISITE (exit 8)
#   launch.py [args] : active.json 의 venv python 으로 `-m corp_dl_agent` 실행(Activate 불필요) ; selftest.py ; upgrade.py --package <zip> (실행 중 job lock 확인, 새 release 설치, DB backup(sqlite backup API)+migration plan, 성공 시 active 전환) ; rollback.py --to <release_id> (코드 rollback 과 DB rollback 구분, 비호환이면 중단·안내)
#   acceptance.py --zip <zip> --evidence-dir ... : 외부 <zip_basename>.acceptance.json (zip hash, 환경, 명령/exit code, PASS/FAIL/NOT_RUN 사유)
#   00_Preflight.cmd 01_Verify.cmd 02_Install.cmd 03_Start.cmd 04_SelfTest.cmd 05_Update.cmd 06_Rollback.cmd (script 위치 기준 %~dp0, 인자 안전 전달, 지정 Python 경로 지원, Store/웹 자동 설치 금지), preflight.ps1, *.sh(Linux rehearsal 용)
# CLI: package verify --package <zip> ; package build ... ; upgrade --package ; rollback --to
```

### 2.8 reporting / ops

```python
# reporting/html.py: render_report_ko(run_summary|dict, out_html, out_md) — CDN/외부 리소스 0, html.escape, 숫자는 전달된 metrics 만
# reporting/model_card.py: build_model_card(run) -> model_card.json/md (data_origin, purpose, metrics, acceptance 상태 NEEDS_ACCEPTANCE_CRITERIA, 합성 모델 운영 자동선택 제외 문구)
# doctor.py: run_doctor(cfg) -> dict (OS/Python/패키지 유무(import 시도, 실패해도 진단 계속)/GPU/경로 쓰기/프로파일 정책/gateway 설정 상태(값 마스킹)/adapter 상태) ; 허용 필드만의 export-diagnostics preview
# selftest.py: run_selftest(cfg) -> 결과 dict (단위·중량 1.0kg 검증, config, sqlite, 문서 라이브러리 유무, torch 유무, 경로) — 설치 후 자동 실행
# demo.py: run_demo(cfg, *, offline=True, device="cpu", out_dir) : CAD A/B import → mass/cost compare → 합성 CSV 회귀 run + 분류 run (각 별도 run, MLP 후보 2, 10 epochs, 300 초) → predict → docs index(fixtures) → search → report_payload.json → review.pptx/comparison.xlsx/evidence/validation_report → demo_manifest.json(모든 산출물 hash, 원본 보존 hash 비교, 수치 일치, synthetic 표시)
# menu_cmd.py: 대화형 메뉴(demo/설정검사/설계비교/학습/문서작성/상태확인) — 구현된 경로만 노출, 외부 로그인 없음
# CLI: doctor, self-test, demo --offline --device cpu --output-dir, config validate --config
```

## 3. 산출물 규격 요약

- run 폴더: environment.json, manifest.json, data_report.json, split_manifest.json, plan.json, events.jsonl, epoch_metrics.jsonl, checkpoints/{latest.pt,best.pt,meta.json}, metrics.csv, final_evaluation.json, report_ko.html, report_ko.md, model_card.json, model_card.md, export/
- 업무 산출물: cad_snapshot.json, mass_result.json, cost_breakdown.json, performance_predictions.csv, design_comparison.json, evidence.json, report_payload.json, review.pptx, comparison.xlsx, document_manifest.json, validation_report.json
- 상태 6종: HOST_CORE_TESTED, TARGET_BUNDLE_PREPARED, TARGET_OFFLINE_TESTED, CORP_INSTALLED, CORP_INTEGRATED, BUSINESS_VALIDATED (개인 개발 단계에서 마지막 셋은 절대 PASS 아님)

## 4. 완료 보고 형식 (각 구현자)

구현 파일 목록, 실행한 pytest 명령과 결과(통과/실패 수), ruff/mypy 결과, 미구현/PARTIAL 항목과 이유, 다른 소유자 파일에 필요한 변경 요청.
