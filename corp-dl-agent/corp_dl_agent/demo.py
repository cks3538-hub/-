"""demo: 합성 fixture 로 전체 업무 흐름을 오프라인으로 실행하고 demo_manifest.json 으로 묶는다 (BUILD_SPEC [16] J).

흐름
  (a) CAD A/B snapshot 수입 → 중량 → 원가(cost_recipes.json 의 injection_synthetic_pp, PART_CUBE 단위 질량 기준)
      → DesignCandidate(revision R1, 기준일 2026-09-01, KRW) → design_comparison.json
  (b) 합성 회귀 run + 이진분류 run (각각 별도 run; demo 예산 후보 2 / 10 epochs / 300초, --fast 는 후보 1 / 2 epochs)
  (c) export 모델로 predict (allow_synthetic=True, demo 목적 명시) → performance_predictions.csv / retention_predictions.csv
      + 설계안 A/B scenario 예측(design_scenario_predictions.csv) → design_comparison.json 을 performance 로 갱신
  (d) fixtures/documents 색인 → 근거 검색 → evidence.json
  (e) report_payload.json 조립 (value_type 표시, synthetic=true, 합계/차이 check 를 계산 엔진으로 재검증)
  (f) 같은 payload 로 template_review.pptx / template_comparison.xlsx → review.pptx + comparison.xlsx
      + document_manifest.json + validation_report.json
  (g) demo_manifest.json: 산출물 경로/sha256, 원본 fixture hash 불변 확인, 문서 수치 == payload, synthetic/predicted 출처,
      실행 환경, 소요 시간, 단계·검사별 PASS/FAIL, outbound 시도 수(OutboundGuard 를 demo 전체에 적용)

규칙
- 원본 fixture 는 읽기 전용이다. 모든 산출물(run 폴더·상태 DB·색인 포함)은 --output-dir 아래에만 쓴다
  (<output-dir>/workspace 를 demo 전용 data_root 로 사용).
- OutboundGuard 는 프로세스 내 socket API 검사이며 OS 수준 네트워크 격리가 아니다 (manifest 에 명시).
- 없는 숫자를 만들지 않는다. 설계안 A/B 의 성능 예측 입력은 CAD 파라미터(두께) + 학습 표본의 중앙값/최빈값이라는
  '명시된 scenario 가정' 으로만 만들고 assumption=True 로 표시한다. 합계/차이는 계산 엔진(compare/payload check) 이 만든다.
- 실패한 단계는 FAIL 로 남기고 이후 단계는 NOT_RUN 으로 표시한다. all_pass 가 아니면 exit code 는 0 이 아니다.
- 이 파일 최상위는 표준 라이브러리 + pydantic + (third-party 를 import 하지 않는) 코어 모듈만 import 한다.
  torch/pandas/pptx/openpyxl 을 쓰는 모듈은 단계 안에서 lazy import 한다.
"""

from __future__ import annotations

import csv
import importlib
import math
import os
import platform
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from corp_dl_agent.common import (
    Quantity,
    Status,
    StrictModel,
    atomic_write_json,
    now_iso,
    read_json,
    sha256_file,
)
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import EXIT_OK, EXIT_VALIDATION, AgentError
from corp_dl_agent.security.network import OutboundGuard
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.version import SCHEMA_VERSION, __version__
from corp_dl_agent.workspace import Workspace

DEMO_FORMAT = "corp-dl-agent/demo-manifest/1"
DEMO_MANIFEST_NAME = "demo_manifest.json"
FIXTURES_ENV = "DIA_FIXTURES_DIR"
INSTALL_ROOT_ENV = "DIA_INSTALL_ROOT"
ACTIVE_NAME = "active.json"

# fixtures/cad/README.md 에 문서화된 검산값 (fixture 를 바꾸면 README 와 함께 바꿔야 한다)
EXPECTED_MASS_KG: dict[str, float] = {"A": 4.675, "B": 3.5425}
EXPECTED_CUBE_UNIT_MASS_KG = 1.0
COST_RECIPE = "injection_synthetic_pp"
COST_OCCURRENCE: dict[str, str] = {"A": "ROOT/ASM_A/PART_CUBE.1", "B": "ROOT/ASM_B/PART_CUBE.1"}
CANDIDATE_LABEL_KO: dict[str, str] = {"A": "설계안 A (DOC-ASM-A)", "B": "설계안 B (DOC-ASM-B, 두께 축소)"}
DEMO_REVISION = "R1"
DEMO_BASE_DATE = "2026-09-01"
DEMO_CURRENCY = "KRW"
THICKNESS_PARAM = "nominal_thickness_mm"  # CAD snapshot param:nominal_thickness_mm
THICKNESS_FEATURE = "thickness_mm"  # 합성 성능 데이터의 feature
PERFORMANCE_KEY = "insertion_force_n"
EVIDENCE_QUERIES: tuple[str, ...] = ("원가", "중량", "결론")
PAYLOAD_COST_LINES: dict[str, str] = {
    "cost_line_material": "재료비",
    "cost_line_process": "사출가공비",
    "cost_line_tooling": "금형상각",
}
FIXTURE_FILES: dict[str, str] = {
    "cad_a": "cad/asm_a_snapshot.csv",
    "cad_b": "cad/asm_b_snapshot.csv",
    "cost_recipes": "cad/cost_recipes.json",
    "task_regression": "ml/task_regression.yaml",
    "task_classification": "ml/task_classification.yaml",
    "csv_regression": "ml/clip_regression.csv",
    "csv_classification": "ml/clip_classification.csv",
    "template_pptx": "documents/template_review.pptx",
    "template_xlsx": "documents/template_comparison.xlsx",
}
FIXTURE_SUBDIRS: tuple[str, ...] = ("cad", "ml", "documents")
FAST_BUDGET: dict[str, Any] = {
    "wall_time_seconds": 120,
    "max_candidates": 1,
    "max_epochs": 2,
    "patience": 2,
    "max_calls": 0,
    "max_tokens": 0,
    "mode": "custom",
}
OUTBOUND_NOTE = "OutboundGuard 는 프로세스 내 socket API 검사이며 OS 수준 네트워크 격리가 아니다 (TARGET_OFFLINE_TESTED 와 구분)"

ArtifactOrigin = Literal["calculated", "predicted", "synthetic_input", "document", "index", "report", "run"]
Progress = Callable[[str], None]


# --------------------------------------------------------------------------- 모델


class DemoStep(StrictModel):
    name: str
    label_ko: str
    status: Status
    elapsed_seconds: float = 0.0
    detail: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class DemoCheck(StrictModel):
    check_id: str
    status: Status
    message_ko: str = ""
    expected: str | None = None
    actual: str | None = None


class ArtifactRecord(StrictModel):
    path: str  # output_dir 기준 상대 경로 (posix)
    sha256: str
    size: int
    origin: ArtifactOrigin
    step: str
    synthetic: bool = True


class FixtureRecord(StrictModel):
    path: str  # fixtures_dir 기준 상대 경로 (posix)
    sha256_before: str
    sha256_after: str | None = None
    unchanged: bool | None = None


class DemoManifest(StrictModel):
    format: str = DEMO_FORMAT
    schema_version: str = SCHEMA_VERSION
    app_version: str = __version__
    demo_id: str
    created_at: str
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    offline: bool = True
    device: str = "cpu"
    fast: bool = False
    profile: str
    fixtures_dir: str
    output_dir: str
    workspace_dir: str
    environment: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    steps: list[DemoStep] = Field(default_factory=list)
    checks: list[DemoCheck] = Field(default_factory=list)
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    fixtures: list[FixtureRecord] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    outbound: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = True
    data_origin: Literal["synthetic"] = "synthetic"
    all_pass: bool = False
    exit_code: int = EXIT_VALIDATION
    notes: list[str] = Field(default_factory=list)

    def step(self, name: str) -> DemoStep:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    def check(self, check_id: str) -> DemoCheck:
        for c in self.checks:
            if c.check_id == check_id:
                return c
        raise KeyError(check_id)


