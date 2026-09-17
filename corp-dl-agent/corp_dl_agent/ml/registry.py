"""MLP 후보 registry: 허용 범위와 모델 종류를 고정한다. 범위 밖 후보는 E_SCHEMA_INVALID.

MLP_SEARCH_SPACE = hidden_dims [[64,32],[128,64]], dropout 0~0.2, learning_rate 1e-4~3e-3, weight_decay 0~1e-3
"""

from __future__ import annotations

import math
import random
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from corp_dl_agent.common import StrictModel, canonical_json, sha256_text, validate_strict
from corp_dl_agent.errors import AgentError

MLP_SEARCH_SPACE: dict[str, Any] = {
    "hidden_dims": [[64, 32], [128, 64]],
    "dropout": (0.0, 0.2),
    "learning_rate": (1e-4, 3e-3),
    "weight_decay": (0.0, 1e-3),
}
ALLOWED_BATCH_SIZES: tuple[int, ...] = (16, 32, 64, 128, 256)
ALLOWED_MODEL_KINDS: tuple[str, ...] = ("sklearn", "mlp")
ALLOWED_ACTIVATIONS: tuple[str, ...] = ("relu",)
MAX_CANDIDATES = 64


class MlpCandidate(StrictModel):
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
    model_kind: Literal["mlp"] = "mlp"
    hidden_dims: list[int]
    dropout: float
    learning_rate: float
    weight_decay: float
    batch_size: int = 64
    activation: Literal["relu"] = "relu"

    @field_validator("hidden_dims")
    @classmethod
    def _hidden_dims(cls, v: list[int]) -> list[int]:
        if v not in MLP_SEARCH_SPACE["hidden_dims"]:
            raise ValueError(
                f"hidden_dims 는 {MLP_SEARCH_SPACE['hidden_dims']} 중 하나여야 합니다 (입력: {v})"
            )
        return v

    @field_validator("dropout", "learning_rate", "weight_decay")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("유한한 수여야 합니다")
        return v

    @field_validator("batch_size")
    @classmethod
    def _batch(cls, v: int) -> int:
        if v not in ALLOWED_BATCH_SIZES:
            raise ValueError(f"batch_size 는 {list(ALLOWED_BATCH_SIZES)} 중 하나여야 합니다 (입력: {v})")
        return v

    @model_validator(mode="after")
    def _ranges(self) -> MlpCandidate:
        for key in ("dropout", "learning_rate", "weight_decay"):
            lo, hi = MLP_SEARCH_SPACE[key]
            val = getattr(self, key)
            if not (lo <= val <= hi):
                raise ValueError(f"{key}={val} 은(는) 허용 범위 [{lo}, {hi}] 밖입니다")
        return self

    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("candidate_id", None)
        return sha256_text(canonical_json(payload))


def candidate_hash(cfg: dict[str, Any]) -> str:
    payload = {k: v for k, v in cfg.items() if k != "candidate_id"}
    return sha256_text(canonical_json(payload))


def validate_candidate(cfg: dict[str, Any]) -> MlpCandidate:
    """dict -> MlpCandidate. 범위/타입/추가 필드 위반은 E_SCHEMA_INVALID. candidate_id 가 없으면 내용 hash 로 만든다."""
    if not isinstance(cfg, dict):
        raise AgentError("E_SCHEMA_INVALID", "후보 설정은 매핑(dict) 이어야 합니다")
    data = dict(cfg)
    if "candidate_id" not in data:
        data["candidate_id"] = "mlp-" + candidate_hash(data)[:12]
    try:
        cand: MlpCandidate = validate_strict(MlpCandidate, data)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(x) for x in e.get('loc', ())) or '<root>'}: {e.get('msg', '')}"
            for e in exc.errors()
        ]
        raise AgentError(
            "E_SCHEMA_INVALID",
            "MLP 후보 설정이 registry 허용 범위를 벗어났습니다",
            hint=f"허용 범위: {MLP_SEARCH_SPACE}, batch_size {list(ALLOWED_BATCH_SIZES)}",
            details={"errors": errors},
        ) from exc
    except (TypeError, ValueError) as exc:
        raise AgentError(
            "E_SCHEMA_INVALID", "MLP 후보 설정을 해석할 수 없습니다", details={"error": str(exc)[:300]}
        ) from exc
    return cand


def _log_uniform(rng: random.Random, lo: float, hi: float) -> float:
    return float(10 ** rng.uniform(math.log10(lo), math.log10(hi)))


def demo_candidates(task_type: str, seed: int, n: int) -> list[MlpCandidate]:
    """결정적(seed) 후보 목록. 첫 후보는 고정 기본값, 나머지는 허용 범위 안에서 샘플."""
    if task_type not in ("regression", "binary_classification"):
        raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")
    if not isinstance(n, int) or n < 1 or n > MAX_CANDIDATES:
        raise AgentError("E_SCHEMA_INVALID", f"후보 수 n 은 1~{MAX_CANDIDATES} 이어야 합니다 (입력: {n})")
    rng = random.Random(f"{task_type}:{seed}")  # noqa: S311 - 결정적 후보 샘플링, 보안 목적 아님
    out: list[MlpCandidate] = []
    seen: set[str] = set()
    base = validate_candidate(
        {
            "candidate_id": f"mlp-{task_type[:3]}-{seed}-000",
            "hidden_dims": [64, 32],
            "dropout": 0.1,
            "learning_rate": 1e-3,
            "weight_decay": 1e-4,
            "batch_size": 64,
        }
    )
    out.append(base)
    seen.add(base.config_hash())
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        cfg = {
            "candidate_id": f"mlp-{task_type[:3]}-{seed}-{len(out):03d}",
            "hidden_dims": list(rng.choice(MLP_SEARCH_SPACE["hidden_dims"])),
            "dropout": round(rng.uniform(*MLP_SEARCH_SPACE["dropout"]), 3),
            "learning_rate": float(f"{_log_uniform(rng, *MLP_SEARCH_SPACE['learning_rate']):.3g}"),
            "weight_decay": round(rng.uniform(*MLP_SEARCH_SPACE["weight_decay"]), 6),
            "batch_size": rng.choice([32, 64, 128]),
        }
        cand = validate_candidate(cfg)
        h = cand.config_hash()
        if h in seen:
            continue
        seen.add(h)
        out.append(cand)
    if len(out) < n:
        raise AgentError("E_INTERNAL", f"서로 다른 후보 {n}개를 만들지 못했습니다 ({len(out)}개)")
    return out
