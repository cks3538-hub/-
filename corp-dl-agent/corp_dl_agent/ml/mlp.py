"""작은 MLP (PyTorch). torch 는 함수 안에서만 lazy import 한다.

- 구조: [Linear -> ReLU -> Dropout] × len(hidden_dims) -> Linear(out_dim). 출력 shape 는 항상 (N, out_dim).
- 회귀: 출력 (N,1) 과 target (N,1) 의 shape/dtype 일치를 검사한다 (MSELoss, reduction="sum" 으로 표본 수 정규화를 호출자가 수행).
- 이진 분류: 출력은 logits (N,1). 손실은 BCEWithLogitsLoss (sigmoid 를 손실 안에서 계산). 예측 확률은 sigmoid(logits).
- 커스텀 autograd 없음. CPU FP32 기본.
- 허용 hidden_dims/dropout 범위는 registry(MlpCandidate) 가 고정한다. 이 모듈은 구조(MlpArch) 만 다룬다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from corp_dl_agent.common import StrictModel
from corp_dl_agent.errors import AgentError, blocked_dependency

ARCH_FORMAT = "corp-dl-agent/mlp-arch/1"
MAX_HIDDEN_LAYERS = 8
MAX_HIDDEN_WIDTH = 4096


def torch_module(feature: str = "MLP 학습") -> Any:
    """torch lazy import. 없으면 E_BLOCKED_DEPENDENCY."""
    try:
        import torch
    except ImportError as exc:
        raise blocked_dependency("torch", feature) from exc
    return torch


class MlpArch(StrictModel):
    """모델 구조 기록 (arch.json). export 번들과 checkpoint 재구성에 사용한다."""

    format: str = ARCH_FORMAT
    task_type: Literal["regression", "binary_classification"]
    in_dim: int = Field(ge=1, le=1_000_000)
    hidden_dims: list[int]
    dropout: float = Field(ge=0.0, le=0.9)
    out_dim: int = Field(1, ge=1, le=1024)
    activation: Literal["relu"] = "relu"
    feature_names: list[str] = Field(default_factory=list)

    @field_validator("format")
    @classmethod
    def _format(cls, v: str) -> str:
        if v != ARCH_FORMAT:
            raise ValueError(f"arch format 이 이 프로그램의 것이 아닙니다: {v}")
        return v

    @field_validator("hidden_dims")
    @classmethod
    def _hidden(cls, v: list[int]) -> list[int]:
        if not v or len(v) > MAX_HIDDEN_LAYERS:
            raise ValueError(f"hidden_dims 는 1~{MAX_HIDDEN_LAYERS}개 층이어야 합니다")
        for h in v:
            if not isinstance(h, int) or isinstance(h, bool) or h < 1 or h > MAX_HIDDEN_WIDTH:
                raise ValueError(f"hidden 폭은 1~{MAX_HIDDEN_WIDTH} 정수여야 합니다 (입력: {h!r})")
        return v

    @model_validator(mode="after")
    def _names(self) -> MlpArch:
        if self.feature_names and len(self.feature_names) != self.in_dim:
            raise ValueError(
                f"feature_names 길이({len(self.feature_names)}) 가 in_dim({self.in_dim}) 과 다릅니다"
            )
        if self.out_dim != 1:
            raise ValueError("이 버전은 out_dim=1 (회귀 값 / 이진 logit) 만 지원합니다")
        return self


_MLP_CLASS: Any = None


def mlp_class() -> Any:
    """torch.nn.Module 하위 클래스 MLP 를 (한 번만) 정의하여 돌려준다. 최상위 torch import 를 피하기 위한 지연 정의."""
    global _MLP_CLASS
    if _MLP_CLASS is not None:
        return _MLP_CLASS
    torch = torch_module()
    nn = torch.nn

    class MLP(nn.Module):  # type: ignore[misc, name-defined]
        """[Linear -> ReLU -> Dropout]*k -> Linear(out_dim). forward(x: (N, in_dim)) -> (N, out_dim)."""

        def __init__(
            self, in_dim: int, hidden_dims: list[int], dropout: float = 0.0, out_dim: int = 1
        ) -> None:
            super().__init__()
            if in_dim < 1 or out_dim < 1:
                raise AgentError(
                    "E_SCHEMA_INVALID", f"in_dim/out_dim 은 1 이상이어야 합니다: {in_dim}/{out_dim}"
                )
            if not hidden_dims:
                raise AgentError("E_SCHEMA_INVALID", "hidden_dims 가 비어 있습니다")
            if not (0.0 <= float(dropout) < 1.0):
                raise AgentError("E_SCHEMA_INVALID", f"dropout 은 [0,1) 이어야 합니다: {dropout}")
            layers: list[Any] = []
            prev = int(in_dim)
            for h in hidden_dims:
                layers.append(nn.Linear(prev, int(h)))
                layers.append(nn.ReLU())
                if dropout > 0.0:
                    layers.append(nn.Dropout(float(dropout)))
                prev = int(h)
            layers.append(nn.Linear(prev, int(out_dim)))
            self.net = nn.Sequential(*layers)
            self.in_dim = int(in_dim)
            self.hidden_dims = [int(h) for h in hidden_dims]
            self.dropout = float(dropout)
            self.out_dim = int(out_dim)

        def forward(self, x: Any) -> Any:
            if x.dim() != 2 or int(x.shape[1]) != self.in_dim:
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"MLP 입력 shape 가 맞지 않습니다: {tuple(x.shape)} (기대: (N, {self.in_dim}))",
                )
            if x.dtype != torch.float32 and x.dtype != torch.float16 and x.dtype != torch.bfloat16:
                raise AgentError("E_INPUT_INVALID", f"MLP 입력 dtype 은 float 이어야 합니다: {x.dtype}")
            return self.net(x)

    _MLP_CLASS = MLP
    return MLP


def build_mlp(arch: MlpArch) -> Any:
    """MlpArch 로 미학습 모델을 만든다 (초기화는 현재 torch RNG 상태를 따른다 → 호출 전 seed 고정)."""
    return mlp_class()(arch.in_dim, list(arch.hidden_dims), arch.dropout, arch.out_dim)


def make_loss(task_type: str) -> Any:
    """reduction="sum" 손실. accumulation 의 마지막 불완전 묶음까지 실제 표본 수로 나누기 위해 합을 쓴다."""
    torch = torch_module()
    if task_type == "regression":
        return torch.nn.MSELoss(reduction="sum")
    if task_type == "binary_classification":
        return torch.nn.BCEWithLogitsLoss(reduction="sum")
    raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")


def check_output_target(output: Any, target: Any, task_type: str) -> None:
    """출력 (N,1) 과 target (N,1) 의 shape/dtype 검사. 분류 target 은 0/1 이어야 한다."""
    torch = torch_module()
    if output.dim() != 2 or output.shape[1] != 1:
        raise AgentError("E_INTERNAL", f"모델 출력 shape 가 (N,1) 이 아닙니다: {tuple(output.shape)}")
    if tuple(target.shape) != tuple(output.shape):
        raise AgentError(
            "E_INTERNAL",
            f"출력 shape {tuple(output.shape)} 와 target shape {tuple(target.shape)} 이 다릅니다",
        )
    if not torch.is_floating_point(target):
        raise AgentError("E_INTERNAL", f"target dtype 은 float 이어야 합니다: {target.dtype}")
    if task_type == "binary_classification":
        if bool(((target != 0) & (target != 1)).any()):
            raise AgentError("E_INPUT_INVALID", "이진 분류 target 은 0/1 이어야 합니다")


def outputs_to_predictions(output: Any, task_type: str) -> Any:
    """(N,1) 출력 -> (N,) 예측. 회귀는 값 그대로(표준화 공간), 분류는 sigmoid 확률."""
    torch = torch_module()
    flat = output.reshape(-1)
    if task_type == "binary_classification":
        return torch.sigmoid(flat.float())
    return flat.float()


def count_parameters(model: Any) -> int:
    return int(sum(int(p.numel()) for p in model.parameters()))


def arch_from_candidate(
    candidate: Any, *, task_type: str, in_dim: int, feature_names: list[str] | None = None
) -> MlpArch:
    """registry.MlpCandidate + 입력 차원 -> MlpArch."""
    return MlpArch(
        task_type=task_type,  # type: ignore[arg-type]
        in_dim=int(in_dim),
        hidden_dims=[int(h) for h in candidate.hidden_dims],
        dropout=float(candidate.dropout),
        out_dim=1,
        activation="relu",
        feature_names=list(feature_names or []),
    )