class _StepAbort(Exception):
    """단계 실패 뒤 남은 단계를 NOT_RUN 으로 표시하기 위한 내부 신호."""


# --------------------------------------------------------------------------- 경로 해석


def _project_fixtures() -> Path | None:
    p = Path(__file__).resolve().parents[1] / "fixtures"
    return p if p.is_dir() else None


def _active_release_fixtures() -> Path | None:
    root = os.environ.get(INSTALL_ROOT_ENV, "").strip()
    if not root:
        return None
    active = Path(root).expanduser() / ACTIVE_NAME
    if not active.is_file():
        return None
    try:
        data = read_json(active)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("release_dir"):
        return None
    p = Path(str(data["release_dir"])) / "fixtures"
    return p if p.is_dir() else None


def missing_fixture_files(fixtures_dir: Path) -> list[str]:
    return [rel for rel in FIXTURE_FILES.values() if not (fixtures_dir / rel).is_file()]


def resolve_fixtures_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """fixture 폴더: --fixtures-dir > DIA_FIXTURES_DIR > 프로젝트 루트 fixtures/ > active release 의 fixtures/ > 현재 폴더 fixtures/."""
    candidates: list[tuple[str, Path | None]] = []
    if explicit:
        candidates.append(("--fixtures-dir", Path(explicit).expanduser()))
    env = os.environ.get(FIXTURES_ENV, "").strip()
    if env:
        candidates.append((FIXTURES_ENV, Path(env).expanduser()))
    candidates.append(("project", _project_fixtures()))
    candidates.append(("active_release", _active_release_fixtures()))
    candidates.append(("cwd", Path.cwd() / "fixtures"))
    tried: list[str] = []
    for source, p in candidates:
        if p is None:
            continue
        resolved = p.resolve()
        if not resolved.is_dir():
            if source in ("--fixtures-dir", FIXTURES_ENV):
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"fixture 폴더가 없습니다 ({source}): {resolved.name}",
                    details={"source": source, "path": str(resolved)},
                )
            tried.append(f"{source}: {resolved}")
            continue
        missing = missing_fixture_files(resolved)
        if missing:
            if source in ("--fixtures-dir", FIXTURES_ENV):
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"fixture 폴더에 필요한 합성 파일이 없습니다 ({source}): {resolved.name}",
                    details={"source": source, "path": str(resolved), "missing": missing},
                )
            tried.append(f"{source}: {resolved} (누락 {len(missing)}개)")
            continue
        return resolved
    raise AgentError(
        "E_INPUT_INVALID",
        "합성 fixture 폴더를 찾을 수 없습니다.",
        hint=f"--fixtures-dir <폴더> 또는 환경변수 {FIXTURES_ENV} 로 release 의 fixtures/ 폴더를 지정하세요.",
        details={"tried": tried, "required": sorted(FIXTURE_FILES.values())},
    )


def _output_roots(cfg: AppConfig) -> list[Path]:
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.outputs, Path.cwd().resolve()]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    return roots


def default_output_dir(cfg: AppConfig) -> Path:
    ws = Workspace.from_config(cfg)
    return ws.outputs / f"demo-{datetime.now(UTC):%Y%m%dT%H%M%S}"


