"""TaskSpec: 학습 과제의 데이터 계약 (strict schema).

- 추가 필드·잘못된 dtype·범위 밖 값·명령 문자열(쉘 메타문자/줄바꿈) 을 거부한다.
- metric/direction 은 task_type 과 일치해야 한다. acceptance 가 없으면 NEEDS_ACCEPTANCE_CRITERIA 이며 임의로 만들지 않는다.
- ID/그룹/사후 결과(excluded_columns)/target 은 feature 가 될 수 없다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from corp_dl_agent.common import StrictModel, canonical_json, sha256_text, validate_strict
from corp_dl_agent.errors import AgentError

TaskType = Literal["regression", "binary_classification"]
MetricName = Literal["mae", "rmse", "r2", "average_precision", "roc_auc", "f1"]
Direction = Literal["min", "max"]

REGRESSION_METRICS: tuple[str, ...] = ("mae", "rmse", "r2")
CLASSIFICATION_METRICS: tuple[str, ...] = ("average_precision", "roc_auc", "f1")
METRIC_DIRECTION: dict[str, str] = {
    "mae": "min",
    "rmse": "min",
    "r2": "max",
    "average_precision": "max",
    "roc_auc": "max",
    "f1": "max",
}
NEEDS_ACCEPTANCE_CRITERIA: Final = "NEEDS_ACCEPTANCE_CRITERIA"

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
# 명령 문자열/주입 의심 패턴: 줄바꿈, 쉘 메타문자, 치환식, 널 문자
_COMMAND_RE = re.compile(
    r"[\r\n\x00;|&`<>]|\$\(|\$\{|\bsudo\b|\brm\s+-|\bdel\s+/|\bpowershell\b|\bcmd(\.exe)?\s*/c"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _check_plain_name(kind: str, value: str, *, allow_space: bool = True) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{kind} 은(는) 비어 있을 수 없습니다")
    if value != value.strip():
        raise ValueError(f"{kind} 앞뒤에 공백이 있습니다: {value!r}")
    if _CONTROL_RE.search(value) or _COMMAND_RE.search(value):
        raise ValueError(f"{kind} 에 명령/제어 문자가 포함되어 있어 거부합니다: {value!r}")
    if not allow_space and any(ch.isspace() for ch in value):
        raise ValueError(f"{kind} 에 공백을 사용할 수 없습니다: {value!r}")
    if len(value) > 200:
        raise ValueError(f"{kind} 이(가) 너무 깁니다 (200자 초과)")
    return value


class Acceptance(StrictModel):
    """업무 허용 기준. 사용자가 입력한 값만 사용한다."""

    metric: MetricName
    threshold: float
    direction: Direction

    @model_validator(mode="after")
    def _direction_matches_metric(self) -> Acceptance:
        expected = METRIC_DIRECTION[self.metric]
        if self.direction != expected:
            raise ValueError(
                f"acceptance.direction 은 metric '{self.metric}' 에 대해 '{expected}' 이어야 합니다"
            )
        if self.threshold != self.threshold or self.threshold in (float("inf"), float("-inf")):
            raise ValueError("acceptance.threshold 는 유한한 수여야 합니다")
        if self.metric in ("r2", "average_precision", "roc_auc", "f1") and not (
            -1.0 <= self.threshold <= 1.0
        ):
            raise ValueError(f"acceptance.threshold 가 metric '{self.metric}' 의 범위(-1~1)를 벗어났습니다")
        if self.metric in ("mae", "rmse") and self.threshold < 0:
            raise ValueError("오차 metric 의 threshold 는 0 이상이어야 합니다")
        return self


class ResourceBudgetSpec(StrictModel):
    """자원 예산. demo/pilot 은 AppConfig.ml 의 상한을 쓰고 custom 은 모든 값을 명시해야 한다."""

    mode: Literal["demo", "pilot", "custom"] = "demo"
    wall_time_seconds: int | None = Field(None, ge=10, le=7 * 24 * 3600)
    max_candidates: int | None = Field(None, ge=1, le=64)
    max_epochs: int | None = Field(None, ge=1, le=10000)
    patience: int | None = Field(None, ge=1, le=1000)

    @model_validator(mode="after")
    def _custom_requires_all(self) -> ResourceBudgetSpec:
        if self.mode == "custom":
            missing = [
                k
                for k in ("wall_time_seconds", "max_candidates", "max_epochs", "patience")
                if getattr(self, k) is None
            ]
            if missing:
                raise ValueError(
                    f"resource_budget.mode=custom 에는 다음 값이 필요합니다: {', '.join(missing)}"
                )
        return self


class TaskSpec(StrictModel):
    task_id: str
    task_type: TaskType
    data_path: str
    target: str
    numeric_features: list[str]
    categorical_features: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    id_column: str
    group_column: str
    split_policy: Literal["group", "time"] = "group"
    time_column: str | None = None
    seed: int = Field(42, ge=0, le=2**31 - 1)
    metric: MetricName
    direction: Direction
    acceptance: Acceptance | None = None
    resource_budget: ResourceBudgetSpec = Field(default_factory=ResourceBudgetSpec)
    data_origin: Literal["synthetic", "corporate"]
    purpose: str = ""
    excluded_columns: list[str] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, v: str) -> str:
        if not _TASK_ID_RE.match(v):
            raise ValueError("task_id 는 영문/숫자/_/./- 만 사용하며 64자 이내여야 합니다")
        return v

    @field_validator("data_path")
    @classmethod
    def _data_path(cls, v: str) -> str:
        _check_plain_name("data_path", v)
        if _URL_RE.match(v):
            raise ValueError("data_path 는 로컬 파일 경로여야 합니다 (URL 불가)")
        if not v.lower().endswith((".csv", ".tsv")):
            raise ValueError("data_path 는 .csv 또는 .tsv 파일이어야 합니다")
        return v

    @field_validator("target", "id_column", "group_column", "time_column")
    @classmethod
    def _column_names(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _check_plain_name("열 이름", v)

    @field_validator("numeric_features", "categorical_features", "excluded_columns")
    @classmethod
    def _column_lists(cls, v: list[str]) -> list[str]:
        for name in v:
            _check_plain_name("열 이름", name)
        dup = sorted({x for x in v if v.count(x) > 1})
        if dup:
            raise ValueError(f"열 이름이 중복되었습니다: {dup}")
        return v

    @field_validator("purpose")
    @classmethod
    def _purpose(cls, v: str) -> str:
        if _CONTROL_RE.search(v.replace("\n", " ")) or _COMMAND_RE.search(v.replace("\n", " ")):
            raise ValueError("purpose 에 명령/제어 문자가 포함되어 있어 거부합니다")
        if len(v) > 2000:
            raise ValueError("purpose 가 너무 깁니다 (2000자 초과)")
        return v

    @field_validator("units")
    @classmethod
    def _units(cls, v: dict[str, str]) -> dict[str, str]:
        for k, u in v.items():
            _check_plain_name("units 열 이름", k)
            if not isinstance(u, str) or not u.strip():
                raise ValueError(f"units['{k}'] 는 비어 있지 않은 단위 문자열이어야 합니다")
            _check_plain_name("단위", u, allow_space=False)
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> TaskSpec:
        if not self.numeric_features and not self.categorical_features:
            raise ValueError("numeric_features 또는 categorical_features 중 하나 이상이 필요합니다")
        overlap = sorted(set(self.numeric_features) & set(self.categorical_features))
        if overlap:
            raise ValueError(f"numeric_features 와 categorical_features 에 같은 열이 있습니다: {overlap}")
        features = list(self.numeric_features) + list(self.categorical_features)
        reserved = {
            "target": self.target,
            "id_column": self.id_column,
            "group_column": self.group_column,
        }
        if self.time_column is not None:
            reserved["time_column"] = self.time_column
        for role, name in reserved.items():
            if name in features:
                raise ValueError(f"{role} '{name}' 은(는) feature 로 사용할 수 없습니다 (누수)")
        if self.id_column == self.group_column:
            raise ValueError("id_column 과 group_column 은 달라야 합니다 (그룹 분할이 행 분할로 퇴화)")
        if self.target in (self.id_column, self.group_column):
            raise ValueError("target 은 id_column/group_column 과 달라야 합니다")
        if self.time_column is not None and self.time_column in (
            self.id_column,
            self.group_column,
            self.target,
        ):
            raise ValueError("time_column 은 id/group/target 과 달라야 합니다")
        leaked = sorted(set(self.excluded_columns) & set(features))
        if leaked:
            raise ValueError(f"excluded_columns 에 있는 열은 feature 가 될 수 없습니다: {leaked}")
        if self.target in self.excluded_columns:
            raise ValueError("target 을 excluded_columns 에 넣을 수 없습니다")
        if self.split_policy == "time" and self.time_column is None:
            raise ValueError("split_policy=time 에는 time_column 이 필요합니다")
        allowed = REGRESSION_METRICS if self.task_type == "regression" else CLASSIFICATION_METRICS
        if self.metric not in allowed:
            raise ValueError(
                f"task_type '{self.task_type}' 에는 metric {list(allowed)} 만 허용됩니다 (입력: {self.metric})"
            )
        if self.direction != METRIC_DIRECTION[self.metric]:
            raise ValueError(
                f"direction 은 metric '{self.metric}' 에 대해 '{METRIC_DIRECTION[self.metric]}' 이어야 합니다"
            )
        if self.acceptance is not None and self.acceptance.metric not in allowed:
            raise ValueError(
                f"acceptance.metric '{self.acceptance.metric}' 은(는) task_type '{self.task_type}' 과 맞지 않습니다"
            )
        unknown_units = sorted(k for k in self.units if k not in self.numeric_features and k != self.target)
        if unknown_units:
            raise ValueError(f"units 의 열이 numeric_features/target 에 없습니다: {unknown_units}")
        return self

    # ---- 편의 속성 -----------------------------------------------------------------
    @property
    def features(self) -> list[str]:
        return list(self.numeric_features) + list(self.categorical_features)

    def required_columns(self) -> list[str]:
        cols = [self.id_column, self.group_column, self.target, *self.features]
        if self.time_column is not None:
            cols.append(self.time_column)
        return cols

    def target_unit(self) -> str:
        return self.units.get(self.target, "")

    def needs_acceptance_criteria(self) -> bool:
        return self.acceptance is None


def _format_errors(err: ValidationError) -> list[str]:
    items: list[str] = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", ())) or "<root>"
        items.append(f"{loc}: {e.get('msg', '')}")
    return items


def taskspec_from_dict(data: Any, *, source: str = "<dict>") -> TaskSpec:
    """dict -> TaskSpec (strict: 문자열 숫자 거부, 추가 필드 거부)."""
    if not isinstance(data, dict):
        raise AgentError("E_SCHEMA_INVALID", f"TaskSpec 최상위는 매핑(dict) 이어야 합니다: {source}")
    try:
        spec: TaskSpec = validate_strict(TaskSpec, data)
    except ValidationError as exc:
        raise AgentError(
            "E_SCHEMA_INVALID",
            f"TaskSpec 검증 실패: {source}",
            hint="추가 필드/잘못된 dtype/범위/명령 문자열은 거부됩니다. 오류 목록의 필드를 수정하세요.",
            details={"errors": _format_errors(exc)},
        ) from exc
    except (TypeError, ValueError) as exc:  # canonical_json 직렬화 불가 등
        raise AgentError(
            "E_SCHEMA_INVALID", f"TaskSpec 을 해석할 수 없습니다: {source}", details={"error": str(exc)[:300]}
        ) from exc
    return spec


def load_taskspec(path: str | Path, *, resolve_relative: bool = True) -> TaskSpec:
    """YAML(.yaml/.yml, safe_load) 또는 JSON(.json) TaskSpec 파일을 strict 검증한다.

    data_path 가 상대 경로이면 TaskSpec 파일이 있는 폴더 기준으로 절대 경로로 바꾼다 (resolve_relative=True).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"TaskSpec 파일을 찾을 수 없습니다: {p}")
    suffix = p.suffix.lower()
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AgentError(
            "E_INPUT_INVALID",
            f"TaskSpec 파일은 UTF-8 이어야 합니다: {p.name}",
            details={"error": str(exc)[:200]},
        ) from exc
    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        else:
            raise AgentError("E_INPUT_INVALID", f"TaskSpec 은 .yaml/.yml/.json 만 지원합니다: {p.name}")
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise AgentError(
            "E_SCHEMA_INVALID", f"TaskSpec 파싱 실패: {p.name}", details={"error": str(exc)[:500]}
        ) from exc
    spec = taskspec_from_dict(data, source=str(p))
    if resolve_relative and not Path(spec.data_path).is_absolute():
        resolved = (p.parent / spec.data_path).resolve()
        spec = spec.model_copy(update={"data_path": str(resolved)})
    return spec


def taskspec_hash(spec: TaskSpec) -> str:
    """TaskSpec 내용 hash. data_path 는 파일 이름만 반영한다 (경로 위치는 환경마다 다르고 내용은 data hash 가 담당)."""
    payload = spec.model_dump(mode="json")
    payload["data_path"] = Path(spec.data_path).name
    return sha256_text(canonical_json(payload))


def taskspec_to_dict(spec: TaskSpec) -> dict[str, Any]:
    return spec.model_dump(mode="json")
