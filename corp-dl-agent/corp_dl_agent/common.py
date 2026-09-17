"""공통 유틸리티: 해시, 원자적 파일 쓰기, 시간, provenance 가 있는 값(Quantity).

third-party 의존은 pydantic 만 사용한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """추가 필드 금지·타입 강제 기본 모델. 모든 schema 모델은 이 클래스를 상속한다."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, frozen=False)


class LaxModel(BaseModel):
    """extra 금지이지만 숫자 문자열 등 관대한 변환 허용 (CSV 유래 입력 전용)."""

    model_config = ConfigDict(extra="forbid", strict=False, validate_assignment=True)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """정렬·공백 없는 JSON. fingerprint 계산에 사용."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def dump_json(obj: Any, *, indent: int = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=_json_default, sort_keys=False)


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """temp -> flush+fsync -> os.replace 로 원자적 교체. 같은 폴더에 temp 를 만든다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> None:
    atomic_write_text(path, dump_json(obj) + "\n")


def read_json(path: str | os.PathLike[str]) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


ValueType = Literal["source", "calculated", "predicted", "missing", "assumed", "conflict"]


class Quantity(StrictModel):
    """값/단위/출처/계산버전/가정 여부를 함께 보존하는 수치.

    value_type:
      source     - 입력 자료에서 그대로 가져온 값 (source_locator 필요)
      calculated - 계산 엔진 결과 (calculation_id 필요)
      predicted  - 학습 모델 예측 (model_run_id 필요)
      missing    - 값 없음 (value=None)
      assumed    - 명시적 scenario 가정값 (assumption=True)
      conflict   - 출처 간 상충 (value=None, notes 에 상충 값 기록)
    """

    value: float | None = None
    unit: str = ""
    value_type: ValueType = "missing"
    source_locator: str | None = None
    calculation_id: str | None = None
    model_run_id: str | None = None
    calculation_version: str | None = None
    assumption: bool = False
    notes: str | None = None

    def is_missing(self) -> bool:
        return self.value is None or self.value_type in ("missing", "conflict")

    @classmethod
    def missing(cls, unit: str = "", notes: str | None = None) -> Quantity:
        return cls(value=None, unit=unit, value_type="missing", notes=notes)

    @classmethod
    def conflict(cls, unit: str, notes: str) -> Quantity:
        return cls(value=None, unit=unit, value_type="conflict", notes=notes)


class Status(StrEnum):
    """검증/기능 상태 표시 (PASS/FAIL/NOT_RUN/BLOCKED/...)."""

    PASS = "PASS"  # noqa: S105 - 상태 문자열이며 비밀값이 아님
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"
    MOCK_TESTED = "MOCK_TESTED"
    SOURCE_ONLY = "SOURCE_ONLY"
    TARGET_UNCONFIRMED = "TARGET_UNCONFIRMED"


class StatusRecord(StrictModel):
    status: Status
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    checked_at: str | None = None


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def stable_id(prefix: str, *parts: str, length: int = 12) -> str:
    """결정적 ID (prefix-<sha256 앞 length자>)."""
    return f"{prefix}-{sha256_text('|'.join(parts))[:length]}"


def validate_strict(model_cls: type[Any], data: Any) -> Any:
    """strict 모델을 python dict 로부터 검증한다.

    strict python 모드는 문자열 -> Enum 변환을 거부하므로 JSON 모드(strict) 로 검증한다.
    JSON strict 모드는 "1"->int 같은 문자열 숫자 변환은 거부하고 int->float 만 허용한다.
    """
    return model_cls.model_validate_json(canonical_json(data), strict=True)