def resolve_output_dir(cfg: AppConfig, explicit: str | os.PathLike[str] | None = None) -> Path:
    """산출물 폴더. 부모가 승인 root(data_root/outputs/현재 폴더/output_root) 안이어야 하며 검사 뒤에만 만든다."""
    p = Path(explicit).expanduser() if explicit else default_output_dir(cfg)
    parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = resolve_within(parent, _output_roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    out = resolved_parent / p.name
    out.mkdir(parents=True, exist_ok=True)
    return out


def demo_config(cfg: AppConfig, out_dir: Path, *, device: str) -> AppConfig:
    """demo 전용 설정: data_root 를 <out_dir>/workspace 로 바꿔 run/상태 DB/색인이 산출물 폴더 안에만 생기게 한다."""
    paths = cfg.paths.model_copy(update={"data_root": str(out_dir / "workspace"), "output_root": None})
    ml = cfg.ml.model_copy(update={"device": device, "num_workers": 0})
    return cfg.model_copy(update={"paths": paths, "ml": ml})


# --------------------------------------------------------------------------- 유틸


def _rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _fixture_snapshot(fixtures_dir: Path) -> list[FixtureRecord]:
    out: list[FixtureRecord] = []
    for sub in FIXTURE_SUBDIRS:
        base = fixtures_dir / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append(FixtureRecord(path=_rel(p, fixtures_dir), sha256_before=sha256_file(p)))
    return out


def _version_of(module: str) -> str | None:
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 - 진단 정보일 뿐
        return None
    return str(getattr(mod, "__version__", "unknown"))


def _environment(cfg: AppConfig, device: str) -> dict[str, Any]:
    """개인 경로/호스트 이름/사용자 이름이 없는 실행 환경 요약."""
    env: dict[str, Any] = {
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "profile": cfg.profile.value,
        "device_requested": device,
        "cpu_count": os.cpu_count(),
        "versions": {
            name: _version_of(mod)
            for name, mod in (
                ("numpy", "numpy"),
                ("pandas", "pandas"),
                ("scikit-learn", "sklearn"),
                ("torch", "torch"),
                ("python-pptx", "pptx"),
                ("openpyxl", "openpyxl"),
                ("pydantic", "pydantic"),
            )
        },
    }
    try:
        torch = importlib.import_module("torch")
        env["cuda_available"] = bool(torch.cuda.is_available())
        env["torch_threads"] = int(torch.get_num_threads())
    except Exception:  # noqa: BLE001
        env["cuda_available"] = None
    return env


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def cuda_available() -> bool | None:
    """torch CUDA 사용 가능 여부. torch 가 없으면 None."""
    try:
        torch = importlib.import_module("torch")
    except Exception:  # noqa: BLE001
        return None
    return bool(torch.cuda.is_available())


def check_device(device: str) -> None:
    """demo 장치 검사. cuda 는 실제로 사용 가능할 때만 허용한다 (산출물 폴더를 만들기 전에 호출)."""
    if device not in ("cpu", "cuda"):
        raise AgentError("E_USAGE", f"--device 는 cpu 또는 cuda 여야 합니다: {device}")
    if device == "cuda" and not cuda_available():
        raise AgentError(
            "E_NOT_SUPPORTED",
            "이 환경에서는 CUDA 를 사용할 수 없어 demo --device cuda 를 실행하지 않습니다 (GPU 시험 NOT_RUN).",
            hint="--device cpu 로 실행하세요. GPU 는 torch CUDA 빌드·드라이버·GPU 조합을 현장에서 확인한 뒤 사용합니다.",
            details={"device": "cuda", "cuda_available": cuda_available(), "status": "NOT_RUN"},
        )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _run_id(task_id: str, ts: str) -> str:
    return f"demo-{task_id}-{ts}"


# --------------------------------------------------------------------------- 실행기


class DemoRunner:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        offline: bool = True,
        device: str = "cpu",
        out_dir: Path,
        fast: bool = False,
        fixtures_dir: Path,
        progress: Progress | None = None,
    ) -> None:
        if not offline:
            raise AgentError(
                "E_NOT_SUPPORTED",
                "demo 는 오프라인 전용입니다 (--offline). 온라인 demo 경로는 없습니다.",
                details={"offline": offline},
            )
        check_device(device)
        self.user_cfg = cfg
        self.device = device
        self.out_dir = out_dir
        self.fast = fast
        self.fixtures_dir = fixtures_dir
        self.progress = progress or (lambda _msg: None)
        self.ts = f"{datetime.now(UTC):%Y%m%dT%H%M%S}"
        self.cfg = demo_config(cfg, out_dir, device=device)
        self.ws = Workspace.from_config(self.cfg)
        self.guard = OutboundGuard((), active=True)
        self.manifest = DemoManifest(
            demo_id=f"demo-{self.ts}",
            created_at=now_iso(),
            offline=True,
            device=device,
            fast=fast,
            profile=cfg.profile.value,
            fixtures_dir=str(fixtures_dir),
            output_dir=str(out_dir),
            workspace_dir=str(self.ws.data_root),
            notes=[
                "모든 입력은 합성(synthetic) fixture 이며 실제 차량·부품·원가·문서를 대표하지 않는다.",
                "합성 데이터로 학습한 모델은 demo 목적으로만 allow_synthetic=True 로 로드했다 (운영 자동 채택 금지).",
                OUTBOUND_NOTE,
            ],
        )
        self._aborted = False
        self._first_error: AgentError | None = None
        # 단계 간 전달값
        self.snapshots: dict[str, Any] = {}
        self.mass: dict[str, Any] = {}
        self.cost: dict[str, Any] = {}
        self.cand_dirs: dict[str, Path] = {}
        self.comparison: Any = None
        self.runs: dict[str, Any] = {}
        self.predictions: dict[str, dict[str, Any]] = {}
        self.scenario: dict[str, Any] = {}
        self.evidence: Any = None
        self.payload_validation: Any = None
        self.doc_manifests: list[Any] = []
        self.validation_report: Any = None

    # ------------------------------------------------------------------ 진입
    def run(self) -> DemoManifest:
        t0 = time.perf_counter()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.ws.ensure()
        self.manifest.fixtures = _fixture_snapshot(self.fixtures_dir)
        self.manifest.environment = _environment(self.cfg, self.device)
        self._check_device()
        steps: list[tuple[str, str, Callable[[], str]]] = [
            ("cad_import", "CAD A/B snapshot 수입", self._step_cad_import),
            ("mass", "중량 계산 (kg = m3 × kg/m3)", self._step_mass),
            ("cost", f"원가 계산 (recipe {COST_RECIPE}, PART_CUBE)", self._step_cost),
            ("compare", "설계안 비교 (mass/cost)", self._step_compare),
            ("train_regression", "합성 회귀 run (삽입력)", self._step_train_regression),
            ("train_classification", "합성 이진분류 run (유지력 합격)", self._step_train_classification),
            ("predict", "export 모델 predict (allow_synthetic=True, demo)", self._step_predict),
            (
                "compare_performance",
                "설계안 비교 갱신 (performance 예측 포함)",
                self._step_compare_performance,
            ),
            ("docs_index", "fixtures/documents 색인", self._step_docs_index),
            ("docs_search", "근거 검색 → evidence.json", self._step_docs_search),
            ("payload", "report_payload.json 조립·재검증", self._step_payload),
            ("docs_generate", "review.pptx / comparison.xlsx 생성·검증", self._step_docs_generate),
        ]
        with self.guard:
            for i, (name, label, fn) in enumerate(steps, start=1):
                self._run_step(i, len(steps), name, label, fn)
        self._finalize(time.perf_counter() - t0)
        return self.manifest

    def _check_device(self) -> None:
        check_device(self.device)

    def _run_step(self, idx: int, total: int, name: str, label: str, fn: Callable[[], str]) -> None:
        if self._aborted:
            self.manifest.steps.append(
                DemoStep(
                    name=name, label_ko=label, status=Status.NOT_RUN, detail="선행 단계 실패로 실행하지 않음"
                )
            )
            self.progress(f"[{idx}/{total}] {label}: NOT_RUN (선행 단계 실패)")
            return
        self.progress(f"[{idx}/{total}] {label} ...")
        t0 = time.perf_counter()
        step = DemoStep(name=name, label_ko=label, status=Status.NOT_RUN)
        self.manifest.steps.append(step)
        try:
            step.detail = fn()
            step.status = Status.PASS
        except AgentError as exc:
            step.status = Status.FAIL
            step.error = exc.to_dict()
            step.detail = exc.message
            self._aborted = True
            self._first_error = self._first_error or exc
        except Exception as exc:  # noqa: BLE001 - 예기치 않은 오류도 단계 FAIL 로 기록한다
            err = AgentError("E_INTERNAL", f"{type(exc).__name__}: {str(exc)[:300]}")
            step.status = Status.FAIL
            step.error = err.to_dict()
            step.detail = err.message
            self._aborted = True
            self._first_error = self._first_error or err
        step.elapsed_seconds = round(time.perf_counter() - t0, 3)
        self.progress(
            f"[{idx}/{total}] {label}: {step.status.value} ({step.elapsed_seconds:.1f}s) {step.detail}"
        )

    # ------------------------------------------------------------------ 산출물 기록
    def _artifact(self, path: Path, origin: ArtifactOrigin, step: str) -> str:
        rel = _rel(path, self.out_dir)
        self.manifest.artifacts[rel] = ArtifactRecord(
            path=rel, sha256=sha256_file(path), size=path.stat().st_size, origin=origin, step=step
        )
        self.manifest.step(step).artifacts.append(rel)
        return rel

    def _fixture(self, key: str) -> Path:
        return self.fixtures_dir / FIXTURE_FILES[key]

    # ------------------------------------------------------------------ (a) CAD → 중량 → 원가 → 비교
    def _step_cad_import(self) -> str:
        from corp_dl_agent.engineering.cad_snapshot import import_snapshot, write_snapshot

        details: list[str] = []
        for cand in ("A", "B"):
            src = self._fixture(f"cad_{cand.lower()}")
            snap = import_snapshot(src, strict=True)
            d = self.out_dir / "candidates" / cand
            d.mkdir(parents=True, exist_ok=True)
            out = write_snapshot(snap, d / "cad_snapshot.json")
            self.snapshots[cand] = snap
            self.cand_dirs[cand] = d
            self._artifact(out, "synthetic_input", "cad_import")
            details.append(
                f"{cand}: {len(snap.rows)}행 revision {','.join(snap.revisions())} ({snap.encoding})"
            )
        return "; ".join(details)

    def _step_mass(self) -> str:
        from corp_dl_agent.engineering.mass import compute_mass

        details: list[str] = []
        for cand in ("A", "B"):
            res = compute_mass(self.snapshots[cand], density_policy=self.cfg.cad.default_density_policy)
            out = self.cand_dirs[cand] / "mass_result.json"
            atomic_write_json(out, res.model_dump(mode="json"))
            self.mass[cand] = res
            self._artifact(out, "calculated", "mass")
            total = res.total.value
            details.append(
                f"{cand}: {total:.6g} kg (complete={res.complete}, 누락 {len(res.missing)}건)"
                if total is not None
                else f"{cand}: MISSING"
            )
        return "; ".join(details)

    def _step_cost(self) -> str:
        from corp_dl_agent.engineering.cost import compute_cost, load_recipes

        recipes = load_recipes(self._fixture("cost_recipes"))
        if COST_RECIPE not in recipes:
            raise AgentError(
                "E_INPUT_INVALID",
                f"recipe '{COST_RECIPE}' 가 없습니다",
                details={"available": sorted(recipes)},
            )
        details: list[str] = []
        for cand in ("A", "B"):
            item = self.mass[cand].item(COST_OCCURRENCE[cand])
            cb = compute_cost(
                item,
                recipes[COST_RECIPE],
                expected_currency=DEMO_CURRENCY,
                expected_base_date=DEMO_BASE_DATE,
            )
            out = self.cand_dirs[cand] / "cost_breakdown.json"
            atomic_write_json(out, cb.model_dump(mode="json"))
            self.cost[cand] = cb
            self._artifact(out, "calculated", "cost")
            uc = cb.unit_cost.value
            details.append(
                f"{cand}: {uc:,.2f} {cb.currency}/unit (complete={cb.complete})"
                if uc is not None
                else f"{cand}: MISSING"
            )
        return "; ".join(details)

    def _write_candidate_meta(self, cand: str, performance: dict[str, Quantity] | None = None) -> Path:
        meta: dict[str, Any] = {
            "label": CANDIDATE_LABEL_KO[cand],
            "revision": DEMO_REVISION,
            "base_date": DEMO_BASE_DATE,
            "currency": DEMO_CURRENCY,
            "synthetic": True,
            "constraints": {},
            "evidence": [],
            "unknowns": [],
            "performance": {k: q.model_dump(mode="json") for k, q in (performance or {}).items()},
        }
        out = self.cand_dirs[cand] / "candidate.json"
        atomic_write_json(out, meta)
        return out

    def _compare(self, step: str, performance: dict[str, dict[str, Quantity]] | None = None) -> str:
        from corp_dl_agent.engineering.compare import compare_designs, load_candidate_dir

        candidates = []
        for cand in ("A", "B"):
            meta = self._write_candidate_meta(cand, (performance or {}).get(cand))
            self._artifact(meta, "calculated", step)
            candidates.append(load_candidate_dir(self.cand_dirs[cand], candidate_id=cand))
        cmp = compare_designs(candidates, require_same_revision_policy=True, baseline_id="A")
        out = self.out_dir / "design_comparison.json"
        atomic_write_json(out, cmp.model_dump(mode="json"))
        self.comparison = cmp
        self._artifact(out, "calculated", step)
        deltas = {d.metric: d for d in cmp.deltas}
        md = deltas["mass_total"].delta.value
        cd = deltas["unit_cost"].delta.value
        parts = [
            f"consistent={cmp.consistent}, revision {cmp.revision}, {cmp.currency}, 기준일 {cmp.base_date}",
            f"Δmass(B-A)={md:+.6g} kg" if md is not None else "Δmass MISSING",
            f"Δcost(B-A)={cd:+,.2f} {DEMO_CURRENCY}/unit" if cd is not None else "Δcost MISSING",
        ]
        perf = deltas.get(f"performance:{PERFORMANCE_KEY}")
        if perf is not None and perf.delta.value is not None:
            parts.append(f"Δ{PERFORMANCE_KEY}(B-A)={perf.delta.value:+.6g} {perf.delta.unit} (predicted)")
        return ", ".join(parts)

    def _step_compare(self) -> str:
        return self._compare("compare")

    # ------------------------------------------------------------------ (b) 학습 run
    def _train(self, kind: str, task_key: str) -> str:
        from corp_dl_agent.ml.runner import run_task
        from corp_dl_agent.ml.taskspec import load_taskspec

        spec = load_taskspec(self._fixture(task_key))
        if spec.data_origin != "synthetic":
            raise AgentError(
                "E_INPUT_INVALID",
                f"demo TaskSpec 은 data_origin=synthetic 이어야 합니다: {spec.task_id}",
                details={"data_origin": spec.data_origin},
            )
        summary = run_task(
            spec,
            self.cfg,
            self.ws,
            run_id=_run_id(spec.task_id, self.ts),
            budget_override=dict(FAST_BUDGET) if self.fast else None,
        )
        self.runs[kind] = summary
        run_dir = Path(summary.run_dir)
        self.manifest.budget[kind] = dict(summary.budget)
        for name in ("summary.json", "final_evaluation.json", "model_card.json", "split_manifest.json"):
            p = run_dir / name
            if p.is_file():
                self._artifact(p, "run", f"train_{kind}")
        export_manifest = run_dir / "export" / "manifest.json"
        if export_manifest.is_file():
            self._artifact(export_manifest, "run", f"train_{kind}")
        if summary.status != "COMPLETED":
            raise AgentError(
                "E_BUDGET_EXCEEDED" if summary.status == "BUDGET_EXCEEDED" else "E_INTERNAL",
                f"{kind} run 이 완료되지 않았습니다: {summary.status}",
                details={"run_id": summary.run_id, "status": summary.status, "message": summary.message},
            )
        if not summary.synthetic:
            raise AgentError(
                "E_ARTIFACT_SYNTHETIC",
                f"{kind} run 산출물에 synthetic 표시가 없습니다",
                details={"run_id": summary.run_id},
            )
        metric = spec.metric
        fe = summary.final_evaluation.get(metric)
        fe_s = f"{fe:.6g}" if _finite(fe) else "없음"
        return (
            f"run {summary.run_id}: {summary.status}, 선택 {summary.selected} ({summary.selected_kind}), "
            f"test {metric}={fe_s}, acceptance={summary.acceptance.get('state')}, "
            f"예산 {summary.budget.get('elapsed_seconds')}s/{summary.budget.get('wall_time_seconds')}s"
        )

    def _step_train_regression(self) -> str:
        return self._train("regression", "task_regression")

    def _step_train_classification(self) -> str:
        return self._train("classification", "task_classification")

    # ------------------------------------------------------------------ (c) predict
    def _export_dir(self, kind: str) -> Path:
        d = Path(self.runs[kind].run_dir) / "export"
        if not (d / "manifest.json").is_file():
            raise AgentError(
                "E_ARTIFACT_UNTRUSTED",
                f"{kind} run 의 export 번들이 없습니다",
                details={"export_dir": str(d)},
            )
        return d

    def _predict(self, kind: str, input_csv: Path, output_name: str, step: str) -> dict[str, Any]:
        from corp_dl_agent.ml.predict import predict_cli

        result = predict_cli(
            self._export_dir(kind),
            input_csv,
            self.out_dir / output_name,
            self.cfg,
            allow_synthetic=True,  # demo 목적: 합성 모델을 명시적으로 허용 (운영 자동 채택 아님)
            extra_roots=[self.out_dir, self.fixtures_dir],
        )
        result["demo_purpose"] = "합성 demo 예측 (allow_synthetic=True 명시). 운영 판단에 사용하지 않는다."
        atomic_write_json(Path(result["sidecar"]), {k: v for k, v in result.items() if k != "sidecar"})
        self._artifact(Path(result["output_csv"]), "predicted", step)
        self._artifact(Path(result["sidecar"]), "predicted", step)
        self.predictions[output_name] = result
        return result

    def _thickness_from_cad(self, cand: str) -> tuple[float, str]:
        occ = COST_OCCURRENCE[cand]
        row = next((r for r in self.snapshots[cand].rows if r.occurrence_path == occ), None)
        if row is None:
            raise AgentError("E_INPUT_INVALID", f"snapshot 에 {occ} 가 없습니다")
        raw = row.parameter_map.get(THICKNESS_PARAM)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise AgentError(
                "E_MISSING_VALUE",
                f"{occ} 에 param:{THICKNESS_PARAM} 이 없어 설계안 scenario 예측 입력을 만들 수 없습니다 (값을 만들지 않음)",
                details={"candidate": cand, "occurrence_path": occ},
            )
        return float(raw), f"{self.snapshots[cand].source_path}#{occ}/param:{THICKNESS_PARAM}"

    def _build_scenarios(self) -> Path:
        """설계안 A/B scenario 입력: 두께는 CAD 파라미터, 나머지 feature 는 회귀 run 학습 표본의 중앙값/최빈값 (명시 가정)."""
        from corp_dl_agent.ml.data import read_csv_text
        from corp_dl_agent.ml.runner import load_run_taskspec

        pd = importlib.import_module("pandas")
        summary = self.runs["regression"]
        run_dir = Path(summary.run_dir)
        spec = load_run_taskspec(run_dir)
        raw_df, _enc, _tried, data_sha = read_csv_text(self._fixture("csv_regression"))
        split = read_json(run_dir / "split_manifest.json")
        train_ids = {str(x) for x in split["ids"]["train"]}
        train = raw_df[raw_df[spec.id_column].astype(str).isin(train_ids)]
        if len(train) == 0:
            raise AgentError("E_INPUT_INVALID", "학습 표본을 찾을 수 없습니다 (split_manifest ids.train)")
        medians: dict[str, float] = {}
        for col in spec.numeric_features:
            if col == THICKNESS_FEATURE:
                continue
            medians[col] = float(pd.to_numeric(train[col]).median())
        modes: dict[str, str] = {}
        for col in spec.categorical_features:
            modes[col] = str(train[col].mode().iloc[0])
        rows: list[dict[str, Any]] = []
        thickness: dict[str, tuple[float, str]] = {}
        for cand in ("A", "B"):
            thk, locator = self._thickness_from_cad(cand)
            thickness[cand] = (thk, locator)
            row: dict[str, Any] = {spec.id_column: f"DESIGN_{cand}"}
            for col in spec.numeric_features:
                row[col] = thk if col == THICKNESS_FEATURE else medians[col]
            for col in spec.categorical_features:
                row[col] = modes[col]
            rows.append(row)
        columns = [spec.id_column, *spec.numeric_features, *spec.categorical_features]
        out = self.out_dir / "design_scenarios.csv"
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        self.scenario = {
            "thickness": {c: {"value_mm": v, "source_locator": loc} for c, (v, loc) in thickness.items()},
            "other_numeric_features_median_of_train": medians,
            "categorical_features_mode_of_train": modes,
            "n_train_rows": int(len(train)),
            "train_data_sha256": data_sha,
            "assumption": (
                f"scenario 가정: {THICKNESS_FEATURE} 는 CAD param:{THICKNESS_PARAM}(설계안별) 을 그대로 사용하고, "
                "나머지 숫자 feature 는 회귀 run 학습 표본의 중앙값, 범주 feature 는 최빈값을 사용했다. "
                "실측/해석 입력이 아니며 값을 새로 만들지 않았다 (assumption=True)."
            ),
            "target_unit": spec.units.get(spec.target, ""),
            "target": spec.target,
        }
        self._artifact(out, "synthetic_input", "predict")
        return out

    def _step_predict(self) -> str:
        details: list[str] = []
        reg = self._predict(
            "regression", self._fixture("csv_regression"), "performance_predictions.csv", "predict"
        )
        details.append(
            f"회귀 {reg['n_rows']}행 (외삽 {reg['n_out_of_train_range']}행, 모델 {reg['model_kind']}/{reg['model_name']})"
        )
        cls = self._predict(
            "classification", self._fixture("csv_classification"), "retention_predictions.csv", "predict"
        )
        details.append(
            f"분류 {cls['n_rows']}행 (외삽 {cls['n_out_of_train_range']}행, threshold {cls['threshold']})"
        )
        scen_csv = self._build_scenarios()
        scen = self._predict("regression", scen_csv, "design_scenario_predictions.csv", "predict")
        preds = {r["id"]: r for r in _read_csv_rows(Path(scen["output_csv"]))}
        for cand in ("A", "B"):
            rec = preds.get(f"DESIGN_{cand}")
            if rec is None:
                raise AgentError("E_INTERNAL", f"scenario 예측 결과에 DESIGN_{cand} 행이 없습니다")
            self.scenario.setdefault("predictions", {})[cand] = {
                "prediction": float(rec["prediction"]),
                "in_train_range": str(rec.get("in_train_range", "")).lower() == "true",
            }
        a = self.scenario["predictions"]["A"]["prediction"]
        b = self.scenario["predictions"]["B"]["prediction"]
        unit = self.scenario["target_unit"]
        details.append(
            f"설계안 scenario 예측 {PERFORMANCE_KEY}: A {a:.6g} {unit}, B {b:.6g} {unit} (assumption)"
        )
        return "; ".join(details)

    def _performance_quantity(self, cand: str) -> Quantity:
        p = self.scenario["predictions"][cand]
        thk = self.scenario["thickness"][cand]
        return Quantity(
            value=float(p["prediction"]),
            unit=str(self.scenario["target_unit"]),
            value_type="predicted",
            model_run_id=self.runs["regression"].run_id,
            assumption=True,
            notes=(
                f"scenario 예측 (합성 모델, demo): {THICKNESS_FEATURE}={thk['value_mm']:g} mm "
                f"({thk['source_locator']}), 나머지 feature 는 학습 표본 중앙값/최빈값 가정, "
                f"in_train_range={p['in_train_range']}"
            ),
        )

    def _step_compare_performance(self) -> str:
        perf = {c: {PERFORMANCE_KEY: self._performance_quantity(c)} for c in ("A", "B")}
        return self._compare("compare_performance", perf)

    # ------------------------------------------------------------------ (d) 색인 → 근거
    def _step_docs_index(self) -> str:
        from corp_dl_agent.documents.index import DocumentIndex

        db_path = self.ws.index_db
        roots = [self.fixtures_dir / "documents"]
        with DocumentIndex(db_path) as index:
            report = index.index_roots(
                roots,
                include_hidden_flag=True,
                include_notes=self.cfg.documents.include_notes,
                max_file_mb=self.cfg.documents.max_index_file_mb,
                prune=True,
                forbid_links=self.cfg.security.forbid_symlinks,
            )
            stats = index.stats()
        out = self.out_dir / "index_report.json"
        atomic_write_json(out, {"report": report.model_dump(mode="json"), "stats": stats})
        self._artifact(out, "index", "docs_index")
        if report.errors:
            raise AgentError(
                "E_DOC_UNSUPPORTED",
                f"색인 오류 {len(report.errors)}건",
                details={"errors": report.errors[:10]},
            )
        return (
            f"문서 {stats['n_documents']}개, fragment {stats['n_fragments']}개 (숨김 {stats['n_hidden_fragments']}개 검색 제외), "
            f"승인 변환 필요 {len(report.needs_conversion)}개"
        )

    def _step_docs_search(self) -> str:
        from corp_dl_agent.documents.evidence import build_evidence, write_evidence
        from corp_dl_agent.documents.index import DocumentIndex
        from corp_dl_agent.documents.search import search

        hits = []
        with DocumentIndex(self.ws.index_db) as index:
            for q in EVIDENCE_QUERIES:
                hits.extend(search(index, q, limit=10, include_hidden=False))
        ev = build_evidence(
            hits,
            purpose="demo: 합성 설계안 A/B 비교 검토 근거 (synthetic fixtures)",
            query=" | ".join(EVIDENCE_QUERIES),
            allow_hidden=False,
        )
        out = write_evidence(ev, self.out_dir / "evidence.json")
        self.evidence = ev
        self._artifact(out, "document", "docs_search")
        if ev.n_items == 0:
            raise AgentError(
                "E_INPUT_INVALID",
                "근거 검색 결과가 없습니다 (fixtures/documents 색인 확인)",
                details={"queries": list(EVIDENCE_QUERIES)},
            )
        return f"검색어 {len(EVIDENCE_QUERIES)}개 → 근거 {ev.n_items}건 (fact {ev.n_fact}, form_reference {ev.n_form_reference}, 숨김 제외 {ev.excluded_hidden})"

    # ------------------------------------------------------------------ (e) payload
    def _pass_rate_quantity(self) -> Quantity:
        rows = _read_csv_rows(self.out_dir / "retention_predictions.csv")
        run_id = self.runs["classification"].run_id
        if not rows or "label" not in rows[0]:
            return Quantity.missing("%", "분류 예측 결과에 label 열이 없어 합격률을 만들지 않음")
        n_pass = sum(1 for r in rows if str(r["label"]).strip() in ("1", "1.0", "True", "true"))
        return Quantity(
            value=100.0 * n_pass / len(rows),
            unit="%",
            value_type="predicted",
            model_run_id=run_id,
            notes=f"분류 모델 예측 label 기준 합격 비율 ({n_pass}/{len(rows)}행, 합성 표본 전체, threshold {self.runs['classification'].threshold})",
        )

    def _build_payload(self) -> Any:
        from corp_dl_agent.documents.payload import (
            PayloadCheck,
            PayloadColumn,
            PayloadItem,
            PayloadRow,
            PayloadTable,
            PayloadText,
            ReportPayload,
        )
        from corp_dl_agent.documents.synthetic import OLD_COST_TEXT, OLD_DATE, OLD_VEHICLE

        cmp = self.comparison
        deltas = {d.metric: d for d in cmp.deltas}
        cost_b = self.cost["B"]
        cur = cost_b.currency

        def item(key: str, label: str, q: Quantity) -> PayloadItem:
            return PayloadItem(key=key, label_ko=label, quantity=q)

        items: dict[str, PayloadItem] = {
            "mass_a": item("mass_a", "중량 A (계산 가능 항목 합계)", self.mass["A"].total),
            "mass_b": item("mass_b", "중량 B (계산 가능 항목 합계)", self.mass["B"].total),
            "mass_delta": item("mass_delta", "중량 차이(B-A)", deltas["mass_total"].delta),
            "cost_a": item(
                "cost_a", f"단위 원가 A (PART_CUBE, {cost_b.estimate_type})", self.cost["A"].unit_cost
            ),
            "cost_b": item("cost_b", f"단위 원가 B (PART_CUBE, {cost_b.estimate_type})", cost_b.unit_cost),
            "cost_delta": item("cost_delta", "단위 원가 차이(B-A)", deltas["unit_cost"].delta),
        }
        for key, line_name in PAYLOAD_COST_LINES.items():
            items[key] = item(key, f"{line_name}(B)", cost_b.line(line_name).amount)
        subtotal_terms = list(PAYLOAD_COST_LINES)
        subtotal_vals = [items[k].quantity.value for k in subtotal_terms]
        if all(v is not None for v in subtotal_vals):
            subtotal = Quantity(
                value=sum(float(v) for v in subtotal_vals if v is not None),
                unit=f"{cur}/unit",
                value_type="calculated",
                calculation_id="cost:subtotal:material+process+tooling:B",
                calculation_version=cost_b.calculation_version,
                notes="재료비+사출가공비+금형상각 소계 (template 의 합계 수식과 대응). 전체 단위 원가는 cost_b",
            )
        else:
            subtotal = Quantity.missing(f"{cur}/unit", "원가 line 누락")
        items["cost_total"] = item(
            "cost_total", "주요 원가 항목 소계(B: 재료비+사출가공비+금형상각)", subtotal
        )
        items["insertion_force_a"] = item(
            "insertion_force_a", "예측 삽입력 A (scenario)", self._performance_quantity("A")
        )
        items["insertion_force_b"] = item(
            "insertion_force_b", "예측 삽입력 B (scenario)", self._performance_quantity("B")
        )
        items["insertion_force_delta"] = item(
            "insertion_force_delta", "삽입력 차이(B-A)", deltas[f"performance:{PERFORMANCE_KEY}"].delta
        )
        items["retention_pass_rate"] = item(
            "retention_pass_rate", "예측 유지력 합격률 (합성 표본)", self._pass_rate_quantity()
        )

        amount_col = PayloadColumn(key="amount", label_ko="금액", unit=f"{cur}/unit")
        tables = {
            "cost_lines": PayloadTable(
                name="cost_lines",
                label_ko="주요 원가 항목(B안)",
                columns=[amount_col],
                rows=[
                    PayloadRow(label_ko=f"{line}", cells={"amount": items[key].quantity})
                    for key, line in PAYLOAD_COST_LINES.items()
                ],
            ),
            "cost_lines_all": PayloadTable(
                name="cost_lines_all",
                label_ko="원가 항목 전체(B안)",
                columns=[amount_col],
                rows=[PayloadRow(label_ko=ln.name, cells={"amount": ln.amount}) for ln in cost_b.lines],
            ),
        }
        md = deltas["mass_total"].delta.value
        cd = deltas["unit_cost"].delta.value
        conclusion = (
            f"B안 중량 {md:+.4g} kg, 단위 원가 {cd:+,.2f} {cur}/unit (A 대비; 합성 자료 계산값, 운영 판단 아님)"
            if md is not None and cd is not None
            else "중량/원가 차이 MISSING (합성 자료)"
        )
        ev = self.evidence
        texts = {
            "vehicle": PayloadText(
                key="vehicle",
                label_ko="대상",
                text="합성 설계안 DOC-ASM-A / DOC-ASM-B (synthetic)",
                origin="source",
                source_locator="asm_a_snapshot.csv#document_id, asm_b_snapshot.csv#document_id",
            ),
            "base_date": PayloadText(
                key="base_date",
                label_ko="기준일",
                text=DEMO_BASE_DATE,
                origin="source",
                source_locator=f"cost_recipes.json#{COST_RECIPE}.base_date",
            ),
            "conclusion": PayloadText(
                key="conclusion", label_ko="결론", text=conclusion, origin="calculated"
            ),
            "evidence_note": PayloadText(
                key="evidence_note",
                label_ko="근거 메모",
                text=f"evidence.json {ev.n_items}건 (fact {ev.n_fact}, form_reference {ev.n_form_reference}; 과거 합성 문서)",
                origin="calculated",
            ),
        }
        checks = [
            PayloadCheck(
                check_id="cost_total_sum",
                kind="sum",
                target="cost_total",
                terms=subtotal_terms,
                description_ko="주요 원가 항목 소계 = 재료비+사출가공비+금형상각",
            ),
            PayloadCheck(
                check_id="cost_total_table_sum",
                kind="sum",
                target="cost_total",
                table="cost_lines",
                column="amount",
                description_ko="주요 원가 항목 소계 = 표 합",
            ),
            PayloadCheck(
                check_id="cost_b_all_lines_sum",
                kind="sum",
                target="cost_b",
                table="cost_lines_all",
                column="amount",
                description_ko="단위 원가 B = 전체 원가 line 합",
            ),
            PayloadCheck(
                check_id="mass_delta_diff", kind="difference", target="mass_delta", terms=["mass_b", "mass_a"]
            ),
            PayloadCheck(
                check_id="cost_delta_diff", kind="difference", target="cost_delta", terms=["cost_b", "cost_a"]
            ),
            PayloadCheck(
                check_id="insertion_force_delta_diff",
                kind="difference",
                target="insertion_force_delta",
                terms=["insertion_force_b", "insertion_force_a"],
            ),
        ]
        return ReportPayload(
            report_id=f"RPT-{self.manifest.demo_id.upper()}",
            created_at=now_iso(),
            subject="합성 설계안 A/B 비교 검토 (demo, synthetic)",
            items=items,
            tables=tables,
            texts=texts,
            checks=checks,
            stale_values=[OLD_VEHICLE, OLD_DATE, OLD_COST_TEXT],
            synthetic=True,
            data_origin="synthetic",
            calculation_version=cost_b.calculation_version,
            notes=[
                "모든 수치는 합성 fixture 에서 계산/예측한 값이며 실제 차량·원가·시험을 대표하지 않는다.",
                str(self.scenario.get("assumption", "")),
                "cost_total 은 template 의 합계 수식(재료비+사출가공비+금형상각)과 대응하는 소계이며, 전체 단위 원가는 cost_a/cost_b 이다.",
                "합성 모델 예측은 demo 목적으로만 allow_synthetic=True 로 로드했다 (운영 자동 채택 금지).",
            ],
        )

    def _step_payload(self) -> str:
        from corp_dl_agent.documents.payload import validate_payload, write_payload

        payload = self._build_payload()
        pv = validate_payload(payload)
        self.payload_validation = pv
        out = write_payload(pv.payload, self.out_dir / "report_payload.json")
        self._artifact(out, "report", "payload")
        if not pv.passed:
            raise AgentError(
                "E_DOC_PAYLOAD_MISMATCH",
                f"report_payload 재검증 실패 (CONFLICT {pv.n_conflict}건, 출처 문제 {len(pv.provenance_issues)}건)",
                details={
                    "checks": [c.model_dump(mode="json") for c in pv.checks if c.status is not Status.PASS],
                    "provenance_issues": pv.provenance_issues,
                },
            )
        return f"항목 {pv.n_items}개 (MISSING {pv.n_missing}, CONFLICT {pv.n_conflict}), 계산 엔진 check {len(pv.checks)}건 통과"

    # ------------------------------------------------------------------ (f) 문서 생성
    def _step_docs_generate(self) -> str:
        from corp_dl_agent.documents.templates import (
            GenerationManifest,
            fill_pptx,
            fill_xlsx,
            write_generation_manifest,
        )
        from corp_dl_agent.documents.validation import validate_documents, write_validation_report

        pv = self.payload_validation
        validated = pv.payload
        pptx_out = self.out_dir / "review.pptx"
        xlsx_out = self.out_dir / "comparison.xlsx"
        manifests = [
            fill_pptx(
                self._fixture("template_pptx"),
                validated,
                pptx_out,
                include_notes=self.cfg.documents.include_notes,
            ),
            fill_xlsx(self._fixture("template_xlsx"), validated, xlsx_out, allow_lossy=False),
        ]
        self.doc_manifests = manifests
        self._artifact(pptx_out, "document", "docs_generate")
        self._artifact(xlsx_out, "document", "docs_generate")
        payload_path = self.out_dir / "report_payload.json"
        evidence_path = self.out_dir / "evidence.json"
        gen = GenerationManifest(
            created_at=now_iso(),
            payload_report_id=validated.report_id,
            payload_path=str(payload_path),
            payload_hash=sha256_file(payload_path),
            evidence_path=str(evidence_path),
            evidence_hash=sha256_file(evidence_path),
            documents=manifests,
            synthetic=validated.synthetic,
            notes=[
                "수식 재계산/렌더링은 수행하지 않았습니다 (RECALC_NOT_RUN / RENDER_NOT_RUN).",
                f"renderer={self.cfg.documents.renderer}, recalc_engine={self.cfg.documents.recalc_engine}",
                "demo: 같은 report_payload.json 으로 PPTX/XLSX 를 생성했다 (synthetic).",
            ],
        )
        gm = write_generation_manifest(gen, self.out_dir / "document_manifest.json")
        self._artifact(gm, "report", "docs_generate")
        report = validate_documents(
            pptx_out,
            xlsx_out,
            validated,
            manifests=manifests,
            payload_validation=pv,
            renderer=self.cfg.documents.renderer,
            recalc_engine=self.cfg.documents.recalc_engine,
        )
        self.validation_report = report
        vr = write_validation_report(report, self.out_dir / "validation_report.json")
        self._artifact(vr, "report", "docs_generate")
        if not report.passed:
            fails = [
                f"{d.document_type}:{c.check_id}: {c.message_ko}"
                for d in report.documents
                for c in d.checks
                if c.status is Status.FAIL
            ]
            raise AgentError(
                "E_DOC_PAYLOAD_MISMATCH",
                f"문서 검증 실패 {len(fails)}건",
                details={"failures": fails[:20]},
            )
        parts = []
        for m, d in zip(manifests, report.documents, strict=True):
            parts.append(
                f"{m.document_type}: 변경 {len(m.changed_locators)}곳, MISSING {len(m.missing_keys)}, "
                f"검증 PASS {d.n_pass}/FAIL {d.n_fail}/NOT_RUN {d.n_not_run}/PARTIAL {d.n_partial}, "
                f"템플릿 hash 불변 {m.template_hash_unchanged}, {m.recalc_status}/{m.render_status}"
            )
        return "; ".join(parts)

    # ------------------------------------------------------------------ (g) 마무리
    def _add_check(
        self,
        check_id: str,
        ok: bool,
        message_ko: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.manifest.checks.append(
            DemoCheck(
                check_id=check_id,
                status=Status.PASS if ok else Status.FAIL,
                message_ko=message_ko,
                expected=expected,
                actual=actual,
            )
        )

    def _not_run(self, check_id: str, message_ko: str) -> None:
        self.manifest.checks.append(
            DemoCheck(check_id=check_id, status=Status.NOT_RUN, message_ko=message_ko)
        )

    def _finalize(self, elapsed: float) -> None:
        m = self.manifest
        # 원본 fixture 불변
        changed: list[str] = []
        for rec in m.fixtures:
            p = self.fixtures_dir / rec.path
            rec.sha256_after = sha256_file(p) if p.is_file() else None
            rec.unchanged = rec.sha256_after == rec.sha256_before
            if not rec.unchanged:
                changed.append(rec.path)
        self._add_check(
            "fixtures_unchanged",
            not changed and bool(m.fixtures),
            "원본 fixture 파일 hash 가 실행 전후 동일"
            if not changed
            else f"원본 fixture 변경 감지: {changed[:5]}",
            expected="unchanged",
            actual="unchanged" if not changed else ", ".join(changed[:5]),
        )
        # 단계
        failed_steps = [s.name for s in m.steps if s.status is not Status.PASS]
        self._add_check(
            "steps_all_pass",
            not failed_steps,
            "모든 단계 PASS" if not failed_steps else f"PASS 아닌 단계: {failed_steps}",
        )
        # 검산값 (fixtures/cad/README.md)
        for cand in ("A", "B"):
            res = self.mass.get(cand)
            if res is None:
                self._not_run(f"mass_expected_{cand}", "mass 단계 미완료")
                continue
            val = res.total.value
            ok = val is not None and math.isclose(val, EXPECTED_MASS_KG[cand], rel_tol=1e-9, abs_tol=1e-9)
            self._add_check(
                f"mass_expected_{cand}",
                ok,
                f"설계안 {cand} 계산 가능 질량 합계 == fixture README 검산값",
                expected=f"{EXPECTED_MASS_KG[cand]} kg",
                actual=f"{val} kg",
            )
        res_a = self.mass.get("A")
        if res_a is not None:
            cube = res_a.item(COST_OCCURRENCE["A"]).unit_mass.value
            self._add_check(
                "unit_mass_1kg",
                cube is not None and math.isclose(cube, EXPECTED_CUBE_UNIT_MASS_KG, rel_tol=1e-12),
                "1,000,000 mm3 × 1.0 g/cm3 = 1.0 kg",
                expected="1.0 kg",
                actual=f"{cube} kg",
            )
        else:
            self._not_run("unit_mass_1kg", "mass 단계 미완료")
        cmp = self.comparison
        if cmp is not None:
            self._add_check(
                "comparison_consistent",
                bool(cmp.consistent)
                and cmp.revision == DEMO_REVISION
                and cmp.currency == DEMO_CURRENCY
                and cmp.base_date == DEMO_BASE_DATE
                and bool(cmp.synthetic),
                "설계안 비교 조건 일치 (revision/기준일/통화) 및 synthetic 표시",
                expected=f"{DEMO_REVISION}/{DEMO_BASE_DATE}/{DEMO_CURRENCY}/consistent",
                actual=f"{cmp.revision}/{cmp.base_date}/{cmp.currency}/{'consistent' if cmp.consistent else 'inconsistent'}",
            )
        else:
            self._not_run("comparison_consistent", "compare 단계 미완료")
        # run
        for kind in ("regression", "classification"):
            s = self.runs.get(kind)
            if s is None:
                self._not_run(f"run_{kind}", "학습 run 미완료")
                continue
            metric = s.task.get("metric")
            fe = s.final_evaluation.get(metric) if metric else None
            self._add_check(
                f"run_{kind}",
                s.status == "COMPLETED" and bool(s.synthetic) and _finite(fe) and bool(s.selected),
                f"{kind} run COMPLETED, synthetic 표시, test {metric} finite, 모델 선택됨",
                expected="COMPLETED/synthetic/finite",
                actual=f"{s.status}/{'synthetic' if s.synthetic else 'not-synthetic'}/{fe}",
            )
        # predict
        if self.predictions:
            bad = [
                name
                for name, r in self.predictions.items()
                if r.get("value_type") != "predicted"
                or not r.get("synthetic")
                or int(r.get("n_rows", 0)) <= 0
            ]
            self._add_check(
                "predictions_marked_predicted",
                not bad,
                "predict 산출물 sidecar 에 value_type=predicted, synthetic=true, 행 수 > 0",
                actual=", ".join(bad) if bad else "ok",
            )
        else:
            self._not_run("predictions_marked_predicted", "predict 단계 미완료")
        # payload
        pv = self.payload_validation
        if pv is not None:
            p = pv.payload
            predicted = [k for k, it in p.items.items() if it.quantity.value_type == "predicted"]
            no_run_id = [k for k in predicted if not p.items[k].quantity.model_run_id]
            self._add_check(
                "payload_validation",
                bool(pv.passed) and pv.n_conflict == 0,
                f"payload 계산 엔진 재검증 (check {len(pv.checks)}건, CONFLICT {pv.n_conflict}, 출처 문제 {len(pv.provenance_issues)})",
            )
            self._add_check(
                "payload_synthetic_and_provenance",
                bool(p.synthetic) and p.data_origin == "synthetic" and not no_run_id and bool(predicted),
                "payload synthetic=true, data_origin=synthetic, predicted 항목마다 model_run_id 표시",
                actual=f"predicted={predicted}, model_run_id 누락={no_run_id}",
            )
        else:
            self._not_run("payload_validation", "payload 단계 미완료")
            self._not_run("payload_synthetic_and_provenance", "payload 단계 미완료")
        # 문서
        vr = self.validation_report
        if vr is not None:
            self._add_check(
                "documents_numbers_match",
                bool(vr.passed) and all(d.n_fail == 0 for d in vr.documents),
                "문서에서 읽은 수치 == payload, 구조 보존, placeholder 잔존 없음",
                actual="; ".join(f"{d.document_type}: FAIL {d.n_fail}" for d in vr.documents),
            )
            stale_ok = all(
                c.status is Status.PASS
                for d in vr.documents
                for c in d.checks
                if c.check_id.endswith(".stale_values")
            )
            self._add_check(
                "documents_no_stale_values",
                stale_ok,
                "과거 차종/날짜/원가 값 잔존 없음",
                expected="none",
                actual="none" if stale_ok else "stale",
            )
            self._add_check(
                "templates_unchanged",
                all(dm.template_hash_unchanged for dm in self.doc_manifests) and bool(self.doc_manifests),
                "템플릿 원본 hash 불변",
            )
            self._add_check(
                "documents_engine_status_explicit",
                vr.recalc_status == "RECALC_NOT_RUN" and vr.render_status == "RENDER_NOT_RUN",
                "수식 재계산/렌더링 미수행 상태를 명시 (RECALC_NOT_RUN / RENDER_NOT_RUN)",
                actual=f"{vr.recalc_status}/{vr.render_status}",
            )
        else:
            for cid in (
                "documents_numbers_match",
                "documents_no_stale_values",
                "templates_unchanged",
                "documents_engine_status_explicit",
            ):
                self._not_run(cid, "docs_generate 단계 미완료")
        # outbound
        m.outbound = {
            **self.guard.describe(),
            "blocked_attempts": len(self.guard.blocked_attempts),
            "attempts": [
                {"host": a.host, "port": a.port, "api": a.api, "at": a.at}
                for a in self.guard.blocked_attempts
            ],
        }
        self._add_check(
            "outbound_attempts_zero",
            len(self.guard.blocked_attempts) == 0,
            "demo 실행 중 외부 연결 시도 0회 (프로세스 내 socket guard 기준)",
            expected="0",
            actual=str(len(self.guard.blocked_attempts)),
        )
        # provenance / 요약
        m.provenance = {
            "synthetic": True,
            "data_origin": "synthetic",
            "model_run_ids": {k: s.run_id for k, s in self.runs.items()},
            "selected_models": {
                k: {"name": s.selected, "kind": s.selected_kind} for k, s in self.runs.items()
            },
            "final_evaluation": {k: dict(s.final_evaluation) for k, s in self.runs.items()},
            "acceptance": {k: dict(s.acceptance) for k, s in self.runs.items()},
            "predicted_payload_items": [
                k for k, it in pv.payload.items.items() if it.quantity.value_type == "predicted"
            ]
            if pv is not None
            else [],
            "scenario": {k: v for k, v in self.scenario.items() if k != "predictions"},
            "scenario_predictions": self.scenario.get("predictions", {}),
            "cost_recipe": COST_RECIPE,
            "cost_occurrence": COST_OCCURRENCE,
            "evidence_items": self.evidence.n_items if self.evidence is not None else 0,
        }
        m.budget["mode"] = "fast" if self.fast else "demo"
        m.finished_at = now_iso()
        m.elapsed_seconds = round(elapsed, 3)
        m.all_pass = all(c.status is Status.PASS for c in m.checks) and all(
            s.status is Status.PASS for s in m.steps
        )
        if m.all_pass:
            m.exit_code = EXIT_OK
        elif self._first_error is not None:
            m.exit_code = self._first_error.exit_code
        else:
            m.exit_code = EXIT_VALIDATION
        out = self.out_dir / DEMO_MANIFEST_NAME
        atomic_write_json(out, m.model_dump(mode="json"))
        self.progress(f"demo_manifest 저장: {out} (all_pass={m.all_pass}, {m.elapsed_seconds:.1f}s)")


def run_demo(
    cfg: AppConfig,
    *,
    offline: bool = True,
    device: str = "cpu",
    out_dir: str | os.PathLike[str] | None = None,
    fast: bool = False,
    fixtures_dir: str | os.PathLike[str] | None = None,
    progress: Progress | None = None,
) -> DemoManifest:
    """demo 전체 실행. 반환 DemoManifest (all_pass / exit_code 포함). 산출물은 out_dir 아래에만 쓴다."""
    check_device(device)  # 산출물 폴더를 만들기 전에 장치를 검사한다
    fixtures = resolve_fixtures_dir(fixtures_dir)
    out = resolve_output_dir(cfg, out_dir)
    runner = DemoRunner(
        cfg, offline=offline, device=device, out_dir=out, fast=fast, fixtures_dir=fixtures, progress=progress
    )
    return runner.run()


def format_demo_ko(m: DemoManifest) -> str:
    lines = [
        f"demo {m.demo_id}: {'PASS' if m.all_pass else 'FAIL'} ({m.elapsed_seconds:.1f}s, device {m.device}, {'fast' if m.fast else 'demo 예산'}, synthetic)",
        f"  산출물 폴더: {m.output_dir}",
        f"  fixture 폴더(읽기 전용): {m.fixtures_dir}",
        "  단계:",
    ]
    for s in m.steps:
        lines.append(f"    {s.status.value:8s} {s.label_ko} ({s.elapsed_seconds:.1f}s) {s.detail}")
    lines.append("  검사:")
    for c in m.checks:
        extra = f" [기대 {c.expected} / 실제 {c.actual}]" if c.status is not Status.PASS and c.actual else ""
        lines.append(f"    {c.status.value:8s} {c.check_id}: {c.message_ko}{extra}")
    lines.append(
        f"  outbound 시도: {m.outbound.get('blocked_attempts', 0)}회 (guard active={m.outbound.get('active')}, {m.outbound.get('kind')})"
    )
    lines.append(f"  산출물 {len(m.artifacts)}개 (sha256 은 {DEMO_MANIFEST_NAME} 참조)")
    for n in m.notes:
        lines.append(f"  참고: {n}")
    return "\n".join(lines)


__all__ = [
    "DEMO_MANIFEST_NAME",
    "check_device",
    "ArtifactRecord",
    "DemoCheck",
    "DemoManifest",
    "DemoRunner",
    "DemoStep",
    "FixtureRecord",
    "demo_config",
    "format_demo_ko",
    "resolve_fixtures_dir",
    "resolve_output_dir",
    "run_demo",
]
